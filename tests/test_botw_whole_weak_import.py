"""Actual original import/relocation functions on owned ELF/Mach-O fixtures."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('BOTW_WHOLE_WEAK_IMPORT_BASELINE')=='1'


def function(source,marker):
    a=source.index(marker);b=source.index('{',a);i=b+1;depth=1
    while depth:
        if source[i]=='{':depth+=1
        elif source[i]=='}':depth-=1
        i+=1
    return source[a:i]


@unittest.skipUnless(sys.platform=='darwin','uses the actual Mach-O swhle section reader')
class WholeWeakImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='botw-whole-weak-import-')
        cls.addClassCleanup(cls.temp.cleanup);work=Path(cls.temp.name)
        shutil.copytree(EXP/'upstream',work/'source')
        names=['host-policy.patch','base-v0-force.patch']+([] if BASELINE else ['weak-import-resolution.patch'])
        for name in names:
            result=subprocess.run(['patch','--fuzz=0','--batch','-p1','-i',str(EXP/'patches'/name)],cwd=work/'source',capture_output=True,text=True)
            if result.returncode:raise AssertionError(result.stdout+result.stderr)
        host=work/'source/dm/host';loader=(host/'loader.c').read_text();hle=(host/'hle.c').read_text();title=(host/'dm_title.c').read_text()
        a=hle.index('#define MAX_STUB');b=hle.index('// Per module',a)
        (work/'original_import.inc').write_text(hle[a:b]+function(hle,'uint64_t resolve_import('))
        markers=['static void *module_sym_linear(', 'static uint32_t sym_hash(', 'static void build_symhash(module_t *m) {', 'void *module_sym(', 'static uint64_t resolve_sym(', 'static void *force_lookup(']
        (work/'original_resolution.inc').write_text('\n'.join(function(loader,marker) for marker in markers))
        a=loader.index('struct relr_context');b=loader.index('// DM_LOADHASH=',a)
        (work/'original_relocation.inc').write_text(loader[a:b])
        (work/'original_stop.inc').write_text(function(title,'void dm_unimpl_hit('))
        cls.binary=work/'probe'
        command=['clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-unused-parameter','-pthread',
                 '-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(host),'-I',str(work),str(EXP/'tests/whole_weak_import_probe.c'),'-o',str(cls.binary)]
        if not BASELINE:command.insert(1,'-DWHOLE_WEAK_IMPORT_ADAPTED=1')
        built=subprocess.run(command,capture_output=True,text=True)
        if built.returncode:raise AssertionError(built.stdout+built.stderr)

    def case(self,mode):
        result=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('WHOLE_WEAK_IMPORT_CPU_OK',result.stdout)

    def test_actual_notype_weak_glob_dat_and_jump_slot_remain_zero_without_stub(self):self.case('weak-zero')
    def test_strong_unknown_preserves_original_stub_reuse_and_controlled_stop(self):self.case('strong')
    def test_weak_after_existing_same_name_strong_stub_still_resolves_zero(self):self.case('weak-after-strong')
    def test_weak_real_hle_binding_stays_callable_and_first_duplicate_wins(self):self.case('hle')
    def test_cross_module_definition_stays_ahead_of_hle_and_weak_zero(self):self.case('module')
    def test_defined_local_weak_keeps_existing_local_priority(self):self.case('defined-weak')
    def test_actual_forced_takeover_stays_ahead_of_local_cross_module_and_hle(self):self.case('force')
    def test_original_abs_glob_s_plus_a_and_jump_existing_addend_strategy(self):self.case('addends')
    def test_last_valid_symbol_index_and_relative_bypass_keep_existing_contract(self):self.case('boundaries')


if __name__=='__main__':unittest.main()
