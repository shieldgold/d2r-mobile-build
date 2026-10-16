"""Actual retained owned mmap and original SVC metadata, without guest execution."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('BOTW_WHOLE_ADDRESS_SPACE_BASELINE')=='1'


def function(source,marker):
    a=source.index(marker);b=source.index('{',a);depth=1;i=b+1
    while depth:
        if source[i]=='{':depth+=1
        elif source[i]=='}':depth-=1
        i+=1
    return source[a:i]


@unittest.skipUnless(sys.platform=='darwin','uses the actual Darwin original QueryMemory stack metadata')
class WholeAddressSpace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='botw-whole-as-')
        cls.addClassCleanup(cls.temp.cleanup);work=Path(cls.temp.name)
        shutil.copytree(EXP/'upstream/dm/host',work/'dm/host')
        names=['base-v0-heap.patch']+([] if BASELINE else ['bounded-address-space.patch'])
        for name in names:
            result=subprocess.run(['patch','--fuzz=0','-p1','-i',str(EXP/'patches'/name)],cwd=work,capture_output=True,text=True)
            if result.returncode:raise AssertionError(result.stdout+result.stderr)
        source=(work/'dm/host/hle_svc.c').read_text()
        a=source.index('static uint8_t *g_as;');b=source.index('// Application memory pool',a)
        prefix=source[a:b]
        if BASELINE:prefix='#define RESERVE (5ull << 30)\n'+prefix
        (work/'memory_init.inc').write_text(prefix)
        a=b;b=source.index('/* Normal allocator capacities',a)
        (work/'memory_pool.inc').write_text(source[a:b])
        (work/'memory_range.inc').write_text(function(source,'void svc_guest_range('))
        a=source.index('enum { I_CoreMask');b=source.index('int svc_thread(',a)
        (work/'memory_registry.inc').write_text(source[a:b])
        cases='\n'.join(function(source,marker) for marker in ['case 0x06:', 'case 0x29:'])
        (work/'memory_cases.inc').write_text(cases)
        (work/'memory_plan.inc').write_text(function(source,'int dm_guest_heap_plan_inputs('))
        planner=(EXP/'adapter/base_v0_heap.c').read_text()
        a=planner.index('#define HEAP_POLICY_UNIT');b=planner.index('/* Provider state',a)
        (work/'original_heap_plan.inc').write_text(planner[a:b])
        cls.binary=work/'probe'
        command=['clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-format','-Wno-unused-function','-Wno-unused-variable','-pthread',
                 '-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(EXP/'adapter'),'-I',str(work),str(EXP/'tests/whole_address_space_probe.c'),'-o',str(cls.binary)]
        if BASELINE:command.insert(1,'-DWHOLE_AS_BASELINE=1')
        built=subprocess.run(command,capture_output=True,text=True)
        if built.returncode:raise AssertionError(built.stdout+built.stderr)

    def case(self,mode):
        result=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('WHOLE_ADDRESS_SPACE_CPU_OK',result.stdout)

    def test_failed_original_five_gib_uses_actual_two_gib_and_honest_svc_metadata(self):self.case('fallback')
    def test_five_gib_success_preserves_original_layout_and_configured_pool(self):self.case('original')
    def test_mac_policy_does_not_fallback_and_failure_preserves_output(self):self.case('mac-failure')
    def test_all_candidates_fail_without_publishing_an_address_or_size(self):self.case('all-failure')
    def test_invalid_arguments_never_call_the_allocator(self):self.case('invalid')
    def test_terminal_initialization_failure_has_no_published_partial_globals(self):self.case('init-failure')
    def test_same_mapping_leaf_retains_real_small_protected_owned_range(self):self.case('small')
    def test_parallel_initialization_publishes_exactly_one_owned_reservation(self):self.case('parallel')
    def test_uninitialized_range_does_not_claim_any_address_space(self):self.case('uninitialized')


if __name__=='__main__':unittest.main()
