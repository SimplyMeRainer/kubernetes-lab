"""Offline manifest regressions; never import deployment scripts or contact Kubernetes.

Run: python3 -m unittest discover -s tests -v
Set LAB_OPERATIONAL_ROOT to also check operational artifacts and generators.
"""
import ast
import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ('vibestudio.py', 'cli.py', 'index.html', 'app.js', 'style.css')


def layouts():
    roots = [ROOT]
    if os.environ.get('LAB_OPERATIONAL_ROOT'):
        roots.append(Path(os.environ['LAB_OPERATIONAL_ROOT']).resolve())
    for root in dict.fromkeys(roots):
        if (root / 'manifests').is_dir():
            yield root, root / 'manifests/05-ollama.json', root / 'manifests/06-vibestudio.json', root / 'manifests/07-home-dashboard.json'
        else:
            yield root, root / 'clusters/lab/ai/ollama.json', root / 'clusters/lab/ai/vibestudio.json', root / 'clusters/lab/home-dashboard/resources.json'


def resource(path, kind, name):
    matches = [x for x in json.loads(path.read_text())['items']
               if x['kind'] == kind and x['metadata']['name'] == name]
    assert len(matches) == 1, (path, kind, name)
    return matches[0]


class ManifestTests(unittest.TestCase):
    def test_ollama_denies_all_steady_state_egress(self):
        for root, ollama, _, _ in layouts():
            with self.subTest(root=str(root)):
                spec = resource(ollama, 'NetworkPolicy', 'ollama-internal-only')['spec']
                self.assertEqual(set(spec['policyTypes']), {'Ingress', 'Egress'})
                self.assertEqual(spec.get('egress'), [])
                self.assertEqual(spec['podSelector'], {'matchLabels': {'app': 'ollama'}})
                generator = root / 'deploy-ollama.py'
                if generator.exists():
                    tree = ast.parse(generator.read_text())
                    call = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                                and any(isinstance(t, ast.Name) and t.id == 'policy' for t in n.targets))
                    assert isinstance(call, ast.Call)
                    self.assertEqual(ast.literal_eval(call.args[2]), spec)

    def test_configmap_matches_all_five_runtime_sources(self):
        canonical = {name: (ROOT / 'apps/vibestudio' / name).read_text() for name in RUNTIME}
        for root, _, manifest, _ in layouts():
            maps = [x for x in json.loads(manifest.read_text())['items']
                    if x['kind'] == 'ConfigMap' and 'vibestudio.py' in x.get('data', {})]
            self.assertEqual(len(maps), 1)
            self.assertEqual(set(maps[0]['data']), set(RUNTIME))
            for name in RUNTIME:
                with self.subTest(root=str(root), source=name):
                    self.assertEqual(maps[0]['data'][name], canonical[name])
                    self.assertEqual((root / 'apps/vibestudio' / name).read_text(), canonical[name])

    def test_homeassistant_monitor_uses_service_port(self):
        for root, _, _, manifest in layouts():
            with self.subTest(root=str(root)):
                service = resource(manifest, 'Service', 'homeassistant')
                config = resource(manifest, 'ConfigMap', 'homepage-config')
                groups = json.loads(config['data']['services.yaml'])
                monitor = next(entry['Home Assistant']['siteMonitor']
                               for group in groups for entries in group.values()
                               for entry in entries if 'Home Assistant' in entry)
                url = urlsplit(monitor)
                self.assertEqual(monitor, 'http://homeassistant.home.svc.cluster.local/')
                self.assertEqual(url.port or 80, service['spec']['ports'][0]['port'])
                generator = root / 'deploy-home-dashboard.py'
                if generator.exists():
                    strings = [n.value for n in ast.walk(ast.parse(generator.read_text()))
                               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                    self.assertIn(monitor, strings)
                    self.assertNotIn('http://homeassistant.home.svc.cluster.local:8123/', strings)


if __name__ == '__main__':
    unittest.main()
