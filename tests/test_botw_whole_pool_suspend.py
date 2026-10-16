"""Suspend waits for the real dequeued-present CPU borrow before returning."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
import test_botw_whole_pool_backing as source_fixtures

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'


class WholePoolSuspend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-whole-pool-suspend-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        shutil.copytree(EXP / 'upstream/dm', work / 'dm')
        patches = ['checked-dirty-write.patch', 'base-v0-pool-builder.patch', 'ordinary-pool-backing.patch']
        if os.environ.get('BOTW_WHOLE_POOL_SUSPEND_BASELINE') != '1':
            patches.append('ordinary-pool-suspend.patch')
        for patch in patches:
            subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(EXP / 'patches' / patch)],
                           cwd=work, check=True, capture_output=True, text=True)
        mtl = (work / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        hle = (work / 'dm/gfx/nvn/hle_nvn.c').read_text()
        (work / 'suspend_drop.inc').write_text(source_fixtures.function(mtl, 'static void present_drop_for_suspend('))
        (work / 'suspend_gate.inc').write_text(hle[hle.index('// WHOLE_POOL_BACKING_GATE_BEGIN'):hle.index('// WHOLE_POOL_BACKING_GATE_END')])
        cls.binary = work / 'probe'
        build = subprocess.run(['clang', '-std=gnu11', '-pthread', '-O1', '-g', '-Wall', '-Wextra',
                                '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined',
                                '-fno-sanitize-recover=all', '-I', str(work), '-I', str(EXP / 'adapter'),
                                str(EXP / 'tests/whole_pool_suspend_probe.c'),
                                str(EXP / 'adapter/whole_pool_backing_gate.c'), '-o', str(cls.binary)],
                               capture_output=True, text=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('WHOLE_POOL_SUSPEND_CPU_OK', result.stdout)

    def test_dequeued_cpu_present_cannot_submit_after_suspend_returns(self): self.case('cpu')
    def test_real_inflight_completion_still_blocks_suspend(self): self.case('gpu')
    def test_unclaimed_queued_frames_drop_without_needing_displaylink(self): self.case('queued')


if __name__ == '__main__': unittest.main()
