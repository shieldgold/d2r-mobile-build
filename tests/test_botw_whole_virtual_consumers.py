"""Actual sole pool catalog, original vpool/texown and complete shader snapshot."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
spec=importlib.util.spec_from_file_location('virtual_consumer_setup',ROOT/'tests/test_botw_whole_virtual_pools.py')
VIRTUAL=importlib.util.module_from_spec(spec);spec.loader.exec_module(VIRTUAL)
BACKING=VIRTUAL.BACKING
BASELINE=os.environ.get('BOTW_WHOLE_VIRTUAL_CONSUMERS_BASELINE')=='1'

@unittest.skipUnless(sys.platform=='darwin','requires actual Darwin VM and owned Objective-C protocol objects')
class WholeVirtualConsumers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        VIRTUAL.WholeVirtualPools.setUpClass.__func__(cls)
        if BASELINE:
            # Parent may already apply this patch through SOURCE or its pending
            # ordered closure. Always restore the actual complete old shaders.
            patch=EXP/'patches/base-v0-vpool-consumers.patch'
            current=(cls.source/'dm/gfx/nvn/botw_shader.c').read_text()
            if 'int botw_shader_pool_can_remap(' in current:
                reversed_patch=subprocess.run(['patch','--fuzz=0','-R','-p1','-i',str(patch)],cwd=cls.source,capture_output=True,text=True)
                if reversed_patch.returncode:raise AssertionError(reversed_patch.stdout+reversed_patch.stderr)
            assert 'int botw_shader_pool_can_remap(' not in (cls.source/'dm/gfx/nvn/botw_shader.c').read_text()
        probe=(EXP/'tests/whole_pool_backing_probe.m').read_text()
        for marker in ('static int botw_shader_pool_add(', 'static void botw_shader_pool_remove(',
                       'static void botw_shader_unsupported(', 'void nvn_texown_retire('):
            block=BACKING.function(probe,marker);assert probe.count(block)==1;probe=probe.replace(block,'',1)
        (cls.work/'owned_consumer_protocols.inc').write_text(probe)
        mtl=(cls.source/'dm/gfx/nvn/nvn_mtl.m').read_text()
        if not BASELINE:
            markers=('static const uint8_t *pool_texture_canonical(', 'static void pool_texture_cpu_authority(',
                     'static int pool_virtual_authority_span(',
                     'static void pool_direct_cpu_only(',
                     'int botw_whole_pool_map_preflight(', 'void botw_whole_pool_map_committed(')
            (cls.work/'pool_consumer_guard.inc').write_text('\n'.join(BACKING.function(mtl,m) for m in markers))
        if not BASELINE:
            assert '{ pool_direct_cpu_only((uintptr_t)val,4);' in BACKING.function(mtl,'void mtl_sub_event(')
            assert '{ pool_direct_cpu_only(addr,16);' in BACKING.function(mtl,'void mtl_sub_counter(')
            texture=BACKING.function(mtl,'static id<MTLTexture> tex_mtl(')
            assert texture.index('if (have) return have;')<texture.index('if (shared) return shared;')<texture.index('pool_texture_cpu_authority(t,memory);',texture.index('if (shared) return shared;'))
            assert '    pool_texture_cpu_authority(t,memory);' in BACKING.function(mtl,'static id<MTLTexture> pkg_upload_(const tex_t *t, uint64_t *stamp) {')
            readback=BACKING.function(mtl,'void mtl_read_texels(')
            assert 'if (t->has_layout && memory) {\n        pool_texture_cpu_authority(t,memory);' in readback
        # Execute the exact production REPORT_COUNTER branch with the actual
        # queueing and visibility-snapshot algorithms; no GPU is constructed.
        definitions=mtl[mtl.index('typedef struct { int kind; volatile uint32_t *val;'):mtl.index('// Target ranges of buffer writes')]
        declarations='\n'.join(line for line in definitions.splitlines() if line.startswith(('typedef struct { int kind;', 'typedef struct { void *buf;', 'typedef struct { int n;')))
        declarations+='\nstatic post_op *g_post;static int g_npost,g_postcap,g_vis_cur=19;static uint64_t g_cnt_report[32];\nstatic vis_slot *g_vs;static int g_nvs;\n'
        declarations+=BACKING.function(mtl,'static vis_list *vis_snapshot(')+'\n'+BACKING.function(mtl,'static void post(')
        (cls.work/'owned_counter_declarations.inc').write_text(declarations)
        replay=BACKING.function(mtl,'void mtl_exec(')
        first=replay.index('            case OP_REPORT_COUNTER:')
        case=replay[first:replay.index('            case OP_RESET_COUNTER:',first)]
        complete=BACKING.function(mtl,'uint64_t mtl_sub_commit(')
        begin=complete.index('else if (ops[i].kind == 3)')
        block=BACKING.function(complete[begin:],'else if (ops[i].kind == 3)')
        block=block[block.index('{'):]
        support=BACKING.function(mtl,'void mtl_sub_counter(')+'\n'
        support+='static void owned_replay_report(const void *a){const void *p=NULL;switch(OP_REPORT_COUNTER){\n'+case+'default:abort();}}\n'
        support+='static uint64_t gpu_ticks(void){return 711;}\nstatic void owned_complete_samples(post_op *ops){int i=0;'+block+'}\n'
        (cls.work/'owned_counter_replay.inc').write_text(support)
        command=['xcrun','clang','-std=gnu11','-fobjc-arc','-fobjc-arc-exceptions','-fblocks','-O1','-g',
            '-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-format','-Wno-deprecated-declarations','-pthread',
            '-fsanitize=address,undefined','-fno-sanitize-recover=all','-include',str(EXP/'adapter/whole_policy.h'),
            '-I',str(cls.source/'dm/gfx/nvn'),'-I',str(cls.source/'dm/gfx/nvn/include'),'-I',str(cls.source/'dm/host'),
            '-I',str(EXP/'adapter'),'-I',str(cls.work),str(EXP/'tests/whole_virtual_consumers_probe.m'),
            str(cls.source/'dm/gfx/nvn/nvn_vpool.c'),str(cls.source/'dm/gfx/nvn/nvn_texown.c'),
            str(cls.source/'dm/gfx/nvn/botw_shader.c'),str(EXP/'adapter/whole_pool_backing_memory.m'),
            str(EXP/'adapter/whole_pool_backing_gate.c'),str(EXP/'adapter/whole_policy.c'),
            '-framework','Foundation','-framework','Metal','-o',str(cls.binary)]
        if BASELINE:command.insert(2,'-DWHOLE_VPOOL_CONSUMER_BASELINE=1')
        built=subprocess.run(command,capture_output=True,text=True)
        if built.returncode:raise AssertionError(built.stdout+built.stderr)
    def case(self,mode):
        run=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('WHOLE_VIRTUAL_CONSUMERS_OWNED_CPU_OK',run.stdout)
    def test_complete_shader_snapshot_reads_canonical_virtual_backing(self):self.case('shader')
    def test_hash_dedup_still_marks_each_source_owner_code_authority(self):self.case('shader-dedup')
    def test_late_private_alias_cannot_publish_shader_snapshot_from_stale_cpu_bytes(self):self.case('private-read')
    def test_virtual_cpu_source_rejects_even_own_private_claim_but_canonical_cache_identity_remains_valid(self):self.case('cpu-authority')
    def test_virtual_cpu_source_keeps_retired_claims_non_authoritative(self):self.case('cpu-retired')
    def test_ordinary_cpu_source_preserves_original_private_claim_policy(self):self.case('cpu-ordinary')
    def test_live_private_authority_rejects_remap_without_changing_old_state(self):self.case('live')
    def test_retired_private_authority_is_not_reset_or_forgotten(self):self.case('retired')
    def test_incoming_physical_range_retired_authority_rejects_initial_mapping(self):self.case('incoming')
    def test_partial_hole_does_not_hide_older_mapped_page_authority(self):self.case('partial-hole')
    def test_texture_uses_exact_contiguous_canonical_physical_span(self):self.case('canonical')
    def test_virtual_event_counter_source_is_rejected_before_post_or_raw_pointer_use(self):self.case('direct-virtual')
    def test_samples_counter_virtual_destination_rejects_before_snapshot_post_or_visibility_change(self):self.case('samples-virtual')
    def test_samples_counter_ordinary_record_keeps_original_post_snapshot_and_completion(self):self.case('samples-ordinary')
    def test_samples_counter_physical_cpu_pointer_rejects_before_snapshot_or_post(self):self.case('samples-physical')
    def test_existing_direct_cpu_source_does_not_gain_virtual_fallback_semantics(self):self.case('direct-ordinary')
    def test_fragmented_physical_identity_is_rejected_before_cpu_texture_use(self):self.case('fragment')
    def test_unmapped_virtual_shader_code_cannot_fall_back_to_raw_copy(self):self.case('hole')
    def test_tombstoned_shader_code_cannot_fall_back_or_publish_output(self):self.case('stale')
    def test_successful_remap_only_retires_nonauthoritative_cached_reference(self):self.case('nonauthority-cache')

if __name__=='__main__':unittest.main()
