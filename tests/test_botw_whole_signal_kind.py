"""Actual signal dispatcher with owned siginfo/context, never a guest signal."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_upstream_host'
PATCH = 'patches/normal-signal-kind.patch'
BASELINE = os.environ.get('BOTW_WHOLE_SIGNAL_KIND_BASELINE') == '1'
GENERIC = '[botw whole-host] unhandled host signal\n'


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'actual Darwin signal ABI')
class WholeSignalKind(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-signal-kind-')
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
        # There is no invented new helper in the baseline: both versions call
        # the same actual on_crash from the complete staged crash translation unit.
        cls.crash_sha256 = hashlib.sha256((cls.source / 'dm/host/crash.c').read_bytes()).hexdigest()
        cls.binary = cls.work / 'probe'
        command = ['xcrun', 'clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-parameter',
                   '-Wno-format', '-pthread', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-include', str(COMPONENT / 'adapter/whole_policy.h'),
                   '-I', str(cls.source / 'dm/host'), '-I', str(COMPONENT / 'adapter'),
                   str(COMPONENT / 'tests/whole_signal_kind_probe.c'),
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

    def case(self, mode, code, line):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, line)

    def test_unhandled_mapping_fault_reports_only_fixed_classification(self):
        self.case('mapping', 128 + signal.SIGSEGV,
                  '[botw whole-host] unhandled host mapping fault\n')

    def test_unhandled_protection_fault_reports_only_fixed_classification(self):
        self.case('protection', 128 + signal.SIGSEGV,
                  '[botw whole-host] unhandled host protection fault\n')

    def test_unknown_codes_and_null_info_preserve_generic_failure(self):
        for mode in ('unknown-zero', 'unknown-positive', 'unknown-negative', 'null-info'):
            with self.subTest(mode=mode):
                self.case(mode, 128 + signal.SIGSEGV, GENERIC)

    def test_other_signal_kinds_preserve_generic_and_do_not_read_context(self):
        for mode, sig in [('bus-mapping', signal.SIGBUS),
                          ('ill-protection', signal.SIGILL), ('trap-mapping', signal.SIGTRAP)]:
            with self.subTest(mode=mode):
                self.case(mode, 128 + sig, GENERIC)

    def test_handled_dirty_fault_returns_after_one_hook_without_mutation_or_output(self):
        for mode in ('dirty-mapping', 'dirty-protection', 'dirty-bus'):
            with self.subTest(mode=mode):
                self.case(mode, 0, '')

    def test_legacy_signal_failure_entry_remains_compatible(self):
        self.case('legacy', 128 + signal.SIGSEGV, GENERIC)


if __name__ == '__main__':
    unittest.main()
