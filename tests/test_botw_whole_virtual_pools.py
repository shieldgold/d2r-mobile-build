"""Complete locked HLE, sole upstream vpool and owned Metal CPU protocols."""
from pathlib import Path
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
spec=importlib.util.spec_from_file_location('whole_virtual_backing_fixture',ROOT/'tests/test_botw_whole_pool_backing.py')
BACKING=importlib.util.module_from_spec(spec);spec.loader.exec_module(BACKING)


@unittest.skipUnless(sys.platform=='darwin','real Darwin VM and Objective-C protocols')
class WholeVirtualPools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if BACKING.BASELINE:raise AssertionError('requires the complete backing fixture')
        # Reuse its protocol objects and reference-retirement support. Its old
        # three-patch SUT is replaced below with the complete ordered closure.
        BACKING.WholePoolBacking.setUpClass.__func__(cls)
        original_mtl=(cls.source/'dm/gfx/nvn/nvn_mtl.m').read_text()
        shutil.rmtree(cls.source/'dm');shutil.copytree(EXP/'upstream/dm',cls.source/'dm')
        lock=json.loads((EXP/'SOURCE.lock.json').read_bytes())
        cls.applied_patches=list(lock['patches'])
        for name in ('patches/base-v0-vpool-strict-token.patch','patches/virtual-pool-backing.patch',
                     'patches/virtual-pool-entrypoints.patch','patches/base-v0-vpool-consumers.patch',
                     'patches/virtual-pool-cpu-boundaries.patch'):
            if name not in cls.applied_patches and (EXP/name).is_file():cls.applied_patches.append(name)
        for name in cls.applied_patches:
            p=subprocess.run(['patch','--fuzz=0','-p1','-i',str(EXP/name)],cwd=cls.source,capture_output=True,text=True)
            if p.returncode:raise AssertionError(name+'\n'+p.stdout+p.stderr)
        hle=(cls.source/'dm/gfx/nvn/hle_nvn.c').read_text()
        mtl=(cls.source/'dm/gfx/nvn/nvn_mtl.m').read_text()
        # Reused reference support must still be the production implementation.
        for marker in ('static void serial_put(', 'void mtl_alias_drop_pool(', 'static void pool_retire_references('):
            if BACKING.function(original_mtl,marker)!=BACKING.function(mtl,marker):
                raise AssertionError('reference fixture requires regeneration: '+marker)
        registry=mtl[mtl.index('// WHOLE_POOL_BACKING_TYPES_BEGIN'):mtl.index('// WHOLE_POOL_BACKING_TYPES_END')]
        registry+='\nstatic uint64_t g_pool_adds,g_cb_commits,g_pool_res_at;\n'
        registry+=BACKING.function(mtl,'static void pool_res(')+'\n#include "whole_pool_backing_registry.inc"\n'
        (cls.work/'pool_registry.inc').write_text(registry)
        (cls.work/'pool_gate.inc').write_text(hle[hle.index('// WHOLE_POOL_BACKING_GATE_BEGIN'):hle.index('// WHOLE_POOL_BACKING_GATE_END')])
        (cls.work/'pool_drains.inc').write_text(BACKING.function(mtl,'void mtl_pool_drain('))
        (cls.work/'pool_residency.inc').write_text('\n'.join(BACKING.function(mtl,x) for x in ('static void res_add(','static void res_remove(')))
        entries=hle[hle.index('// WHOLE_POOL_BACKING_ENTRIES_BEGIN'):hle.index('// WHOLE_POOL_BACKING_ENTRIES_END')]
        entries+='\n'+BACKING.function(hle,'static NVNboolean botw_nvn_memory_pool_initialize_impl(')
        for name in ('Initialize','Finalize','Map','GetSize','GetFlags','GetBufferAddress','FlushMappedRange','InvalidateMappedRange'):
            entries+='\n'+BACKING.function(hle,'NVN(nvnMemoryPool'+name+',')
        if 'NVN(nvnMemoryPoolMapVirtual,' in hle:
            entries+='\n'+BACKING.function(hle,'NVN(nvnMemoryPoolMapVirtual,')
            entries='#define WHOLE_VIRTUAL_MAP_AVAILABLE 1\n'+entries
        (cls.work/'pool_entries.inc').write_text(entries)
        (cls.work/'owned_backing_protocols.inc').write_text((EXP/'tests/whole_pool_backing_probe.m').read_text())
        command=['xcrun','clang','-std=gnu11','-fobjc-arc','-fobjc-arc-exceptions','-fblocks','-O1','-g',
                 '-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-format','-pthread',
                 '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                 '-I',str(cls.source/'dm/gfx/nvn'),'-I',str(cls.source/'dm/gfx/nvn/include'),
                 '-I',str(EXP/'adapter'),'-I',str(cls.work),
                 str(EXP/'tests/whole_virtual_pools_probe.m'),str(cls.source/'dm/gfx/nvn/nvn_vpool.c'),
                 str(EXP/'adapter/whole_pool_backing_memory.m'),str(EXP/'adapter/whole_pool_backing_gate.c'),
                 str(EXP/'adapter/whole_policy.c'),'-framework','Foundation','-framework','Metal','-o',str(cls.binary)]
        p=subprocess.run(command,capture_output=True,text=True)
        if p.returncode:raise AssertionError(p.stdout+p.stderr)

    def case(self,mode):
        p=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('WHOLE_VIRTUAL_POOLS_CPU_OK',p.stdout)

    def test_real_physical_pool_initialization_uses_the_complete_backing_path(self):self.case('physical')
    def test_real_virtual_pool_initialization_publishes_a_logical_owned_interval(self):self.case('virtual')
    def test_zero_count_never_reads_the_array_or_advances_mapping_and_callback_state(self):self.case('zero')
    def test_real_batch_resolves_contiguous_current_physical_bytes(self):self.case('contiguous')
    def test_fragmented_batch_gathers_fresh_bytes_but_refuses_one_buffer_alias(self):self.case('fragmented')
    def test_invalid_last_request_preserves_the_whole_previous_mapping(self):self.case('atomic')
    def test_owned_preflight_rejection_preserves_mapping_without_claiming_private_guard_coverage(self):self.case('preflight-denied')
    def test_other_device_and_invalid_pool_kinds_never_publish(self):self.case('invalid-inputs')
    def test_unmap_holes_and_finalized_virtual_tokens_never_fall_back_to_physical_lookup(self):self.case('unmap-stale')
    def test_physical_finalize_invalidates_mappings_and_owner_reuse_requires_a_new_map(self):self.case('physical-finalize')
    def test_physical_finalize_waits_for_the_actual_owned_buffer_deallocator(self):self.case('physical-ack')
    def test_unrelated_physical_finalize_does_not_consume_another_mapping_guard(self):self.case('unrelated-finalize')


if __name__=='__main__':unittest.main()
