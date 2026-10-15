"""Real public Mach copies through graphics helpers, with owned CPU buffers."""
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'),
                     'public Mach API fixture; no Metal device or GPU used')
class GraphicsProtectedCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-graphics-copy-')
        cls.addClassCleanup(cls.directory.cleanup)
        cls.executable = Path(cls.directory.name) / 'copy'
        result = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc',
                                 '-fobjc-arc-exceptions', '-fblocks', '-O1', '-g',
                                 '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                                 str(ROOT / 'native/botw_graphics/tools/probe_graphics_copy.m'),
                                 '-framework', 'Foundation', '-framework', 'Metal',
                                 '-framework', 'QuartzCore', '-o', str(cls.executable)],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def mode(self, name):
        result = subprocess.run([self.executable, name], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('GRAPHICS_PROTECTED_COPY_CPU_OK', result.stdout)
        self.assertNotIn('ERROR: AddressSanitizer', result.stderr)
        self.assertNotIn('runtime error:', result.stderr)

    def test_bulk_read_preserves_owned_vm_entries_and_objects(self): self.mode('bulk-read')
    def test_bulk_write_preserves_owned_vm_entries_and_objects(self): self.mode('bulk-write')
    def test_block_linear_write_run_preserves_owned_vm_entries_and_objects(self): self.mode('bulk-run')
    def test_static_source_and_destination_faults_precede_any_prefix(self): self.mode('faults')
    def test_overlapping_operands_match_memmove_in_both_directions(self): self.mode('overlap')
    def test_logical_access_and_whole_destination_prepare_keep_order(self): self.mode('logical-prepare')
    def test_zero_and_overflow_operands_remain_controlled(self): self.mode('bounds')
