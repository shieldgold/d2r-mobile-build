"""Owned source-package boundaries; no shader or Guest execution."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'experiments/nxnative_base_v0/tools/package_shader_sources.py'
H = lambda raw: hashlib.sha256(raw).hexdigest()
J = lambda value: (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


class ShaderSourcePackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not TOOL.is_file():
            raise AssertionError('native shader source package wrapper is missing')
        spec = importlib.util.spec_from_file_location('owned_native_source_package', TOOL)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owned-native-source-package-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.chain = '123456789abc'
        self.capture = 'a' * 64
        self.native = 'b' * 64
        self.admission = {'captures': {self.capture[:16]: {'capture_sha256': self.capture,
                            'stage_name': 'vertex', 'variants': []}}}
        modes = []
        self.files = {'conditional-publication.json': J({'owned': 'publication'}),
                      'subset-proof.json': J({'owned': 'proof'}),
                      'base-bindings.json': J({'owned': 'bindings'}),
                      'base-formats.json': J({'owned': 'formats'})}
        for w, d in ((0, 0), (0, 1), (1, 0), (1, 1)):
            contract = J({'owned': [w, d]})
            variant = H(contract)
            self.admission['captures'][self.capture[:16]]['variants'].append(variant)
            modes.append(dict(window_origin_mode=w, depth_mode=d, variant_sha256=variant,
                contract_sha256=variant,
                contract_path=f'build/botw-c-{self.chain}/{variant}.contract.json'))
            for suffix, raw in (('.contract.json', contract), ('.json', b'{}'),
                                ('.metal', b'// owned source\n')):
                self.files[f'botw-c-{self.chain}/{variant}{suffix}'] = raw
        self.files['conditional-admission.json'] = J(self.admission)
        self.files[f'botw-c-{self.chain}/manifest-build.json'] = b'{}'
        self.index = dict(schema='botw-whole-conditional-shader-package-v1',
            profile='botw-switch-base-v0', chain=self.chain, directory='botw-c-' + self.chain,
            publication_sha256=H(self.files['conditional-publication.json']),
            admission_sha256=H(self.files['conditional-admission.json']),
            subset_sha256=H(self.files['subset-proof.json']),
            binding_contract_sha256=H(self.files['base-bindings.json']),
            format_contract_sha256=H(self.files['base-formats.json']))
        self.files['shader-package.json'] = J(self.index)
        self.bridge = dict(schema='nxnative-base-v0-shader-key-bridge-v1',
            profile='botw-switch-base-v0', game_version='1.0.0', captures=1, native_keys=1,
            catalog_complete_for_entire_game=False, native_loader_admitted=False,
            gameplay_verified=False, guest_or_gpu_executed=False,
            conditional_source=dict(publication_sha256=self.index['publication_sha256'],
                admission_sha256=self.index['admission_sha256'],
                subset_sha256=self.index['subset_sha256'], captures=1, variants=4),
            rows=[dict(native_sha256=self.native, native_key=self.native[:16],
                       capture_sha256=self.capture, capture_key=self.capture[:16], stage=0,
                       stage_name='vertex', status='conditional_reference_only', conditional_modes=modes)])

    def combine(self, bridge=None, files=None, index=None):
        raw = J(bridge if bridge is not None else self.bridge)
        return self.tool.combine_verified(files or self.files, index or self.index, raw,
                                           expected_bridge_sha256=H(raw))

    def test_bundle_preserves_original_bytes_and_excludes_private_inputs(self):
        result = self.combine()
        self.assertEqual(set(result), set(self.files) | {'native-key-bridge.json', 'native-source-package.json'})
        for name, raw in self.files.items():
            self.assertEqual(result[name], raw)
        self.assertEqual(result['native-key-bridge.json'], J(self.bridge))
        wrapper = json.loads(result['native-source-package.json'])
        self.assertEqual(wrapper['bridge_sha256'], H(J(self.bridge)))
        self.assertEqual(wrapper['conditional_index_sha256'], H(self.files['shader-package.json']))

    def test_changed_bridge_or_publication_rejected(self):
        with self.assertRaises(ValueError):
            self.tool.combine_verified(self.files, self.index, J(self.bridge), expected_bridge_sha256='0' * 64)
        files = dict(self.files, **{'conditional-publication.json': b'changed'})
        with self.assertRaises(ValueError):
            self.combine(files=files)

    def test_missing_duplicate_or_wrong_full_capture_and_stage_rejected(self):
        for edit in ('missing', 'duplicate', 'capture', 'stage', 'mode', 'native'):
            bridge = copy.deepcopy(self.bridge)
            row = bridge['rows'][0]
            if edit == 'missing': row['status'] = 'only_identity'
            elif edit == 'duplicate': bridge['rows'].append(copy.deepcopy(row))
            elif edit == 'capture': row['capture_sha256'] = 'c' * 64
            elif edit == 'stage': row['stage_name'] = 'fragment'
            elif edit == 'mode': row['conditional_modes'][0]['depth_mode'] = True
            elif edit == 'native': row['native_key'] = 'c' * 16
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.combine(bridge=bridge)

    def test_unknown_files_and_private_audit_paths_rejected(self):
        for name in ('captures/private.code', '../escape', 'toolchain/a.py', 'env.txt'):
            files = dict(self.files); files[name] = b'owned'
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.combine(files=files)

    def test_fresh_atomic_directory_and_exact_file_bytes(self):
        out = self.root / 'shaders'
        files = self.combine()
        self.tool.publish(files, out)
        self.assertEqual({p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()}, set(files))
        for name, raw in files.items():
            self.assertEqual((out / name).read_bytes(), raw)
            self.assertEqual((out / name).stat().st_mode & 0o777, 0o444)
        with self.assertRaises(FileExistsError): self.tool.publish(files, out)

    def test_nonblocking_regular_input_and_symlink_paths(self):
        fifo = self.root / 'fifo'; os.mkfifo(fifo)
        with self.assertRaises(ValueError): self.tool.read(fifo, 1024)
        file = self.root / 'regular'; file.write_bytes(b'owned')
        link = self.root / 'link'; link.symlink_to(file)
        with self.assertRaises(ValueError): self.tool.read(link, 1024)
        self.assertEqual(self.tool.read(file, 5), b'owned')

    def test_fixed_reuse_modules_are_loaded_from_fresh_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.tool.original_validator(Path(temporary).resolve()) as (validator, ports):
                self.assertEqual(validator.PROFILE, 'botw-switch-base-v0')
                self.assertEqual(len(ports.source_builder.source_toolchain_hashes.__code__.co_code) > 0, True)
                self.assertTrue(Path(validator.__file__).is_relative_to(Path(temporary).resolve()))
                self.assertFalse(any(Path(temporary).rglob('*.pyc')))
                bad = self.root / 'incomplete'; bad.mkdir()
                with self.assertRaises((ValueError, OSError)): validator.shader_files(bad, ports)


if __name__ == '__main__':
    unittest.main()
