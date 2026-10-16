"""The original present watchdog must retain a prior dequeued CPU borrow."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
import test_botw_whole_pool_backing as source_fixtures

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'


class WholePoolPresentWait(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-whole-pool-present-wait-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        shutil.copytree(EXP / 'upstream/dm', work / 'dm')
        patches = ['checked-dirty-write.patch', 'base-v0-pool-builder.patch',
                   'ordinary-pool-backing.patch', 'ordinary-pool-suspend.patch']
        if os.environ.get('BOTW_WHOLE_POOL_PRESENT_WAIT_BASELINE') != '1':
            patches.append('ordinary-pool-present-wait.patch')
        for patch in patches:
            subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(EXP / 'patches' / patch)],
                           cwd=work, check=True, capture_output=True, text=True)
        mtl = (work / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        (work / 'actual_present_wait.inc').write_text(source_fixtures.function(mtl, 'void mtl_present_wait('))
        cls.binary = work / 'probe'
        build = subprocess.run(['clang', '-std=gnu11', '-pthread', '-O1', '-g', '-Wall', '-Wextra',
                                '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                                '-I', str(work), str(EXP / 'tests/whole_pool_present_wait_probe.c'),
                                '-o', str(cls.binary)], capture_output=True, text=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('WHOLE_POOL_PRESENT_WAIT_CPU_OK', result.stdout)

    def test_watchdog_cannot_ack_an_earlier_dequeued_cpu_present(self): self.case('cpu')
    def test_actual_inflight_completion_still_blocks_the_watchdog_ack(self): self.case('gpu')
    def test_unclaimed_frames_still_drop_after_original_watchdog_timeout(self): self.case('queued')


if __name__ == '__main__': unittest.main()
