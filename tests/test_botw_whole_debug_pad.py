"""Complete host HID plus the actual native DebugPad producer on owned buffers.

No Guest instructions, UIKit, hardware controller or GPU is executed.
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
PATCH = 'patches/base-v0-debug-pad.patch'
BASELINE = os.environ.get('BOTW_WHOLE_DEBUG_PAD_BASELINE') == '1'


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('clang'), 'real Darwin static registry')
class WholeDebugPad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-debug-pad-')
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
                   '-I', str(ROOT), '-I', str(ROOT / 'native/botw_input'),
                   '-I', str(ROOT / 'native/botw_host'),
                   '-I', str(COMPONENT / 'adapter'), '-I', str(cls.source),
                   '-I', str(cls.source / 'dm/host'), '-I', str(cls.work),
                   str(COMPONENT / 'tests/whole_debug_pad_probe.c'),
                   str(ROOT / 'native/botw_input/input.c'), '-o', str(cls.binary)]
        if BASELINE:
            command += ['-DBOTW_WHOLE_DEBUG_PAD_BASELINE=1']
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_two_debug_pad_entries_are_registered(self):
        self.case('registry')

    def test_native_empty_and_invalid_states_do_not_write_output_or_backing(self):
        self.case('native-boundaries')

    def test_debug_only_ticks_preserve_npad_bytes_and_getter_does_not_advance(self):
        self.case('native-isolation')

    def test_real_initialization_is_idempotent_and_only_tick_advances_state(self):
        self.case('lifecycle')

    def test_preinit_and_null_output_are_real_typed_failures(self):
        for mode in ('preinit', 'null-out'):
            self.case(mode)

    def test_actual_checked_prepare_failure_preserves_output_and_current_sample(self):
        self.case('prepare-failed')

    def test_concurrent_ticks_and_reads_preserve_original_npad_source(self):
        self.case('concurrent')

if __name__ == '__main__':
    unittest.main()
