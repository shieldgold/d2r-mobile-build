"""Patched production PCM with owned mock AudioUnits; no hardware or guest use."""
from pathlib import Path
import os
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'experiments/botw_upstream_host'


@unittest.skipUnless(platform.system() == 'Darwin', 'requires actual macOS AudioToolbox SDK')
class WholeAudioPlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-whole-audio-cpu-')
        cls.work = Path(cls.scratch.name)
        cls.source = cls.work / 'source'
        source = EXPERIMENT / 'upstream'
        (cls.source / 'dm/host/tests').mkdir(parents=True)
        for name in ('botw_pcm.c', 'botw_pcm.h', 'tests/t_botw_pcm.c'):
            shutil.copyfile(source / 'dm/host' / name, cls.source / 'dm/host' / name)
        for patch in ('ios-audio-output.patch', 'audio-write-observed.patch',
                      'pcm-stress-frontier.patch'):
            patched = subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(EXPERIMENT / 'patches' / patch)],
                cwd=cls.source, text=True, capture_output=True)
            if patched.returncode:
                raise AssertionError(patched.stdout + patched.stderr)
        cls.binaries = {}
        base = ['xcrun', 'clang', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-DBOTW_PCM_MOCK_AU', '-pthread',
            '-I', str(EXPERIMENT / 'adapter'), '-I', str(cls.source / 'dm/host')]
        if os.environ.get('BOTW_WHOLE_AUDIO_SANITIZERS') == '1':
            base += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        for name in ('platform', 'upstream'):
            binary = cls.work / name
            sources = ([str(EXPERIMENT / 'tests/whole_audio_platform_probe.c'),
                        str(cls.source / 'dm/host/botw_pcm.c')] if name == 'platform' else
                       [str(cls.source / 'dm/host/tests/t_botw_pcm.c')])
            result = subprocess.run(base + sources + [str(EXPERIMENT / 'adapter/whole_audio_platform.m'),
                '-framework', 'AudioToolbox', '-o', str(binary)], text=True, capture_output=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[name] = binary

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def probe(self, case):
        result = subprocess.run([str(self.binaries['platform']), case], text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_platform_pause_flushes_all_sinks_and_new_sinks_inherit_pause(self):
        self.probe('lifecycle')

    def test_start_stop_failures_propagate_and_resume_failure_stops_all_sinks(self):
        self.probe('failures')

    def test_close_retires_dispose_failed_storage_without_resurrecting_it(self):
        self.probe('retired')

    def test_platform_lifecycle_serializes_with_owned_pcm_producer(self):
        self.probe('concurrency')

    def test_observed_write_separates_old_flush_from_new_callback_consumption(self):
        self.probe('observed')

    def test_missing_observation_rejects_without_publishing_frames(self):
        self.probe('missing-observation')

    def test_original_ring_gain_mapping_and_callback_quiescence_suite(self):
        result = subprocess.run([str(self.binaries['upstream'])], text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('t_botw_pcm ok', result.stdout)


if __name__ == '__main__':
    unittest.main()
