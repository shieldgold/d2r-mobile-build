"""Original Eden pool code on actual bounded host memory; owned CPU only."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

COMPONENT = Path(__file__).resolve().parents[1]


def checked(command, **kwargs):
    result = subprocess.run([str(part) for part in command], text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise AssertionError(f'{command!r}\n{result.stdout}{result.stderr}')
    return result


class DependencyIdentityTests(unittest.TestCase):
    def test_pinned_fmt_and_eden_license_original_bytes(self):
        lock = json.loads((COMPONENT / 'DEPENDENCIES.lock.json').read_text())
        fmt = lock['fmt']
        self.assertEqual(fmt['commit'], '407c905e45ad75fc29bf0f9bb7c5c2fd3475976f')
        for entry in fmt['files']:
            data = (COMPONENT / 'vendor/fmt' / entry['path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'], entry['path'])
        for entry in lock['eden_additional_original_blobs']:
            data = (COMPONENT / entry['destination']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'], entry['path'])
            self.assertEqual(hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest(), entry['git_blob'])
        original_cpm = json.loads((COMPONENT / 'provenance/eden-cpmfile.json').read_text())
        self.assertEqual(original_cpm['fmt']['version'], fmt['tag'])
        self.assertEqual(original_cpm['fmt']['hash'], fmt['sha512'])


@unittest.skipUnless(platform.system() == 'Darwin', 'actual macOS native toolchain required')
class RendererPortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-renderer-ports-')
        cls.work = Path(cls.scratch.name)
        cls.source = cls.work / 'source'
        checked([sys.executable, COMPONENT / 'tools/prepare_eden.py', cls.source,
                 '--patch', 'pool-memory.patch'])
        base = ['xcrun', 'clang++', '-std=c++23', '-O1', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', '-DFMT_HEADER_ONLY=1', '-pthread',
                '-I', COMPONENT / 'platform', '-I', COMPONENT / 'native',
                '-I', COMPONENT / 'vendor/fmt/include', '-I', cls.source / 'src']
        if os.environ.get('BOTW_AUDIO_RENDERER_SANITIZERS') == '1':
            base += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        cls.binaries = {}
        for port in ('renderer_host', 'renderer_event', 'pool_mapper', 'renderer_pause', 'renderer_failure'):
            binary = cls.work / port
            sources = ([COMPONENT / f'native/{port}.cpp'] if port not in ('pool_mapper', 'renderer_pause', 'renderer_failure') else
                       [COMPONENT / 'native/renderer_host.cpp'] if port in ('renderer_pause', 'renderer_failure') else
                       [COMPONENT / 'native/renderer_memory.cpp', COMPONENT / 'native/common_ports.cpp',
                        cls.source / 'src/audio_core/renderer/memory/pool_mapper.cpp',
                        cls.source / 'src/audio_core/renderer/memory/memory_pool_info.cpp'])
            checked(base + [COMPONENT / 'platform/platform.cpp',
                            COMPONENT / f'tests/{port}_probe.cpp', *sources, '-o', binary])
            cls.binaries[port] = binary

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def test_actual_host_clock_paused_wait_resume_and_failure_wakeup(self):
        checked([self.binaries['renderer_host']], timeout=20)

    def test_pause_ack_cancels_current_round_waits_for_exit_and_resumes_new_epoch(self):
        checked([self.binaries['renderer_pause']], timeout=20)

    def test_first_worker_fatal_calls_actual_failure_producer_once_and_wakes_host(self):
        checked([self.binaries['renderer_failure']], timeout=20)

    def test_required_real_kernel_event_producer_and_close_rejection(self):
        checked([self.binaries['renderer_event']], timeout=20)

    def test_original_pool_update_full_pin_map_translate_busy_detach_and_unmap(self):
        checked([self.binaries['pool_mapper']], timeout=20)


if __name__ == '__main__':
    unittest.main()
