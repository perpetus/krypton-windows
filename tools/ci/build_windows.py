#!/usr/bin/env python3
# Copyright 2026 The Krypton Authors
# Use of this source code is governed by the GPL-3.0 license in LICENSE.
"""Build production targets across checkpointed GitHub-hosted Windows runners.

Never cleans, replaces an existing graph, or silently updates prepared sources.
"""

import argparse
import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(os.environ['KRYPTON_SOURCE_ROOT']).resolve()
sys.path.insert(0, str(ROOT / 'tools'))
from krypton import layout, ui_runtime


def load_build():
    spec = importlib.util.spec_from_file_location('krypton_build', ROOT / 'build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_tracking(ninja, output):
    # --version must run separately, before any command that opens the graph.
    version = subprocess.run([ninja, '--version'], check=True, capture_output=True,
                             text=True).stdout.strip()
    if version != '1.13.2':
        raise ValueError(f'Expected verified Ninja 1.13.2, found {version}: {ninja}')
    if (output / 'build.ninja').exists():
        for name in ('.ninja_log', '.ninja_deps'):
            if not (output / name).is_file():
                raise ValueError(f'Existing graph is missing {name}; preserve it for inspection')
        with (output / '.ninja_log').open(encoding='utf-8') as stream:
            if stream.readline().strip() != '# ninja log v7':
                raise ValueError('Incompatible Ninja log; no graph was opened')


def stage_runtime(runtime):
    destination = layout.SOURCE_TREE / 'krypton_ui_runtime'
    files = (*ui_runtime.RUNTIME_FILES, Path('runtime-manifest.json'))
    backup = layout.BUILD_DIR / 'ci' / f'runtime-before-{os.environ["GITHUB_RUN_ID"]}-{os.environ.get("GITHUB_RUN_ATTEMPT", "1")}'
    if backup.exists():
        raise ValueError(f'Runtime backup already exists: {backup}')
    for relative in files:
        source, target = runtime / relative, destination / relative
        if target.is_file() and target.read_bytes() == source.read_bytes():
            continue
        if target.is_file():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    ui_runtime.verify_manifest(destination, layout.KRYPTON_UI_ABI)


def stage_browser_payload(output, report):
    """Keep Chromium's distribution layout for downstream installer packaging."""
    destination = layout.BUILD_DIR / 'ci' / (
        f'browser-payload-{os.environ["GITHUB_RUN_ID"]}-{os.environ.get("GITHUB_RUN_ATTEMPT", "1")}')
    destination.mkdir(parents=True, exist_ok=False)
    rules_file = layout.SOURCE_TREE / 'chrome/installer/mini_installer/chrome.release'
    rules = configparser.ConfigParser(interpolation=None, strict=False)
    rules.optionxform = str
    rules.read(rules_file, encoding='utf-8')
    if not rules.sections():
        raise ValueError('Missing Chromium distribution manifest')
    selected = set()
    for section in rules.sections():
        for pattern in rules[section]:
            if pattern.lower().endswith(('.pdb', '.lib', '.exp', '.obj')):
                continue
            selected.update(path for path in output.glob(pattern.replace('\\', '/')) if path.is_file())
    # Local dependency closure, native setup, driver, and the static patch helper.
    selected.update(output.glob('*.dll'))
    selected.update(output / name for name in (
        'chrome.exe', 'chromedriver.exe', 'setup.exe', 'mini_installer.exe',
        'zucchini.exe', 'KryptonPatch.exe'))
    selected.update(output / relative for relative in ui_runtime.RUNTIME_FILES)
    selected.add(rules_file)
    selected.add(layout.SOURCE_TREE / 'LICENSE')
    files = {}
    for path in sorted(selected):
        relative = path.relative_to(layout.SOURCE_TREE)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing or linked distribution input: {relative}')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        files[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    report['files'] = files
    (destination / 'browser-build.json').write_text(
        json.dumps(report, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--ui-commit', required=True)
    parser.add_argument('--initialize', action='store_true',
                        help='Allow first preparation only when the runner has no source tree')
    parser.add_argument('--stage-started-at', type=int,
                        help='Hosted stage start time; reserve the last hour for checkpoints')
    args = parser.parse_args()
    if os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('Use this entry point only on a Windows Actions runner')
    runtime = args.runtime.resolve()
    ui_runtime.verify_manifest(runtime, layout.KRYPTON_UI_ABI)
    info = json.loads((runtime / 'build-info.json').read_text(encoding='utf-8'))
    if info.get('repository') != 'perpetus/KryptonUI' or info.get('commit') != args.ui_commit:
        raise ValueError('UI runtime source revision differs from the upstream job output')
    build = load_build()
    output = layout.SOURCE_TREE / 'out/Default'
    ninja = shutil.which(os.environ.get('KRYPTON_NINJA') or 'ninja')
    if not ninja:
        raise ValueError('Provision native Ninja 1.13.2 or set KRYPTON_NINJA')
    check_tracking(ninja, output)
    # Also make the environment helper resolve this exact, checked executable.
    os.environ['PATH'] = str(Path(ninja).parent) + os.pathsep + os.environ.get('PATH', '')
    fingerprint = build._prepare_fingerprint()
    if (layout.SOURCE_TREE / 'BUILD.gn').exists():
        if build._read_state().get('prepare') != fingerprint:
            raise ValueError('Prepared source inputs changed. Synchronize the runner source '
                             'with the owned preparation tools; preserve out/Default and tracking. '
                             'This workflow will not clean or reprepare the existing tree.')
        if not (output / 'build.ninja').is_file():
            raise ValueError('Prepared source exists without a configured graph; provision it explicitly')
    else:
        if not args.initialize or layout.SOURCE_TREE.exists():
            raise ValueError('No prepared source. Enable initialize only for a new, empty CI runner')
        subprocess.run([sys.executable, str(ROOT / 'build.py'), '--configure-only',
                        '--krypton-ui-runtime', str(runtime)], cwd=ROOT, check=True)
        # The new graph has never compiled, so tracking files do not exist yet.
        if not (output / 'build.ninja').is_file():
            raise ValueError('Initial configuration did not produce build.ninja')
    if (layout.SOURCE_TREE / layout.KRYPTON_UI_ABI.relative_to(ROOT / 'chromium_src')).read_bytes() != layout.KRYPTON_UI_ABI.read_bytes():
        raise ValueError('Prepared browser C ABI differs from the owned contract')
    gn_args = (output / 'args.gn').read_text(encoding='utf-8')
    for assignment in ('krypton_browser_link_jobs = 1', 'krypton_thinlto_jobs = 1',
                       'krypton_concurrent_links = 1'):
        if assignment not in gn_args:
            raise ValueError(f'Runner must retain single-process/worker linking: {assignment}')
    stage_runtime(runtime)
    os.chdir(layout.SOURCE_TREE)
    command = (ninja, '-C', 'out/Default', 'chrome', 'chromedriver',
               'setup', 'mini_installer', 'zucchini', 'KryptonPatch.exe')
    if args.stage_started_at:
        remaining = int(args.stage_started_at + 5 * 60 * 60 - time.time())
        if remaining < 60:
            return 42
        build._run_build_process_timeout(*command, timeout=remaining)
    else:
        build._run_build_process(*command)
    for name in ('chrome.exe', 'chrome.dll', 'chromedriver.exe', 'setup.exe',
                 'mini_installer.exe', 'zucchini.exe', 'KryptonPatch.exe'):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'Missing production output: {path}')
    for relative, digest in ui_runtime.verify_runtime(runtime).items():
        if hashlib.sha256((output / relative).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Output runtime differs from the UI build: {relative}')
    report = layout.BUILD_DIR / 'ci' / 'build-info.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    information = {
        'repository': 'perpetus/Krypton',
        'commit': subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                 check=True, capture_output=True, text=True).stdout.strip(),
        'ui_commit': args.ui_commit,
        'chromium_version': layout.chromium_version(),
        'product_version': layout.KRYPTON_VERSION.read_text(encoding='utf-8').strip(),
        'runtime': ui_runtime.verify_runtime(output),
        'ninja': '1.13.2',
        'run_id': os.environ['GITHUB_RUN_ID'],
        'run_repository': os.environ['GITHUB_REPOSITORY'],
    }
    stage_browser_payload(output, information)
    report.write_text(json.dumps(information, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
