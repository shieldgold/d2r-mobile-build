"""Owned control/code identities only; never translates or executes shaders."""
import copy
import hashlib
import importlib._bootstrap_external
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'experiments/nxnative_base_v0/tools/shader_key_bridge.py'
FIELDS = ('version', 'stage', 'control_size', 'code_size', 'code_offset', 'c1_offset', 'c1_size')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()


def owned_capture(version=8, stage=0):
    control = bytearray(2000 if version == 8 else 2001)
    for off, value in ((0, 0x98761234), (4, 1), (8, version), (12, 0x120), (16, 11),
                       (20, len(control)), (0x20, len(control)), (0x6F0, len(control)),
                       (0x700, 0x100), (0x704, 0x108), (0x708, 0x30), (0x714, stage)):
        struct.pack_into('<I', control, off, value)
    code = bytearray(0x108)
    struct.pack_into('<I', code, 0, 0x12345678)
    struct.pack_into('<I', code, 0x30, {0: 1, 1: 5, 2: 4}[stage] << 10)
    code[-8:] = b'OWNED-C1'
    fields = dict(version=version, stage=stage, control_size=len(control), code_size=len(code),
                  code_offset=0x30, c1_offset=0x100, c1_size=8)
    full = digest(b'botw-shader-v1\0' + struct.pack('<7I', *(fields[k] for k in FIELDS)) + control + code)
    key = full[:16]
    meta = dict(schema='botw-shader-v1', key=key, sha256=full, control_sha256=digest(control),
                code_sha256=digest(code), hash_metadata_le32=[fields[k] for k in FIELDS],
                control_file=key + '.control', code_file=key + '.code', **fields)
    return meta, bytes(control), bytes(code)


class NxnativeShaderKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('owned_shader_key_bridge', TOOL)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owned-base0-shader-keys-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.captures = self.root / 'captures'
        self.captures.mkdir()
        self.meta, self.control, self.code = owned_capture()
        self.write_capture(self.meta, self.control, self.code)
        self.index = {'schema': 'botw-static-base-v0-combined-shader-inputs-v1',
                      'catalog_complete_for_entire_game': False, 'unique_captures': 1,
                      'occurrences': [dict(key=self.meta['key'], sha256=self.meta['sha256'],
                                           stage=0, control_version=8)]}

    def write_capture(self, meta, control, code):
        for suffix, raw in (('.json', canonical(meta)), ('.control', control), ('.code', code)):
            (self.captures / (meta['key'] + suffix)).write_bytes(raw)

    def reject(self, callback, reason=None):
        with self.assertRaises(self.tool.BridgeError) as caught:
            callback()
        if reason:
            self.assertEqual(caught.exception.code, reason)

    def test_v8_v9_exact_native_hash_and_c1_identity(self):
        for version in (8, 9):
            meta, control, code = owned_capture(version, 1)
            row = self.tool.capture_identity(meta, control, code)
            self.assertEqual(row['native_sha256'], digest(control + code))
            self.assertNotEqual(row['native_key'], meta['key'])
            self.assertEqual(row['native_key'], row['native_sha256'][:16])
            self.assertEqual(row['c1_sha256'], digest(b'OWNED-C1'))
            self.assertEqual(row['control_sha256'], digest(control))

    def test_preflight_bootstrap_uses_locked_source_instead_of_timestamp_valid_pyc(self):
        directory = self.root / 'component/tools'
        directory.mkdir(parents=True)
        bridge = directory / TOOL.name
        bridge.write_bytes(TOOL.read_bytes())
        helper = directory / 'preflight_profile.py'
        raw = (TOOL.parent / helper.name).read_bytes()
        helper.write_bytes(raw)
        cache = Path(importlib.util.cache_from_source(str(helper)))
        cache.parent.mkdir()
        owned_canary = compile(raw + b"\nPROFILE_SHA256='OWNED_ONLY_PYC_CANARY'\n", str(helper), 'exec')
        cache.write_bytes(importlib._bootstrap_external._code_to_timestamp_pyc(
            owned_canary, int(helper.stat().st_mtime), len(raw)))
        child = """import sys,types
path=sys.argv[1]; module=types.ModuleType('owned_bridge');module.__file__=path
exec(compile(open(path,'rb').read(),path,'exec'),module.__dict__)
print(module.inputs.PROFILE_SHA256)
"""
        completed = subprocess.run([sys.executable, '-B', '-S', '-c', child, str(bridge)],
                                   capture_output=True, timeout=2)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(completed.stdout, b'1269ab1df53a1a19852cb7c65dcd5da1baa49b23e96e5d691b005fc4bbf26312\n')

    def test_control_and_code_failures_do_not_yield_identity(self):
        for off, value in ((0, 0), (8, 14), (0x20, 1999), (0x700, 0x110),
                           (0x704, 0), (0x708, 0), (0x714, 5)):
            control = bytearray(self.control)
            struct.pack_into('<I', control, off, value)
            self.reject(lambda: self.tool.capture_identity(self.meta, control, self.code))
        code = bytearray(self.code)
        code[0] ^= 1
        self.reject(lambda: self.tool.capture_identity(self.meta, self.control, code))

    def test_old_metadata_domain_and_full_digest_are_required(self):
        for field, value in (('key', '1' * 16), ('sha256', 'a' * 64), ('code_size', True),
                             ('code_file', '../code'), ('schema', '1.9'), ('hash_metadata_le32', [])):
            meta = copy.deepcopy(self.meta)
            meta[field] = value
            self.reject(lambda: self.tool.capture_identity(meta, self.control, self.code))
        meta = copy.deepcopy(self.meta)
        meta['hash_metadata_le32'][1] = False  # False == 0 must not make a u32 field.
        self.reject(lambda: self.tool.capture_identity(meta, self.control, self.code), 'capture_metadata')

    def test_native_short_key_collision_rejected_but_exact_alias_is_explicit(self):
        seen = {}
        self.tool.register_key(seen, 'a' * 16, 'a' * 64)
        self.tool.register_key(seen, 'a' * 16, 'a' * 64)
        self.reject(lambda: self.tool.register_key(seen, 'a' * 16, 'a' * 16 + 'b' * 48), 'native_key_collision')

    def test_identity_only_catalog_has_no_fake_modes_or_shader_assets(self):
        rows = self.tool.catalog_rows(self.index, self.captures, self.tool.Reader())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'only_identity')
        self.assertEqual(rows[0]['conditional_modes'], [])
        self.assertNotIn('metallib', rows[0])

    def test_catalog_requires_exact_index_members_and_full_identity(self):
        bad = copy.deepcopy(self.index)
        bad['occurrences'][0]['sha256'] = '0' * 64
        self.reject(lambda: self.tool.catalog_rows(bad, self.captures, self.tool.Reader()))
        extra = self.captures / 'unexpected.json'
        extra.write_text('{}')
        self.reject(lambda: self.tool.catalog_rows(self.index, self.captures, self.tool.Reader()), 'capture_members')

    def test_symlink_and_fifo_are_rejected_without_wait(self):
        source = self.captures / self.meta['code_file']
        alias = self.root / 'alias'
        alias.symlink_to(source)
        self.reject(lambda: self.tool.Reader().read(alias, 1024), 'input_regular')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        child = subprocess.run([sys.executable, str(TOOL), '--index', str(fifo), '--captures', str(self.captures)],
                               capture_output=True, timeout=2)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(json.loads(child.stdout)['reason'], 'input_regular')

    def test_cli_rejects_owned_or_renamed_1_9_catalog_and_never_overwrites(self):
        path = self.root / 'index.json'
        path.write_bytes(canonical(self.index))
        output = self.root / 'existing.json'
        output.write_bytes(b'ORIGINAL-CANARY')
        command = [sys.executable, str(TOOL), '--index', str(path), '--captures', str(self.captures), '--output', str(output)]
        child = subprocess.run(command, capture_output=True, timeout=2)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(json.loads(child.stdout)['reason'], 'catalog_digest')
        self.assertEqual(output.read_bytes(), b'ORIGINAL-CANARY')

    def owned_conditional(self):
        source = self.root / 'conditional'
        package = source / 'build/botw-c-123456789abc'
        admitted = source / 'admitted'
        package.mkdir(parents=True)
        admitted.mkdir()
        rows = self.tool.catalog_rows(self.index, self.captures, self.tool.Reader())
        capture = {k: self.meta[k] for k in FIELDS}
        capture.update(key=self.meta['key'], sha256=self.meta['sha256'], stage_name='vertex')
        variants, modes, results = [], [], []
        for w, d in ((0, 0), (0, 1), (1, 0), (1, 1)):
            contract = dict(schema='botw-shader-conditional-translation-contract-v1',
                            profile='botw-switch-base-v0', chain='123456789abc', capture=capture,
                            proofs={'binding_contract_sha256': 'b' * 64, 'subset_sha256': 'c' * 64},
                            requirements={'device': {'window_origin_mode': w, 'depth_mode': d}},
                            translation={'job_sha256': 'd' * 64})
            raw = canonical(contract)
            variant = digest(raw)
            (package / (variant + '.contract.json')).write_bytes(raw)
            variants.append(variant)
            modes.append({'window_origin_mode': w, 'depth_mode': d, 'job_sha256': 'd' * 64})
            results.append({'capture': capture, 'variant_sha256': variant,
                            'files': {'contract': {'path': variant + '.contract.json', 'sha256': variant}}})
        proof = {'schema': 'botw-shader-conditional-subset-proof-v1', 'profile': 'botw-switch-base-v0',
                 'input_manifest_sha256': self.tool.CATALOG_SHA256,
                 'accepted': {self.meta['key']: {'capture_sha256': self.meta['sha256'], 'capture': capture, 'modes': modes}}}
        proof_raw = canonical(proof)
        (admitted / 'subset-proof.json').write_bytes(proof_raw)
        admission = {'schema': 'botw-shader-conditional-admission-v1', 'profile': 'botw-switch-base-v0',
                     'subset_sha256': digest(proof_raw), 'captures': {self.meta['key']: {'capture_sha256': self.meta['sha256'], 'variants': variants}}}
        ar = canonical(admission)
        (admitted / 'conditional-admission.json').write_bytes(ar)
        manifest = {'results': results}
        mr = canonical(manifest)
        (package / 'manifest-build.json').write_bytes(mr)
        pub = {'schema': 'botw-shader-conditional-publication-v1', 'profile': 'botw-switch-base-v0',
               'package': 'build/botw-c-123456789abc', 'captures': 1, 'variants': 4,
               'conditional_admission_sha256': digest(ar), 'subset_sha256': digest(proof_raw),
               'manifest_sha256': digest(mr), 'device_compilation_tested': False, 'guest_or_gpu_executed': False}
        raw = canonical(pub)
        (source / 'conditional-publication.json').write_bytes(raw)
        return source, rows, digest(raw)

    def test_optional_conditional_keeps_four_modes_as_references_only(self):
        source, rows, trusted = self.owned_conditional()
        self.tool.attach_conditional(rows, source, self.tool.Reader(), trusted_publication=trusted)
        self.assertEqual(rows[0]['status'], 'conditional_reference_only')
        self.assertEqual(len(rows[0]['conditional_modes']), 4)
        self.assertEqual({(r['window_origin_mode'], r['depth_mode']) for r in rows[0]['conditional_modes']},
                         {(0, 0), (0, 1), (1, 0), (1, 1)})
        self.assertTrue(all(r['contract_sha256'] == r['variant_sha256'] for r in rows[0]['conditional_modes']))

    def test_optional_conditional_rejects_changed_contract_or_missing_mode(self):
        source, rows, trusted = self.owned_conditional()
        contract = next((source / 'build/botw-c-123456789abc').glob('*.contract.json'))
        contract.write_text('{}')
        self.reject(lambda: self.tool.attach_conditional(rows, source, self.tool.Reader(), trusted_publication=trusted))
        self.assertEqual(rows[0]['status'], 'only_identity')
        self.assertEqual(rows[0]['conditional_modes'], [])

    def test_optional_modes_cannot_be_missing_or_duplicated(self):
        source, rows, _ = self.owned_conditional()
        proof_path = source / 'admitted/subset-proof.json'
        admission_path = source / 'admitted/conditional-admission.json'
        pub_path = source / 'conditional-publication.json'
        original = json.loads(proof_path.read_bytes())
        for duplicate in (False, True):
            proof = copy.deepcopy(original)
            modes = proof['accepted'][self.meta['key']]['modes']
            if duplicate:
                modes[-1] = copy.deepcopy(modes[0])
            else:
                modes.pop()
            raw = canonical(proof)
            proof_path.write_bytes(raw)
            admission = json.loads(admission_path.read_bytes())
            admission['subset_sha256'] = digest(raw)
            admission_raw = canonical(admission)
            admission_path.write_bytes(admission_raw)
            pub = json.loads(pub_path.read_bytes())
            pub.update(subset_sha256=digest(raw), conditional_admission_sha256=digest(admission_raw))
            pub_raw = canonical(pub)
            pub_path.write_bytes(pub_raw)
            self.reject(lambda: self.tool.attach_conditional(rows, source, self.tool.Reader(),
                        trusted_publication=digest(pub_raw)), 'conditional_modes')
            self.assertEqual(rows[0]['conditional_modes'], [])


if __name__ == '__main__':
    unittest.main()
