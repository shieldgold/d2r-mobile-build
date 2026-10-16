"""Owned applet state and notification contracts; never execute guest code."""
from pathlib import Path
import os
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'experiments/botw_upstream_host'


class WholeAppletTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-whole-applet-cpu-')
        cls.binary = Path(cls.scratch.name) / 'applet'
        command = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread',
            '-I', str(EXPERIMENT / 'adapter'), str(EXPERIMENT / 'adapter/whole_applet.c'),
            str(EXPERIMENT / 'tests/whole_applet_probe.c'), '-o', str(cls.binary)]
        if os.environ.get('BOTW_WHOLE_APPLET_SANITIZERS') == '1':
            command += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def check_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_measured_state_is_required_and_initial_focus_is_queued_once(self):
        self.check_case('initial')

    def test_focus_and_subscribed_modes_do_not_invent_execution_resume(self):
        self.check_case('notifications')

    def test_explicit_unsupported_suspension_fails_without_changing_state(self):
        self.check_case('unsupported')

    def test_resume_requires_an_execution_handler_accepted_suspend_edge(self):
        self.check_case('execution')

    def test_full_queue_rejects_state_transition_transactionally(self):
        self.check_case('overflow')


@unittest.skipUnless(platform.system() == 'Darwin', 'typed HLE entries use Mach-O sections')
class WholeAppletTypedEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-whole-applet-entries-')
        work = Path(cls.scratch.name)
        source = work / 'source'
        host = source / 'dm/host'
        host.mkdir(parents=True)
        for name in ('botw_oe.c', 'hle_sdkinit.c', 'hle.h', 'dm.h'):
            shutil.copyfile(EXPERIMENT / 'upstream/dm/host' / name, host / name)
        if os.environ.get('BOTW_WHOLE_APPLET_BASELINE') != '1':
            result = subprocess.run(['patch', '-p1', '-i', str(EXPERIMENT / 'patches/applet-lifecycle.patch')],
                cwd=source, text=True, capture_output=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        cls.binary = work / 'entries'
        command = ['xcrun', 'clang', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-pthread',
            '-I', str(host), '-I', str(EXPERIMENT / 'adapter'),
            str(EXPERIMENT / 'adapter/whole_applet.c'),
            str(EXPERIMENT / 'tests/whole_applet_sdk_probe.c'), '-o', str(cls.binary)]
        if os.environ.get('BOTW_WHOLE_APPLET_SANITIZERS') == '1':
            command += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def test_actual_typed_consumers_share_the_measured_producer_and_queue(self):
        result = subprocess.run([str(self.binary), 'consumers'], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sdk_environment_initialization_rejects_unconfigured_lifecycle(self):
        result = subprocess.run([str(self.binary), 'init-unconfigured'], text=True, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('measured lifecycle', result.stderr)


if __name__ == '__main__':
    unittest.main()
