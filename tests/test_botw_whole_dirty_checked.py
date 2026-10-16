"""Normal dirty/write failure paths on owned CPU mappings, never guest/GPU."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_WHOLE_DIRTY_BASELINE') == '1'


def function(source, start):
    a = source.index(start)
    b = source.index('{', a)
    depth = 1
    end = b + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[a:end]


@unittest.skipUnless(sys.platform == 'darwin', 'requires real Darwin Mach clock and owned mappings')
class WholeDirtyChecked(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-whole-dirty-cpu-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(EXP / 'upstream/dm', cls.source / 'dm')
        if not BASELINE:
            subprocess.run(['patch', '-p1', '-i', str(EXP / 'patches/checked-dirty-write.patch')],
                           cwd=cls.source, check=True, capture_output=True, text=True)
        crash = (cls.source / 'dm/host/crash.c').read_text()
        a = crash.index('#define MAX_FAULT_HOOK')
        b = crash.index('// The diagnostics module', a)
        (cls.work / 'normal_registry.inc').write_text(crash[a:b])
        shader = (cls.source / 'dm/gfx/nvn/botw_shader.c').read_text()
        a = shader.index('typedef struct shader_pool')
        b = shader.index('typedef struct shader_snapshot', a)
        registry = shader[a:b] + '\nstatic pthread_mutex_t shader_lock = PTHREAD_MUTEX_INITIALIZER;\nstatic shader_pool *pools;\n'
        registry += function(shader, 'static int fail(') + '\n'
        registry += function(shader, 'int botw_shader_pool_add(') + '\n'
        registry += function(shader, 'void botw_shader_pool_remove(') + '\n'
        (cls.work / 'shader_pool_registry.inc').write_text(registry)
        nvn = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        (cls.work / 'pool_initialize.inc').write_text(function(nvn, 'NVN(nvnMemoryPoolInitialize,'))
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-format', '-Wno-unused-function', '-pthread',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I', str(cls.source / 'dm/host'), '-I', str(cls.source / 'dm/gfx/nvn'),
                   '-I', str(cls.source / 'dm/gfx/nvn/include'), '-I', str(cls.work),
                   str(EXP / 'tests/whole_dirty_checked_probe.c'), '-o', str(cls.binary)]
        if not BASELINE:
            command += ['-DBOTW_WHOLE_DIRTY_CHECKED=1']
        build = subprocess.run(command, capture_output=True, text=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def case(self, mode):
        run = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_real_mprotect_failure_reaches_checked_prepare(self):
        self.case('prep-failure')

    @unittest.skipIf(BASELINE, 'requires checked registry port, but keeps its original BUSY fault branch')
    def test_busy_fault_rechecks_real_failed_permission_transition(self):
        self.case('busy-failure')

    @unittest.skipIf(BASELINE, 'requires checked registry port')
    def test_busy_fault_accepts_only_real_completed_unprotection(self):
        self.case('busy-success')

    def test_full_real_4096_slot_table_rejects_without_publication(self):
        self.case('capacity')

    def test_memory_pool_capacity_failure_rolls_back_shader_before_publication(self):
        self.case('pool-capacity')

    def test_first_calloc_failure_leaves_no_pool_or_dirty_publication(self):
        self.case('alloc-1')

    def test_second_calloc_failure_frees_private_range(self):
        self.case('alloc-2')

    def test_third_calloc_failure_preserves_shared_page_references(self):
        self.case('alloc-3')

    def test_invalid_range_does_not_claim_registration(self):
        self.case('invalid')

    @unittest.skipIf(BASELINE, 'baseline has only void callbacks')
    def test_registry_snapshot_allows_callback_registration_without_lock(self):
        self.case('snapshot')

    @unittest.skipIf(BASELINE, 'baseline has only void callbacks')
    def test_concurrent_registration_and_preparation_use_one_registry(self):
        self.case('concurrent')

    @unittest.skipIf(BASELINE, 'baseline has only void callbacks')
    def test_legacy_prepare_and_registration_fail_explicitly(self):
        self.case('legacy')

    @unittest.skipIf(BASELINE, 'baseline has only void callbacks')
    def test_original_signal_fault_and_overlapping_page_ownership_survive(self):
        self.case('fault-overlap')

    @unittest.skipIf(BASELINE, 'baseline has only void callbacks')
    def test_successful_pool_publishes_after_shader_and_dirty_registration(self):
        self.case('pool-success')


if __name__ == '__main__':
    unittest.main()
