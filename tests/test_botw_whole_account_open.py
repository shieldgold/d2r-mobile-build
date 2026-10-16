"""Actual complete host account module with a closed, owned identity seam.

No real UID/save is accessed and no Guest instructions or device code is run.
"""
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
PATCH = 'patches/base-v0-account-open.patch'
BASELINE = os.environ.get('BOTW_WHOLE_ACCOUNT_OPEN_BASELINE') == '1'


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('clang'), 'real Darwin static registry')
class WholeAccountOpen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-account-open-')
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
                   '-I', str(COMPONENT / 'adapter'), str(COMPONENT / 'tests/whole_account_open_probe.c'),
                   str(COMPONENT / 'adapter/whole_policy.c'), '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_result_typed_open_entry_is_registered(self):
        self.case('registry')

    def test_open_result_and_original_get_list_pop_share_one_real_handle_state(self):
        self.case('roundtrip')

    def test_original_fallback_handle_and_new_open_use_the_same_uid_and_token(self):
        self.case('pop-then-open')

    def test_uninitialized_and_null_output_fail_without_writes_or_open_state(self):
        for mode in ('uninitialized', 'null-out'):
            self.case(mode)

    def test_actual_checked_prepare_rejection_precedes_output_and_open_commit(self):
        self.case('prepare-failed')

    def test_24_byte_aligned_output_and_readonly_handle_consumer(self):
        self.case('alignment-readonly')

    def test_concurrent_original_producer_and_readonly_uid_resolution_are_coherent(self):
        self.case('concurrent')


if __name__ == '__main__':
    unittest.main()
