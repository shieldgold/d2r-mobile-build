"""Synthetic real-Metal virtual-pool consumption, remap and failure preflight."""
from pathlib import Path
import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from botw_metal_counter_capability import require_counter_sampling

ROOT = Path(__file__).resolve().parents[1]
GRAPHICS = ROOT / 'native/botw_graphics'


@unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'),
                     'real Metal fixture requires Darwin public frameworks')
class VirtualPoolGPUTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-vpool-gpu-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        source = folder / 'pool.m'
        source.write_text(f'#include "{GRAPHICS / "graphics.m"}"\n'
                          f'#include "{GRAPHICS / "tools/probe_virtual_pool.c"}"\n')
        cls.executable = folder / 'pool'
        result = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                                 '-Wall', '-Wextra', '-Werror', '-O1', '-g',
                                 '-fsanitize=address,undefined', '-Wl,-export_dynamic', str(source),
                                 '-framework', 'Foundation', '-framework', 'Metal',
                                 '-framework', 'QuartzCore', '-o', str(cls.executable)],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def mode(self, name):
        result = subprocess.run([self.executable, name], capture_output=True,
                                text=True, timeout=40, env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
        if result.returncode == 77:
            self.skipTest('Metal device unavailable')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('VIRTUAL_POOL_REAL_GPU_', result.stdout)
        self.assertNotIn('runtime error:', result.stderr)
        self.assertNotIn('ERROR: AddressSanitizer', result.stderr)
        self.assertNotIn('failed assertion', result.stderr)

    def test_finalize_waits_for_shared_event_controlled_inflight_gpu_and_releases_backing(self):
        self.mode('inflight-finalize')

    def test_remap_waits_for_shared_event_controlled_inflight_gpu_before_publication(self):
        self.mode('inflight-remap')

    def test_repeated_completed_gpu_submissions_do_not_accumulate_records(self):
        self.mode('bounded-records')

    def test_virtual_vertex_uniform_and_writable_storage_bind_current_physical_bytes_on_two_queues(self):
        self.mode('shader-buffers')

    def test_sampler_descriptor_checks_device_and_preserves_registered_immutable_copy(self):
        self.mode('sampler-device')

    def test_checked_shutdown_retains_real_nocopy_backing_without_callback_and_retries(self):
        self.mode('checked-shutdown')

    def test_encode_error_releases_uncommitted_real_metal_resources_for_checked_shutdown(self):
        self.mode('encode-error')

    def test_static_reflection_error_rejects_before_preceding_counter_is_published(self):
        require_counter_sampling()
        self.mode('short-uniform-preflight')

    def test_blit_encode_exception_closes_encoder_discards_gpu_writes_and_releases_nocopy(self):
        self.mode('copy-encode-error')

    def test_copied_code_matches_gpu_producer_and_requires_cpu_cache_flush(self):
        self.mode('copied-code')

    def test_recorded_logical_address_consumes_current_mapping_at_submit(self):
        self.mode('copy-remap')

    def test_unmapped_later_copy_rejects_before_earlier_gpu_write(self):
        self.mode('unmap-preflight')

    def test_fragmented_gpu_range_is_named_rejected(self):
        self.mode('fragmented')

    def test_color_kind_cannot_be_consumed_as_raw_buffer(self):
        self.mode('fe-buffer')
