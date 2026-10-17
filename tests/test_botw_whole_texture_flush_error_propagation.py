"""Actual Flush import-check errors, with the original dirty/ownership modules."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
SPEC = importlib.util.spec_from_file_location('flush_import_fixture', ROOT / 'tests/test_botw_whole_texture_flush_import.py')
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
BASELINE = os.environ.get('BOTW_WHOLE_TEXFLUSH_ERROR_BASELINE') == '1'
PATCH = EXP / 'patches/ordinary-texture-flush-error-propagation.patch'


@unittest.skipUnless(sys.platform == 'darwin', 'actual Darwin VM and Objective-C protocols')
class WholeTextureFlushErrorPropagation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FIXTURE.WholeTextureFlushImport.setUpClass.__func__(cls)
        function = FIXTURE.FIXTURE.BACKING.function
        path = cls.source / 'dm/gfx/nvn/hle_nvn.c'
        cls.original_hle = path.read_text()
        if not BASELINE and PATCH.exists():
            subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(PATCH)], cwd=cls.source,
                           capture_output=True, check=True)
        cls.error_hle = path.read_text()
        entries = cls.work / 'flush_entries.inc'
        old = function(cls.original_hle, 'static void flush_texels(')
        new = function(cls.error_hle, 'static void flush_texels(')
        entries.write_text(entries.read_text().replace(old, new, 1))
        # Keep the frozen original probe intact. This local copy only replaces
        # its log sink and renames its test main; all producer bodies stay real.
        probe = (EXP / 'tests/whole_texture_flush_import_probe.m').read_text()
        sink = 'static void owned_log(const char *format,...){(void)format;}'
        assert probe.count(sink) == 1
        probe = probe.replace(sink, 'static void owned_log(const char *format,...);', 1)
        probe = probe.replace('int main(int argc,char **argv)', 'int frozen_flush_import_main(int argc,char **argv)', 1)
        (cls.work / 'owned_error_base_probe.inc').write_text(probe)
        flags = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                 '-Wno-unused-variable', '-Wno-format', '-pthread', '-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all', '-I', str(cls.source / 'dm/gfx/nvn'),
                 '-I', str(cls.source / 'dm/gfx/nvn/include'), '-I', str(EXP / 'adapter'), '-I', str(cls.work)]
        cls.binary = cls.work / 'flush-error-propagation'
        command = ['xcrun', 'clang', *flags, '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                   str(EXP / 'tests/whole_texture_flush_error_propagation_probe.m'), str(cls.work / 'actual_dirty.o'),
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
        self.assertEqual(result.stdout, 'WHOLE_FLUSH_ERROR_PROPAGATION_CPU_OK\n')
        self.assertNotIn('OWNED_SENSITIVE_POOL_CANARY', result.stdout + result.stderr)
        self.assertNotIn('runtime error:', result.stderr)
        self.assertNotIn('AddressSanitizer', result.stderr)

    def test_unsupported_import_preserves_its_fixed_error(self): self.case('unsupported')
    def test_live_claim_preserves_its_fixed_error(self): self.case('live')
    def test_retired_claim_preserves_its_fixed_error(self): self.case('retired')
    def test_existing_serial_and_alias_still_use_the_original_generic_error(self):
        self.case('serial')
        self.case('alias')
    def test_post_import_protection_and_cpu_change_still_use_the_original_generic_error(self):
        self.case('protection')
        self.case('changed')
    def test_successful_import_still_synchronizes_the_actual_snapshot(self): self.case('fresh')

    def test_only_the_check_result_branch_changes(self):
        if BASELINE or not PATCH.exists():
            self.assertEqual(self.error_hle, self.original_hle)
            return
        old = '        if (mtl_tex_import_check(o, &why) > 0) {'
        new = ('        int ready = mtl_tex_import_check(o, &why);\n'
               '        if (ready < 0) texwrite_stop(fn, "UNIMPL", why, o);\n'
               '        if (ready > 0) {')
        self.assertEqual(self.error_hle.replace(new, old, 1), self.original_hle)
        restored = subprocess.run(['patch', '--fuzz=0', '-R', '-p1', '-i', str(PATCH)], cwd=self.source,
                                  capture_output=True, check=True)
        self.assertEqual(restored.returncode, 0)
        self.assertEqual((self.source / 'dm/gfx/nvn/hle_nvn.c').read_text(), self.original_hle)


if __name__ == '__main__': unittest.main()
