"""Bridge verified base-1.0 shader identities; never builds or admits a shader."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import types

PREFLIGHT_SHA256 = 'f3f44bb41c94a1d07c3819c49458f713237e7e657e204d964ee1bb4b324ec096'


def locked_inputs():
    """Bootstrap from a bounded source snapshot, without consulting Python caches."""
    path = Path(__file__).resolve().parent / 'preflight_profile.py'
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 65536:
            raise ValueError('preflight source identity')
        stream = os.fdopen(fd, 'rb')
        fd = None
        with stream:
            raw = stream.read(65537)
            after = os.fstat(stream.fileno())
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in fields)
                or hashlib.sha256(raw).hexdigest() != PREFLIGHT_SHA256):
            raise ValueError('preflight source identity')
    finally:
        if fd is not None:
            os.close(fd)
    module = types.ModuleType('_nxnative_shader_key_inputs')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


inputs = locked_inputs()

BridgeError = inputs.PreflightError
require = inputs.require
CATALOG_SHA256 = '4104e1b31fef3df07be8d421606972d4ef5dca4a2a474531b13b8586bc9a4078'
PUBLICATION_SHA256 = '7aa508e282eef9e0fcb558354b45bea1154101da88e470ef62f76fa298512939'
FIELDS = ('version', 'stage', 'control_size', 'code_size', 'code_offset', 'c1_offset', 'c1_size')
STAGES = {0: 'vertex', 1: 'fragment', 2: 'geometry'}
HEX64 = re.compile('[0-9a-f]{64}')
HEX16 = re.compile('[0-9a-f]{16}')
MODES = {(0, 0), (0, 1), (1, 0), (1, 1)}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class Reader:
    """Reuse the profile gate's nonblocking regular-file/FD stability checks."""
    def __init__(self):
        self.bytes = 0

    def read(self, path, maximum):
        stream, before = inputs.regular(path, maximum)
        with stream:
            require(self.bytes + before.st_size <= 256 * 1024 * 1024, 'input_budget')
            raw = stream.read(maximum + 1)
            inputs.stable(stream, before)
        require(len(raw) == before.st_size and len(raw) <= maximum, 'input_regular')
        self.bytes += len(raw)
        return raw

    def document(self, path, expected=None, maximum=32 * 1024 * 1024):
        raw = self.read(path, maximum)
        if expected is not None:
            require(digest(raw) == expected, 'evidence_digest')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                require(key not in value, 'json_metadata')
                value[key] = item
            return value
        def nonfinite(_):
            raise BridgeError('json_metadata')
        try:
            return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite), digest(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BridgeError('json_metadata') from exc


def capture_identity(meta, control, code):
    """Check the original domain-separated identity before calculating native's key."""
    expected = set(FIELDS) | {'schema', 'key', 'sha256', 'control_sha256', 'code_sha256',
                              'hash_metadata_le32', 'control_file', 'code_file'}
    require(isinstance(meta, dict) and set(meta) == expected, 'capture_metadata')
    require(meta['schema'] == 'botw-shader-v1' and isinstance(meta['key'], str) and
            HEX16.fullmatch(meta['key']) is not None, 'capture_metadata')
    for field in ('sha256', 'control_sha256', 'code_sha256'):
        require(isinstance(meta[field], str) and HEX64.fullmatch(meta[field]) is not None, 'capture_metadata')
    require(meta['control_file'] == meta['key'] + '.control' and
            meta['code_file'] == meta['key'] + '.code', 'capture_metadata')
    require(all(type(meta[k]) is int and 0 <= meta[k] <= 0xFFFFFFFF for k in FIELDS), 'capture_metadata')
    require(0x718 <= len(control) <= 1024 * 1024 and 0x88 <= len(code) <= 16 * 1024 * 1024,
            'capture_bounds')
    u32 = lambda offset: struct.unpack_from('<I', control, offset)[0]
    require(u32(0) == 0x98761234 and u32(4) == 1 and u32(8) in (8, 9) and
            u32(0x20) == len(control), 'capture_header')
    fields = dict(version=u32(8), stage=u32(0x714), control_size=len(control),
                  code_size=u32(0x704), code_offset=u32(0x708), c1_offset=u32(0x700))
    require(fields['stage'] in STAGES and fields['code_size'] == len(code) and len(code) % 8 == 0 and
            fields['code_offset'] == 0x30 and 0x88 <= fields['c1_offset'] <= len(code) and
            fields['c1_offset'] % 8 == 0, 'capture_bounds')
    fields['c1_size'] = len(code) - fields['c1_offset']
    if fields['version'] == 8:
        require(len(control) == 2000 and u32(12) == 0x120 and u32(16) == 11 and
                u32(20) == len(control) and u32(0x6F0) == len(control), 'capture_header')
    require(struct.unpack_from('<I', code, 0)[0] == 0x12345678 and
            (struct.unpack_from('<I', code, 0x30)[0] >> 10) & 15 == {0: 1, 1: 5, 2: 4}[fields['stage']],
            'capture_header')
    require(isinstance(meta['hash_metadata_le32'], list) and
            len(meta['hash_metadata_le32']) == len(FIELDS) and
            all(type(word) is int for word in meta['hash_metadata_le32']) and
            all(meta[k] == fields[k] for k in FIELDS) and
            meta['hash_metadata_le32'] == [fields[k] for k in FIELDS], 'capture_metadata')
    require(digest(control) == meta['control_sha256'] and digest(code) == meta['code_sha256'], 'capture_digest')
    original = digest(b'botw-shader-v1\0' + struct.pack('<7I', *(fields[k] for k in FIELDS)) + control + code)
    require(original == meta['sha256'] and original[:16] == meta['key'], 'capture_digest')
    # Exact fixed native shader_split.ctl_len / hle_nvn.keys_hash formula.
    length = len(control) if 0x760 <= len(control) < 0x880 else 0x880
    require(len(control) >= length, 'capture_bounds')
    native = digest(control[:length] + code)
    return dict(capture_key=meta['key'], capture_sha256=original,
                native_key=native[:16], native_sha256=native,
                control_sha256=meta['control_sha256'], code_sha256=meta['code_sha256'],
                native_hashed_control_size=length, c1_sha256=digest(code[fields['c1_offset']:]),
                stage_name=STAGES[fields['stage']], **fields,
                status='only_identity', conditional_modes=[])


def register_key(seen, key, full):
    require(isinstance(key, str) and isinstance(full, str) and HEX16.fullmatch(key) is not None and
            HEX64.fullmatch(full) is not None and full[:16] == key, 'native_identity')
    require(key not in seen or seen[key] == full, 'native_key_collision')
    seen[key] = full


def catalog_rows(index, captures, reader):
    require(isinstance(index, dict) and index.get('schema') == 'botw-static-base-v0-combined-shader-inputs-v1'
            and index.get('catalog_complete_for_entire_game') is False, 'catalog_identity')
    occurrences = index.get('occurrences')
    require(isinstance(occurrences, list) and 0 < len(occurrences) <= 100000, 'catalog_identity')
    expected = {}
    for row in occurrences:
        require(isinstance(row, dict) and isinstance(row.get('key'), str) and HEX16.fullmatch(row['key']) is not None
                and isinstance(row.get('sha256'), str) and HEX64.fullmatch(row['sha256']) is not None
                and row['sha256'][:16] == row['key'] and type(row.get('stage')) is int and row['stage'] in STAGES
                and type(row.get('control_version')) is int and row['control_version'] in (8, 9), 'catalog_identity')
        identity = (row['sha256'], row['stage'], row['control_version'])
        require(row['key'] not in expected or expected[row['key']] == identity, 'catalog_identity')
        expected[row['key']] = identity
    require(type(index.get('unique_captures')) is int and index['unique_captures'] == len(expected), 'catalog_identity')
    captures = Path(captures)
    require(captures.is_dir() and not captures.is_symlink(), 'input_regular')
    members = {key + suffix for key in expected for suffix in ('.json', '.control', '.code')}
    require({p.name for p in captures.iterdir()} == members, 'capture_members')
    rows, seen = [], {}
    for key in sorted(expected):
        meta, _ = reader.document(captures / (key + '.json'), maximum=4096)
        control = reader.read(captures / (key + '.control'), 1024 * 1024)
        code = reader.read(captures / (key + '.code'), 16 * 1024 * 1024)
        result = capture_identity(meta, control, code)
        require((result['capture_sha256'], result['stage'], result['version']) == expected[key] and
                result['capture_key'] == key, 'catalog_identity')
        register_key(seen, result['native_key'], result['native_sha256'])
        rows.append(result)
    require({p.name for p in captures.iterdir()} == members, 'input_changed')
    return rows


def summary(row):
    return dict(key=row['capture_key'], sha256=row['capture_sha256'], stage_name=row['stage_name'],
                **{k: row[k] for k in FIELDS})


def attach_conditional(rows, source, reader, *, trusted_publication=PUBLICATION_SHA256):
    """Bind existing immutable references, without replaying their shader admission."""
    source = Path(source)
    require(source.is_dir() and not source.is_symlink(), 'input_regular')
    pub, pub_sha = reader.document(source / 'conditional-publication.json', trusted_publication)
    require(pub.get('schema') == 'botw-shader-conditional-publication-v1' and
            pub.get('profile') == 'botw-switch-base-v0' and pub.get('device_compilation_tested') is False and
            pub.get('guest_or_gpu_executed') is False and isinstance(pub.get('package'), str) and
            re.fullmatch(r'build/botw-c-[0-9a-f]{12}', pub['package']) is not None, 'conditional_identity')
    admitted = source / 'admitted'
    package = source / pub['package']
    require(not admitted.is_symlink() and not package.is_symlink() and not (source / 'build').is_symlink(), 'input_regular')
    admission, admission_sha = reader.document(admitted / 'conditional-admission.json', pub['conditional_admission_sha256'])
    proof, proof_sha = reader.document(admitted / 'subset-proof.json', pub['subset_sha256'])
    manifest, manifest_sha = reader.document(package / 'manifest-build.json', pub['manifest_sha256'])
    require(admission.get('schema') == 'botw-shader-conditional-admission-v1' and
            proof.get('schema') == 'botw-shader-conditional-subset-proof-v1' and
            admission.get('profile') == proof.get('profile') == pub['profile'] and
            admission.get('subset_sha256') == proof_sha and proof.get('input_manifest_sha256') == CATALOG_SHA256,
            'conditional_identity')
    captures, accepted = admission.get('captures'), proof.get('accepted')
    require(isinstance(captures, dict) and isinstance(accepted, dict) and set(captures) == set(accepted)
            and type(pub.get('captures')) is int and len(captures) == pub['captures'], 'conditional_identity')
    indexed = {row['capture_key']: row for row in rows}
    require(set(captures) <= set(indexed), 'conditional_identity')
    results = manifest.get('results')
    require(isinstance(results, list) and type(pub.get('variants')) is int and len(results) == pub['variants'], 'conditional_identity')
    variants = {}
    for result in results:
        key = result.get('variant_sha256') if isinstance(result, dict) else None
        require(isinstance(key, str) and HEX64.fullmatch(key) is not None and key not in variants, 'conditional_identity')
        variants[key] = result
    updates, used = {}, set()
    for key in sorted(captures):
        row = indexed[key]
        capture = summary(row)
        entry, proved = captures[key], accepted[key]
        require(entry.get('capture_sha256') == proved.get('capture_sha256') == row['capture_sha256'] and
                proved.get('capture') == capture and isinstance(entry.get('variants'), list) and
                len(entry['variants']) == len(set(entry['variants'])) == 4, 'conditional_identity')
        modes = proved.get('modes')
        require(isinstance(modes, list) and len(modes) == 4, 'conditional_modes')
        by_mode = {}
        for mode in modes:
            require(isinstance(mode, dict) and type(mode.get('window_origin_mode')) is int and
                    type(mode.get('depth_mode')) is int, 'conditional_modes')
            pair = (mode['window_origin_mode'], mode['depth_mode'])
            require(pair in MODES and pair not in by_mode, 'conditional_modes')
            by_mode[pair] = mode
        require(set(by_mode) == MODES, 'conditional_modes')
        references, contract_modes = [], set()
        for variant in entry['variants']:
            require(isinstance(variant, str) and HEX64.fullmatch(variant) is not None and variant in variants and
                    variant not in used, 'conditional_identity')
            result = variants[variant]
            require(result.get('capture') == capture, 'conditional_identity')
            file = result.get('files', {}).get('contract', {})
            require(file == {'path': variant + '.contract.json', 'sha256': variant}, 'conditional_identity')
            contract, contract_sha = reader.document(package / file['path'], variant, maximum=65536)
            require(contract.get('schema') == 'botw-shader-conditional-translation-contract-v1' and
                    contract.get('profile') == pub['profile'] and contract.get('capture') == capture and
                    contract.get('chain') == pub['package'].removeprefix('build/botw-c-'), 'conditional_identity')
            device = contract.get('requirements', {}).get('device', {})
            require(type(device.get('window_origin_mode')) is int and type(device.get('depth_mode')) is int, 'conditional_modes')
            pair = (device['window_origin_mode'], device['depth_mode'])
            require(pair in by_mode and pair not in contract_modes and
                    contract.get('translation', {}).get('job_sha256') == by_mode[pair].get('job_sha256'), 'conditional_modes')
            contract_modes.add(pair)
            used.add(variant)
            references.append(dict(window_origin_mode=pair[0], depth_mode=pair[1], variant_sha256=variant,
                                   contract_path=pub['package'] + '/' + file['path'], contract_sha256=contract_sha))
        require(contract_modes == MODES, 'conditional_modes')
        updates[key] = sorted(references, key=lambda r: (r['window_origin_mode'], r['depth_mode']))
    require(used == set(variants), 'conditional_identity')
    # Commit only after every reference and all four modes have been checked.
    for key, modes in updates.items():
        indexed[key].update(status='conditional_reference_only', conditional_modes=modes)
    return dict(publication_sha256=pub_sha, admission_sha256=admission_sha, subset_sha256=proof_sha,
                manifest_sha256=manifest_sha, captures=len(captures), variants=len(used),
                shader_admission_replayed=False, shader_assets_revalidated=False,
                scope='Original conditional contract references only; no native loader admission or per-shader facts.')


def bridge(index_path, captures, profile=inputs.DEFAULT_PROFILE, conditional_source=None):
    inputs.load_profile(profile)
    reader = Reader()
    index, index_sha = reader.document(index_path)
    require(index_sha == CATALOG_SHA256, 'catalog_digest')
    rows = catalog_rows(index, captures, reader)
    conditional = attach_conditional(rows, conditional_source, reader) if conditional_source else None
    return dict(schema='nxnative-base-v0-shader-key-bridge-v1', profile='botw-switch-base-v0',
                game_version='1.0.0', profile_sha256=inputs.PROFILE_SHA256, source_index_sha256=index_sha,
                scope='Fixed exported base-v0 subset; not an entire-game shader catalog or a facts table.',
                catalog_complete_for_entire_game=False, captures=len(rows),
                native_keys=len({row['native_key'] for row in rows}), input_bytes_verified=reader.bytes,
                rows=rows, conditional_source=conditional, native_loader_admitted=False,
                translation_performed=False, metallib_produced=False, guest_or_gpu_executed=False,
                gameplay_verified=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', required=True)
    parser.add_argument('--captures', required=True)
    parser.add_argument('--profile', default=str(inputs.DEFAULT_PROFILE))
    parser.add_argument('--conditional-source')
    parser.add_argument('--output', help='Fresh JSON output; an existing file is never overwritten.')
    args = parser.parse_args(argv)
    try:
        result = bridge(args.index, args.captures, args.profile, args.conditional_source)
        raw = json.dumps(result, sort_keys=True, indent=2) + '\n'
        if args.output:
            with Path(args.output).open('x', encoding='utf-8') as stream:
                stream.write(raw)
        else:
            sys.stdout.write(raw)
        return 0
    except (BridgeError, OSError, ValueError, KeyError, TypeError) as exc:
        reason = exc.code if isinstance(exc, BridgeError) else 'bridge_metadata'
        print(json.dumps({'schema': 'nxnative-base-v0-shader-key-bridge-error-v1', 'reason': reason}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
