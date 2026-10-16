"""The actual loader initializer calls owned callbacks, never Guest code."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('BOTW_WHOLE_CONSTRUCTOR_BASELINE')=='1'


class WholeConstructorOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='botw-owned-constructors-');cls.addClassCleanup(cls.temp.cleanup)
        work=Path(cls.temp.name);shutil.copytree(EXP/'upstream/dm',work/'dm')
        if not BASELINE:
            run=subprocess.run(['patch','--fuzz=0','-p1','-i',str(EXP/'patches/base-v0-constructor-order.patch')],cwd=work,capture_output=True,text=True)
            if run.returncode:raise AssertionError(run.stdout+run.stderr)
        original=(EXP/'upstream/dm/host/loader.c').read_text();actual=(work/'dm/host/loader.c').read_text()
        def split(source):
            body=source.index('void run_module_inits(void)');start=source.rfind('\n}\n',0,body)+3
            if start<3:raise AssertionError('original load_modules boundary missing')
            left=source.index('{',body);level=1;right=left+1
            while level:
                level+=(source[right]=='{')-(source[right]=='}');right+=1
            return source[:start],source[body:right],source[right:]
        old_prefix,_,old_suffix=split(original);prefix,body,suffix=split(actual)
        if prefix!=old_prefix or suffix!=old_suffix:raise AssertionError('constructor patch changed loading/relocation/force code')
        (work/'actual_run_module_inits.inc').write_text(body+'\n')
        cls.binary=work/'probe'
        cmd=['clang','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-format','-fsanitize=address,undefined','-fno-sanitize-recover=all',
             '-I',str(work/'dm/host'),'-I',str(work),str(EXP/'tests/whole_constructor_order_probe.c'),'-o',str(cls.binary)]
        run=subprocess.run(cmd,capture_output=True,text=True)
        if run.returncode:raise AssertionError(run.stdout+run.stderr)

    def case(self,mode):
        run=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_fixed_six_module_constructors_follow_reverse_loading_order(self):self.case('six')
    def test_sdk_constructor_initializes_dependency_before_main(self):self.case('dependency')
    def test_missing_dt_init_is_skipped_without_reordering_the_remaining_modules(self):self.case('holes')
    def test_empty_module_list_does_not_call_any_function(self):self.case('empty')
    def test_single_module_runs_once(self):self.case('single')
    def test_no_dt_init_never_calls_init_array_directly(self):self.case('none')
    def test_owned_init_is_the_only_array_owner_and_loader_metadata_is_unchanged(self):self.case('metadata')


if __name__=='__main__':unittest.main()
