"""Actual whole flush/import, dirty pages and claims; owned Metal CPU protocols."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
SPEC = importlib.util.spec_from_file_location('flush_pool_fixture', ROOT / 'tests/test_botw_whole_virtual_pools.py')
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
BASELINE = os.environ.get('BOTW_WHOLE_TEXFLUSH_BASELINE') == '1'
PATCH = EXP / 'patches/ordinary-texture-flush-import.patch'
ERROR_PATCH = EXP / 'patches/ordinary-texture-flush-error-propagation.patch'


@unittest.skipUnless(sys.platform == 'darwin', 'actual Darwin VM and Objective-C protocols')
class WholeTextureFlushImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FIXTURE.WholeVirtualPools.setUpClass.__func__(cls)
        function = FIXTURE.BACKING.function
        path = cls.source / 'dm/gfx/nvn/hle_nvn.c'
        if 'if (ready < 0) texwrite_stop(fn, "UNIMPL", why, o);' in path.read_text():
            subprocess.run(['patch', '--fuzz=0', '-R', '-p1', '-i', str(ERROR_PATCH)], cwd=cls.source,
                           capture_output=True, check=True)
        if 'if (st == NVN_TEXFLUSH_UNTRACKED)' in path.read_text():
            subprocess.run(['patch', '--fuzz=0', '-R', '-p1', '-i', str(PATCH)], cwd=cls.source,
                           capture_output=True, check=True)
        cls.base_hle = path.read_text()
        if not BASELINE and PATCH.exists():
            subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(PATCH)], cwd=cls.source,
                           capture_output=True, check=True)
        cls.hle = path.read_text()
        mtl = (cls.source / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        # Remove explicit old test doubles. The new TU links the actual dirty
        # and ownership modules and calls their checked public entry points.
        owned = (cls.work / 'owned_backing_protocols.inc').read_text()
        for name in ('static int nvn_dirty_add_range_checked(', 'static void nvn_dirty_remove_range(',
                     'void nvn_texown_retire(', 'int dm_guest_write_prepare_checked('):
            body = function(owned, name)
            assert owned.count(body) == 1
            owned = owned.replace(body, '')
        (cls.work / 'owned_flush_protocols.inc').write_text(owned)
        entries = '\n'.join(function(cls.hle, name) for name in (
            'static int pool_cpu_kind_supported(', 'static void pool_texture_require_cpu(',
            'NVN(nvnTextureInitialize,', 'static void texwrite_stop(',
            'static uint32_t texflush_layer_count(', 'static uint8_t *texwrite_read_storage(',
            'static void texwrite_import(', 'static void flush_texels(',
            'NVN(nvnTextureFlushTexels,'))
        (cls.work / 'flush_entries.inc').write_text(entries)
        support = '\n'.join(function(mtl, name) for name in (
            'static void hm_del_id(',
            'static const uint8_t *pool_texture_canonical(', 'static id alias_attach(',
            'static __unsafe_unretained id serial_get(', 'static void serial_del(', 'static int texels_format_ok(',
            'static int texels_storage_ok(', 'static size_t texels_image(',
            'static int texels_stage(', 'static void texels_encode(',
            'static int import_fail(', 'static int import_target_ok(',
            'static void import_conflict_log(', 'int mtl_tex_import_check(', 'int mtl_tex_import('))
        # Definitions follow their dependencies in the actual production TU.
        for name in ('static __unsafe_unretained id serial_get(',):
            body = function(mtl, name)
            support = body + '\n' + support.replace(body, '')
        (cls.work / 'flush_metal_entries.inc').write_text(support)
        flags = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                 '-Wno-unused-variable', '-Wno-format', '-pthread', '-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all', '-I', str(cls.source / 'dm/gfx/nvn'),
                 '-I', str(cls.source / 'dm/gfx/nvn/include'), '-I', str(EXP / 'adapter'), '-I', str(cls.work)]
        dirty = cls.work / 'actual_dirty.o'
        built = subprocess.run(['xcrun', 'clang', *flags, '-Dmprotect=owned_dirty_mprotect', '-c',
                                str(cls.source / 'dm/gfx/nvn/nvn_dirty.c'), '-o', str(dirty)],
                               capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)
        cls.binary = cls.work / 'flush-import'
        command = ['xcrun', 'clang', *flags, '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                   str(EXP / 'tests/whole_texture_flush_import_probe.m'), str(dirty),
                   str(cls.source / 'dm/gfx/nvn/nvn_vpool.c'),
                   str(cls.source / 'dm/gfx/nvn/nvn_texflush.c'),
                   str(cls.source / 'dm/gfx/nvn/nvn_texown.c'),
                   str(cls.source / 'dm/gfx/nvn/nvn_texwrite.c'),
                   str(cls.source / 'dm/gfx/nvn/nvn_tex.c'),
                   str(cls.source / 'dm/gfx/tex/nvn_deswizzle.c'),
                   str(cls.source / 'dm/gfx/tex/nvn_fmt_mtl.c'),
                   str(EXP / 'adapter/whole_pool_backing_memory.m'),
                   str(EXP / 'adapter/whole_pool_backing_gate.c'), str(EXP / 'adapter/whole_policy.c'),
                   '-framework', 'Foundation', '-framework', 'Metal', '-o', str(cls.binary)]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, 'WHOLE_TEXTURE_FLUSH_IMPORT_CPU_OK\n')
        self.assertNotIn('runtime error:', result.stderr)
        self.assertNotIn('AddressSanitizer', result.stderr)

    def test_fresh_multilevel_array_flush_imports_real_snapshot_and_keeps_pool_padding(self): self.case('fresh')
    def test_clean_repeat_does_not_upload_and_later_checked_write_is_changed(self): self.case('changed')
    def test_existing_serial_and_shared_alias_keep_the_original_rejection(self): self.case('existing')
    def test_live_and_retired_private_claims_keep_the_original_rejection(self): self.case('claims')
    def test_actual_upload_failure_never_publishes_storage_or_certificate(self):
        self.case('upload-failure')
        self.case('claim-during-upload')
    def test_actual_dirty_protection_failure_never_certifies_an_upload(self): self.case('protection-failure')
    def test_cpu_change_during_upload_is_not_certified_as_the_consumed_snapshot(self): self.case('changed-during-upload')

    def test_only_two_hle_functions_change_and_nonnull_overlay_body_is_preserved(self):
        if BASELINE:
            self.assertEqual(self.hle, self.base_hle)
            return
        function = FIXTURE.BACKING.function
        restored = self.hle
        for name in ('static void texwrite_import(', 'static void flush_texels('):
            restored = restored.replace(function(self.hle, name), function(self.base_hle, name), 1)
        restored = restored.replace(
            '// CPU texel flush (NVN FlushTexels): fixed base-v0 forwards selected whole-layer allocations to its pool flush;\n'
            '// the copy region is unused (nvn_texflush.c). The host has no native data cache to flush, so it must establish\n'
            '// that the CPU pool bytes are the content that Metal will see:',
            '// CPU texel flush (NVN FlushTexels): natively a data cache flush of the selected whole-layer allocations, done only\n'
            '// for CPU_CACHED pools; the region is ignored (nvn_texflush.c). The host has no cache to flush, so a flush is where\n'
            '// it proves that what the CPU wrote is what the GPU will see:')
        restored = restored.replace(
            '// The fixed base-v0 frontend reads the view independently of CPU_CACHED; the underlying driver cache policy is\n'
            '// not established here. This backend applies the view only for CPU_CACHED pools and otherwise checks all layers\n'
            '// conservatively, to avoid silently missing uncached CPU stores in other layers.',
            '// Pools without CPU_CACHED get no native flush and the view is not read; the host still checks every layer, because\n'
            '// uncached CPU stores are GPU-visible on hardware without any flush.')
        self.assertEqual(restored, self.base_hle)
        old = function(self.base_hle, 'static void texwrite_import(')
        new = function(self.hle, 'static void texwrite_import(')
        new = new.replace('if (p) nvn_texwrite_apply(&o->d, p, copy, image);',
                          'nvn_texwrite_apply(&o->d, p, copy, image);')
        new = new.replace('    if (p) {\n        dm_guest_write_prepare(o->mem + p->span_lo, (size_t)(p->span_hi - p->span_lo));\n'
                          '        nvn_texwrite_apply(&o->d, p, copy, o->mem);\n    }',
                          '    dm_guest_write_prepare(o->mem + p->span_lo, (size_t)(p->span_hi - p->span_lo));\n'
                          '    nvn_texwrite_apply(&o->d, p, copy, o->mem);')
        self.assertEqual(new, old)


if __name__ == '__main__': unittest.main()
