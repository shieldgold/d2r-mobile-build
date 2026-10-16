"""Synthetic capture adapters and pinned offline shader module contract."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from scripts.botw_native import upstream_shaders as adapter
except ImportError:
    adapter = None


def synthetic(version=9, stage=0):
    control = bytearray({8: 2000, 9: 2001, 14: 2120}[version])
    for off, value in ((0, 0x98761234), (4, 1), (8, version), (0x20, len(control)),
                       (0x700, 0x100), (0x704, 0x180), (0x708, 0x30), (0x714, stage)):
        struct.pack_into('<I', control, off, value)
    code = bytearray(0x180)
    struct.pack_into('<I', code, 0, 0x12345678)
    struct.pack_into('<I', code, 0x30, 1 | {0: 1, 1: 5, 2: 4}[stage] << 10)
    return bytes(control), bytes(code)


def inputs(path, version=9, stage=0):
    path.mkdir()
    control, code = synthetic(version, stage)
    h = lambda b: hashlib.sha256(b).hexdigest()
    row = {'control_file': h(control) + '.control', 'code_file': h(code) + '.nvn',
           'control_sha256': h(control), 'code_sha256': h(code),
           'stage': {0: 'vertex', 1: 'fragment', 2: 'geometry'}[stage],
           'container': 'synthetic', 'variation': 0}
    (path / row['control_file']).write_bytes(control)
    (path / row['code_file']).write_bytes(code)
    (path / 'inputs.json').write_text(json.dumps({'schema': 1, 'records': [row]}))
    return row


class UpstreamShaderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(adapter, 'pinned offline shader module is not integrated')
        self.temp = tempfile.TemporaryDirectory(prefix='botw_capture_adapter_')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def test_vendor_source_and_mit_provenance_are_pinned(self):
        report = adapter.verify_vendor()
        self.assertEqual(report['commit'], '21f026e0c7fd9fee575a1bfa8d925553bb4db88e')
        self.assertEqual(report['verified_files'], 374)
        self.assertTrue((adapter.VENDOR / 'LICENSE').read_text().startswith('MIT License'))

    def test_complete_extract_derive_and_nso_dependency_modules_are_preserved(self):
        for name in ('dm/gfx/tools/botw_shader_extract.py', 'dm/gfx/tools/botw_shader_derive.py',
                     'dm/gfx/tools/botw_yaz0.c', 'dm/verify/nsoimg.py',
                     'dm/gfx/tests/test_botw_shader_extract.py', 'dm/gfx/tests/test_botw_shader_derive.py'):
            self.assertTrue((adapter.VENDOR / name).is_file(), name)

    def test_export_matches_upstream_digest_and_source_hashes(self):
        row = inputs(self.path / 'inputs')
        out = self.path / 'captures'
        report = adapter.export_captures(self.path / 'inputs', out)
        self.assertEqual(report['exported'], 1)
        self.assertEqual(report['rejected'], [])
        key = report['captures'][0]['key']
        tool = adapter.load_tool()
        capture = tool.load_capture(str(out), key)
        self.assertEqual((capture.stage_name, capture.fields['version']), ('vertex', 9))
        self.assertEqual(report['captures'][0]['sources'][0]['control_sha256'], row['control_sha256'])
        self.assertEqual(json.loads((out / 'capture-sources.json').read_text()), report)
        self.assertEqual((out / (key + '.control')).stat().st_mode & 0o777, 0o600)

    def test_exact_duplicates_keep_every_source_and_one_capture(self):
        row = inputs(self.path / 'inputs')
        second = dict(row, variation=1)
        (self.path / 'inputs/inputs.json').write_text(json.dumps({'schema': 1, 'records': [row, second]}))
        report = adapter.export_captures(self.path / 'inputs', self.path / 'captures')
        self.assertEqual((report['input_records'], report['exported']), (2, 1))
        self.assertEqual(len(report['captures'][0]['sources']), 2)

    def test_version8_remains_rejected_without_changing_bytes(self):
        row = inputs(self.path / 'inputs', version=8)
        original = (self.path / 'inputs' / row['control_file']).read_bytes()
        report = adapter.export_captures(self.path / 'inputs', self.path / 'captures')
        self.assertEqual(report['exported'], 0)
        self.assertIn('version', report['rejected'][0]['reason'])
        self.assertEqual(list((self.path / 'captures').glob('*.control')), [])
        self.assertEqual((self.path / 'inputs' / row['control_file']).read_bytes(), original)

    def test_tampered_input_is_rejected_before_destination_creation(self):
        row = inputs(self.path / 'inputs')
        (self.path / 'inputs' / row['code_file']).write_bytes(bytes(0x180))
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            adapter.export_captures(self.path / 'inputs', self.path / 'captures')
        self.assertFalse((self.path / 'captures').exists())

    def test_symlink_and_traversal_are_rejected(self):
        row = inputs(self.path / 'inputs')
        code = self.path / 'inputs' / row['code_file']
        saved = self.path / 'saved'
        code.rename(saved)
        code.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, 'regular|symlink|open'):
            adapter.export_captures(self.path / 'inputs', self.path / 'captures')
        row['code_file'] = '../saved'
        (self.path / 'inputs/inputs.json').write_text(json.dumps({'schema': 1, 'records': [row]}))
        with self.assertRaisesRegex(ValueError, 'filename'):
            adapter.export_captures(self.path / 'inputs', self.path / 'captures')

    def test_non_object_and_duplicate_key_manifests_fail_before_writes(self):
        row = inputs(self.path / 'inputs')
        manifest = self.path / 'inputs/inputs.json'
        nonfinite = json.dumps({'schema': 1, 'records': [row], 'value': 'NUMBER'}).replace('"NUMBER"', '1e999')
        for text in ('[]', '{"schema":1,"schema":1,"records":[]}', nonfinite):
            manifest.write_text(text)
            with self.assertRaises(ValueError):
                adapter.export_captures(self.path / 'inputs', self.path / 'captures')
            self.assertFalse((self.path / 'captures').exists())

    def test_stage_identity_and_total_budget_are_checked(self):
        row = inputs(self.path / 'inputs')
        with self.assertRaisesRegex(ValueError, 'budget'):
            adapter.export_captures(self.path / 'inputs', self.path / 'captures', max_total_bytes=32)
        row['stage'] = 'fragment'
        (self.path / 'inputs/inputs.json').write_text(json.dumps({'schema': 1, 'records': [row]}))
        with self.assertRaisesRegex(ValueError, 'stage'):
            adapter.export_captures(self.path / 'inputs', self.path / 'captures')

    def test_existing_and_repository_destinations_are_rejected(self):
        inputs(self.path / 'inputs')
        out = self.path / 'captures'
        out.mkdir()
        with self.assertRaises(FileExistsError):
            adapter.export_captures(self.path / 'inputs', out)
        with self.assertRaisesRegex(ValueError, 'Git work tree'):
            adapter.export_captures(self.path / 'inputs', ROOT / 'outputs/forbidden-capture-test')

    def test_cli_rejected_capture_returns_failure_with_bounded_summary(self):
        inputs(self.path / 'inputs', version=8)
        result = subprocess.run([sys.executable, '-m', 'scripts.botw_native.upstream_shaders',
                                 'export', str(self.path / 'inputs'), str(self.path / 'captures')],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['rejected'], 1)
        self.assertLess(len(result.stdout), 512)

    def test_cli_help_and_upstream_synthetic_suite(self):
        result = subprocess.run([sys.executable, '-m', 'scripts.botw_native.upstream_shaders', '--help'],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(adapter.VENDOR / 'dm/gfx/tests/test_botw_shader_build.py')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Ran 38 tests', result.stderr)


if __name__ == '__main__':
    unittest.main()
