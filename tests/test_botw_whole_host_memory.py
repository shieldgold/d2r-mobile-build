"""Owned CPU original module/thread metadata and normal IPC TLR, no Guest."""
from pathlib import Path
import os
import platform
import subprocess
import unittest
import test_botw_whole_transfer as source_fixtures

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('BOTW_WHOLE_HOST_MEMORY_BASELINE')=='1'


@unittest.skipUnless(platform.system()=='Darwin','actual original Darwin VM and pthread bounds required')
class WholeHostMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_fixtures.WholeTransfer.setUpClass()
        cls.addClassCleanup(source_fixtures.WholeTransfer.doClassCleanups)
        work=source_fixtures.WholeTransfer.binary.parent
        if not BASELINE:
            result=subprocess.run(['patch','--fuzz=0','-p1','-i',str(EXP/'patches/normal-thread-tlr.patch')],cwd=work,capture_output=True,text=True)
            if result.returncode:raise AssertionError(result.stdout+result.stderr)
        cls.binary=work/'host-memory'
        cmd=['clang','-std=gnu11','-O1','-g','-pthread','-Wall','-Wextra','-Werror',
             '-Wno-unused-function','-Wno-unused-variable','-Wno-format',
             '-fsanitize=address,undefined','-fno-sanitize-recover=all',
             '-I',str(work/'dm/host'),'-I',str(EXP/'adapter'),'-I',str(EXP/'tests'),'-I',str(work),
             '-include',str(EXP/'tests/whole_transfer_vm_hooks.h'),str(EXP/'tests/whole_host_memory_probe.c'),
             str(EXP/'adapter/whole_transfer_vm.c'),str(EXP/'adapter/base_v0_heap.c'),str(EXP/'adapter/whole_policy.c')]
        if BASELINE:cmd+=['-DWHOLE_HOST_MEMORY_BASELINE=1']
        else:cmd += [str(EXP/'adapter/whole_host_memory.c'),str(EXP/'adapter/whole_guest_memory.c')]
        result=subprocess.run(cmd+['-o',str(cls.binary)],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)

    def case(self,mode):
        run=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=20,
            env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=0'})
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_logical_readonly_text_patch_and_key_reject_writes_before_prepare(self):self.case('readonly')
    def test_whole_contiguous_module_is_readable(self):self.case('module-read')
    def test_exact_original_data_start_allows_normal_protected_write(self):self.case('module-data')
    def test_module_partial_overlap_zero_overflow_and_boundary_crossing_fail_closed(self):self.case('module-bounds')
    def test_malformed_loader_metadata_is_not_an_unrelated_range(self):self.case('module-layout')
    def test_overlapping_original_module_records_fail_closed(self):self.case('module-overlap')
    def test_metadata_does_not_override_actual_vm_noaccess_hole(self):self.case('module-vm')
    def test_unrelated_host_allocation_is_not_owned(self):self.case('unknown')
    def test_current_pthread_stack_bounds_and_partial_overlap(self):self.case('stack')
    def test_real_private_tls_and_tlr_allocation_bounds(self):self.case('tls')
    def test_different_thread_cannot_borrow_parent_stack_or_private_tsd(self):self.case('thread')
    def test_normal_tlr_is_exactly_512_bytes_and_not_elf_tls(self):self.case('tlr')
    def test_unready_thread_does_not_even_fetch_pthread_key_zero(self):self.case('unready')
    def test_rewritten_tlr_does_not_invent_an_8192_byte_owner(self):self.case('tlr-rewrite')
    def test_rewritten_tlr_needs_full_512_bytes_inside_original_ownership(self):self.case('tlr-bounds')
    def test_tlr_requires_actual_read_and_write_vm_access(self):self.case('tlr-vm')
    def test_tlr_prepare_failure_does_not_publish_outputs(self):self.case('tlr-prepare')
    def test_kernel_known_denial_precedes_module_and_thread_fallback(self):self.case('kernel-denial')


if __name__=='__main__':unittest.main()
