"""Full fixed upstream vpool module with a narrowly reused strict-token opt-in."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('BOTW_WHOLE_VPOOL_STRICT_BASELINE')=='1'

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('clang'),'requires actual owned Darwin mmap and clang')
class WholeVirtualPoolStrict(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='whole-vpool-strict-');cls.addClassCleanup(cls.folder.cleanup)
        work=Path(cls.folder.name);target=work/'dm/gfx/nvn';target.mkdir(parents=True)
        for name in ['nvn_vpool.c','nvn_vpool.h']:
            shutil.copy2(EXP/'upstream/dm/gfx/nvn'/name,target/name)
        if not BASELINE:
            result=subprocess.run(['patch','--fuzz=0','-p1','-i',str(EXP/'patches/base-v0-vpool-strict-token.patch')],cwd=work,capture_output=True,text=True)
            if result.returncode:raise AssertionError(result.stdout+result.stderr)
        cls.binary=work/'probe'
        command=['clang','-std=c11','-D_DARWIN_C_SOURCE','-Wall','-Wextra','-Werror','-O1','-g','-pthread',
                 '-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(target),
                 '-I',str(EXP/'upstream/dm/gfx/nvn/include'),str(EXP/'tests/whole_vpool_strict_probe.c'),'-o',str(cls.binary)]
        if BASELINE:command.insert(1,'-DWHOLE_STRICT_BASELINE=1')
        result=subprocess.run(command,capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)
    def case(self,name):
        result=subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('WHOLE_VPOOL_STRICT_OWNED_CPU_OK',result.stdout)
    def test_strict_high_actual_reservation_rejected_and_released_without_publication(self):self.case('strict-high')
    def test_original_high_fallback_and_permanent_tombstone_unchanged(self):self.case('legacy-high')
    def test_strict_success_retains_real_aligned_bounded_prot_none_token(self):self.case('ordinary')
    def test_invalid_input_has_no_reservation_or_registry_mutation(self):self.case('invalid')
    def test_same_thread_held_lease_rejects_before_any_new_reservation(self):self.case('nested')
    def test_duplicate_owner_discards_new_reservation_and_keeps_generation(self):self.case('duplicate')

if __name__=='__main__':unittest.main()
