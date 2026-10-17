"""Owned headers/metadata only; no rewriter, Guest, graphics or upstream script runs."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'experiments/nxnative_base_v0/tools/preflight_profile.py'
PROFILE = ROOT / 'experiments/nxnative_base_v0/profile/base_v0.json'
NAMES = ('main', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3', 'sdk')
ALL = (*NAMES, 'rtld')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def owned_nso(build_id='ab' * 32):
    header = bytearray(0x100)
    struct.pack_into('<4sIII', header, 0, b'NSO0', 0, 0, 0x38)
    header[0x40:0x60] = bytes.fromhex(build_id)
    for index, data in enumerate((b'OWN1', b'OWN2', b'OWN3')):
        struct.pack_into('<III', header, 0x10 + 16 * index, 0x100 + 4 * index, 0x1000 * index, 4)
        struct.pack_into('<I', header, 0x60 + 4 * index, 4)
        header[0xA0 + 32 * index:0xC0 + 32 * index] = hashlib.sha256(data).digest()
    return bytes(header) + b'OWN1OWN2OWN3'


def owned_romfs():
    raw = bytearray(0x100)
    struct.pack_into('<10Q', raw, 0, 0x50, 0x50, 4, 0x54, 24, 0x6C, 4, 0x70, 0, 0x100)
    struct.pack_into('<I', raw, 0x50, 0)
    struct.pack_into('<6I', raw, 0x54, 0, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    struct.pack_into('<I', raw, 0x6C, 0xFFFFFFFF)
    return bytes(raw)


def metadata_sha(raw):
    words = struct.unpack('<10Q', raw[:0x50])
    h = hashlib.sha256(raw[:0x50])
    for pos in (1, 3, 5, 7):
        offset, size = words[pos:pos + 2]
        h.update(struct.pack('<QQ', offset, size))
        h.update(raw[offset:offset + size])
    return h.hexdigest()


class BaseV0ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('owned_base_v0_preflight', TOOL)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owned-base-v0-profile-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exefs = self.root / 'ExeFS'
        self.exefs.mkdir()
        self.modules = {}
        for index, name in enumerate(ALL):
            raw = owned_nso(f'{index + 1:02x}' * 32)
            (self.exefs / name).write_bytes(raw)
            self.modules[name] = {'bytes': len(raw), 'sha256': digest(raw), 'build_id': raw[0x40:0x60].hex(), 'load': name != 'rtld'}
        self.romfs = self.root / 'romfs.bin'
        raw = owned_romfs()
        self.romfs.write_bytes(raw)
        self.romfs_expected = {'bytes': len(raw), 'header_sha256': digest(raw[:0x50]), 'metadata_sha256': metadata_sha(raw), 'image_sha256_from_manifest': digest(raw), 'file_count': 0, 'directory_count': 1, 'data_offset': 0x100}

    def check_rejected(self, callback, code=None):
        with self.assertRaises(self.tool.PreflightError) as caught:
            callback()
        if code is not None:
            self.assertEqual(caught.exception.code, code)

    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        fifo = self.root / 'no-writer.fifo'
        os.mkfifo(fifo)
        child = """import importlib.util,json,os,sys
spec=importlib.util.spec_from_file_location('owned_fifo_preflight',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
before=len(os.listdir('/dev/fd'))
for _ in range(32):
    try:
        stream,_=module.regular(sys.argv[2]);stream.close();raise AssertionError('FIFO admitted')
    except module.PreflightError as error:
        assert error.code=='input_regular'
assert len(os.listdir('/dev/fd'))==before,'rejected input leaked a descriptor'
print(json.dumps({'reason':'input_regular','rejections':32,'descriptor_leaks':0}))
sys.exit(2)
"""
        started = time.monotonic()
        try:
            # subprocess.run kills and reaps the owned child on timeout.
            result = subprocess.run([sys.executable, '-c', child, str(TOOL), str(fifo)],
                                    capture_output=True, text=True, timeout=1.5)
        except subprocess.TimeoutExpired:
            self.fail('FIFO admission waited for a writer instead of rejecting input_regular')
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'reason': 'input_regular', 'rejections': 32, 'descriptor_leaks': 0})
        self.assertEqual(result.stderr, '')

    def test_valid_owned_nso_header_identity(self):
        result = self.tool.inspect_nso_file(self.exefs / 'main', self.modules['main'])
        self.assertEqual(result['bytes'], self.modules['main']['bytes'])
        self.assertEqual(result['sha256'], self.modules['main']['sha256'])
        self.assertEqual(result['build_id'], self.modules['main']['build_id'])
        self.assertFalse(result['decompressed_or_executed'])

    def test_nso_rejects_truncated_magic_and_format_version(self):
        path = self.exefs / 'main'
        raw = path.read_bytes()
        for altered, code in ((raw[:0xFF], 'nso_header'), (b'BAD!' + raw[4:], 'nso_header'), (raw[:4] + struct.pack('<I', 1) + raw[8:], 'nso_header')):
            path.write_bytes(altered)
            expected = {**self.modules['main'], 'bytes': len(altered), 'sha256': digest(altered)}
            self.check_rejected(lambda: self.tool.inspect_nso_file(path, expected), code)

    def test_nso_rejects_unsafe_header_geometry_even_when_hash_matches(self):
        path = self.exefs / 'main'
        raw = path.read_bytes()
        for offset, value in ((0x0C, 0x80), (0x10, 0x80), (0x18, 0xFFFFFFFF), (0x24, 0), (0x3C, 0xFFFFFFFF), (0x60, 0)):
            altered = bytearray(raw)
            struct.pack_into('<I', altered, offset, value)
            path.write_bytes(altered)
            expected = {**self.modules['main'], 'sha256': digest(altered)}
            self.check_rejected(lambda: self.tool.inspect_nso_file(path, expected), 'nso_header')

    def test_nso_rejects_build_id_length_and_whole_file_hash_mismatch(self):
        for field, value, code in (('build_id', 'ff' * 32, 'nso_identity'), ('bytes', 269, 'nso_identity'), ('sha256', '0' * 64, 'nso_identity')):
            expected = {**self.modules['main'], field: value}
            self.check_rejected(lambda: self.tool.inspect_nso_file(self.exefs / 'main', expected), code)

    def test_nso_rejects_same_header_changed_payload(self):
        path = self.exefs / 'main'
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + b'!')
        self.check_rejected(lambda: self.tool.inspect_nso_file(path, self.modules['main']), 'nso_identity')

    def test_exefs_requires_six_load_modules_and_rtld_is_checked_not_loaded(self):
        (self.exefs / 'main.npdm').write_bytes(b'owned metadata')
        rows = self.tool.inspect_exefs(self.exefs, self.modules)
        self.assertEqual(set(rows), set(ALL))
        self.assertEqual([name for name in NAMES if rows[name]['load']], list(NAMES))
        self.assertFalse(rows['rtld']['load'])
        (self.exefs / 'subsdk2').unlink()
        self.check_rejected(lambda: self.tool.inspect_exefs(self.exefs, self.modules), 'exefs_members')

    def test_exefs_rejects_extra_business_module_and_symlink(self):
        extra = self.exefs / 'subsdk4'
        extra.write_bytes(owned_nso())
        self.check_rejected(lambda: self.tool.inspect_exefs(self.exefs, self.modules), 'exefs_members')
        extra.unlink()
        (self.exefs / 'main').unlink()
        (self.exefs / 'main').symlink_to(self.exefs / 'sdk')
        self.check_rejected(lambda: self.tool.inspect_exefs(self.exefs, self.modules), 'input_regular')

    def test_owned_romfs_metadata_is_bounded_and_payload_is_not_hash_claimed(self):
        result = self.tool.inspect_romfs_metadata(self.romfs, self.romfs_expected)
        self.assertEqual(result['metadata_sha256'], self.romfs_expected['metadata_sha256'])
        self.assertEqual(result['image_sha256_from_manifest'], self.romfs_expected['image_sha256_from_manifest'])
        self.assertFalse(result['full_image_sha256_verified_now'])
        self.assertEqual(result['payload_bytes_read'], 0)

    def test_romfs_rejects_truncated_bounds_metadata_or_length_change(self):
        raw = self.romfs.read_bytes()
        for changed in (raw[:0x4F], raw + b'x', raw[:0x50] + b'x' + raw[0x51:]):
            self.romfs.write_bytes(changed)
            self.check_rejected(lambda: self.tool.inspect_romfs_metadata(self.romfs, self.romfs_expected))
        changed = bytearray(raw)
        struct.pack_into('<Q', changed, 8, 0xFFFFFFFFFFFFFFFC)
        self.romfs.write_bytes(changed)
        self.check_rejected(lambda: self.tool.inspect_romfs_metadata(self.romfs, self.romfs_expected))

    def test_profile_is_exact_v0_and_cannot_be_renamed_from_v19(self):
        actual = json.loads(PROFILE.read_text())
        self.tool.validate_profile_contract(actual)
        for field, value in (('game_version', '1.9.0'), ('profile_id', 'botw-1.9-renamed-base-v0'), ('required_modules', ['main', 'subsdk0', 'sdk']), ('nvn_api', {'major': 55, 'minor': 15})):
            wrong = copy.deepcopy(actual)
            wrong[field] = value
            self.check_rejected(lambda: self.tool.validate_profile_contract(wrong), 'profile_identity')
        wrong = copy.deepcopy(actual)
        wrong['modules']['rtld']['load'] = True
        self.check_rejected(lambda: self.tool.validate_profile_contract(wrong), 'profile_identity')

    def test_profile_file_rejects_changed_record_even_if_label_stays_base0(self):
        profile = json.loads(PROFILE.read_text())
        profile['modules']['main']['sha256'] = '0' * 64
        path = self.root / 'base_v0.json'
        path.write_text(json.dumps(profile))
        self.check_rejected(lambda: self.tool.load_profile(path), 'profile_digest')

    def test_evidence_payloads_are_checked_against_immutable_profile_pins(self):
        profile = json.loads(PROFILE.read_text())
        root = self.root / 'evidence-root'
        root.mkdir()
        for row in profile['identity_evidence'].values():
            path = root / row['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'{}')
        self.check_rejected(lambda: self.tool.verify_identity_evidence(profile, root), 'evidence_digest')

    def test_owned_guest29_source_report_checks_seven_originals_but_framework_loads_six(self):
        profile = json.loads(PROFILE.read_text())
        originals = [{'name': name, **{k: row[k] for k in ('bytes', 'sha256', 'build_id')}}
                     for name, row in profile['modules'].items()]
        documents = {
            'original_nso_report': {'modules': originals},
            'guest29_source_report': {'profile': {'title_id': profile['title_id'].lower(), 'package_version': 0,
                                                'revision': 'switch-base-v0'}, 'modules': originals},
            'guest29_framework_report': {'modules': [
                {'name': name, 'build_id': profile['modules'][name]['build_id'],
                 'source_sha256': profile['modules'][name]['sha256']} for name in NAMES]},
            'base_v0_abi': {'profile': profile['profile_id'], 'source': {
                'module': 'subsdk0', 'build_id': profile['modules']['subsdk0']['build_id'],
                'nso_sha256': profile['modules']['subsdk0']['sha256']}},
            'base_v0_nvn_version': {'profile': profile['profile_id'], 'contracts': {
                'main': {'major_required': 52, 'minor_minimum': 32},
                'original_primary_driver': {'major': 52, 'minor': 32}},
                'modules': {name: {'source_sha256': profile['modules'][name]['sha256']}
                            for name in ('main', 'subsdk0')}},
        }
        for key, doc in documents.items():
            raw = json.dumps(doc).encode()
            name = key + '.json'
            (self.root / name).write_bytes(raw)
            profile['identity_evidence'][key] = {'path': name, 'bytes': len(raw), 'sha256': digest(raw)}
        try:
            result = self.tool.verify_identity_evidence(profile, self.root)
        except self.tool.PreflightError as error:
            self.fail('coherent owned 7-original/6-load evidence rejected: ' + error.code)
        self.assertEqual(result, {key: row['sha256'] for key, row in profile['identity_evidence'].items()})

    def test_cli_rejects_owned_fake_inputs_without_admitting_a_real_base(self):
        run = subprocess.run([sys.executable, str(TOOL), '--exefs', str(self.exefs), '--romfs', str(self.romfs)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        payload = json.loads(run.stdout)
        self.assertEqual(payload['status'], 'rejected')
        self.assertFalse(payload['guest_or_upstream_executed'])
        self.assertEqual(run.stderr, '')


if __name__ == '__main__':
    unittest.main()
