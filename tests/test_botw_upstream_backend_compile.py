"""Exercise the complete iPhoneOS build path with explicit fake compiler tools."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'experiments/botw_upstream_backend/scripts/build_compile_only.py'


class UpstreamBackendCompilePolicyTests(unittest.TestCase):
    def test_iphoneos_build_never_executes_a_compiled_program(self):
        spec = importlib.util.spec_from_file_location('backend_compile_policy', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_run = subprocess.run
        calls = []

        def tool(argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'xcrun':
                value = '/fake/clang' if '--find' in argv else '/fake/iPhoneOS.sdk'
                return subprocess.CompletedProcess(argv, 0, value + '\n', '')
            if argv[0] == '/usr/bin/patch':
                return real_run(argv, **kwargs)
            if argv[0] == '/usr/bin/nm':
                return subprocess.CompletedProcess(argv, 0, '', '')
            if argv[0] == '/fake/clang':
                self.assertIn('arm64-apple-ios18.0', argv)
                self.assertIn('/fake/iPhoneOS.sdk', argv)
                Path(argv[argv.index('-o') + 1]).write_bytes(b'explicit fake compiler output')
                return subprocess.CompletedProcess(argv, 0, '', '')
            self.fail('iPhoneOS build attempted to execute a program: ' + str(argv[0]))

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'build'
            argv = [str(SCRIPT), '--sdk', 'iphoneos', '--output', str(output)]
            with mock.patch.object(module.sys, 'argv', argv), \
                 mock.patch.object(module.subprocess, 'run', side_effect=tool), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    result = module.main()
                except SystemExit as error:
                    self.fail(f'valid iPhoneOS compile-only request rejected: {error.code}')
            self.assertEqual(result, 0)
            receipt = json.loads((output / 'final-receipt.json').read_text())
            self.assertTrue(receipt['ios_compiled'])
            self.assertFalse(receipt['backend_executed'])
            self.assertFalse(receipt['CPU_probes_executed'])
            self.assertFalse(receipt['GPU_or_guest_or_device_executed'])
            self.assertEqual(receipt['complete_translation_units'], 25)
            self.assertEqual(sum('-c' in command for command in calls), 27)
            self.assertFalse(any('CPU-probe' in str(command[0]) for command in calls))
