"""Explicit-workspace local Ollama client. Never writes or runs suggestions."""
import argparse
import sys
from vibestudio import Config, Workspace, prepare, generate, InputError, ModelError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help='Existing explicit workspace root; no symlink components')
    parser.add_argument('--prompt', required=True, help='Request, at most 1200 UTF-8 bytes')
    parser.add_argument('--file', action='append', default=[], help='Relative source file to include; repeat up to 8 times')
    args = parser.parse_args(argv)
    try:
        config = Config()
        with Workspace(args.workspace) as ws:
            prompt = prepare(ws, {'prompt': args.prompt, 'files': args.file})
        for text in generate(config, prompt):
            # Prevent model-supplied terminal escape sequences, including OSC.
            safe = ''.join(c for c in text if c in '\n\t' or (ord(c) >= 32 and not 127 <= ord(c) <= 159))
            print(safe, end='', flush=True)
        print()
        return 0
    except (InputError, ModelError, OSError, ValueError) as exc:
        print('VibeStudio: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
