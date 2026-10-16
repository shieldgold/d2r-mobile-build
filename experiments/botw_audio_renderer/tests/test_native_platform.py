"""Owned CPU fixtures only: no guest, renderer execution, device, or GPU."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

COMPONENT = Path(__file__).resolve().parents[1]
WHOLE = COMPONENT.parent / 'botw_upstream_host'


def run(command, **kwargs):
    result = subprocess.run([str(part) for part in command], text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise AssertionError(f'{command!r}\n{result.stdout}{result.stderr}')
    return result


def sanitizers():
    return (['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
            if os.environ.get('BOTW_AUDIO_RENDERER_SANITIZERS') == '1' else [])


def compile_pcm(work, adapter_source):
    source = work / 'pcm-source'
    (source / 'dm/host').mkdir(parents=True)
    for name in ('botw_pcm.c', 'botw_pcm.h'):
        shutil.copyfile(WHOLE / 'upstream/dm/host' / name, source / 'dm/host' / name)
    run(['patch', '-p1', '-i', WHOLE / 'patches/ios-audio-output.patch'], cwd=source)
    objects = []
    base = ['xcrun', 'clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
            '-DBOTW_PCM_MOCK_AU', '-pthread', '-I', source / 'dm/host',
            '-I', WHOLE / 'adapter'] + sanitizers()
    for index, path in enumerate([source / 'dm/host/botw_pcm.c',
                                  WHOLE / 'adapter/whole_audio_platform.m',
                                  COMPONENT / 'tests/pcm_mock_au.c']):
        obj = work / f'pcm-{index}.o'
        run(base + ['-c', path, '-o', obj])
        objects.append(obj)
    binary = work / 'pcm-output'
    run(['xcrun', 'clang++', '-std=c++23', '-O1', '-Wall', '-Wextra', '-Werror', '-pthread',
         '-I', COMPONENT / 'platform', '-I', source / 'dm/host', '-I', WHOLE / 'adapter',
         COMPONENT / 'tests/pcm_output_probe.cpp', adapter_source, *objects,
         '-framework', 'AudioToolbox', '-o', binary] + sanitizers())
    return binary


class SourceIdentityTests(unittest.TestCase):
    def test_every_original_git_blob_and_complete_renderer_snapshot(self):
        lock = json.loads((COMPONENT / 'SOURCE.lock.json').read_text())
        self.assertEqual(lock['commit'], 'd8c6c59b679867d2595fdc0953df033475e23380')
        files = lock['files']
        self.assertEqual(len([f for f in files if f['path'].startswith('src/audio_core/renderer/')]), 149)
        self.assertEqual(len([f for f in files if f['path'].startswith('src/audio_core/adsp/apps/audio_renderer/')]), 5)
        self.assertEqual(len([f for f in files if f['path'].startswith('src/audio_core/common/')]), 5)
        expected = {f['path'] for f in files}
        actual = {str(p.relative_to(COMPONENT / 'vendor/eden'))
                  for p in (COMPONENT / 'vendor/eden').rglob('*') if p.is_file()}
        self.assertEqual(actual, expected)
        for entry in files:
            data = (COMPONENT / 'vendor/eden' / entry['path']).read_bytes()
            self.assertEqual(len(data), entry['bytes'], entry['path'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'], entry['path'])
            self.assertEqual(hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest(),
                             entry['git_blob'], entry['path'])


@unittest.skipUnless(platform.system() == 'Darwin', 'actual macOS native toolchain required')
class NativePlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-audio-platform-')
        cls.work = Path(cls.scratch.name)
        cls.binary = cls.work / 'platform'
        run(['xcrun', 'clang++', '-std=c++23', '-O1', '-Wall', '-Wextra', '-Werror', '-pthread',
             '-I', COMPONENT / 'platform', COMPONENT / 'tests/platform_probe.cpp',
             COMPONENT / 'platform/platform.cpp', '-o', cls.binary] + sanitizers())
        cls.pcm = compile_pcm(cls.work, COMPONENT / 'platform/pcm_output.cpp')

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def probe(self, case, pcm=False):
        run([self.pcm if pcm else self.binary, case], timeout=20)

    def test_memory_owner_bounds_permissions_and_unregister(self):
        self.probe('memory')

    def test_real_backing_lives_until_last_bounded_pin(self):
        self.probe('lifetime')

    def test_transfer_attach_borrows_and_busy_detach(self):
        self.probe('pool')

    def test_event_wait_signal_reset_timeout_and_closed_wakeup(self):
        self.probe('event')

    def test_monotonic_clock_and_rejected_diagnostic_modes(self):
        self.probe('clock')

    def test_pcm_submission_is_not_callback_consumption(self):
        self.probe('consumption', pcm=True)

    def test_pcm_existing_lifecycle_gate_and_new_sink_inheritance(self):
        self.probe('lifecycle', pcm=True)

    def test_pcm_device_and_stop_failures_propagate(self):
        self.probe('failure', pcm=True)


if __name__ == '__main__':
    unittest.main()
