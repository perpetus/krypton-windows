"""Stage an existing Chromium build for a UI maintenance release.

This follows Chromium's alternate_version_generator approach: equal-length
release strings in read-only data/resources and matching fixed file-version
fields are updated without changing code, section layout, or the engine version.
The original build is never modified. No private keys or browser profiles enter
the staging directory.
"""

import argparse
import configparser
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_versions(path):
    api = ctypes.WinDLL('version', use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR,
                                           ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                      wintypes.DWORD, wintypes.LPVOID]
    api.GetFileVersionInfoW.restype = wintypes.BOOL
    api.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR,
                                  ctypes.POINTER(ctypes.c_void_p),
                                  ctypes.POINTER(wintypes.UINT)]
    api.VerQueryValueW.restype = wintypes.BOOL
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size or size > 1024 * 1024:
        raise ValueError(f'Missing or oversized version resource: {path}')
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, data):
        raise ctypes.WinError(ctypes.get_last_error())
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not api.VerQueryValueW(data, '\\', ctypes.byref(pointer),
                             ctypes.byref(length)) or length.value < 52:
        raise ValueError(f'Invalid fixed version resource: {path}')
    values = struct.unpack('<13I', ctypes.string_at(pointer, 52))
    if values[0] != 0xFEEF04BD:
        raise ValueError(f'Invalid version signature: {path}')

    def version(index):
        high, low = values[index:index + 2]
        return '.'.join(map(str, (high >> 16, high & 65535,
                                 low >> 16, low & 65535)))

    return version(2), version(4)


def pe_sections(data):
    if data[:2] != b'MZ':
        raise ValueError('Expected a Windows PE image')
    offset = struct.unpack_from('<I', data, 0x3C)[0]
    if data[offset:offset + 4] != b'PE\0\0':
        raise ValueError('Invalid PE signature')
    count = struct.unpack_from('<H', data, offset + 6)[0]
    optional_size = struct.unpack_from('<H', data, offset + 20)[0]
    optional = offset + 24
    if struct.unpack_from('<H', data, optional)[0] != 0x20B:
        raise ValueError('This release tool requires an x64 PE image')
    certificate, certificate_size = struct.unpack_from('<II', data,
                                                       optional + 112 + 4 * 8)
    if certificate or certificate_size:
        raise ValueError('Refusing to invalidate an Authenticode-signed image')
    table = optional + optional_size
    result = []
    for index in range(count):
        section = table + index * 40
        name = bytes(data[section:section + 8]).rstrip(b'\0')
        size, start = struct.unpack_from('<II', data, section + 16)
        if start + size > len(data):
            raise ValueError('PE section extends outside the image')
        result.append((name, start, size))
    return optional + 64, result


def stamp(path, previous, target):
    before = path.read_bytes()
    data = bytearray(before)
    checksum_offset, sections = pe_sections(data)
    old_parts = list(map(int, previous.split('.')))
    new_parts = list(map(int, target.split('.')))
    old_pair = (old_parts[0] << 16 | old_parts[1],
                old_parts[2] << 16 | old_parts[3])
    new_pair = (new_parts[0] << 16 | new_parts[1],
                new_parts[2] << 16 | new_parts[3])
    changes = 0
    code_hashes = {}
    for name, start, size in sections:
        if name not in (b'.rdata', b'.rsrc'):
            code_hashes[name.decode('ascii', 'replace')] = hashlib.sha256(
                before[start:start + size]).hexdigest()
            continue
        content = bytes(data[start:start + size])
        for encoding in ('ascii', 'utf-16le'):
            # Embedded assembly manifests use quoted version values rather
            # than NUL-terminated strings. Update those dependencies as well.
            old = previous.encode(encoding)
            new = target.encode(encoding)
            changes += content.count(old)
            content = content.replace(old, new)
        data[start:start + size] = content
        if name == b'.rsrc':
            cursor = start
            while True:
                cursor = data.find(b'\xbd\x04\xef\xfe', cursor, start + size)
                if cursor < 0:
                    break
                if cursor + 52 <= start + size:
                    values = struct.unpack_from('<13I', data, cursor)
                    if values[1] == 0x10000:
                        for index in (2, 4):
                            if values[index:index + 2] == old_pair:
                                struct.pack_into('<II', data, cursor + index * 4,
                                                 *new_pair)
                                changes += 1
                cursor += 4
    if not changes:
        return None
    if len(data) != len(before):
        raise ValueError('Version replacement changed PE layout')
    for name, start, size in sections:
        key = name.decode('ascii', 'replace')
        if key in code_hashes and hashlib.sha256(
                data[start:start + size]).hexdigest() != code_hashes[key]:
            raise ValueError(f'Unexpected change to PE section {key}')
    path.write_bytes(data)
    imagehlp = ctypes.WinDLL('imagehlp', use_last_error=True)
    imagehlp.MapFileAndCheckSumW.argtypes = [wintypes.LPCWSTR,
                                          ctypes.POINTER(wintypes.DWORD),
                                          ctypes.POINTER(wintypes.DWORD)]
    imagehlp.MapFileAndCheckSumW.restype = wintypes.DWORD
    old_sum, new_sum = wintypes.DWORD(), wintypes.DWORD()
    if imagehlp.MapFileAndCheckSumW(str(path), ctypes.byref(old_sum),
                                  ctypes.byref(new_sum)) != 0:
        raise ValueError(f'Could not calculate PE checksum: {path}')
    with path.open('r+b') as stream:
        stream.seek(checksum_offset)
        stream.write(struct.pack('<I', new_sum.value))
    return {'name': path.name, 'replacements': changes,
            'before_sha256': hashlib.sha256(before).hexdigest(),
            'after_sha256': digest(path), 'unchanged_sections': code_hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-dir', type=Path, required=True)
    parser.add_argument('--ui-dir', type=Path, required=True,
                        help='Built Release UI runtime directory')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    browser = args.browser_dir.resolve(strict=True)
    ui = args.ui_dir.resolve(strict=True)
    output = args.output_dir.resolve()
    previous, engine = file_versions(browser / 'chrome.exe')
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', args.version):
        parser.error('Expected a canonical four-part release version')
    if (len(previous) != len(args.version) or
            any(part != str(int(part)) or int(part) > 65535
                for part in args.version.split('.')) or
            tuple(map(int, args.version.split('.'))) <=
            tuple(map(int, previous.split('.')))):
        parser.error('The maintenance version must be newer and equal-length')
    output.mkdir(parents=True, exist_ok=False)
    destination = output / 'src/out/Default'
    destination.mkdir(parents=True)
    source_root = browser.parents[1]
    distribution = Path('chrome/installer/mini_installer/chrome.release')
    rules = configparser.ConfigParser(interpolation=None, strict=False)
    rules.optionxform = str
    rules.read(source_root / distribution, encoding='utf-8')
    if not rules.sections():
        raise ValueError('Missing Chromium runtime distribution manifest')
    inputs = set()
    for section in rules.sections():
        for pattern, _ in rules.items(section):
            if pattern.lower().endswith(('.pdb', '.lib', '.exp', '.obj')):
                continue
            inputs.update(path for path in browser.glob(pattern.replace('\\', '/'))
                          if path.is_file())
    inputs.update(browser.glob('*.dll'))
    inputs.add(browser / 'setup.exe')
    copies = []
    for source in sorted(inputs):
        if source.name.lower() in ('winsparkle.dll', 'helium_update_helper.exe'):
            continue
        relative = source.relative_to(browser)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copies.append({'path': relative.as_posix(), 'sha256': digest(source)})
    target_manifest = output / 'src' / distribution
    target_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_root / distribution, target_manifest)
    shutil.copy2(source_root / 'LICENSE', output / 'src/LICENSE')
    for name in ('krypton_ui.dll', 'Microsoft.WindowsAppRuntime.Bootstrap.dll'):
        shutil.copy2(ui / name, destination / name)
    shutil.copytree(ui / 'assets/settings', destination / 'assets/settings',
                    dirs_exist_ok=True)
    controlled = {'chrome.exe', 'chrome.dll', 'chrome_elf.dll', 'chrome_proxy.exe',
                  'setup.exe', 'elevation_service.exe', 'notification_helper.exe',
                  'eventlog_provider.dll', 'chrome_wer.dll', 'chromedriver.exe'}
    changed = []
    for path in sorted(destination.iterdir()):
        if path.name in controlled:
            result = stamp(path, previous, args.version)
            if result:
                changed.append(result)
    manifest = destination / f'{previous}.manifest'
    if manifest.is_file():
        content = manifest.read_bytes()
        replacement = content
        for encoding in ('ascii', 'utf-16le'):
            replacement = replacement.replace(previous.encode(encoding),
                                              args.version.encode(encoding))
        if replacement == content:
            raise ValueError('The versioned assembly manifest was not updated')
        target = destination / f'{args.version}.manifest'
        if target.exists():
            raise ValueError('The new assembly manifest already exists')
        target.write_bytes(replacement)
        manifest.unlink()
        changed.append({'name': target.name,
                        'before_sha256': hashlib.sha256(content).hexdigest(),
                        'after_sha256': digest(target)})
    for name in ('chrome.exe', 'chrome.dll', 'chrome_elf.dll'):
        file, product = file_versions(destination / name)
        if file != args.version or product != engine:
            raise ValueError(f'Unexpected release or engine version: {name}')
    expected_ui = digest(ui / 'krypton_ui.dll')
    if digest(destination / 'krypton_ui.dll') != expected_ui:
        raise ValueError('Staged UI DLL differs from the Release build')
    record = {'version': args.version, 'base_version': previous,
              'engine_version': engine, 'browser_dir': str(destination),
              'krypton_ui_sha256': expected_ui, 'restamped': changed,
              'inputs': copies}
    (output / 'ui-release-inputs.json').write_text(
        json.dumps(record, indent=2) + '\n', encoding='utf-8')
    summary = {key: value for key, value in record.items()
               if key not in ('inputs', 'restamped')}
    summary['restamped'] = [{'name': item['name'],
                            'replacements': item.get('replacements', 1)}
                           for item in changed]
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
