"""Complete fixed native renderer, owned CPU service/worker/PCM contracts only."""
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

COMPONENT = Path(__file__).resolve().parents[1]


def checked(command, **kwargs):
    result = subprocess.run([str(part) for part in command], text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise AssertionError(f'{command!r}\n{result.stdout}{result.stderr}')
    return result


@unittest.skipUnless(platform.system() == 'Darwin', 'actual macOS SDK required; no fake SDK')
class CompleteRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-owned-complete-renderer-')
        cls.build = Path(cls.scratch.name) / 'build'
        command = ['cmake', '-S', COMPONENT / 'native', '-B', cls.build,
                   '-DBOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE=ON', '-DCMAKE_BUILD_TYPE=Debug']
        if os.environ.get('BOTW_AUDIO_RENDERER_SANITIZERS') == '1':
            command += ['-DBOTW_AUDIO_RENDERER_NATIVE_SANITIZERS=ON']
        checked(command, timeout=60)
        checked(['cmake', '--build', cls.build, '--parallel', '3'], timeout=240)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def test_complete_original_rev1_update_and_prewrite_descriptor_rejection(self):
        checked([self.build / 'botw_owned_renderer_update'], timeout=20)

    def test_real_workers_contain_event_producer_failure_join_and_release_owned_maps(self):
        checked([self.build / 'botw_owned_renderer_worker'], timeout=20)

    def test_c_abi_strong_resources_normal_wire_results_update_stop_failure_and_retry(self):
        checked([self.build / 'botw_owned_audren_host'], timeout=20)

    def test_actual_full_pcm_fifo_pause_ack_resume_and_nonzero_original_device_command(self):
        checked([self.build / 'botw_owned_adsp_pause'], timeout=20)

    def test_fieldwise_wire_profiles_and_checked_update_section_lengths(self):
        checked([self.build / 'botw_owned_renderer_wire'], timeout=20)


if __name__ == '__main__':
    unittest.main()
