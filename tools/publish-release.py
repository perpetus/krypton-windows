"""Publish a prepared stable release after verifying every draft asset."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'perpetus/krypton-windows'


def run(*arguments, capture=False, data=None):
    result = subprocess.run(list(map(str, arguments)), check=True, text=True,
                            encoding='utf-8', input=data,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def api(path, body=None):
    arguments = ['gh', 'api', path]
    if body is not None:
        arguments += ['--method', 'PATCH', '--input', '-']
    return json.loads(run(*arguments, capture=True,
                          data=json.dumps(body) if body is not None else None))


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_update_base(current, update, verifier):
    names = (verifier.METADATA, verifier.METADATA + '.sig')
    with tempfile.TemporaryDirectory(prefix='krypton-published-metadata-') as temporary:
        run('gh', 'release', 'download', current['tag_name'], '--repo', REPOSITORY,
            '--dir', temporary, '--pattern', names[0], '--pattern', names[1])
        for name in names:
            remote = next(asset for asset in current['assets'] if asset['name'] == name)
            file = Path(temporary) / name
            if (file.stat().st_size != remote['size'] or
                    'sha256:' + sha256(file) != remote['digest']):
                raise ValueError(f'Latest release metadata differs from GitHub: {name}')
        metadata = Path(temporary) / names[0]
        verifier.stage.verify(metadata)
        base = verifier.read_json(metadata)
    if (base['tag'] != current['tag_name'] or base['repository'] != REPOSITORY
            or base['channel'] != 'stable' or base['sequence'] >= update['sequence']):
        raise ValueError('The signed update sequence did not advance from the published stable release')
    for delta in update['deltas']:
        if delta['base_version'] == base['version'] and delta['base_sha256'] != base['full']['sha256']:
            raise ValueError('The delta does not apply to the published stable package')
    if update['deltas'] and not any(delta['base_version'] == base['version'] for delta in update['deltas']):
        raise ValueError('The update provides no delta for the current stable release')



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--title', help='GitHub release title')
    parser.add_argument('--assets-dir', type=Path, help='Verified local upload directory')
    parser.add_argument('--public-root', type=Path, help='Reviewed public krypton-windows checkout')
    parser.add_argument('--draft-id', type=int, help='Resume this previously verified draft in place')
    args = parser.parse_args()
    if not re.fullmatch(r'(0|[1-9][0-9]{0,4})(\.(0|[1-9][0-9]{0,4})){3}', args.version):
        parser.error('Expected a four-part release version')
    tag = 'v' + args.version
    if args.assets_dir and not args.public_root:
        parser.error('Local publication requires --public-root pointing to the scrubbed Windows checkout')
    checkout = (args.public_root or ROOT).resolve(strict=True)
    directory = (args.assets_dir or ROOT / 'release-assets' / tag).resolve(strict=True)
    record = json.loads((directory / 'release-assets.json').read_text(encoding='utf-8'))
    if (record['repository'] != REPOSITORY or record['tag'] != tag or
            record['version'] != args.version or record['prerelease']):
        raise ValueError('Unexpected release publication record')
    if run('git', '-C', checkout, 'status', '--porcelain', '--untracked-files=no', capture=True).strip():
        raise ValueError('The scrubbed release checkout must be clean')
    head = run('git', '-C', checkout, 'rev-parse', 'HEAD', capture=True).strip()
    if api(f'repos/{REPOSITORY}/commits/main')['sha'] != head:
        raise ValueError('The release target must match the reviewed public main commit')
    spec = importlib.util.spec_from_file_location('release_assets', ROOT / 'tools/prepare-release-assets.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    update = verifier.verify_upload_set(directory)
    if args.assets_dir:
        verifier.verify_public_revisions(directory, update, head)
        notes = directory / 'RELEASE_NOTES.md'
        if record['notes'] != notes.name or not notes.is_file():
            raise ValueError('The reviewed release notes must accompany the local asset set')
    else:
        notes = (checkout / record['notes']).resolve(strict=True)
        if notes.parent != (checkout / 'releases').resolve():
            raise ValueError('Unexpected release notes path')
    note_text = notes.read_text(encoding='utf-8-sig').rstrip()
    verifier.stage.check_public_content('release notes', note_text.encode('utf-8'))
    assets = [directory / name for name in record['assets']]
    for asset in assets:
        if asset.parent != directory or not asset.is_file():
            raise ValueError(f'Invalid release asset: {asset}')
    local = {asset.name: {'size': asset.stat().st_size, 'sha256': sha256(asset)}
             for asset in assets}
    prefix = f'repos/{REPOSITORY}'
    current = api(f'{prefix}/releases/latest')
    if tuple(map(int, current['tag_name'].removeprefix('v').split('.'))) >= tuple(map(int, args.version.split('.'))):
        raise ValueError('The release version must be newer than the current stable release')
    verify_update_base(current, update, verifier)
    if args.draft_id:
        release = api(f'{prefix}/releases/{args.draft_id}')
    else:
        # Read the draft receipt from the create response to avoid the release
        # list's propagation delay. GitHub may return an untagged draft URL.
        releases = api(f'{prefix}/releases?per_page=100')
        if any(release['tag_name'] == tag for release in releases):
            raise ValueError('This release already exists. Resume only the reviewed draft by ID.')
        url = run('gh', 'release', 'create', tag, '--repo', REPOSITORY, '--draft',
                  '--target', head, '--title', args.title or args.version,
                  '--notes-file', notes, capture=True).strip()
        release = json.loads(run('gh', 'release', 'view', url.rsplit('/', 1)[-1],
                                 '--repo', REPOSITORY, '--json', 'databaseId', capture=True))
        release = api(f'{prefix}/releases/{release["databaseId"]}')
    release_path = f'{prefix}/releases/{release["id"]}'
    print(f'Release draft ID: {release["id"]}', flush=True)

    def verify_draft(draft):
        if (not draft['draft'] or draft['tag_name'] != tag or draft['prerelease']
                or draft['target_commitish'] != head
                or draft['body'].replace('\r\n', '\n').rstrip() != note_text):
            raise ValueError('The draft does not match the approved tag, source commit, or notes')

    verify_draft(release)

    def verify_remote(asset):
        expected = local.get(asset['name'])
        if (not expected or asset['state'] != 'uploaded' or asset['size'] != expected['size'] or
                asset.get('digest') != 'sha256:' + expected['sha256']):
            raise ValueError(f'Uploaded size/SHA-256 mismatch: {asset["name"]}')

    for asset in release['assets']:
        verify_remote(asset)
    uploaded = {item['name'] for item in release['assets']}
    missing = [path for path in assets if path.name not in uploaded]
    if missing:
        run('gh', 'release', 'upload', tag, '--repo', REPOSITORY, *missing)
    release = api(release_path)
    verify_draft(release)
    if ({item['name'] for item in release['assets']} != set(local)
            or len(release['assets']) != len(local)):
        raise ValueError('Remote draft differs from the complete upload set')
    for asset in release['assets']:
        verify_remote(asset)
    print(f'Verified size and SHA-256 for all {len(local)} uploaded assets.', flush=True)
    latest = api(f'{prefix}/releases/latest')
    if latest['id'] != current['id']:
        raise ValueError('The latest stable release changed during this upload')
    refs = run('git', 'ls-remote', f'https://github.com/{REPOSITORY}.git',
               f'refs/tags/{tag}', f'refs/tags/{tag}^{{}}', capture=True)
    if refs.strip() and any(line.split()[0] != head for line in refs.splitlines()):
        raise ValueError('The release tag points at a different source commit')
    published = api(release_path, {'draft': False, 'prerelease': False, 'make_latest': 'true'})
    if (published['draft'] or published['prerelease'] or not published['zipball_url']
            or not published['tarball_url'] or len(published['assets']) != len(local)
            or {item['name'] for item in published['assets']} != set(local)):
        raise ValueError('Published release differs from the verified draft')
    for asset in published['assets']:
        verify_remote(asset)
    if api(f'{prefix}/git/ref/tags/{tag}')['object']['sha'] != head:
        raise ValueError('Published tag differs from the reviewed source commit')
    for attempt in range(6):
        latest = api(f'repos/{REPOSITORY}/releases/latest')
        if latest['tag_name'] == tag and not latest['draft'] and not latest['prerelease']:
            break
        if attempt == 5:
            raise ValueError('The new release is not visible as the latest stable release')
        time.sleep(min(15, 2 ** attempt))
    check = directory / 'remote-check'
    check.mkdir(exist_ok=True)
    for name in ('KryptonUpdate-win-x64.json', 'KryptonUpdate-win-x64.json.sig'):
        request = urllib.request.Request(
            f'https://github.com/{REPOSITORY}/releases/latest/download/{name}',
            headers={'User-Agent': 'KryptonReleaseVerification/1.0'})
        for attempt in range(6):
            with urllib.request.urlopen(request, timeout=120) as response:
                contents = response.read(2 * 1024 * 1024)
            if contents == (directory / name).read_bytes():
                break
            if attempt == 5:
                raise ValueError(f'The public latest download does not match: {name}')
            time.sleep(min(15, 2 ** attempt))
        (check / name).write_bytes(contents)
    run('pwsh', '-NoProfile', '-File', ROOT / 'tools/Verify-Download.ps1',
        '-Path', check / 'KryptonUpdate-win-x64.json')
    report = {'url': published['html_url'], 'id': published['id'], 'tag': tag,
              'source_commit': head, 'verified_assets': local,
              'public_download_count': len(local) + 2,
              'public_metadata_signature_verified': True}
    (directory / 'published-release.json').write_text(
        json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(published['html_url'], flush=True)


if __name__ == '__main__':
    main()
