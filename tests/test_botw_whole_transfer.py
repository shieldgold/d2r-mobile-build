"""Normal owned CPU VM and actual single-table TransferMemory integration."""
from pathlib import Path
import os
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import test_botw_whole_pool_backing as source_fixtures

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_WHOLE_TRANSFER_BASELINE') == '1'


@unittest.skipUnless(sys.platform == 'darwin', 'requires actual Darwin shared vm_remap and permissions')
class WholeTransfer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-whole-transfer-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        shutil.copytree(EXP / 'upstream/dm/host', work / 'dm/host')
        for name in ['base-v0-heap.patch', 'current-thread-ownership.patch', 'kernel-services.patch', 'ipc-services.patch'] + ([] if BASELINE else ['transfer-memory.patch']):
            patch = EXP / 'patches' / name
            if name == 'transfer-memory.patch' and os.environ.get('BOTW_WHOLE_TRANSFER_SYNC_BASELINE') == '1':
                patch = ROOT / 'outputs/botw-whole-transfer-work/pre-sync-memory.patch'
            result = subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(patch)], cwd=work, capture_output=True, text=True)
            if result.returncode: raise AssertionError(result.stdout + result.stderr)
        old = (ROOT / 'native/botw_host/kernel.c').read_text()
        (work / 'original_alias.inc').write_text(source_fixtures.function(old, 'static int transfer_alias('))
        if not BASELINE:
            text = (work / 'dm/host/hle_thread.c').read_text()
            a = text.index('#define R_TIMEOUT'); b = text.index('static thr_t *cur(void)', a)
            registry = text[a:b]
            a = text.index('static void wait_unlink('); b = text.index('// Suspend until woken', a)
            waits = text[a:b]
            a = text.index('static int sig_obj('); b = text.index('static int64_t now_mono_ns(', a)
            consumers = text[a:b]
            a = text.index('// ---------- Checked native service kernel bridge ----------')
            (work / 'original_kernel_registry.inc').write_text(registry + waits + consumers + text[a:])
            (work / 'original_cur.inc').write_text(source_fixtures.function(text, 'static thr_t *cur('))
            a = text.index('static inline uint32_t ld32('); b = text.index('// ---------- Threads ----------', a)
            (work / 'original_atomics.inc').write_text(text[a:b])
            a = text.index('    case 0x1A:'); b = text.index('    case 0x45:', a)
            words = text[a:b]
            a = text.index('    case 0x18:'); b = text.index('    case 0x19:', a)
            (work / 'original_word_cases.inc').write_text(words + text[a:b])
            svc = (work / 'dm/host/hle_svc.c').read_text()
            a = svc.index('#define MAX_MAP '); b = svc.index('int svc_thread(', a)
            (work / 'original_memory_registry.inc').write_text(svc[a:b])
            a = svc.index('    case 0x01:'); b = svc.index('    case 0x2C:', a)
            (work / 'original_memory_cases.inc').write_text(svc[a:b])
        cls.binary = work / 'probe'
        cmd = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-function', '-Wno-unused-variable', '-Wno-format', '-pthread',
               '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
               '-I', work / 'dm/host', '-I', EXP / 'adapter', '-I', work,
               '-include', EXP / 'tests/whole_transfer_vm_hooks.h',
               EXP / 'tests/whole_transfer_probe.c']
        if BASELINE: cmd += ['-DWHOLE_TRANSFER_BASELINE']
        else: cmd += [EXP / 'adapter/whole_transfer_vm.c', EXP / 'adapter/base_v0_heap.c']
        cmd += ['-o', cls.binary]
        result = subprocess.run([str(p) for p in cmd], capture_output=True, text=True)
        if result.returncode: raise AssertionError(result.stdout + result.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('WHOLE_TRANSFER_CPU_OK', result.stdout)

    def test_actual_alias_cleanup_failure_preserves_mapping_owner(self): self.case('alias-cleanup')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_shared_alias_is_not_a_snapshot_and_last_lease_restores_source(self): self.case('shared')
    @unittest.skipIf(BASELINE, 'baseline has no source-permission overlay')
    def test_exact_owner_permissions_and_query_boundaries(self): self.case('permissions')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_actual_unmapped_source_restore_failure_retains_last_lease(self): self.case('last-restore')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_actual_alias_unmap_failure_retains_last_lease_until_retry(self): self.case('last-unmap')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_exhausted_original_handle_table_rolls_back_real_alias_and_permissions(self): self.case('capacity-rollback')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_failed_capacity_rollback_keeps_only_existing_map_quarantine(self): self.case('capacity-quarantine')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_actual_owner_protection_failure_rolls_back_before_publication(self): self.case('create-protect')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_failed_owner_protection_and_cleanup_preserve_quarantine_until_ack(self): self.case('create-quarantine')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_duplicate_and_numeric_aba_do_not_retarget_the_strong_lease(self): self.case('duplicate-aba')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_parallel_close_and_acquire_use_one_original_G_lock(self): self.case('parallel')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_real_host_page_boundaries_committed_extent_and_failed_allocations(self): self.case('invalid')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_heap_shrink_and_original_map_memory_respect_the_same_borrow_metadata(self): self.case('shrink-map')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_original_wait_word_borrow_blocks_create_and_svc_close_retires(self): self.case('synchronization')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_original_atomic_svc_rejects_new_noaccess_source_before_raw_read(self): self.case('sync-noaccess')
    @unittest.skipIf(BASELINE, 'baseline has no typed TransferMemory provider')
    def test_failed_svc_last_close_keeps_handle_and_registers_until_actual_ack(self): self.case('svc-close-failure')
    @unittest.skipIf(BASELINE, 'baseline has no port provenance')
    def test_complete_original_shared_vm_leaf_and_static_source_provenance(self):
        profile = json.loads((EXP / 'adapter/whole_transfer.json').read_text())
        for path, expected in profile['reused_sources'].items():
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), expected)
        original = source_fixtures.function((ROOT / 'native/botw_host/kernel.c').read_text(), 'static int transfer_alias(')
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(), profile['original_alias_body_sha256'])
        expected = original.replace('static int transfer_alias(uint64_t source, size_t size, uint64_t destination,\n                          void **backing, char *error, size_t capacity)',
                                    'int botw_whole_transfer_vm_alias(uint64_t source,size_t size,uint64_t destination,\n                                void **backing,char *error,size_t capacity)')
        expected = expected.replace('    if ((destination && target != destination)',
                                    '    /* Retain every successful VM mapping until the actual unmap ACK. */\n    *backing = (void *)(uintptr_t)target;\n    if ((destination && target != destination)', 1)
        expected = expected.replace('        if (!destination) munmap((void *)(uintptr_t)target, size);',
                                    '''        if (!destination) {
            if (munmap((void *)(uintptr_t)target, size))
                return diagnostic(error, capacity, "transfer alias cleanup failed: %s",strerror(errno));
            *backing = NULL;
        }''')
        expected = expected.replace('    *backing = (void *)(uintptr_t)target; return 0;', '    return 0;')
        actual = source_fixtures.function((EXP / 'adapter/whole_transfer_vm.c').read_text(), 'int botw_whole_transfer_vm_alias(')
        self.assertEqual(actual, expected)


if __name__ == '__main__': unittest.main()
