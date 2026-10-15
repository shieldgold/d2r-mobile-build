"""Physical/virtual pool adapter CPU contracts with owned synthetic mappings.

This fixture supplies host object/Metal-buffer stand-ins, never a Metal device or
GPU submission. Production integration and real Metal data tests are separate.
"""
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
GRAPHICS = ROOT / 'native/botw_graphics'

@unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'),
                     'Darwin VM and Objective-C required; no GPU is used')
class MemoryPoolAdapterCPUTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-memory-pools-')
        cls.addClassCleanup(cls.directory.cleanup)
        cls.executable = Path(cls.directory.name) / 'pool-cpu'
        built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                                '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                '-O1', '-g', '-fsanitize=address,undefined', '-DNVN_VPOOL_TESTING',
                                str(GRAPHICS / 'tools/probe_memory_pools.m'),
                                '-framework', 'Foundation', '-framework', 'Metal',
                                '-o', str(cls.executable)], capture_output=True,
                               text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)
    def run_mode(self, mode):
        result = subprocess.run([str(self.executable), mode], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('POOL_ADAPTER_CPU_OK', result.stdout)
        self.assertNotIn('ERROR: AddressSanitizer', result.stderr)
        self.assertNotIn('runtime error:', result.stderr)
    def test_completed_synthetic_record_bookkeeping_is_bounded(self): self.run_mode('bounded-records')
    def test_admission_diagnostic_preserves_logical_short_circuit_and_copied_fallback(self): self.run_mode('admission-logical')
    def test_admission_diagnostic_reports_write_denial_without_vm_query(self): self.run_mode('admission-write')
    def test_admission_diagnostic_reports_failed_query_without_reading_region_info(self): self.run_mode('admission-vm-query')
    def test_admission_diagnostic_distinguishes_gap_empty_and_missing_start(self): self.run_mode('admission-vm-region')
    def test_admission_diagnostic_reports_leaf_protection_after_existing_submap_queries(self): self.run_mode('admission-vm-protection')
    def test_admission_diagnostic_reports_actual_single_region_span_boundary(self): self.run_mode('admission-vm-span')
    def test_admission_diagnostic_reports_rounding_overflow_before_callbacks(self): self.run_mode('admission-rounding')
    def test_request_bounds_and_readability_precede_snapshot_allocation(self): self.run_mode('mapping-allocation-bounds')
    def test_failed_registry_and_dirty_registration_release_nocopy_before_return(self): self.run_mode('unpublished-cleanup')
    def test_failed_unpublished_initialization_remains_pinned_until_ack_and_unregister(self): self.run_mode('unpublished-pinned')
    def test_initialize_reaps_acknowledged_dirty_failure_and_retries(self): self.run_mode('unpublished-retry')
    def test_registry_failure_still_pins_unreleased_metal_and_reaps_before_retry(self): self.run_mode('unpublished-register-failure')
    def test_external_nocopy_reference_prevents_successful_finalize(self): self.run_mode('borrowed-pinned')
    def test_finalize_clears_all_borrowed_resource_references(self): self.run_mode('retire-references')
    def test_ordinary_exact_nocopy_permission_failures_stay_at_consumers(self): self.run_mode('ordinary-protection')
    def test_lookup_and_current_contiguous_resolution(self): self.run_mode('resolve')
    def test_fragmented_kind_hole_stale_and_physical_direct_rejections(self): self.run_mode('resolve-negative')
    def test_batch_preflight_snapshots_and_sealed_miss(self): self.run_mode('batch')
    def test_physical_virtual_public_family_and_nocopy_bias(self): self.run_mode('family')
    def test_full_request_snapshot_later_failure_and_unknown_kind(self): self.run_mode('mapping')
    def test_unmap_count_zero_and_actual_request_protection(self): self.run_mode('mapping-negative')
    def test_physical_vm_alignment_max_and_failed_creation_publish_nothing(self): self.run_mode('initialize-negative')
    def test_dirty_finalize_failure_quarantines_borrowed_backing(self): self.run_mode('finalize')
    def test_invalid_and_copied_object_provenance_fails_before_header_read(self): self.run_mode('provenance')
    def test_cpu_noaccess_is_not_a_public_mapping(self): self.run_mode('cpu-access')
    def test_synthetic_gpu_records_retain_until_completion_and_report_error(self): self.run_mode('gpu-records')

if __name__ == '__main__': unittest.main()
