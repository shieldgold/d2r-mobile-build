"""Owned CPU lifecycle fixtures; never load guest data or create a renderer."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / 'experiments/botw_upstream_host/frontend'
PROBE = ROOT / 'experiments/botw_upstream_host/tests/whole_front_state_probe.c'


class WholeFrontendStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-whole-front-state-')
        cls.binary = Path(cls.build.name) / 'probe'
        result = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
            '-pthread', '-I', str(FRONT), str(FRONT / 'whole_front_state.c'),
            str(PROBE), '-lm', '-o', str(cls.binary)], text=True, capture_output=True)
        if result.returncode:
            cls.build.cleanup()
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def check_case(self, case):
        result = subprocess.run([str(self.binary), case], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_runtime_surface_lifecycle_and_audio_are_required_before_one_start(self):
        self.check_case('start')

    def test_focus_loss_releases_owned_input_without_replaying_it_on_resume(self):
        self.check_case('input')

    def test_only_bound_vi_callback_ticks_while_active_and_is_retired_on_stop(self):
        self.check_case('vsync')

    def test_platform_failure_latches_closed_before_start_and_while_running(self):
        self.check_case('failure')

    def test_unconfigured_or_incomplete_runtime_never_passes_the_callback_contract(self):
        self.check_case('contract')

    def test_start_waits_through_focus_loss_and_resumes_without_reclaiming_start(self):
        self.check_case('wait-resume')

    def test_platform_failure_and_permanent_stop_cancel_waiting_workers(self):
        self.check_case('wait-cancel')


if __name__ == '__main__':
    unittest.main()
