#!/usr/bin/env python3
# Copyright 2026 The Krypton Authors
# Use of this source code is governed by the GPL-3.0 license in LICENSE.
"""Save and validate staged hosted builds without replacing Ninja tracking."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(os.environ['KRYPTON_SOURCE_ROOT']).resolve()
TRACKING = ('.ninja_log', '.ninja_deps', 'build.ninja', 'args.gn')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity():
    return {
        'commit': subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                 check=True, capture_output=True, text=True).stdout.strip(),
        'image': os.environ.get('ImageVersion', ''),
        'python': sys.version.split()[0],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('save', 'verify'))
    args = parser.parse_args()
    state_path = ROOT / 'build/checkpoint-state.json'
    graph = ROOT / 'build/src/out/Default'
    if args.command == 'save':
        if state_path.is_file():
            previous = json.loads(state_path.read_text(encoding='utf-8'))
            if any(previous[key] != value for key, value in identity().items()):
                raise ValueError('Preserve the original checkpoint identity; source or tools changed')
        files = {}
        for name in TRACKING:
            path = graph / name
            if path.is_file():
                files[name] = {'sha256': digest(path), 'mtime_ns': path.stat().st_mtime_ns}
        state = {
            **identity(), 'tracking': files,
            'resumable': all(name in files for name in TRACKING)
                         and (ROOT / 'build/.krypton_state.json').is_file(),
        }
        state_path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
        print('Checkpoint saved; resumable:', state['resumable'])
    else:
        saved = json.loads(state_path.read_text(encoding='utf-8'))
        if not saved['resumable']:
            raise ValueError('This recovery checkpoint predates a complete graph; keep it for inspection')
        if any(saved[key] != value for key, value in identity().items()):
            raise ValueError('Checkpoint source, hosted image or Python changed; preserve the original checkpoint')
        for name, expected in saved['tracking'].items():
            path = graph / name
            if digest(path) != expected['sha256'] or path.stat().st_mtime_ns != expected['mtime_ns']:
                raise ValueError(f'Checkpoint tracking content/timestamp changed: {name}')
        print('Checkpoint source, tools, Ninja tracking and timestamps verified.')


if __name__ == '__main__':
    main()
