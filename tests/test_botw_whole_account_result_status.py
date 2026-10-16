"""Result metadata from actual complete account API calls, never direct logger calls."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_upstream_host'
PATCH = 'patches/normal-account-result-status.patch'
BASELINE = os.environ.get('BOTW_WHOLE_ACCOUNT_RESULT_STATUS_BASELINE') == '1'
PREFIX = '[botw whole-host] api='
NAMES = ('AccountOpenPreselectedUser', 'AccountGetUserId', 'AccountListAllUsers')


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('clang'), 'real Darwin static registry')
class WholeAccountResultStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-account-status-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(COMPONENT / 'upstream', cls.source)
        patches = json.loads((COMPONENT / 'SOURCE.lock.json').read_text())['patches']
        applied = []
        for name in patches:
            if BASELINE and name == PATCH:
                continue
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(COMPONENT / name)],
                                    cwd=cls.source, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            applied.append(name)
        if not BASELINE and PATCH not in applied and (COMPONENT / PATCH).exists():
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(COMPONENT / PATCH)],
                                    cwd=cls.source, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        crash = (cls.source / 'dm/host/crash.c').read_text()
        begin = crash.index('#define MAX_FAULT_HOOK')
        end = crash.index('// The diagnostics module', begin)
        (cls.work / 'normal_registry.inc').write_text(crash[begin:end])
        ignore = cls.work / 'metadata-only.asan.ignore'
        ignore.write_text('global:hle_force_*\n')
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-format', '-Wno-unused-function', '-pthread', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-fsanitize-ignorelist=' + str(ignore),
                   '-I', str(cls.source), '-I', str(cls.source / 'dm/host'), '-I', str(cls.work),
                   '-I', str(COMPONENT / 'adapter'), '-I', str(COMPONENT / 'tests'),
                   str(COMPONENT / 'tests/whole_account_result_status_probe.c'),
                   str(COMPONENT / 'adapter/whole_policy.c'), '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, mode, expected):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Closed public metadata contract: discard the separate original
        # account-initialize ordinary log; retain no operand or identity data.
        lines = [line for line in result.stderr.splitlines() if line.startswith(PREFIX)]
        self.assertCountEqual(lines, [PREFIX + name + ' result=' + outcome for name, outcome in expected])

    def test_three_real_success_results_are_recorded_once(self):
        self.case('success', [(name, 'ok') for name in NAMES])

    def test_each_real_success_and_error_result_is_recorded_only_once(self):
        self.case('outcomes', [(name, outcome) for name in NAMES for outcome in ('ok', 'error')])

    def test_uninitialized_real_results_preserve_failure_and_original_errno(self):
        self.case('uninitialized', [(name, 'error') for name in NAMES])

    def test_actual_prewrite_rejection_never_invents_a_completed_result(self):
        self.case('prepare-open', [])
        self.case('prepare-get', [(NAMES[0], 'ok')])
        self.case('prepare-list', [])


if __name__ == '__main__':
    unittest.main()
