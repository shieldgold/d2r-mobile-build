"""Synthetic virtual textures must consume current, real Metal backing bytes."""
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
                     'requires Darwin and public Metal frameworks')
class VirtualTextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-virtual-texture-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        source = folder / 'texture.m'
        source.write_text(f'#include "{GRAPHICS / "graphics.m"}"\n'
                          f'#include "{GRAPHICS / "tools/probe_virtual_texture.c"}"\n')
        cls.executable = folder / 'texture'
        result = subprocess.run([
            'xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall',
            '-Wextra', '-Werror', '-O1', '-g', '-fsanitize=address,undefined',
            str(source), '-framework', 'Foundation', '-framework', 'Metal',
            '-framework', 'QuartzCore', '-o', str(cls.executable)],
            capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def mode(self, mode):
        result = subprocess.run([self.executable, mode], capture_output=True,
                                text=True, timeout=45, env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
        if result.returncode == 77:
            self.skipTest('Metal device unavailable')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'VIRTUAL_TEXTURE_{mode}_OK', result.stdout)
        for failure in ('runtime error:', 'ERROR: AddressSanitizer',
                        'failed assertion'):
            self.assertNotIn(failure, result.stderr)

    def test_linear_render_uses_current_mapping_and_preserves_padding(self):
        self.mode('linear-remap')

    def test_descriptor_view_rebuild_preserves_selection_after_remap(self):
        self.mode('descriptor-remap')

    def test_ordinary_color_kind_uses_real_block_linear_sampling(self):
        self.mode('fe-sample')

    def test_unmapped_initialization_is_metadata_only_until_gpu_use(self):
        self.mode('unmapped-init')

    def test_other_page_remap_keeps_private_gpu_authority(self):
        self.mode('nonoverlap-private')

    def test_private_gpu_authority_remap_is_rejected_without_losing_content(self):
        self.mode('private-remap')

    def test_virtual_alias_feedback_is_rejected_before_any_batch_write(self):
        self.mode('alias-feedback')

    def test_fragmented_texture_rejects_before_earlier_clear(self):
        self.mode('fragmented')

    def test_metadata_kind_mismatch_is_rejected(self):
        self.mode('wrong-kind')

    def test_public_cpu_write_cannot_access_virtual_backing(self):
        self.mode('cpu-write')

    def test_private_alias_after_counter_rejects_before_first_gpu_publication(self):
        require_counter_sampling()
        self.mode('private-alias-counter')

    def test_private_copy_after_counter_rejects_before_first_gpu_publication(self):
        require_counter_sampling()
        self.mode('private-copy-counter')

    def test_finalize_releases_recorded_views_before_backing_is_freed(self):
        self.mode('finalize-view')

    def test_virtual_shader_code_remap_preserves_exact_active_translation(self):
        self.mode('code-remap')

    def test_copy_to_active_virtual_code_rejects_before_counter_and_draw(self):
        require_counter_sampling()
        self.mode('code-copy-preflight')

    def test_descriptor_storage_remap_requires_current_canonical_slot(self):
        self.mode('descriptor-storage')

    def test_raw_gpu_descriptor_write_cannot_reuse_registered_host_semantics(self):
        self.mode('descriptor-raw-gpu-write')

    def test_raw_cpu_descriptor_write_cannot_reuse_registered_host_semantics(self):
        self.mode('descriptor-raw-cpu-write')

    def test_raw_descriptor_write_then_draw_rejects_before_counter_publication(self):
        require_counter_sampling()
        self.mode('descriptor-raw-batch-write')

    def test_same_draw_storage_write_to_active_code_rejects_before_publication(self):
        require_counter_sampling()
        self.mode('code-storage-self')

    def test_same_draw_linear_target_write_to_active_code_rejects_before_publication(self):
        require_counter_sampling()
        self.mode('code-linear-target')

    def test_same_draw_linear_attachment_cannot_overwrite_active_descriptor(self):
        require_counter_sampling()
        self.mode('descriptor-target-linear-self')

    def test_earlier_linear_clear_cannot_overwrite_later_descriptor(self):
        require_counter_sampling()
        self.mode('descriptor-target-linear-history')

    def test_same_draw_private_attachment_cannot_claim_active_descriptor(self):
        require_counter_sampling()
        self.mode('descriptor-target-private-self')

    def test_earlier_private_clear_cannot_claim_later_descriptor(self):
        require_counter_sampling()
        self.mode('descriptor-target-private-history')

    def test_tail_copy_to_active_code_rejects_before_followup_submit(self):
        require_counter_sampling()
        self.mode('code-tail-copy')
