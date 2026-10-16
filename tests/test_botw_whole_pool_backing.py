"""Complete ordinary backing transactions; owned CPU Metal protocol stand-ins."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_WHOLE_POOL_BACKING_BASELINE') == '1'


def function(source, start):
    a = source.index(start)
    b = source.index('{', a)
    while ';' in source[a:b]:
        a = source.index(start, source.index(';', a) + 1)
        b = source.index('{', a)
    depth, end = 1, b + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[a:end]


@unittest.skipUnless(sys.platform == 'darwin', 'requires real Darwin VM and Objective-C')
class WholePoolBacking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-whole-pool-backing-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(EXP / 'upstream/dm', cls.source / 'dm')
        patches = ['checked-dirty-write.patch', 'base-v0-pool-builder.patch']
        if not BASELINE:
            patches += ['ordinary-pool-backing.patch']
        for name in patches:
            subprocess.run(['patch', '-p1', '-i', str(EXP / 'patches' / name)], cwd=cls.source,
                           check=True, capture_output=True, text=True)
        hle = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        mtl = (cls.source / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        names = ('Initialize', 'Finalize', 'Map', 'GetSize', 'GetFlags', 'GetBufferAddress',
                 'FlushMappedRange', 'InvalidateMappedRange')
        entries = [function(hle, 'NVN(nvnMemoryPool' + name + ',') for name in names]
        if BASELINE:
            registry = mtl[mtl.index('typedef struct { uintptr_t lo, hi;'):mtl.index('static uint64_t g_pool_adds;')]
            registry += '\nstatic uint64_t g_pool_adds,g_cb_commits,g_pool_res_at;\n'
            registry += '\n'.join(function(mtl, prefix) for prefix in
                                  ('static void pool_res(', 'void mtl_pool_add(', 'void mtl_pool_del('))
            registry += '\nstatic void defer_obj_release(id obj){(void)obj;}\n'
            registry = 'static void defer_obj_release(id obj);\n' + registry
            gate = hle[hle.index('static pthread_mutex_t g_susp_mu ='):hle.index('// Declaration state of one real-time thread:')]
        else:
            registry = mtl[mtl.index('// WHOLE_POOL_BACKING_TYPES_BEGIN'):mtl.index('// WHOLE_POOL_BACKING_TYPES_END')]
            registry += '\nstatic uint64_t g_pool_adds,g_cb_commits,g_pool_res_at;\n'
            registry += function(mtl, 'static void pool_res(') + '\n'
            registry += '#include "whole_pool_backing_registry.inc"\n'
            gate = hle[hle.index('// WHOLE_POOL_BACKING_GATE_BEGIN'):hle.index('// WHOLE_POOL_BACKING_GATE_END')]
            entries.insert(0, hle[hle.index('// WHOLE_POOL_BACKING_ENTRIES_BEGIN'):hle.index('// WHOLE_POOL_BACKING_ENTRIES_END')])
        (cls.work / 'pool_registry.inc').write_text(registry)
        (cls.work / 'pool_gate.inc').write_text(gate)
        (cls.work / 'pool_drains.inc').write_text('' if BASELINE else function(mtl, 'void mtl_pool_drain('))
        (cls.work / 'pool_residency.inc').write_text(function(mtl, 'static void res_add(') + '\n' + function(mtl, 'static void res_remove('))
        references = ''
        if not BASELINE:
            a = mtl.index('@interface NvDeferredRelease')
            b = mtl.index('static pthread_mutex_t g_bf_want_mu', a)
            references = mtl[a:b]
            references += '\nstatic nvn_hmap g_by_serial={.klen=8};\nstatic pthread_mutex_t g_smu=PTHREAD_MUTEX_INITIALIZER;\nstatic _Atomic uint64_t g_ser_gen=1;\n'
            a = mtl.index('typedef struct { uint64_t pool;')
            b = mtl.index('static void serial_drop_ref(', a)
            references += mtl[a:b]
            references += '\nstatic char pool_owner_key;\n'
            a = mtl.index('@interface NvPkg')
            b = mtl.index('@end', mtl.index('@implementation NvPkg', a)) + len('@end')
            references += mtl[a:b] + '\n'
            references += function(mtl, 'static __unsafe_unretained id hm_get_id(').replace('__unsafe_unretained ', '') + '\n'
            references += function(mtl, 'static void serial_drop_ref(') + '\n'
            references += function(mtl, 'static void serial_put(') + '\n'
            if 'static id pool_owned_resource(' in mtl:
                references += function(mtl, 'static id pool_owned_resource(') + '\n'
            if 'static void pool_tag_owned_object(' in mtl:
                references += function(mtl, 'static void pool_tag_owned_object(') + '\n'
            references += function(mtl, 'static void serial_put_pool(') + '\n'
            references += function(mtl, 'static void hm_put_id(') + '\n'
            # hm_put_id must precede the copied alias_insert body.
            helper = function(mtl, 'static void hm_put_id(')
            references = helper + '\n' + references.replace(helper + '\n', '')
            a = mtl.index('typedef struct { tex_t t;')
            b = mtl.index('\n', mtl.index('static treg_t *g_treg;', a))
            references += mtl[a:b] + '\n'
            references += 'static pthread_mutex_t g_rmu=PTHREAD_MUTEX_INITIALIZER;\n'
            references += function(mtl, 'static void tex_cached_views_drop(') + '\n'
            references += function(mtl, 'static void treg_drop_pool_views(') + '\n'
            references += function(mtl, 'void mtl_alias_drop_pool(') + '\n'
            references += 'static id<MTLTexture> g_bloomq_view,g_bloomq_base;\n'
            references += 'static pthread_mutex_t g_pool_present_cache_mu=PTHREAD_MUTEX_INITIALIZER;\n'
            references += 'static id<MTLTexture> g_pool_present_sources[4],g_pool_present_views[4];static uint64_t g_pool_present_used[4];\n'
            references += function(mtl, 'static void pool_retire_cached_views(') + '\n'
            references += function(mtl, 'static void pool_retire_references(').replace(
                'static void pool_retire_references(', 'static void actual_pool_retire_references(', 1)
        (cls.work / 'pool_references.inc').write_text(references)
        (cls.work / 'pool_entries.inc').write_text('\n'.join(entries))
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                   '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                   '-Wno-unused-variable', '-Wno-format', '-pthread',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I', str(cls.source / 'dm/gfx/nvn'), '-I', str(cls.source / 'dm/gfx/nvn/include'),
                   '-I', str(EXP / 'adapter'), '-I', str(cls.work),
                   str(EXP / 'tests/whole_pool_backing_probe.m'), '-framework', 'Foundation', '-framework', 'Metal',
                   '-o', str(cls.binary)]
        if BASELINE:
            command += ['-DWHOLE_POOL_BASELINE']
        else:
            command += [str(EXP / 'adapter/whole_pool_backing_memory.m'),
                        str(EXP / 'adapter/whole_pool_backing_gate.c')]
        build = subprocess.run(command, capture_output=True, text=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def case(self, mode):
        run = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('WHOLE_POOL_BACKING_CPU_OK', run.stdout)

    def test_readonly_metadata_defers_real_nocopy_until_consumer(self): self.case('readonly-deferred')
    def test_failed_factory_rolls_back_object_shader_dirty_and_registry(self): self.case('factory-rollback')
    def test_failed_checked_publication_rolls_back_actual_borrowed_resource(self): self.case('publish-rollback')
    def test_existing_capture_and_cpu_queue_drain_precede_mutation(self): self.case('mutation-barrier')
    def test_last_frame_waits_for_actual_late_deallocator_ack(self): self.case('late-ack')
    def test_unacked_last_frame_keeps_object_and_registry_quarantined(self): self.case('quarantine')
    def test_app_suspension_reason_is_independent_of_pool_mutation(self): self.case('suspend')
    def test_reader_cannot_upgrade_and_wait_for_itself(self): self.case('upgrade')
    def test_copied_cached_backing_flush_and_invalidate_transfer_real_bytes(self): self.case('copied-cache')
    def test_deferred_backing_requires_real_rw_leaf_at_consumption(self): self.case('deferred-consumer')
    def test_present_cpu_borrow_precedes_gpu_inflight_and_blocks_retirement(self): self.case('present-cpu-borrow')
    @unittest.skipIf(BASELINE, 'baseline has no per-cache owner metadata for this retirement fixture')
    def test_original_alias_serial_descriptor_view_and_deferred_refs_retire_after_drain(self): self.case('all-references')
    def test_overlapping_pool_rejects_before_touching_existing_dirty_registration(self): self.case('overlap')
    def test_real_adjacent_vm_leaves_use_cached_copy_or_refuse_uncached(self): self.case('split-owned')
    def test_owned_metadata_and_flags_reject_invalid_backing_before_registration(self): self.case('invalid-input')
    def test_real_device_capacity_failure_publishes_nothing(self): self.case('max-cap')
    def test_checked_dirty_failure_precedes_any_metal_factory(self): self.case('dirty-failure')
    def test_actual_catalog_allocation_failure_rolls_back_without_publishing(self): self.case('catalog-failure')
    def test_factory_throw_with_actual_external_borrow_keeps_unpublished_anchor(self): self.case('factory-throws')
    @unittest.skipIf(BASELINE, 'baseline has no pool birth serial contract')
    def test_same_address_reinitialized_pool_rejects_old_texture_birth_serial(self): self.case('texture-birth')
    def test_cached_invalidation_reports_real_readonly_write_failure(self): self.case('readonly-invalidate')
    @unittest.skipIf(BASELINE, 'baseline has no per-cache owner metadata for this wrapper fixture')
    def test_packaged_wrapper_tags_and_retires_its_actual_texture_view_caches(self): self.case('wrapper-references')

    @unittest.skipIf(BASELINE, 'baseline has no port profile')
    def test_complete_original_geometry_and_current_source_provenance(self):
        profile = json.loads((EXP / 'adapter/whole_pool_backing.json').read_text())
        for name, row in profile['reused_sources'].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), row['sha256'])
        original = (ROOT / 'native/botw_graphics/memory_pool_family.inc').read_text()
        port = (EXP / 'adapter/whole_pool_backing_memory.m').read_text()
        for name, prefix in [('poolVMRegions', 'static NSArray<'), ('poolNoCopyGeometry', 'static BOOL ')]:
            if name == 'poolVMRegions':
                a = original.index('static NSArray<BWPoolSegment*> *poolVMRegions(')
                b = port.index('static NSArray<BotwWholePoolSegment*> *poolVMRegions(')
                source = function(original[a:], 'static NSArray<')
                actual = function(port[b:], 'static NSArray<')
            else:
                source = function(original, prefix + name + '(')
                actual = function(port, prefix + name + '(')
                actual = actual.replace('int(*memoryValidator)(const void*,size_t),int(*memoryAccessValidator)(const void*,size_t,int),', '', 1)
            actual = actual.replace('BotwWholePoolSegment', 'BWPoolSegment').replace(
                'BotwWholePoolNoCopyAdmission', 'BWPoolNoCopyAdmission').replace(
                'BOTW_WHOLE_POOL_MAX_NOCOPY_SEGMENTS', 'BW_POOL_MAX_NOCOPY_SEGMENTS')
            self.assertEqual(actual, source)
        hle = (self.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        for name in profile['capture_helpers']:
            start = next(start for start in ('static const tex_t *', 'static void ', 'static NVNboolean ') if start + name + '(' in hle)
            self.assertIn('POOL_CAPTURE();', function(hle, start + name + '('))
        for name in profile['capture_entries']:
            self.assertIn('POOL_CAPTURE();', function(hle, 'NVN(' + name + ','))
        for name in profile['mutations']:
            self.assertIn('POOL_MUTATION();', function(hle, 'NVN(nvn' + name + ','))


if __name__ == '__main__': unittest.main()
