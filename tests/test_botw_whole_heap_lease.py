"""Strong normal heap aliases plus the actual two original registry locks."""
from pathlib import Path
import os
import hashlib
import json
import shutil
import subprocess
import sys
import unittest
import test_botw_whole_transfer as transfer_fixtures

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'


@unittest.skipUnless(sys.platform=='darwin','requires actual Darwin vm_remap and original dirty Mach clock')
class WholeHeapLease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class PrivateTransferStaging(transfer_fixtures.WholeTransfer): pass
        PrivateTransferStaging.setUpClass()
        cls.addClassCleanup(PrivateTransferStaging.doClassCleanups)
        work=PrivateTransferStaging.binary.parent
        shutil.copytree(EXP/'upstream/dm/gfx',work/'dm/gfx')
        baseline=os.environ.get('BOTW_WHOLE_HEAP_LEASE_BASELINE')=='1'
        patches=[EXP/'patches/checked-dirty-write.patch',
                 ROOT/'outputs/botw-whole-heap-lease-work/normal-heap-lease-baseline.patch' if baseline else EXP/'patches/normal-heap-lease.patch']
        for patch in patches:
            result=subprocess.run(['patch','--fuzz=0','-p1','-i',str(patch)],cwd=work,capture_output=True,text=True)
            if result.returncode:raise AssertionError(result.stdout+result.stderr)
        text=(work/'dm/host/hle_thread.c').read_text()
        a=text.index('#define R_TIMEOUT');b=text.index('static thr_t *cur(void)',a);registry=text[a:b]
        a=text.index('static void wait_unlink(');b=text.index('// Suspend until woken',a);waits=text[a:b]
        a=text.index('static int sig_obj(');b=text.index('static int64_t now_mono_ns(',a);consumers=text[a:b]
        a=text.index('// ---------- Checked native service kernel bridge ----------')
        (work/'original_kernel_registry.inc').write_text(registry+waits+consumers+text[a:])
        svc=(work/'dm/host/hle_svc.c').read_text();a=svc.index('#define MAX_MAP ');b=svc.index('int svc_thread(',a)
        (work/'original_memory_registry.inc').write_text(svc[a:b])
        crash=(work/'dm/host/crash.c').read_text();a=crash.index('#define MAX_FAULT_HOOK');b=crash.index('// The diagnostics module',a)
        (work/'normal_registry.inc').write_text(crash[a:b])
        cls.binary=work/'normal-probe'
        cmd=['clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-format','-pthread',
             '-fsanitize=address,undefined','-fno-sanitize-recover=all',
             '-I',work/'dm/host','-I',work/'dm/gfx/nvn','-I',EXP/'adapter','-I',EXP/'tests','-I',work,
             '-include',EXP/'tests/whole_transfer_vm_hooks.h',EXP/'tests/whole_heap_lease_probe.c',
             EXP/'adapter/whole_transfer_vm.c',EXP/'adapter/base_v0_heap.c',EXP/'adapter/whole_policy.c',EXP/'adapter/whole_heap_lease.c',
             EXP/'adapter/whole_heap_provider.c','-o',cls.binary]
        result=subprocess.run([str(v) for v in cmd],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)

    def case(self,mode):
        result=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('WHOLE_HEAP_LEASE_CPU_OK',result.stdout)

    def test_true_shared_partial_host_page_view_and_provenance(self):self.case('shared')
    def test_existing_normal_pin_rejects_later_actual_dirty_registration(self):self.case('normal-before-dirty')
    def test_existing_dirty_ro_range_rejects_alias_before_preparation(self):self.case('dirty-before-normal')
    def test_transfer_noaccess_is_rejected_before_any_checked_prep(self):self.case('noaccess')
    def test_untracked_actual_readonly_operand_is_never_made_writable(self):self.case('readonly')
    def test_actual_checked_prepare_rejection_precedes_alias_factory(self):self.case('checked-failure')
    def test_full_host_page_envelope_cannot_cross_a_logical_noaccess_hole(self):self.case('envelope-hole')
    def test_overlapping_normal_leases_share_actual_heap_provenance(self):self.case('overlap')
    def test_last_actual_unmap_failure_retains_alias_and_dirty_exclusion(self):self.case('last-failure')
    def test_partial_alias_cleanup_failure_retains_pending_original_map(self):self.case('quarantine')
    def test_unlocked_preparation_keeps_strong_owner_and_blocks_dirty_add(self):self.case('preparing')
    def test_actual_dirty_slot_publisher_excludes_concurrent_normal_acquire(self):self.case('dirty-publisher')
    def test_parallel_two_way_admission_can_never_publish_both(self):self.case('race')
    def test_shrink_map_and_transfer_create_all_observe_normal_pin(self):self.case('mutations')
    def test_invalid_or_failed_allocations_leave_outputs_and_catalog_unchanged(self):self.case('invalid')
    def test_typed_process_context_refs_status_and_wrong_owner_release(self):self.case('provider')
    def test_provider_destroy_waits_for_actual_pending_cleanup_ack(self):self.case('provider-pending')
    def test_frozen_source_and_canonical_provider_provenance(self):
        profile=json.loads((EXP/'adapter/whole_heap_lease.json').read_text())
        for path,expected in profile['reused_sources'].items():
            self.assertEqual(hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),expected)
        patch=(EXP/'patches/normal-heap-lease.patch').read_text()
        self.assertNotIn('nvn_dirty_protect_checked(',patch)
        self.assertNotIn('nvn_dirty_since(',patch)


if __name__=='__main__':unittest.main()
