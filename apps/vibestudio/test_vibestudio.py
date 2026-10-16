import importlib.util
import pathlib
import tempfile
import unittest

BASE = pathlib.Path(__file__).parent

class WorkspaceTests(unittest.TestCase):
    def test_safe_explicit_context(self):
        self.assertIsNotNone(importlib.util.find_spec('vibestudio'), 'workspace implementation missing')
        import vibestudio as v
        with tempfile.TemporaryDirectory(dir=BASE) as root:
            p = pathlib.Path(root)
            (p / 'main.py').write_text('print(1)')
            (p / '.env').write_text('secret')
            (p / 'link.py').symlink_to(p / 'main.py')
            (p / 'credentials.py').write_text('secret')
            with v.Workspace(root) as ws:
                self.assertEqual(ws.files(), ['main.py'])
                self.assertEqual(ws.read('main.py'), 'print(1)')
                for name in ['../main.py', '/etc/passwd', '.env', 'link.py', 'credentials.py']:
                    with self.subTest(name=name), self.assertRaises(v.InputError):
                        ws.read(name)

class ClientTests(unittest.TestCase):
    def test_empty_visible_model_output_is_an_error(self):
        import vibestudio as v
        from unittest.mock import patch, MagicMock
        response = MagicMock(status=200)
        response.readline.side_effect = [b'{"thinking":"internal reasoning","response":"","done":true}\n']
        conn = MagicMock()
        conn.getresponse.return_value = response
        with patch('vibestudio.http.client.HTTPConnection', return_value=conn), self.assertRaises(v.ModelError):
            list(v.generate(v.Config(), 'hello'))

    def test_bounded_client_and_context(self):
        import vibestudio as v
        self.assertTrue(hasattr(v, 'prepare'), 'bounded client missing')
        from unittest.mock import patch, MagicMock
        import json
        with tempfile.TemporaryDirectory(dir=BASE) as root, v.Workspace(root) as ws:
            self.assertIn('hello', v.prepare(ws, {'prompt': 'hello'}))
            for payload in [{'prompt': ''}, {'prompt': 'x' * 1201}, {'prompt': 'x', 'uploads': [{'name': 'a.py', 'content': 'a' * 2049}]}, {'prompt': 'x', 'files': '../x'}, {'prompt': 'x', 'uploads': [{'name': '.env', 'content': 'x'}]}, {'prompt': 'x', 'uploads': [{'name': 'a.py', 'content': 'a' * 1800}, {'name': 'b.py', 'content': 'b' * 1800}]}]:
                with self.subTest(payload=str(payload)[:50]), self.assertRaises(v.InputError):
                    v.prepare(ws, payload)
        cfg = v.Config()
        response = MagicMock(status=200)
        response.readline.side_effect = [b'{"response":"<script>","done":false}\n', b'{"response":"ok","done":true}\n']
        conn = MagicMock()
        conn.getresponse.return_value = response
        with patch('vibestudio.http.client.HTTPConnection', return_value=conn):
            self.assertEqual(list(v.generate(cfg, 'hello')), ['<script>', 'ok'])
        sent = json.loads(conn.request.call_args.kwargs['body'])
        self.assertTrue(sent['stream'])
        self.assertIs(sent.get('think'), False)
        self.assertEqual(sent['options']['num_ctx'], 4096)
        self.assertEqual(sent['options']['temperature'], 0.2)
        self.assertEqual(sent['options']['top_p'], 0.9)
        response.status = 404
        with patch('vibestudio.http.client.HTTPConnection', return_value=conn), self.assertRaises(v.ModelError):
            list(v.generate(cfg, 'hello'))
        with patch('vibestudio.http.client.HTTPConnection', side_effect=TimeoutError), self.assertRaises(v.ModelError):
            list(v.generate(cfg, 'hello'))
        with patch.dict('os.environ', {'OLLAMA_HOST': 'https://cloud.example.com'}), self.assertRaises(v.InputError):
            v.Config()

class HTTPTests(unittest.TestCase):
    def test_http_contract_and_browser_safety(self):
        import vibestudio as v
        self.assertTrue(hasattr(v, 'Server'), 'HTTP server missing')
        import http.client
        import json
        import threading
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=BASE) as root, v.Workspace(root) as ws:
            cfg = v.Config()
            server = v.Server(('127.0.0.1', 0), cfg, ws)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host = '127.0.0.1:%s' % server.server_port
            def req(method, path, data=None, headers=None):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                conn.request(method, path, body=data, headers=headers or {})
                res = conn.getresponse()
                result = res.status, dict(res.getheaders()), res.read().decode()
                conn.close()
                return result
            headers = {'Origin': 'http://' + host, 'Content-Type': 'application/json'}
            try:
                self.assertEqual(req('GET', '/healthz')[0], 200)
                self.assertEqual(json.loads(req('GET', '/api/files')[2]), {'files': []})
                self.assertEqual(req('POST', '/api/generate', '{}')[0], 403)
                self.assertEqual(req('POST', '/api/generate', '{}', dict(headers, Origin='https://evil.example'))[0], 403)
                self.assertEqual(req('POST', '/api/generate', '{}', headers)[0], 400)
                self.assertEqual(req('POST', '/api/generate', 'x' * 32769, headers)[0], 413)
                self.assertEqual(req('POST', '/api/generate', '{', headers)[0], 400)
                self.assertEqual(req('POST', '/api/generate', '{}', dict(headers, **{'Content-Type': 'text/plain'}))[0], 415)
                self.assertEqual(req('GET', '/../../etc/passwd')[0], 404)
                with patch('vibestudio.generate', return_value=iter(['<script>alert(1)</script>'])):
                    status, response_headers, body = req('POST', '/api/generate', json.dumps({'prompt': 'hi'}), headers)
                self.assertEqual(status, 200)
                self.assertEqual(response_headers['Content-Type'], 'application/x-ndjson; charset=utf-8')
                events = [json.loads(x) for x in body.splitlines()]
                self.assertEqual(events[0]['text'], '<script>alert(1)</script>')
                self.assertEqual(events[-1]['type'], 'done')
                with patch('vibestudio.generate', side_effect=v.ModelError('not ready')):
                    self.assertEqual(json.loads(req('POST', '/api/generate', '{"prompt":"hi"}', headers)[2])['type'], 'error')
                server.inference.acquire()
                try:
                    self.assertEqual(req('POST', '/api/generate', '{"prompt":"hi"}', headers)[0], 429)
                finally:
                    server.inference.release()
                ui = req('GET', '/')[2]
                js = req('GET', '/app.js')[2]
                self.assertIn('VibeStudio', ui)
                self.assertIn('textContent', js)
                self.assertNotIn('innerHTML', js)
                self.assertIn('Content-Security-Policy', req('GET', '/')[1])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

class ExtraSafetyTests(unittest.TestCase):
    def test_cli_explicit_workspace(self):
        self.assertIsNotNone(importlib.util.find_spec('cli'), 'CLI implementation missing')
        import cli
        import io
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=BASE) as root:
            with patch('cli.generate', return_value=iter(['suggestion'])), patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(cli.main(['--workspace', root, '--prompt', 'hi']), 0)
                self.assertIn('suggestion', out.getvalue())
            with patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(cli.main(['--workspace', root, '--prompt', 'hi', '--file', '../outside.py']), 2)

    def test_client_output_bound(self):
        import vibestudio as v
        from unittest.mock import patch, MagicMock
        import json
        conn = MagicMock()
        conn.getresponse.return_value.status = 200
        conn.getresponse.return_value.readline.side_effect = [json.dumps({'response': 'x' * 40000, 'done': False}).encode()] * 2 + [b'{"done":true}']
        with patch('vibestudio.http.client.HTTPConnection', return_value=conn), self.assertRaises(v.ModelError):
            list(v.generate(v.Config(), 'hi'))

    def test_hidden_directory_symlink_hardlink_and_key(self):
        import vibestudio as v
        import os
        with tempfile.TemporaryDirectory(dir=BASE) as root:
            p = pathlib.Path(root)
            (p / 'sub').mkdir()
            (p / 'sub' / 'a.py').write_text('hello')
            (p / 'alias').symlink_to(p / 'sub', target_is_directory=True)
            (p / '.hidden').mkdir()
            (p / '.hidden' / 'b.py').write_text('hidden')
            private_key_marker = '-----BEGIN ' + 'OPENSSH ' + 'PRIVATE KEY-----'
            (p / 'key.txt').write_text(private_key_marker)
            os.link(p / 'sub' / 'a.py', p / 'hard.py')
            with v.Workspace(root) as ws:
                for name in ['alias/a.py', '.hidden/b.py', 'hard.py', 'key.txt']:
                    with self.subTest(name=name), self.assertRaises(v.InputError):
                        ws.read(name)
                self.assertNotIn('alias/a.py', ws.files())
                self.assertNotIn('.hidden/b.py', ws.files())

    def test_invalid_ollama_records(self):
        import vibestudio as v
        from unittest.mock import patch, MagicMock
        for record in [b'not json', b'[]', b'{"error":"bad"}', b'{"response":4}', b'']:
            conn = MagicMock()
            conn.getresponse.return_value.status = 200
            conn.getresponse.return_value.readline.return_value = record
            with self.subTest(record=record), patch('vibestudio.http.client.HTTPConnection', return_value=conn), self.assertRaises(v.ModelError):
                list(v.generate(v.Config(), 'hi'))

if __name__ == '__main__':
    unittest.main()
