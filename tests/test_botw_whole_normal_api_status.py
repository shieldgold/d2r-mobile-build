"""Fixed normal API results and host-owned plan metadata; owned CPU only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import test_botw_whole_transfer as transfer_fixtures

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('BOTW_NORMAL_API_STATUS_BASELINE')=='1'
NAMES=('SetHeapSize','SetMemoryPermission','SetMemoryAttribute','MapMemory','UnmapMemory','QueryMemory','CreateThread','StartThread',
       'CreateTransferMemory','CloseHandle','WaitSynchronization','GetInfo','ConnectToNamedPort','SendSyncRequest',
       'NvnMemoryPoolInitialize')
CAPACITY='[botw whole-host] heap_plan heap_bytes=671088640 root_bytes=637534208\n'


def build(work,kind,extra=()):
    common=['clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-pthread',
            '-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',EXP/'adapter',
            '-I',EXP/'tests','-I',work,'-I',work/'dm/host']
    policy=work/(kind+'-policy.o')
    command=common+['-Dwrite=owned_status_write','-c',EXP/'adapter/whole_policy.c','-o',policy]
    result=subprocess.run(list(map(str,command)),capture_output=True,text=True)
    if result.returncode:raise AssertionError(result.stdout+result.stderr)
    command=common+list(extra)
    if BASELINE:command+=['-DBOTW_NORMAL_API_STATUS_BASELINE=1']
    command+=[EXP/'tests/whole_normal_api_status_probe.c',policy,'-o',work/kind]
    result=subprocess.run(list(map(str,command)),capture_output=True,text=True)
    if result.returncode:raise AssertionError(result.stdout+result.stderr)
    return work/kind


class OwnedRunner:
    def run_owned(self,binary,mode):
        result=subprocess.run([str(binary),mode],capture_output=True,text=True,timeout=15,
            env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=0'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(result.stdout,'')
        self.assertNotRegex(result.stderr,r'0x|x[0-9]+=|pc=|lr=|stack|secret-canary')
        return result.stderr


class NormalApiStatus(OwnedRunner,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='botw-normal-status-');cls.addClassCleanup(cls.temp.cleanup)
        work=Path(cls.temp.name)
        cls.logger=build(work,'logger')
        cls.heap=build(work,'heap',['-DOWNED_STATUS_HEAP=1'])

    def test_fixed_enum_and_bounded_outcomes_preserve_errno(self):
        text=self.run_owned(self.logger,'enum')
        self.assertEqual(text,''.join('[botw whole-host] api='+name+' result='+outcome+'\n'
            for name in NAMES for outcome in ('ok','error')))

    def test_appended_nvn_boolean_api_preserves_all_fourteen_original_enum_values(self):
        self.assertEqual(self.run_owned(self.logger,'appended-api'),
            '[botw whole-host] api=NvnMemoryPoolInitialize result=ok\n'
            '[botw whole-host] api=NvnMemoryPoolInitialize result=error\n')

    def test_invalid_api_or_outcome_never_logs(self):self.assertEqual(self.run_owned(self.logger,'invalid'),'')
    def test_failed_write_and_duplicate_preserve_errno(self):self.assertEqual(self.run_owned(self.logger,'write-failure'),'')
    def test_parallel_duplicate_outcomes_emit_only_two_literal_lines(self):
        self.assertCountEqual(self.run_owned(self.logger,'race').splitlines(),
            ['[botw whole-host] api=MapMemory result=ok','[botw whole-host] api=MapMemory result=error'])

    def test_failed_query_then_retry_logs_one_successful_host_plan(self):self.assertEqual(self.run_owned(self.heap,'heap-query-retry'),CAPACITY)
    def test_real_failed_second_key_then_retry_preserves_commit_log(self):self.assertEqual(self.run_owned(self.heap,'heap-key-retry'),CAPACITY)
    def test_parallel_initializers_log_only_one_immutable_host_plan(self):self.assertEqual(self.run_owned(self.heap,'heap-parallel'),CAPACITY)


@unittest.skipUnless(sys.platform=='darwin','actual owned MapMemory uses Darwin shared VM leaf')
class NormalApiStatusMap(OwnedRunner,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the complete existing owned registry/VM fixture, never mirror
        # MapMemory's algorithm or create another live registry.
        class PrivateTransfer(transfer_fixtures.WholeTransfer):pass
        PrivateTransfer.setUpClass();cls.addClassCleanup(PrivateTransfer.doClassCleanups)
        work=PrivateTransfer.binary.parent
        original=(work/'dm/host/hle_svc.c').read_text()
        if not BASELINE:
            result=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(EXP/'patches/normal-api-status.patch')],
                cwd=work,capture_output=True,text=True)
            if result.returncode:raise AssertionError(result.stdout+result.stderr)
            patched=(work/'dm/host/hle_svc.c').read_text()
            old=original[original.index('uint64_t svc_dispatch('):]
            impl=patched[patched.index('static uint64_t svc_dispatch_impl('):patched.index('\n/* The original dispatcher owns')]
            expected=old.replace('uint64_t svc_dispatch(','static uint64_t svc_dispatch_impl(',1)
            if impl.rstrip()!=expected.rstrip():raise AssertionError('original dispatcher implementation changed')
            (work/'actual_status_wrapper.inc').write_text(patched[patched.index('\nuint64_t svc_dispatch('):])
        cls.map=build(work,'map',['-DOWNED_STATUS_MAP=1','-Wno-unused-function','-Wno-unused-variable','-Wno-format',
            '-include',EXP/'tests/whole_transfer_vm_hooks.h',EXP/'adapter/whole_transfer_vm.c',EXP/'adapter/base_v0_heap.c'])

    # Only these two map tests belong here; generic/heap contracts have their
    # own portable fixture and must not be discovered twice.
    def test_actual_map_failure_returns_zero_but_logs_error_and_success_unchanged(self):
        self.assertEqual(self.run_owned(self.map,'map'),CAPACITY+
            '[botw whole-host] api=MapMemory result=error\n[botw whole-host] api=MapMemory result=ok\n'+
            '[botw whole-host] api=UnmapMemory result=ok\n')

    def test_unknown_counter_exit_sleep_and_debug_are_not_metadata_results(self):
        self.assertEqual(self.run_owned(self.map,'excluded'),CAPACITY)

if __name__=='__main__':unittest.main()
