"""Real owned VM spans and canonical protected copies; no guest execution."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_upstream_host'


@unittest.skipUnless(platform.system() == 'Darwin', 'actual Darwin normal operand VM APIs required')
class WholeGuestMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-whole-guest-memory-')
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.binary = Path(cls.scratch.name) / 'probe'
        result = subprocess.run(['xcrun', 'clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra',
            '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-I', str(COMPONENT / 'adapter'), str(COMPONENT / 'tests/whole_guest_memory_probe.c'),
            str(COMPONENT / 'adapter/whole_guest_memory.c'), '-o', str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_owned_read_and_actual_write_copy(self): self.case('copy')
    def test_logical_noaccess_denial_precedes_fallback_and_preparation(self): self.case('denial')
    def test_unknown_host_mapping_is_not_authorized(self): self.case('unknown')
    def test_cross_boundary_and_overflow_never_call_preparer(self): self.case('bounds')
    def test_real_vm_noaccess_is_rejected_without_fault(self): self.case('none')
    def test_actual_read_only_vm_cannot_be_written(self): self.case('readonly')
    def test_dirty_protection_requires_real_preparation_ack_before_write(self): self.case('dirty')
    def test_failed_preparation_preserves_destination_bytes(self): self.case('prepare-fail')
    def test_module_and_current_thread_metadata_can_supply_unrelated_ownership(self): self.case('fallback')
    def test_incomplete_configuration_is_rejected(self): self.case('config')
    def test_invalid_owner_classification_is_not_success(self): self.case('invalid-class')
    def test_multileaf_read_and_write_permissions_are_checked_over_the_whole_span(self): self.case('multileaf')


if __name__ == '__main__':
    unittest.main()
