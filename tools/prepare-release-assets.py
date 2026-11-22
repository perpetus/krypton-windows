# Copyright 2026 The Krypton Authors
# Use of this source code is governed by the GPL-3.0 license in LICENSE.
"""Build, sign, and verify the complete Windows upload set from a staged browser."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'perpetus/krypton-windows'
METADATA = 'KryptonUpdate-win-x64.json'
REPOS = ('Krypton', 'KryptonUI', 'KryptonUpdater', 'krypton-windows')
ABI_PATH = 'chrome/browser/ui/krypton_ui/include/krypton_ui/abi.h'
NOTICE_FILES = ('LICENSE', 'README.chromium', 'ThirdPartyNotices.txt', 'Privacy.md',
                'notices/*/LICENSE', 'notices/*/README.chromium')
ENGINE_NOTICES = ('krypton_bergamot', 'krypton_moonshine', 'krypton_onnxruntime')


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


stage = load_module('stage_release', ROOT / 'tools/stage-release.py')
stamp = load_module('prepare_ui_release', ROOT / 'tools/prepare-ui-release.py')
digest = stage.digest


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def capture(*args):
    return subprocess.check_output([str(arg) for arg in args], encoding='utf-8').strip()


def run_logged(logs, name, *args, cwd=None):
    path = logs / f'{name}.log'
    print(f'{name}: {path}', flush=True)
    with path.open('wb') as log:
        subprocess.run([str(arg) for arg in args], cwd=cwd, stdout=log,
                       stderr=subprocess.STDOUT, check=True)
    return path


def version(text):
    parts = text.split('.')
    require(len(parts) == 4 and all(re.fullmatch(r'0|[1-9][0-9]{0,4}', p)
                                   and int(p) <= 65535 for p in parts),
            'Expected a canonical four-part version')
    return tuple(map(int, parts))


def asset_names(update):
    release = update['version']
    names = [METADATA, METADATA + '.sig', 'SHA256SUMS', 'SHA256SUMS.sig',
             f'KryptonSource-{release}.zip', f'KryptonSource-{release}.zip.sig',
             update['full']['name'], *(delta['name'] for delta in update['deltas'])]
    for kind in ('offline', 'web'):
        name = f'KryptonSetup-{release}-{kind}-x64.exe'
        names.extend((name, name + '.sha256', name + '.sig'))
    require(all(Path(name).name == name and not Path(name).drive and name not in ('.', '..')
                for name in names) and len(set(names)) == len(names), 'Unsafe or duplicate release filenames')
    return set(names)


def verify_upload_set(directory):
    update_path = directory / METADATA
    stage.verify(update_path)
    update = read_json(update_path)
    release = update['version']
    version(release)
    require((update['repository'], update['channel'], update['tag']) ==
            (REPOSITORY, 'stable', f'v{release}'), 'Unexpected signed update identity')
    require(update['issued'] <= int(time.time()) < update['expires'], 'Signed update metadata is not current')
    require(update['full']['name'] == f'KryptonUpdate-{release}-win-x64.7z', 'Wrong full package name')
    for delta in update['deltas']:
        require(delta['algorithm'] == 'zucchini' and version(delta['base_version']) < version(release)
                and re.fullmatch(r'[0-9a-f]{64}', delta['base_sha256']), 'Invalid delta identity')
        require(delta['name'] ==
                f"KryptonUpdate-{delta['base_version']}-{delta['base_sha256'][:12]}-to-{release}-win-x64.delta",
                'Wrong delta name')

    expected = asset_names(update)
    plan = read_json(directory / 'release-assets.json')
    upload = (directory / 'UPLOAD-ASSETS.txt').read_text(encoding='utf-8-sig').splitlines()
    require(len(upload) == len(expected) and set(upload) == expected and upload == plan['assets'],
            'Release plan and upload list must match the complete signed asset set')
    require((plan['tag'], plan['version'], plan['repository'], plan['prerelease']) ==
            (update['tag'], release, REPOSITORY, False), 'Release plan does not describe the signed stable update')
    sums = directory / 'SHA256SUMS'
    stage.verify(sums)
    checksums = {}
    for line in sums.read_text(encoding='ascii').splitlines():
        hash_value, name = line.split('  ', 1)
        require(name not in checksums and re.fullmatch('[0-9a-f]{64}', hash_value),
                'Invalid or duplicate checksum entry')
        checksums[name] = hash_value
    require(set(checksums) == expected - {'SHA256SUMS', 'SHA256SUMS.sig'},
            'Signed checksums must cover every other upload')
    for name, hash_value in checksums.items():
        require(digest(directory / name) == hash_value, f'Checksum mismatch: {name}')
    for item in [update['full'], *update['deltas']]:
        require(checksums[item['name']] == item['sha256']
                and (directory / item['name']).stat().st_size == item['size'],
                f"Update differs from its signed metadata: {item['name']}")
    for kind in ('offline', 'web'):
        name = f'KryptonSetup-{release}-{kind}-x64.exe'
        stage.verify(directory / name)
        require((directory / (name + '.sha256')).read_text(encoding='ascii').split() ==
                [checksums[name], name], f'Bad installer checksum sidecar: {name}')
        require(stamp.file_versions(directory / name)[0] == release, f'Wrong installer version: {name}')

    source = directory / f'KryptonSource-{release}.zip'
    stage.verify(source)
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read('SOURCE_MANIFEST.json'))
        require(manifest['version'] == release and
                manifest['release_inputs']['update_sha256'] == update['full']['sha256'],
                'Source snapshot does not correspond to the update')
        files = manifest['files']
        names = {item['path'] for item in files}
        entries = archive.namelist()
        require(len(names) == len(files) and len(entries) == len(set(entries))
                and set(entries) == names | {'SOURCE_MANIFEST.json', 'SOURCE_BUILD.md'},
                'Source archive differs from its inventory')
        for item in files:
            path = item['path']
            data = archive.read(path)
            require(len(data) == item['size'] and hashlib.sha256(data).hexdigest() == item['sha256'],
                    f'Source inventory mismatch: {path}')
            stage.check_public_content(path, data)
        for name in ('SOURCE_MANIFEST.json', 'SOURCE_BUILD.md'):
            stage.check_public_content(name, archive.read(name))
    notes = directory / 'RELEASE_NOTES.md'
    if notes.is_file():
        stage.check_public_content('release notes', notes.read_bytes())
    print(f'Verified {len(expected)} upload files for {release}; GitHub adds two source archives.')
    return update


def verify_public_revisions(directory, update, windows_head):
    source = directory / f"KryptonSource-{update['version']}.zip"
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read('SOURCE_MANIFEST.json'))
    revisions = manifest['release_inputs'].get('public_source_commits')
    if revisions is None:
        revisions = {item['directory']: item['commit'] for item in manifest['repositories']}
    for repository in ('Krypton', 'KryptonUI', 'KryptonUpdater', 'KryptonWindows'):
        sha = revisions[repository]
        require(re.fullmatch(r'[0-9a-f]{40}', sha), f'Invalid source pin for {repository}')
        if repository == 'KryptonWindows':
            require(sha == windows_head, 'The source archive Windows revision differs from the tag target')
        else:
            result = json.loads(capture('gh', 'api', f'repos/perpetus/{repository}/compare/{sha}...main'))
            require(result['status'] in ('ahead', 'identical'),
                    f'{repository} source revision is outside the reviewed public main history')


def ninja_preflight(graph, *, fresh=False):
    cache = dict(line.split('=', 1) for line in (graph / 'CMakeCache.txt').read_text(
        encoding='utf-8').splitlines() if re.match(r'^[A-Za-z_][^=:]*:[A-Z]+=', line))
    ninja = Path(cache.get('CMAKE_MAKE_PROGRAM:FILEPATH') or
                 cache['CMAKE_MAKE_PROGRAM:UNINITIALIZED']).resolve(strict=True)
    cmake = Path(cache['CMAKE_COMMAND:INTERNAL']).resolve(strict=True)
    require(capture(ninja, '--version') == '1.13.2', 'Expected native Ninja 1.13.2')
    if fresh:
        require(not any((graph / name).exists() for name in ('.ninja_log', '.ninja_deps')),
                f'Unexpected Ninja tracking in newly configured graph {graph}')
    else:
        with (graph / '.ninja_log').open('rb') as log:
            require(log.readline().strip() == b'# ninja log v7', f'Incompatible Ninja tracking in {graph}')
        require((graph / '.ninja_deps').is_file(), f'Missing Ninja dependencies in {graph}')
    require((graph / 'build-Release.ninja').is_file(), f'Missing Release graph in {graph}')
    require(cache.get('KR_BUILD_TESTS:BOOL') == cache.get('KR_ALLOW_UNSIGNED:BOOL') == 'OFF',
            f'Expected tests and unsigned packaging disabled in {graph}')
    return ninja, cmake


def build_installer_targets(updater, graph, release, logs, label, targets,
                            package=None, *, initialize=False):
    fresh = not graph.exists()
    if fresh:
        require(initialize, f'Missing updater Release graph: {graph}')
        ninja = Path(shutil.which('ninja') or '').resolve(strict=True)
        cmake = Path(shutil.which('cmake') or '').resolve(strict=True)
        require(capture(ninja, '--version') == '1.13.2', 'Expected native Ninja 1.13.2')
    else:
        ninja, cmake = ninja_preflight(graph)
        shutil.copy2(graph / 'CMakeCache.txt', logs / f'{label}-cache-before.txt')
    settings = [f'-DKR_PRODUCT_VERSION={release}',
                f'-DKR_REQUIRE_OFFLINE:BOOL={"ON" if package else "OFF"}']
    for option, field in (('PAYLOAD', 'archive'), ('MANIFEST', 'manifest'),
                          ('SIGNATURE', 'manifest_signature')):
        settings.append(f'-DKR_OFFLINE_{option}:FILEPATH={package[field] if package else ""}')
    preset = ()
    if fresh:
        preset = ('--preset', 'windows-offline' if package else 'windows')
        settings.extend((f'-DCMAKE_MAKE_PROGRAM:FILEPATH={ninja.as_posix()}',
                         '-DKR_BUILD_TESTS:BOOL=OFF', '-DKR_ALLOW_UNSIGNED:BOOL=OFF'))
    run_logged(logs, f'{label}-configure', cmake, *preset, '-S', updater, '-B', graph, *settings)
    require(ninja_preflight(graph, fresh=fresh)[0] == ninja, 'Ninja changed during configuration')
    command = (ninja, '-C', graph, '-f', 'build-Release.ninja')
    run_logged(logs, f'{label}-plan', *command, '-n', *targets)
    require(ninja_preflight(graph, fresh=fresh)[0] == ninja, 'Ninja changed after the plan')
    log = run_logged(logs, f'{label}-build', *command, *targets)
    require(ninja_preflight(graph)[0] == ninja, 'Ninja changed during the Release build')
    require(not re.search(r'\bwarning(?:\s+[A-Z]+\d+)?:', log.read_text(errors='replace'), re.I),
            f'Review the diagnostics in {log} before packaging')


def published_base(directory, work, package_browser, logs, target_version):
    remote = json.loads(capture('gh', 'api', f'repos/{REPOSITORY}/releases/latest'))
    require(not remote['draft'] and not remote['prerelease'], 'Expected a published stable delta base')
    release = remote['tag_name'].removeprefix('v')
    require(version(target_version) > version(release),
            'Release must be newer than the public stable version')
    full = f'KryptonUpdate-{release}-win-x64.7z'
    names = (METADATA, METADATA + '.sig', full)
    if directory is None:
        directory = work / 'download'
        directory.mkdir()
        patterns = [arg for name in names for arg in ('--pattern', name)]
        run_logged(logs, 'download-published-base', 'gh', 'release', 'download',
                   remote['tag_name'], '--repo', REPOSITORY, '--dir', directory, *patterns)
    assets = {item['name']: item for item in remote['assets']}
    for name in names:
        path = directory / name
        asset = assets[name]
        require(asset['state'] == 'uploaded' and path.stat().st_size == asset['size']
                and 'sha256:' + digest(path) == asset['digest'], f'Published base asset mismatch: {name}')
    metadata_path = directory / METADATA
    stage.verify(metadata_path)
    info = read_json(metadata_path)
    require((info['repository'], info['channel'], info['version'], info['tag']) ==
            (REPOSITORY, 'stable', release, remote['tag_name']), 'Invalid published base identity')
    archive = directory / full
    require(info['full']['name'] == full and info['full']['size'] == archive.stat().st_size
            and info['full']['sha256'] == digest(archive), 'Published base digest does not match signed metadata')
    manifest = work / 'browser.7z.manifest.json'
    manifest.write_bytes(info['package_manifest'].encode('utf-8'))
    manifest.with_suffix('.json.sig').write_bytes(bytes.fromhex(info['package_signature']))
    stage.verify(manifest)
    record = work / 'package.json'
    save_json(record, {'browser_version': release, 'archive': str(archive),
                       'archive_sha256': info['full']['sha256'], 'manifest': str(manifest)})
    native = work / 'helium.7z'
    run_logged(logs, 'extract-base-native', '7z', 'e', archive, native.name, f'-o{work}', '-y')
    native_entry = next(item for item in read_json(manifest)['files'] if item['path'] == native.name)
    require(native.stat().st_size == native_entry['size'] and digest(native) == native_entry['sha256'],
            'Published native archive differs from its signed file inventory')
    helper = work / 'KryptonUpdater.exe'
    helper_entry = f'Helium-bin/{release}/Installer/{helper.name}'
    run_logged(logs, 'extract-base-updater', '7z', 'e', native, helper_entry,
               helper_entry + '.sig', f'-o{work}', '-y')
    stage.verify(helper)
    patcher = work / 'KryptonPatch.exe'
    package_browser.extract_resource(helper, 107, patcher)
    return info, record, patcher, native


def snapshot_inputs(browser, host):
    files = [host / 'version.txt', browser.parents[1] / 'chrome/VERSION',
             *(browser / name for name in ('args.gn', '.ninja_log', '.ninja_deps', 'chrome.exe',
                                           'chrome.dll', 'chrome_elf.dll', 'setup.exe'))]
    return {file: (file.stat().st_mtime_ns, digest(file)) for file in files}


def check_runtime(browser, host, ui_source, ui_dir):
    runtime = load_module('ui_runtime', host / 'tools/krypton/ui_runtime.py')
    abi = host / 'chromium_src' / ABI_PATH
    runtime.verify_contract(abi, ui_source)
    require(abi.read_bytes() == (browser.parents[1] / ABI_PATH).read_bytes(),
            'Prepared browser ABI differs from the owned host ABI')
    runtime.verify_manifest(browser, abi)
    require(runtime.verify_runtime(browser) == runtime.verify_runtime(ui_dir),
            'The staged browser and Release UI runtime differ')
    entries = []
    translations = browser / 'krypton_translation_manifest.json'
    speech = browser / 'krypton_speech_manifest.json'
    if translations.is_file() or (browser / 'krypton_translate.dll').exists():
        item = read_json(translations)
        require(item['path'] == 'krypton_translate.dll', 'Wrong translation engine in build manifest')
        entries.append({**item, 'name': item['path']})
    if speech.is_file() or any((browser / name).exists() for name in ('krypton_speech.dll', 'onnxruntime.dll')):
        speech_files = read_json(speech)['files']
        require({'krypton_speech.dll', 'onnxruntime.dll'} <= {item['name'] for item in speech_files},
                'Voice build manifest is missing its engine or inference runtime')
        entries.extend(speech_files)
    require(len({item['name'] for item in entries}) == len(entries), 'Duplicate native engine manifest entry')
    for item in entries:
        name = item['name']
        require(Path(name).name == name and not Path(name).drive, 'Unsafe engine filename')
        file = browser / name
        require(file.stat().st_size == item['size'] and digest(file) == item['sha256'],
                f'Engine runtime does not match its build manifest: {name}')
    return {item['name']: item['sha256'] for item in entries}


def copy_notices(browser, staged, info, native, work, logs):
    source = browser.parents[1] / 'third_party'
    target = staged.parents[1] / 'third_party'
    for component in ENGINE_NOTICES:
        for pattern in NOTICE_FILES:
            for path in (source / component).glob(pattern):
                if path.is_file():
                    destination = target / path.relative_to(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
    # The official CPU binary shipped with this notice even when it was not in
    # the public source tree. Reuse the signed previous copy for that same DLL.
    notice = target / 'krypton_onnxruntime/Privacy.md'
    if (staged / 'onnxruntime.dll').is_file() and not notice.is_file():
        prefix = f"Helium-bin/{info['version']}"
        run_logged(logs, 'recover-ort-notice', '7z', 'e', native, f'{prefix}/onnxruntime.dll',
                   f'{prefix}/Installer/Licenses/krypton_onnxruntime/Privacy.md', f'-o{work}', '-y')
        previous = work / 'onnxruntime.dll'
        published_notice = work / 'Privacy.md'
        if (previous.is_file() and published_notice.is_file()
                and digest(previous) == digest(staged / 'onnxruntime.dll')):
            notice.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(published_notice, notice)


def verify_native_inventory(work, native_base, base_version, release, runtime, allowed_missing):
    manifest = read_json(work / 'chromium-files.manifest.json')
    require(manifest['version'] == release, 'Unexpected native package version')
    files = {entry['path']: entry['sha256'] for entry in manifest['files']}
    require(len(files) == len(manifest['files']), 'Duplicate native package path')
    for name, sha in runtime.items():
        require(files.get(f'{release}/{name}') == sha, f'Engine file dropped from versioned payload: {name}')
    listing = capture('7z', 'l', '-slt', '-ba', native_base)
    previous = set()
    for entry in listing.replace('\r\n', '\n').split('\n\n'):
        fields = dict(line.split(' = ', 1) for line in entry.splitlines() if ' = ' in line)
        path = fields.get('Path', '').replace('\\', '/')
        if path.startswith('Helium-bin/') and not fields.get('Attributes', '').startswith('D'):
            relative = path.removeprefix('Helium-bin/')
            if relative.startswith(f'{base_version}/'):
                relative = f'{release}/' + relative.removeprefix(f'{base_version}/')
            relative = relative.replace(f'/{base_version}.manifest', f'/{release}.manifest')
            previous.add(relative)
    require(previous, 'Could not read the previous native package inventory')
    missing = previous - files.keys()
    require(missing == set(allowed_missing),
            f'Previous payload removals differ from the approved list: {sorted(missing)}')
    save_json(work / 'runtime-inventory-comparison.json',
              {'previous_files': len(previous), 'new_files': len(files),
               'removed': sorted(missing), 'added': sorted(files.keys() - previous)})


def prepare(args):
    require(os.name == 'nt' and os.environ.get('VSCMD_ARG_TGT_ARCH', '').lower() == 'x64',
            'Open an x64 Visual Studio developer shell before building packages')
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(args.public_root),
            'Choose a fresh output directory outside the public source checkouts')
    require(all(tool for tool in (shutil.which('7z'), shutil.which('pwsh'), shutil.which('gh'))),
            '7z, PowerShell 7, and GitHub CLI are required')
    browser, host, updater, ui_dir = args.browser, args.host_root, args.updater_root, args.ui_dir
    require(browser == (host / 'build/src/out/Default').resolve(),
            'Select the existing host development output with --browser')
    require(not any((host / 'build').glob('*/benchmark-quiet-hold.json')), 'Benchmark quiet hold is active')
    require(args.notes.is_file(), 'Provide reviewed Markdown release notes')
    stage.check_public_content('release notes', args.notes.read_bytes())
    public = args.public_root
    repos = [public / name for name in REPOS]
    for name, checkout in zip(REPOS, repos):
        require(checkout.resolve().parent == public and (checkout / '.git').exists(),
                f'Expected a separate reviewed public checkout for {name}')
    require(host not in repos and updater not in repos, 'Use separate development and public checkouts')
    for name, path in zip(REPOS, repos):
        origin = capture('git', '-C', path, 'remote', 'get-url', 'origin').lower().removesuffix('.git')
        require(origin in (f'https://github.com/perpetus/{name.lower()}',
                           f'git@github.com:perpetus/{name.lower()}'), f'Unexpected public origin for {name}')
        # Resolve and validate all Git pins before opening a build graph.
        for _ in stage.git_sources(path, Path(name), [], initial=True):
            pass
    require(capture('git', '-C', updater, 'rev-parse', 'HEAD^{tree}') ==
            capture('git', '-C', repos[2], 'rev-parse', 'HEAD^{tree}') and
            not capture('git', '-C', updater, 'status', '--porcelain', '--untracked-files=no'),
            'The updater used for the build must match the reviewed public source tree')
    packager = load_module('package_browser', updater / 'tools/package-browser.py')
    compiled, engine = stamp.file_versions(browser / 'chrome.exe')
    release = args.version or compiled
    version(release)
    require(compiled == (host / 'version.txt').read_text().strip() ==
            (repos[0] / 'version.txt').read_text().strip(), 'Browser source and build versions differ')
    require(version(release) >= version(compiled) and (release == compiled or len(release) == len(compiled)),
            'A package-only version bump must be newer and equal-length')
    runtime = check_runtime(browser, host, repos[1], ui_dir)
    online, offline = (updater / 'out/build' / name for name in ('windows', 'windows-offline'))
    for graph in (online, offline):
        if graph.exists() or not args.initialize_updater_graphs:
            ninja_preflight(graph)
    before = snapshot_inputs(browser, host)

    output.mkdir(parents=True)
    logs = output / 'logs'
    work = output / 'work'
    base = work / 'published-base'
    logs.mkdir()
    base.mkdir(parents=True)
    info, base_record, patcher, native_base = published_base(args.base_dir, base, packager, logs, release)
    require(not any(item['tag_name'] == f'v{release}' for item in json.loads(
        capture('gh', 'api', f'repos/{REPOSITORY}/releases?per_page=100'))),
        'This release already exists. Choose a new version to prepare another stable update.')

    staged = browser
    stage_record = None
    if release != compiled:
        copy = work / 'browser-stage'
        run_logged(logs, 'stamp-distribution-copy', sys.executable, '-B', ROOT / 'tools/prepare-ui-release.py',
                   '--browser-dir', browser, '--ui-dir', ui_dir, '--version', release, '--output-dir', copy)
        staged = copy / 'src/out/Default'
        stage_record = copy / 'ui-release-inputs.json'
        copy_notices(browser, staged, info, native_base, base, logs)
        require(stamp.file_versions(staged / 'chrome.exe') == (release, engine), 'Stamped browser version mismatch')
    build_installer_targets(updater, online, release, logs, 'online',
                            ('KryptonWebSetup', 'KryptonUpdater', 'KryptonUninstaller'),
                            initialize=args.initialize_updater_graphs)
    helpers = work / 'helpers'
    helpers.mkdir()
    for name in ('KryptonUpdater.exe', 'KryptonUninstaller.exe'):
        shutil.copy2(online / 'bin/Release' / name, helpers)
    tools = updater / 'tools'
    prereqs = work / 'prerequisites'
    run_logged(logs, 'verify-prerequisites', 'pwsh', '-NoProfile', '-File',
               tools / 'prepare-prerequisites.ps1', '-BrowserDir', staged, '-OutputDir', prereqs,
               '-KryptonUiDir', repos[1], '-UiBinary', ui_dir / 'krypton_ui.dll')
    key = ('--key-path', args.key_path) if args.key_path else ()
    run_logged(logs, 'package-browser', sys.executable, '-B', tools / 'package-browser.py', 'stage',
               '--browser-dir', staged, '--work-dir', work, '--updater', helpers / 'KryptonUpdater.exe',
               '--uninstaller', helpers / 'KryptonUninstaller.exe', '--prerequisites-dir', prereqs, *key)
    package_json = work / 'package.json'
    package = read_json(package_json)
    require(package['browser_version'] == release, 'Wrong packaged native version')
    version_dir = work / 'chromium-payload/Helium-bin' / release
    for name in ('KryptonUpdater.exe', 'KryptonUninstaller.exe'):
        file = version_dir / 'Installer' / name
        stage.verify(file)
        require(stamp.file_versions(file)[0] == release, f'Wrong versioned helper: {name}')
    verify_native_inventory(work, native_base, info['version'], release, runtime, args.allow_removed)
    build_installer_targets(updater, offline, release, logs, 'offline', ('KryptonSetup',),
                            package, initialize=args.initialize_updater_graphs)

    built = (('offline', offline / 'bin/Release/KryptonSetup.exe'),
             ('web', online / 'bin/Release/KryptonWebSetup.exe'))
    dist = output / 'dist'
    dist.mkdir()
    for kind, installer in built:
        destination = dist / f'KryptonSetup-{release}-{kind}-x64.exe'
        require(stamp.file_versions(installer)[0] == release, 'Installer version mismatch')
        shutil.copy2(installer, destination)
        stage.sign(destination, updater, args.key_path)
        destination.with_suffix('.exe.sha256').write_bytes(
            f'{digest(destination)}  {destination.name}\n'.encode('ascii'))
        mode = 'verify-embedded' if kind == 'offline' else 'verify-web'
        extra = ('--package-json', package_json) if kind == 'offline' else ()
        run_logged(logs, f'verify-{kind}', sys.executable, '-B', tools / 'package-browser.py',
                   mode, '--installer', destination, *extra)
    delta = ('--skip-deltas',) if args.without_delta else (
        '--base-package-json', base_record, '--zucchini', patcher,
        '--delta-generator', browser / 'KryptonDeltaGenerator.exe', '--delta-timeout-seconds', 900)
    sequence = max(time.time_ns() // 1_000_000, info['sequence'] + 1)
    run_logged(logs, 'prepare-signed-update', sys.executable, '-B', tools / 'prepare-release.py',
               '--package-json', package_json, '--output-dir', dist / 'github',
               '--sequence', sequence, *delta, *key)
    signed = read_json(dist / 'github' / METADATA)
    require(len(signed['deltas']) == (0 if args.without_delta else 1),
            'Expected a verified delta. Review the delta log before choosing a full-only release.')
    upload = output / 'github'
    stage_args = ('--browser-stage-record', stage_record) if stage_record else ()
    run_logged(logs, 'assemble-complete-upload-set', sys.executable, '-B', ROOT / 'tools/stage-release.py',
               '--browser-root', repos[0], '--ui-root', repos[1], '--updater-source', repos[2],
               '--windows-root', repos[3], '--updater-root', updater, '--ui-binary', ui_dir / 'krypton_ui.dll',
               '--dist-dir', dist, '--output-dir', upload, '--notes-file', args.notes,
               '--release-type', 'stable', *stage_args, *key)
    require(snapshot_inputs(browser, host) == before, 'The development browser or tracking changed during packaging')
    require(check_runtime(browser, host, repos[1], ui_dir) == runtime,
            'The language runtime changed during packaging')
    verify_upload_set(upload)
    print(f'Upload list: {upload / "UPLOAD-ASSETS.txt"}', flush=True)


def main():
    workspace = ROOT.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', help='Package-only version override for an equal-length maintenance bump')
    parser.add_argument('--output', type=Path, help='Fresh directory for the upload set and build evidence')
    parser.add_argument('--notes', type=Path, help='Reviewed release Markdown')
    parser.add_argument('--public-root', type=Path, default=workspace / 'public',
                        help='Root of the four reviewed, scrubbed source checkouts')
    parser.add_argument('--host-root', type=Path, default=workspace / 'Krypton')
    parser.add_argument('--updater-root', type=Path, default=workspace / 'KryptonUpdater')
    parser.add_argument('--browser', type=Path, help='Existing staged development browser output')
    parser.add_argument('--ui-dir', type=Path, default=workspace / 'KryptonUI/out/build/release-max/bin/Release')
    parser.add_argument('--base-dir', type=Path, help='Cached files from the latest public stable release')
    parser.add_argument('--key-path', type=Path, help='Path to the existing DPAPI signing key')
    parser.add_argument('--initialize-updater-graphs', action='store_true',
                        help='Allow initial configuration of missing updater Release graphs')
    parser.add_argument('--without-delta', action='store_true', help='Prepare an explicitly approved full-only release')
    parser.add_argument('--allow-removed', action='append', default=[], metavar='PAYLOAD_PATH',
                        help='An explicitly reviewed removal from the previous native payload')
    parser.add_argument('--verify-only', type=Path, metavar='UPLOAD_DIR',
                        help='Check an existing signed upload set without building')
    args = parser.parse_args()
    if args.verify_only:
        verify_upload_set(args.verify_only.resolve(strict=True))
    else:
        if not args.output or not args.notes:
            parser.error('Packaging requires --output and --notes')
        if args.browser is None:
            args.browser = args.host_root / 'build/src/out/Default'
        for name in ('host_root', 'updater_root', 'browser', 'ui_dir', 'public_root', 'notes', 'base_dir', 'key_path'):
            path = getattr(args, name)
            if path is not None:
                setattr(args, name, path.resolve(strict=True))
        prepare(args)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'Release preparation stopped: {error}')
