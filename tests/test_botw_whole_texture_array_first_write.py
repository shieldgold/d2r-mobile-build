"""Complete selected-layer first writes must initialize a fresh array's other layers."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
SPEC = importlib.util.spec_from_file_location('array_actual_flush_fixture', ROOT / 'tests/test_botw_whole_texture_flush_error_propagation.py')
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
BASELINE = os.environ.get('BOTW_WHOLE_ARRAY_FIRST_WRITE_BASELINE') == '1'
PATCH = EXP / 'patches/ordinary-texture-array-first-write.patch'
BLOCK = '''    if (!o->has_layout && coherence == NVN_TEXFLUSH_UNTRACKED && texflush_full_layer(o, &p) &&
        (sync_layer || sync_count != texflush_layer_count(o))) {
        int ready = mtl_tex_import_check(o, &why);
        if (ready > 0) { texwrite_import(fn, o, &p, copy, texflush_layer_count(o)); free(copy); return; }
    }
'''


@unittest.skipUnless(sys.platform == 'darwin', 'actual Darwin VM and Objective-C protocols')
class WholeTextureArrayFirstWrite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FIXTURE.WholeTextureFlushErrorPropagation.setUpClass.__func__(cls)
        function = FIXTURE.FIXTURE.FIXTURE.BACKING.function
        path = cls.source / 'dm/gfx/nvn/hle_nvn.c'
        if BLOCK in path.read_text():
            subprocess.run(['patch', '--fuzz=0', '-R', '-p1', '-i', str(PATCH)], cwd=cls.source,
                           capture_output=True, check=True)
        cls.original_hle = path.read_text()
        if not BASELINE and PATCH.exists():
            subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(PATCH)], cwd=cls.source,
                           capture_output=True, check=True)
        cls.array_hle = path.read_text()
        mtl = (cls.source / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        entries = cls.work / 'flush_entries.inc'
        entries.write_text(entries.read_text() + '\n' + '\n'.join(function(cls.array_hle, name) for name in (
            'static uint8_t *texwrite_snapshot(', 'static int texflush_full_layer(',
            'static void texwrite_changed_log(', 'static uint8_t *texwrite_replace_begin(',
            'static void texwrite_replace_end(', 'static void write_texels(', 'NVN(nvnTextureWriteTexels,')))
        (cls.work / 'actual_tex_mtl.inc').write_text('\n'.join(function(mtl, name) for name in (
            'static void pool_texture_cpu_authority(', 'static size_t mtl_pf_bytes(',
            'static id<MTLBuffer> zero_buf(', 'static void tex_zero_fill(', 'static id<MTLTexture> tex_mtl(')))
        (cls.work / 'actual_mtl_write.inc').write_text(function(mtl, 'int mtl_write_texels('))
        (cls.work / 'actual_empty_xv.inc').write_text('\n'.join(function(mtl, name) for name in (
            'static xv_ent *xv_find(', 'static void xv_check(')))
        flags = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                 '-Wno-unused-variable', '-Wno-format', '-pthread', '-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                 '-I', str(cls.source / 'dm/gfx/nvn'), '-I', str(cls.source / 'dm/gfx/nvn/include'),
                 '-I', str(EXP / 'adapter'), '-I', str(cls.work)]
        cls.binary = cls.work / 'texture-array-first-write'
        command = ['xcrun', 'clang', *flags, str(EXP / 'tests/whole_texture_array_first_write_probe.m'),
                   str(cls.work / 'actual_dirty.o')]
        command += [str(cls.source / p) for p in (
            'dm/gfx/nvn/nvn_vpool.c', 'dm/gfx/nvn/nvn_texflush.c', 'dm/gfx/nvn/nvn_texown.c',
            'dm/gfx/nvn/nvn_texwrite.c', 'dm/gfx/nvn/nvn_tex.c',
            'dm/gfx/tex/nvn_deswizzle.c', 'dm/gfx/tex/nvn_fmt_mtl.c')]
        command += [str(EXP / 'adapter' / p) for p in (
            'whole_pool_backing_memory.m', 'whole_pool_backing_gate.c', 'whole_policy.c')]
        command += ['-framework', 'Foundation', '-framework', 'Metal', '-o', str(cls.binary)]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, 'WHOLE_TEXTURE_ARRAY_FIRST_WRITE_CPU_OK\n')
        self.assertNotIn('runtime error:', result.stderr)
        self.assertNotIn('AddressSanitizer', result.stderr)

    def test_first_complete_layer_zero_write_then_all_layer_flush(self): self.case('fresh0')
    def test_first_complete_layer_one_write_preserves_layer_zero(self): self.case('fresh1')
    def test_existing_serial_and_exact_alias_keep_the_selected_layer_path(self):
        self.case('serial')
        self.case('alias')
    def test_other_layer_live_and_retired_claims_do_not_add_a_write_rejection(self):
        self.case('live')
        self.case('retired')
    def test_all_layer_fast_path_does_not_gain_a_precheck(self): self.case('whole')
    def test_original_partial_layer_negative_check_still_rejects(self): self.case('partial-negative')
    def test_uncertain_post_import_state_still_rejects_flush(self):
        self.case('protection')
        self.case('changed')

    def test_only_the_positive_incomplete_coverage_block_is_added(self):
        if BASELINE or not PATCH.exists():
            self.assertEqual(self.array_hle, self.original_hle)
            return
        self.assertEqual(self.array_hle.replace(BLOCK, '', 1), self.original_hle)
        subprocess.run(['patch', '--fuzz=0', '-R', '-p1', '-i', str(PATCH)], cwd=self.source,
                       capture_output=True, check=True)
        self.assertEqual((self.source / 'dm/gfx/nvn/hle_nvn.c').read_text(), self.original_hle)


if __name__ == '__main__': unittest.main()
