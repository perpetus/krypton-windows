"""Verify and stage signed Windows release assets; never uploads or publishes."""

import argparse
import configparser
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {'.git', '.agents', '.cache', '.codex', '.deps', '.serena', '.t3',
             'docs', 'out', 'reference', 'rtk', 'node_modules', '__pycache__'}
SKIP_SUFFIXES = {'.dpapi', '.pfx', '.key', '.pem', '.pdb', '.obj', '.lib', '.exp',
                 '.log', '.exe', '.dll', '.msi', '.7z', '.zip'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(*arguments, capture=False):
    result = subprocess.run(list(map(str, arguments)), check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def verify(path):
    run('pwsh', '-NoProfile', '-File', ROOT / 'tools/Verify-Download.ps1', '-Path', path)


def sign(path, updater):
    run('pwsh', '-NoProfile', '-File', updater / 'tools/release-signing.ps1',
        'sign', '-InputFile', path)
    verify(path)


def safe_source(path, relative):
    return (path.is_file() and not path.is_symlink() and
            not any(part in SKIP_DIRS for part in relative.parts) and
            path.suffix.lower() not in SKIP_SUFFIXES and
            path.name not in {'AGENTS.md', 'CLAUDE.md', 'CMakeUserPresets.json'} and
            not path.name.startswith('.env'))


def git_sources(directory, prefix, records, initial=False):
    head = run('git', '-C', directory, 'rev-parse', 'HEAD', capture=True).decode().strip()
    dirty = bool(run('git', '-C', directory, 'status', '--porcelain', capture=True).strip())
    records.append({'directory': prefix.as_posix(), 'commit': head,
                    'working_tree_changes_included': dirty})
    paths = run('git', '-C', directory, 'ls-files', '--cached', '--others',
                '--exclude-standard', '-z', capture=True)
    for name in sorted(set(paths.decode('utf-8').strip('\0').split('\0'))):
        if not name:
            continue
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe Git source path')
        if initial and relative.parts[0] == 'build':
            continue
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        path = directory / relative
        if path.is_dir() and (path / '.git').exists() and not path.is_symlink():
            yield from git_sources(path, prefix / relative, records)
        elif safe_source(path, relative):
            yield path, prefix / relative


def snapshot(output, browser, ui, updater, version, inputs, upstream=None):
    records = []
    sources = list(git_sources(browser, Path('Krypton'), records, initial=True))
    if upstream is not None:
        if (browser / 'upstream/helium-windows/.git').exists():
            raise ValueError('The browser already has a populated upstream checkout')
        expected = run('git', '-C', browser, 'rev-parse',
                       'HEAD:upstream/helium-windows', capture=True).decode().strip()
        actual = run('git', '-C', upstream, 'rev-parse',
                     'HEAD', capture=True).decode().strip()
        if expected != actual:
            raise ValueError('The supplied upstream checkout differs from the pinned revision')
        sources += list(git_sources(upstream, Path('Krypton/upstream/helium-windows'),
                                    records))
    sources += list(git_sources(ui, Path('KryptonUI'), records, initial=True))
    sources += list(git_sources(ROOT, Path('KryptonWindows'), records, initial=True))
    for name in ('src', 'assets', 'resources', 'cmake', 'tools', 'licenses', 'tests'):
        for path in sorted((updater / name).rglob('*')):
            relative = path.relative_to(updater)
            if safe_source(path, relative):
                sources.append((path, Path('KryptonUpdater') / relative))
    for name in ('CMakeLists.txt', 'CMakePresets.json', 'README.md', '.gitignore'):
        path = updater / name
        if path.is_file():
            sources.append((path, Path('KryptonUpdater') / name))
    if not sources:
        raise ValueError('No release sources were found')
    inventory = []
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        used = set()
        for path, name in sorted(sources, key=lambda item: item[1].as_posix()):
            relative = name.as_posix()
            if relative in used:
                continue
            used.add(relative)
            inventory.append({'path': relative, 'sha256': digest(path), 'size': path.stat().st_size})
            archive.write(path, relative)
        archive.writestr('SOURCE_BUILD.md', (
            f'# Krypton {version} source snapshot\n\n'
            'This snapshot includes the current Krypton browser layers, pinned upstream\n'
            'Helium layers, KryptonUI, and KryptonUpdater source used for release preparation.\n'
            'Local working-tree changes are included; see SOURCE_MANIFEST.json for hashes\n'
            'and Git revision records. This is not a reproducible-build attestation.\n\n'
            'Extract the three project directories side by side. Follow Krypton/README.md\n'
            'and Krypton/build.py for Chromium source/dependency retrieval and browser build\n'
            'preparation. The exact Chromium release and download hashes are defined by\n'
            'the bundled upstream/helium-windows/helium-chromium configuration. Chromium\n'
            'and Windows toolchain downloads are not embedded in this snapshot.\n\n'
            'Build KryptonUI with its CMake/dev.ps1 instructions, then build the Krypton\n'
            'browser and supply that output directory to KryptonUpdater/tools/build-offline.ps1.\n'
            'The updater uses static Poco from vcpkg, and Windows signing uses CNG/DPAPI.\n'
            'Public trust keys are included. Private release keys, installed dependencies,\n'
            'compiled binaries, unrelated reference trees, and browser profiles are excluded.\n\n'
            'Krypton browser licensing is in Krypton/LICENSE and its third-party notices.\n'
            'Native UI attributions are in KryptonUI/THIRD_PARTY_NOTICES.md. Preserve all\n'
            'source-file and upstream copyright/license notices.\n'
        ))
        manifest = {'version': version, 'repositories': records, 'release_inputs': inputs,
                    'files': inventory}
        archive.writestr('SOURCE_MANIFEST.json', json.dumps(manifest, indent=2) + '\n')
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('The source archive failed its CRC integrity check')
    return len(inventory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--updater-root', type=Path, default=ROOT.parent / 'KryptonUpdater')
    parser.add_argument('--browser-root', type=Path, default=ROOT.parent / 'Krypton')
    parser.add_argument('--ui-root', type=Path, default=ROOT.parent / 'KryptonUI')
    parser.add_argument('--upstream-root', type=Path,
                        help='Pinned upstream checkout when the browser submodule is not populated')
    parser.add_argument('--browser-stage-record', type=Path)
    parser.add_argument('--dist-dir', type=Path)
    parser.add_argument('--release-type', choices=('prerelease', 'stable'), default='prerelease',
                        help='GitHub release classification; stable is visible to the automatic updater')
    args = parser.parse_args()
    updater = args.updater_root.resolve(strict=True)
    dist = args.dist_dir.resolve(strict=True) if args.dist_dir else updater / 'out/dist'
    plan = json.loads((dist / 'github/release-assets.json').read_text(encoding='utf-8'))
    package = json.loads(Path(plan['source_package']).read_text(encoding='utf-8'))
    version = package['browser_version']
    tag = 'v' + version
    if (plan['repository'] != 'perpetus/krypton-windows' or plan['tag'] != tag or
            re.fullmatch(r'\d+\.\d+\.\d+\.\d+', version) is None):
        raise ValueError('Unexpected release repository, tag, or browser version')
    directory = ROOT / 'release-assets' / tag
    directory.mkdir(parents=True, exist_ok=True)
    update_metadata = dist / 'github/KryptonUpdate-win-x64.json'
    verify(update_metadata)
    signed = json.loads(update_metadata.read_text(encoding='utf-8'))
    if signed['tag'] != tag or signed['repository'] != plan['repository']:
        raise ValueError('Signed metadata does not describe this release')
    if datetime.fromtimestamp(signed['expires'], timezone.utc) <= datetime.now(timezone.utc):
        raise ValueError('The signed update metadata has expired')
    if signed['full']['size'] != package['archive_bytes'] or signed['full']['sha256'] != package['archive_sha256']:
        raise ValueError('The signed update manifest does not match the prepared package')
    update_archive = dist / 'github' / signed['full']['name']
    if update_archive.stat().st_size != signed['full']['size'] or digest(update_archive) != signed['full']['sha256']:
        raise ValueError('Update archive size/hash verification failed')
    runtime = json.loads((Path(plan['source_package']).parent / 'chromium-files.manifest.json').read_text(encoding='utf-8'))
    ui_file = next(item for item in runtime['files'] if item['path'] == f'{version}/krypton_ui.dll')
    ui_binary = args.ui_root / 'out/build/windows/bin/Release/krypton_ui.dll'
    if digest(ui_binary) != ui_file['sha256']:
        raise ValueError('The release package does not contain the current Release UI DLL')
    marker = Path(plan['source_package']).parent / 'payload/KryptonPackage.manifest'
    verify(marker)
    native_package = configparser.ConfigParser()
    native_package.read(marker, encoding='ascii')
    engine_version = native_package['Package']['EngineVersion']
    if (native_package['Package']['Version'] != version or
            re.fullmatch(r'\d+\.\d+\.\d+\.\d+', engine_version) is None):
        raise ValueError('The native package marker does not match the release version')
    setup = dist / f'KryptonSetup-{version}-offline-x64.exe'
    verify(setup)
    setup_hash = digest(setup)
    checksum = Path(str(setup) + '.sha256').read_text(encoding='ascii').split()[0]
    if setup_hash != checksum:
        raise ValueError('The setup checksum sidecar does not match')
    web = dist / f'KryptonSetup-{version}-web-x64.exe'
    verify(web)
    if Path(str(web) + '.sha256').read_text(encoding='ascii').split()[0] != digest(web):
        raise ValueError('The web setup checksum sidecar does not match')
    sources = [setup, Path(str(setup) + '.sig'), Path(str(setup) + '.sha256'),
               web, Path(str(web) + '.sig'), Path(str(web) + '.sha256')]
    for name in plan['assets']:
        if Path(name).name != name:
            raise ValueError('Release asset names must be plain filenames')
        sources.append(dist / 'github' / name)
    assets = []
    for source in sources:
        destination = directory / source.name
        shutil.copy2(source, destination)
        if digest(destination) != digest(source):
            raise ValueError('Staged asset copy failed verification')
        assets.append(destination.name)
    source = directory / f'KryptonSource-{version}.zip'
    inputs = {'setup_sha256': setup_hash,
              'update_sha256': signed['full']['sha256'],
              'krypton_ui_sha256': ui_file['sha256']}
    if args.browser_stage_record:
        staging = json.loads(args.browser_stage_record.read_text(encoding='utf-8'))
        if (staging['version'] != version or staging['engine_version'] != engine_version or
                staging['krypton_ui_sha256'] != ui_file['sha256']):
            raise ValueError('The browser staging record does not match this release')
        inputs['browser_staging'] = {
            key: staging[key] for key in ('version', 'base_version', 'engine_version',
                                          'krypton_ui_sha256', 'restamped') if key in staging
        }
    source_count = snapshot(source, args.browser_root, args.ui_root, updater, version,
                            inputs, args.upstream_root)
    sign(source, updater)
    assets += [source.name, source.name + '.sig']
    sums = directory / 'SHA256SUMS'
    sums.write_bytes(('\n'.join(f'{digest(directory / name)}  {name}' for name in sorted(assets)) + '\n').encode('ascii'))
    sign(sums, updater)
    assets += [sums.name, sums.name + '.sig']
    record = {'repository': plan['repository'], 'tag': tag,
              'prerelease': args.release_type == 'prerelease',
              'version': version, 'engine_version': engine_version, 'assets': assets,
              'notes': f'releases/{tag}.md', 'source_file_count': source_count,
              'krypton_ui_sha256': ui_file['sha256'], 'setup_sha256': setup_hash,
              'update_metadata_expires': datetime.fromtimestamp(signed['expires'], timezone.utc).isoformat()}
    (directory / 'release-assets.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
