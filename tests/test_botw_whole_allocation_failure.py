"""BotW title-local failure dispatch over the complete actual title source."""
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
COMPONENT = ROOT / 'experiments/botw_upstream_host'
PATCH = 'patches/base-v0-allocation-failure.patch'
BASELINE = os.environ.get('BOTW_WHOLE_ALLOCATION_FAILURE_BASELINE') == '1'
FAILURE = '[botw whole-host] account heap allocation failed\n'


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'),
                     'actual Darwin title section and policy ABI')
class WholeAllocationFailure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-allocation-failure-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(COMPONENT / 'upstream', cls.source)
        applied = []
        for name in json.loads((COMPONENT / 'SOURCE.lock.json').read_text())['patches']:
            if BASELINE and name == PATCH:
                continue
            cls.apply_patch(name)
            applied.append(name)
        if not BASELINE and PATCH not in applied and (COMPONENT / PATCH).exists():
            cls.apply_patch(PATCH)
        cls.title_sha256 = hashlib.sha256((cls.source / 'dm/host/title_botw.c').read_bytes()).hexdigest()
        cls.binary = cls.work / 'probe'
        command = ['xcrun', 'clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-pthread', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-include', str(COMPONENT / 'adapter/whole_policy.h'),
                   '-I', str(cls.source / 'dm/host'), '-I', str(COMPONENT / 'adapter'),
                   str(COMPONENT / 'tests/whole_allocation_failure_probe.c'),
                   str(COMPONENT / 'adapter/whole_policy.c'), '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def apply_patch(cls, name):
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(COMPONENT / name)],
                                cwd=cls.source, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(name + '\n' + result.stdout + result.stderr)

    def case(self, mode, status=0, stderr=''):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, stderr)

    def test_allocation_failure_with_null_argument_is_closed_literal_exit70(self):
        self.case('failure-null', 70, FAILURE)

    def test_allocation_failure_does_not_read_inaccessible_owned_argument(self):
        self.case('failure-guard', 70, FAILURE)

    def test_allocation_failure_does_not_modify_readonly_owned_argument(self):
        self.case('failure-readonly', 70, FAILURE)

    def test_horizon_numbers_return_zero_preserving_errno_and_owned_bytes(self):
        self.case('horizon')

    def test_foreign_host_negative_and_large_numbers_do_not_touch_arguments(self):
        self.case('other-host-calls')

    def test_other_actual_title_hooks_identity_and_force_metadata_are_preserved(self):
        self.case('descriptor')


if __name__ == '__main__':
    unittest.main()
