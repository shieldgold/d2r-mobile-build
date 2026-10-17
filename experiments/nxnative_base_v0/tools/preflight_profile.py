"""Read-only base-v0 disk preflight. Never executes or rewrites an NSO."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE = ROOT / 'experiments/nxnative_base_v0/profile/base_v0.json'
PROFILE_SHA256 = '1269ab1df53a1a19852cb7c65dcd5da1baa49b23e96e5d691b005fc4bbf26312'
REQUIRED_MODULES = ('main', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3', 'sdk')
ORIGINAL_MODULES = (*REQUIRED_MODULES, 'rtld')
MAX_NSO = 64 * 1024 * 1024
MAX_MEMORY = 512 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024


class PreflightError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise PreflightError(code)


def regular(path, maximum=None):
    path = Path(path)
    fd = None
    try:
        require(not path.is_symlink(), 'input_regular')
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and (maximum is None or info.st_size <= maximum), 'input_regular')
        stream = os.fdopen(fd, 'rb')
        fd = None  # The returned stream now owns the descriptor.
        return stream, info
    except OSError as exc:
        raise PreflightError('input_regular') from exc
    finally:
        if fd is not None:
            os.close(fd)


def stable(stream, before):
    after = os.fstat(stream.fileno())
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'input_changed')


def json_document(path, expected=None):
    stream, before = regular(path, MAX_JSON)
    with stream:
        raw = stream.read(MAX_JSON + 1)
        stable(stream, before)
    require(len(raw) <= MAX_JSON, 'input_regular')
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None:
        require(len(raw) == expected['bytes'] and digest == expected['sha256'], 'evidence_digest')
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, 'json_metadata')
                result[key] = value
            return result
        def reject_constant(_):
            raise PreflightError('json_metadata')
        return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant), digest
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError('json_metadata') from exc


def validate_profile_contract(profile):
    try:
        require(profile['schema'] == 'nxnative-base-v0-profile-v1' and
                profile['profile_id'] == 'botw-switch-base-v0' and
                profile['title_id'] == '01007EF00011E000' and
                profile['game_version'] == '1.0.0' and profile['package_version'] == 'v0' and
                profile['nvn_api'] == {'major': 52, 'minor': 32} and
                profile['required_modules'] == list(REQUIRED_MODULES), 'profile_identity')
        require(set(profile['modules']) == set(ORIGINAL_MODULES) and
                set(profile['auxiliary_modules']) == {'rtld'} and
                profile['auxiliary_modules']['rtld']['load'] is False and
                profile['game_execution_ready'] is False and profile['runtime_verified'] is False and
                profile['progress'] == dict.fromkeys(('compile', 'rewrite', 'shader', 'gameplay'), 'pending'), 'profile_identity')
        for name, row in profile['modules'].items():
            require(type(row['bytes']) is int and 0x100 <= row['bytes'] <= MAX_NSO and
                    re.fullmatch('[0-9a-f]{64}', row['sha256']) and
                    re.fullmatch('[0-9a-f]{64}', row['build_id']) and
                    row['load'] is (name != 'rtld'), 'profile_identity')
    except (KeyError, TypeError) as exc:
        raise PreflightError('profile_identity') from exc


def load_profile(path=DEFAULT_PROFILE):
    document, digest = json_document(path)
    require(digest == PROFILE_SHA256, 'profile_digest')
    validate_profile_contract(document)
    return document


def verify_identity_evidence(profile, root=ROOT):
    documents = {}
    for key, row in profile['identity_evidence'].items():
        name = Path(row['path'])
        require(not name.is_absolute() and '..' not in name.parts, 'profile_identity')
        documents[key], _ = json_document(Path(root) / name, row)
    originals = documents['original_nso_report']['modules']
    require(len(originals) == 7 and {r['name'] for r in originals} == set(ORIGINAL_MODULES), 'evidence_identity')
    for row in originals:
        expected = profile['modules'][row['name']]
        require(all(row[k] == expected[k] for k in ('bytes', 'sha256', 'build_id')), 'evidence_identity')
    abi = documents['base_v0_abi']
    source = abi['source']
    require(abi['profile'] == profile['profile_id'] and source['module'] == 'subsdk0' and
            source['build_id'] == profile['modules']['subsdk0']['build_id'] and
            source['nso_sha256'] == profile['modules']['subsdk0']['sha256'], 'evidence_identity')
    source_report = documents['guest29_source_report']
    require(source_report['profile']['title_id'].upper() == profile['title_id'] and
            source_report['profile']['package_version'] == 0 and
            source_report['profile']['revision'] == 'switch-base-v0', 'evidence_identity')
    for key, sha_key in (('guest29_source_report', 'sha256'), ('guest29_framework_report', 'source_sha256')):
        rows = documents[key]['modules']
        names = ORIGINAL_MODULES if key == 'guest29_source_report' else REQUIRED_MODULES
        require(len(rows) == len(names) and {r['name'] for r in rows} == set(names), 'evidence_identity')
        for row in rows:
            expected = profile['modules'][row['name']]
            require(row['build_id'] == expected['build_id'] and row[sha_key] == expected['sha256'], 'evidence_identity')
    version = documents['base_v0_nvn_version']
    contract = version['contracts']
    require(version['profile'] == profile['profile_id'] and
            contract['main']['major_required'] == contract['original_primary_driver']['major'] == 52 and
            contract['main']['minor_minimum'] == contract['original_primary_driver']['minor'] == 32, 'evidence_identity')
    for name in ('main', 'subsdk0'):
        require(version['modules'][name]['source_sha256'] == profile['modules'][name]['sha256'], 'evidence_identity')
    return {key: row['sha256'] for key, row in profile['identity_evidence'].items()}


def inspect_nso_file(path, expected):
    stream, before = regular(path, MAX_NSO)
    with stream:
        header = stream.read(0x100)
        require(len(header) == 0x100 and header[:4] == b'NSO0', 'nso_header')
        version, reserved, flags = struct.unpack_from('<III', header, 4)
        require(version == reserved == 0 and not flags & ~0x3F, 'nso_header')
        memory, stored_ranges = [], []
        for index in range(3):
            offset, address, size = struct.unpack_from('<III', header, 0x10 + 16 * index)
            packed = struct.unpack_from('<I', header, 0x60 + 4 * index)[0]
            stored = packed if flags & (1 << index) else size
            require(size > 0 and packed > 0 and stored > 0 and offset >= 0x100 and
                    offset <= before.st_size and stored <= before.st_size - offset and
                    address <= MAX_MEMORY and size <= MAX_MEMORY - address, 'nso_header')
            memory.append((address, address + size))
            stored_ranges.append((offset, offset + stored))
        bss = struct.unpack_from('<I', header, 0x3C)[0]
        require(memory[0][0] == 0 and memory[2][1] + bss <= MAX_MEMORY and
                memory[2][1] + bss >= max(end for _, end in memory), 'nso_header')
        for spans in (memory, stored_ranges):
            ordered = sorted(spans)
            require(all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])), 'nso_header')
        require(before.st_size == expected['bytes'] and header[0x40:0x60].hex() == expected['build_id'], 'nso_identity')
        digest = hashlib.sha256(header)
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        stable(stream, before)
    require(digest.hexdigest() == expected['sha256'], 'nso_identity')
    return {'bytes': before.st_size, 'sha256': digest.hexdigest(), 'build_id': header[0x40:0x60].hex(),
            'nso_format_version': version, 'header_bounds_checked': True, 'decompressed_or_executed': False}


def inspect_exefs(exefs, expected):
    exefs = Path(exefs)
    require(exefs.is_dir() and not exefs.is_symlink(), 'input_regular')
    entries = list(exefs.iterdir())
    require({p.name for p in entries} in (set(ORIGINAL_MODULES), set(ORIGINAL_MODULES) | {'main.npdm'}), 'exefs_members')
    for path in entries:
        require(not path.is_symlink() and path.is_file(), 'input_regular')
    require(set(expected) == set(ORIGINAL_MODULES), 'profile_identity')
    return {name: {**inspect_nso_file(exefs / name, expected[name]), 'load': name != 'rtld'} for name in ORIGINAL_MODULES}


def inspect_romfs_metadata(path, expected):
    # Reuse the existing bounded RomFS geometry validator; no upstream code is imported.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.botw_native.romfs import _layout
    stream, before = regular(path, 64 * 1024**3)
    with stream:
        require(before.st_size == expected['bytes'], 'romfs_identity')
        header = stream.read(0x50)
        try:
            words, _ = _layout(header, before.st_size)
        except (ValueError, struct.error) as exc:
            raise PreflightError('romfs_header') from exc
        require(hashlib.sha256(header).hexdigest() == expected['header_sha256'] and
                words[9] == expected['data_offset'], 'romfs_identity')
        digest = hashlib.sha256(header)
        count = len(header)
        for pos in (1, 3, 5, 7):
            offset, size = words[pos:pos + 2]
            digest.update(struct.pack('<QQ', offset, size))
            stream.seek(offset)
            data = stream.read(size)
            require(len(data) == size, 'romfs_header')
            digest.update(data)
            count += len(data)
        stable(stream, before)
    require(digest.hexdigest() == expected['metadata_sha256'], 'romfs_identity')
    return {'bytes': before.st_size, 'header_sha256': expected['header_sha256'], 'metadata_sha256': digest.hexdigest(),
            'metadata_bytes_read': count, 'payload_bytes_read': 0,
            'image_sha256_from_manifest': expected['image_sha256_from_manifest'],
            'full_image_sha256_verified_now': False,
            'file_count_from_manifest': expected['file_count'], 'directory_count_from_manifest': expected['directory_count']}


def preflight(exefs, romfs, profile_path=DEFAULT_PROFILE, romfs_manifest=None):
    profile = load_profile(profile_path)
    evidence = verify_identity_evidence(profile)
    modules = inspect_exefs(exefs, profile['modules'])
    expected = profile['romfs']
    manifest_path = Path(romfs_manifest) if romfs_manifest else ROOT / expected['manifest']['path']
    manifest, _ = json_document(manifest_path, expected['manifest'])
    require(manifest['schema'] == 1 and manifest['format'] == 'switch-romfs' and
            manifest['image']['bytes'] == expected['bytes'] and
            manifest['image']['sha256'] == expected['image_sha256_from_manifest'] and
            manifest['file_count'] == len(manifest['files']) == expected['file_count'] and
            manifest['directory_count'] == len(manifest['directories']) == expected['directory_count'] and
            manifest['data_offset'] == expected['data_offset'], 'romfs_identity')
    romfs_result = inspect_romfs_metadata(romfs, expected)
    return {'schema': 'nxnative-base-v0-input-preflight-v1', 'status': 'preflight_ok',
            'profile_id': profile['profile_id'], 'profile_sha256': PROFILE_SHA256,
            'title_id': profile['title_id'], 'game_version': profile['game_version'], 'nvn_api': profile['nvn_api'],
            'required_modules': list(REQUIRED_MODULES), 'rtld_loaded': False,
            'modules': modules, 'identity_evidence_sha256': evidence, 'romfs': romfs_result,
            'progress': profile['progress'], 'game_execution_ready': False, 'runtime_verified': False, 'guest_or_upstream_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exefs', type=Path, required=True)
    parser.add_argument('--romfs', type=Path, required=True)
    parser.add_argument('--romfs-manifest', type=Path)
    parser.add_argument('--profile', type=Path, default=DEFAULT_PROFILE)
    args = parser.parse_args()
    try:
        result = preflight(args.exefs, args.romfs, args.profile, args.romfs_manifest)
    except (PreflightError, OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({'schema': 'nxnative-base-v0-input-preflight-v1', 'status': 'rejected',
                          'reason': exc.code if isinstance(exc, PreflightError) else 'invalid_input_metadata',
                          'guest_or_upstream_executed': False}, separators=(',', ':')))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
