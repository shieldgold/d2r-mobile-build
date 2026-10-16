"""Fixed graphics image tables through the actual complete offline analyzer."""
from pathlib import Path
import hashlib
import importlib.util
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_upstream_host'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WholeShaderImageTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='botw-image-source-')
        cls.addClassCleanup(cls.work.cleanup)
        cls.stage = Path(cls.work.name) / 'stage'
        cls.adapter = load('whole_image_source_adapter', COMPONENT / 'adapter/shader_source_build.py')
        cls.receipt = cls.adapter.stage_source_toolchain(cls.stage)
        cls.tool = load('whole_image_complete_analyzer', cls.stage / 'dm/gfx/tools/botw_shader_build.py')

    def test_graphics_exact_image_rows_join_the_existing_texture_table(self):
        for stage in (0, 1):
            for offset in range(0x20, 0x160, 16):
                with self.subTest(stage=stage, offset=offset):
                    self.assertEqual(self.tool.driver_bank_reads(
                        'vs_c2)._m0[%du]' % (offset // 16), 'vs_c2', 2, stage), ([offset], []))

    def test_other_stages_and_bank_boundaries_remain_rejected(self):
        for stage in (2, 3, 4, 5):
            for offset in range(0x120, 0x160, 16):
                with self.subTest(stage=stage, offset=offset):
                    rows, outside = self.tool.driver_bank_reads(
                        'ps_c2)._m0[%du]' % (offset // 16), 'ps_c2', 2, stage)
                    self.assertEqual(rows, [offset]); self.assertEqual(outside, ['c2+%#x' % offset])
        for offset in (0, 0x10, 0x160, 0x170):
            self.assertTrue(self.tool.driver_bank_reads('vs_c2)._m0[%du]' % (offset // 16), 'vs_c2', 2, 0)[1])
        self.assertEqual(self.tool.driver_bank_reads('vs_c2)._m0[index]', 'vs_c2', 2, 0), ([], ['dynamic index']))
        self.assertEqual(self.tool.DRIVER_TABLES[0], {0: [(0x110, 0x210)], 1: [(0x510, 0x610)], 5: [(0x210, 0x410)]})
        self.assertEqual(self.tool.DRIVER_TABLES[2][5], [(0x20, 0x120)])

    def test_full_vendor_is_preserved_and_exact_staged_hashes_are_reported(self):
        from scripts.botw_native import upstream_shaders
        self.assertEqual(upstream_shaders.verify_vendor()['verified_files'], 374)
        expected = self.adapter.source_toolchain_hashes(self.stage)
        self.assertEqual(len(expected), 374)
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((self.stage / name).read_bytes()).hexdigest(), digest)
        receipt = json.loads((self.stage / 'base-graphics-stage-receipt.json').read_bytes())
        self.assertEqual(receipt, self.receipt)
        self.assertEqual(receipt['image_stage_slots'], {'0': 8, '1': 8})
        self.assertFalse(receipt['facts_invented'])
        self.assertFalse(receipt['guest_or_gpu_executed'])

    def test_modified_stage_or_receipt_is_refused(self):
        receipt = self.stage / 'base-graphics-stage-receipt.json'
        original = receipt.read_bytes()
        try:
            value = json.loads(original); value['image_stage_slots']['5'] = 8
            receipt.write_text(json.dumps(value))
            with self.assertRaises(ValueError): self.adapter.source_toolchain_hashes(self.stage)
            for value in (b'{', b'[]', original.replace(b'"facts_invented": false', b'"facts_invented": 0'),
                          original.replace(b'"0": 8', b'"0": 8.0')):
                receipt.write_bytes(value)
                with self.subTest(receipt=value[:40]), self.assertRaises(ValueError):
                    self.adapter.source_toolchain_hashes(self.stage)
        finally: receipt.write_bytes(original)
        path = self.stage / 'dm/gfx/tools/botw_shader_build.py'; original = path.read_bytes()
        try:
            path.write_bytes(original + b'\n')
            with self.assertRaises(ValueError): self.adapter.source_toolchain_hashes(self.stage)
        finally: path.write_bytes(original)


if __name__ == '__main__': unittest.main()
