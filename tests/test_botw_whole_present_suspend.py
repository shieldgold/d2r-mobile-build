"""Actual XQ/present/lifecycle bodies on owned queues; no Guest, drawable or GPU."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from test_botw_whole_queue_acquire import definition

ROOT = Path(__file__).resolve().parents[1]
PART = ROOT / 'experiments/botw_upstream_host'
PATCH = 'patches/ordinary-present-suspend-cancellation.patch'
BASELINE = os.environ.get('BOTW_WHOLE_PRESENT_SUSPEND_BASELINE') == '1'


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('clang'), 'owned Darwin queue bodies')
class WholePresentSuspend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-owned-present-suspend-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(PART / 'upstream', cls.source)
        for name in json.loads((PART / 'SOURCE.lock.json').read_text())['patches']:
            if name != PATCH:
                cls.apply(name)
        cls.original_hle = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        cls.original_mtl = (cls.source / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        if not BASELINE and (PART / PATCH).exists():
            cls.apply(PATCH)
        hle = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        mtl = (cls.source / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        cls.hle, cls.mtl = hle, mtl
        keys = ['static void present_drop_for_suspend(void) {', 'static void *present_main(',
                'static void present_start(', 'uint64_t mtl_present(', 'void mtl_present_discard(']
        prefix = ''
        if 'static uint64_t present_cancel_queued_locked(' in mtl:
            prefix = definition(mtl, 'static uint64_t present_cancel_queued_locked(') + '\n'
        (cls.work / 'actual_present_queue.inc').write_text(prefix + '\n'.join(definition(mtl, k) for k in keys))
        start = mtl.index('@implementation NvMetalDisplayLinkDelegate')
        end = mtl.index('@end', start) + len('@end')
        (cls.work / 'actual_link_delegate.inc').write_text(mtl[start:end] + '\n')
        # The original completion body is driven explicitly by an owned CPU
        # action. No successful Metal/GPU operation is fabricated by the seam.
        line = next(line.strip() for line in mtl.splitlines()
                    if 'if (ticket > g_pq_done)' in line and 'g_pq_inflight--' in line)
        (cls.work / 'actual_present_completion.inc').write_text(line + '\n')
        a = hle.index('enum { XJ_SUBMIT')
        b = hle.index('static void x_submit(', a)
        keys = ['static void x_run(', 'static void *x_thread(', 'static void x_start(',
                'static uint64_t x_push(', 'static void x_wait(', 'static void x_drain(void) {']
        (cls.work / 'actual_xq.inc').write_text(hle[a:b] + '\n' + '\n'.join(definition(hle, k) for k in keys))
        (cls.work / 'actual_suspend.inc').write_text(definition(hle, 'void dm_gfx_suspend(') + '\n')
        cls.binary = cls.work / 'probe'
        command = ['clang', '-fobjc-arc', '-fblocks', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra',
                   '-Werror', '-Wno-unused-function', '-Wno-unused-variable', '-pthread',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-framework', 'Foundation',
                   '-I', str(cls.work), '-I', str(PART / 'adapter'),
                   str(PART / 'tests/whole_present_suspend_probe.m'),
                   str(PART / 'adapter/whole_pool_backing_gate.c'), '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def apply(cls, name):
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PART / name)],
                                cwd=cls.source, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=10,
                                env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('WHOLE_PRESENT_SUSPEND_CPU_OK', result.stdout)

    def test_silent_full_present_queue_cancels_then_resumes_monotonic_tickets(self):
        for mode in ('queued-classic', 'queued-link'):
            with self.subTest(mode=mode): self.case(mode)

    def test_full_xq_reader_is_released_before_suspend_waits_for_admission(self):
        for mode in ('xq-reader-classic', 'xq-reader-link'):
            with self.subTest(mode=mode): self.case(mode)

    def test_late_enqueue_rechecks_discard_after_the_initial_fast_check(self):
        for mode in ('late-classic', 'late-link'):
            with self.subTest(mode=mode): self.case(mode)

    def test_claimed_cpu_borrow_is_never_acknowledged_before_it_releases(self):
        for mode in ('cpu-classic', 'cpu-link'):
            with self.subTest(mode=mode): self.case(mode)

    def test_committed_gpu_completion_is_not_fabricated_for_either_present_mode(self):
        for mode in ('gpu-classic', 'gpu-link'):
            with self.subTest(mode=mode): self.case(mode)

    def test_empty_suspend_and_resume_require_no_present_worker(self): self.case('empty')

    def test_xq_and_encoder_completion_algorithm_remain_byte_identical(self):
        for key in ('static void x_run(', 'static void *x_thread(', 'static void x_start(',
                    'static uint64_t x_push(', 'static void x_wait(', 'static void x_drain(void) {'):
            self.assertEqual(definition(self.hle, key), definition(self.original_hle, key))
        self.assertEqual(definition(self.mtl, 'static void *present_main('),
                         definition(self.original_mtl, 'static void *present_main('))
        self.assertEqual(definition(self.mtl, 'static int present_now('),
                         definition(self.original_mtl, 'static int present_now('))


if __name__ == '__main__': unittest.main()
