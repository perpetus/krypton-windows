"""Verify and stage signed Windows release assets; never uploads or publishes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {'.git', '.agents', '.cache', '.deps', '.serena', 'out', 'reference',
             'node_modules', '__pycache__'}
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


def snapshot(output, browser, ui, updater, version, inputs):
    records = []
    sources = list(git_sources(browser, Path('Krypton'), records, initial=True))
    sources += list(git_sources(ui, Path('KryptonUI'), records, initial=True))
    for name in ('src', 'assets', 'resources', 'cmake', 'tools'):
        for path in sorted((updater / name).rglob('*')):
            relative = path.relative_to(updater)
            if safe_source(path, relative):
                sources.append((path, Path('KryptonUpdater') / relative))
    for name in ('CMakeLists.txt', 'CMakePresets.json', 'README.md', '.gitignore'):
        path = updater / name
        if path.is_file():
            sources.append((path, Path('KryptonUpdater') / name))
    for path in sorted((updater / 'docs').rglob('*.md')):
        sources.append((path, Path('KryptonUpdater') / path.relative_to(updater)))
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
    args = parser.parse_args()
    updater = args.updater_root.resolve(strict=True)
    dist = updater / 'out/dist'
    plan = json.loads((dist / 'github/release-assets.json').read_text(encoding='utf-8'))
    package = json.loads(Path(plan['source_package']).read_text(encoding='utf-8'))
    version = package['browser_version']
    tag = 'v' + version
    if plan['repository'] != 'perpetus/krypton-windows' or plan['tag'] != tag or version != '0.20.1.1':
        raise ValueError('Unexpected first-release repository, tag, or version')
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
    setup = dist / f'KryptonSetup-{version}-offline-x64.exe'
    verify(setup)
    setup_hash = digest(setup)
    checksum = Path(str(setup) + '.sha256').read_text(encoding='ascii').split()[0]
    if setup_hash != checksum:
        raise ValueError('The setup checksum sidecar does not match')
    sources = [setup, Path(str(setup) + '.sig'), Path(str(setup) + '.sha256')]
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
    source_count = snapshot(source, args.browser_root, args.ui_root, updater, version,
                            {'setup_sha256': setup_hash, 'update_sha256': signed['full']['sha256'],
                             'krypton_ui_sha256': ui_file['sha256']})
    sign(source, updater)
    assets += [source.name, source.name + '.sig']
    sums = directory / 'SHA256SUMS'
    sums.write_bytes(('\n'.join(f'{digest(directory / name)}  {name}' for name in sorted(assets)) + '\n').encode('ascii'))
    sign(sums, updater)
    assets += [sums.name, sums.name + '.sig']
    record = {'repository': plan['repository'], 'tag': tag, 'prerelease': True,
              'version': version, 'engine_version': '156.0.8078.47', 'assets': assets,
              'notes': f'releases/{tag}.md', 'source_file_count': source_count,
              'krypton_ui_sha256': ui_file['sha256'], 'setup_sha256': setup_hash,
              'update_metadata_expires': datetime.fromtimestamp(signed['expires'], timezone.utc).isoformat()}
    (directory / 'release-assets.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
