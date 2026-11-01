"""Publish a prepared stable release after verifying every draft asset."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', args.version):
        parser.error('Expected a four-part release version')
    tag = 'v' + args.version
    directory = ROOT / 'release-assets' / tag
    record = json.loads((directory / 'release-assets.json').read_text(encoding='utf-8'))
    if (record['repository'] != REPOSITORY or record['tag'] != tag or
            record['version'] != args.version or record['prerelease']):
        raise ValueError('Unexpected release publication record')
    if run('git', '-C', ROOT, 'status', '--porcelain', capture=True).strip():
        raise ValueError('Commit the release repository before publication')
    head = run('git', '-C', ROOT, 'rev-parse', 'HEAD', capture=True).strip()
    if api(f'repos/{REPOSITORY}/commits/main')['sha'] != head:
        raise ValueError('Push the release commit to main before publication')
    notes = (ROOT / record['notes']).resolve(strict=True)
    if notes.parent != (ROOT / 'releases').resolve():
        raise ValueError('Unexpected release notes path')
    assets = [directory / name for name in record['assets']]
    for asset in assets:
        if asset.parent != directory or not asset.is_file():
            raise ValueError(f'Invalid release asset: {asset}')
    local = {asset.name: {'size': asset.stat().st_size, 'sha256': sha256(asset)}
             for asset in assets}
    for name in ('SHA256SUMS', 'KryptonUpdate-win-x64.json',
                 f'KryptonSetup-{args.version}-offline-x64.exe',
                 f'KryptonSource-{args.version}.zip'):
        run('pwsh', '-NoProfile', '-File', ROOT / 'tools/Verify-Download.ps1',
            '-Path', directory / name)
    releases = api(f'repos/{REPOSITORY}/releases?per_page=100')
    if any(release['tag_name'] == tag for release in releases):
        raise ValueError('This release tag already exists; inspect it before replacing assets')
    run('gh', 'release', 'create', tag, '--repo', REPOSITORY, '--draft',
        '--target', head, '--title', f'Krypton {args.version} - Windows UI improvements',
        '--notes-file', notes)
    run('gh', 'release', 'upload', tag, '--repo', REPOSITORY, *assets)
    release = next(item for item in api(f'repos/{REPOSITORY}/releases?per_page=100')
                   if item['tag_name'] == tag)
    if not release['draft']:
        raise ValueError('Expected an unpublished draft during verification')
    remote = {item['name']: item for item in release['assets']}
    if set(remote) != set(local):
        raise ValueError('Remote release asset names differ from prepared assets')
    for name, expected in local.items():
        uploaded = remote[name]
        if (uploaded['state'] != 'uploaded' or uploaded['size'] != expected['size'] or
                uploaded.get('digest') != 'sha256:' + expected['sha256']):
            raise ValueError(f'Uploaded size/SHA-256 mismatch: {name}')
    print(f'Verified size and SHA-256 for all {len(local)} uploaded assets.', flush=True)
    published = api(f'repos/{REPOSITORY}/releases/{release["id"]}',
                    {'draft': False, 'prerelease': False, 'make_latest': 'true'})
    latest = api(f'repos/{REPOSITORY}/releases/latest')
    if latest['tag_name'] != tag or latest['draft'] or latest['prerelease']:
        raise ValueError('The new release is not visible as the latest stable release')
    check = directory / 'remote-check'
    check.mkdir(exist_ok=True)
    for name in ('KryptonUpdate-win-x64.json', 'KryptonUpdate-win-x64.json.sig'):
        request = urllib.request.Request(
            f'https://github.com/{REPOSITORY}/releases/latest/download/{name}',
            headers={'User-Agent': 'KryptonReleaseVerification/1.0'})
        with urllib.request.urlopen(request, timeout=120) as response:
            contents = response.read(2 * 1024 * 1024)
        if contents != (directory / name).read_bytes():
            raise ValueError(f'The public latest download does not match: {name}')
        (check / name).write_bytes(contents)
    run('pwsh', '-NoProfile', '-File', ROOT / 'tools/Verify-Download.ps1',
        '-Path', check / 'KryptonUpdate-win-x64.json')
    report = {'url': published['html_url'], 'id': published['id'], 'tag': tag,
              'source_commit': head, 'verified_assets': local,
              'public_metadata_signature_verified': True}
    (directory / 'published-release.json').write_text(
        json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(published['html_url'], flush=True)


if __name__ == '__main__':
    main()
