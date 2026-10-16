"""One original kernel table for real service transports and event consumers."""
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'


class WholeKernelServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (EXP / 'adapter/whole_kernel.h').is_file():
            raise AssertionError('the complete host has no checked single-table service kernel bridge')
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-whole-kernel-services-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name); source = work / 'source'
        shutil.copytree(EXP / 'upstream', source)
        patch = subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(EXP / 'patches/kernel-services.patch')],
                               cwd=source, capture_output=True, text=True)
        if patch.returncode: raise AssertionError(patch.stdout + patch.stderr)
        text = (source / 'dm/host/hle_thread.c').read_text()
        # Keep the actual structs, table, allocator, lookup, wait-list wake and
        # synchronization consumer. Only unrelated thread execution is excluded.
        a = text.index('enum { O_FREE,'); b = text.index('static thr_t *cur(void)', a)
        registry = text[a:b]
        a = text.index('static void wait_unlink('); b = text.index('// Suspend until woken', a)
        waits = text[a:b]
        a = text.index('static int sig_obj('); b = text.index('static int64_t now_mono_ns(', a)
        consumers = text[a:b]
        a = text.index('// ---------- Checked native service kernel bridge ----------')
        (work / 'original_kernel_registry.inc').write_text(registry + waits + consumers + text[a:])
        cls.binary = work / 'probe'
        command = [shutil.which('clang') or 'cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-unused-function', '-Wno-unused-variable', '-pthread',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I', str(source / 'dm/host'), '-I', str(EXP / 'adapter'), '-I', str(work),
                   str(EXP / 'tests/whole_kernel_services_probe.c'),
                   '-I', str(ROOT / 'native/botw_host'),
                   *[str(ROOT / p) for p in ('native/botw_host/services.c', 'native/botw_resources/romfs.c',
                      'native/botw_resources/savefs.c', 'native/botw_resources/local_user.c', 'native/botw_input/input.c')],
                   '-o', str(cls.binary)]
        if platform.system() == 'Darwin': command += ['-framework', 'Security']
        build = subprocess.run(command, capture_output=True, text=True)
        if build.returncode: raise AssertionError(build.stdout + build.stderr)

    def probe(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_transports_threads_and_event_ends_use_the_same_original_table(self): self.probe('registry')
    def test_transport_release_verifies_owner_and_duplicate_owner_never_publishes(self): self.probe('owner')
    def test_only_one_free_slot_cannot_partially_publish_an_event(self): self.probe('capacity')
    def test_event_endpoint_direction_duplicates_and_manual_clear(self): self.probe('events')
    def test_signal_wakes_real_original_wait_list_and_condition(self): self.probe('wake')
    def test_close_wakes_waiter_and_original_consumer_rejects_retired_handle(self): self.probe('close-wake')
    def test_producer_keeps_original_event_after_handle_close_and_slot_reuse(self): self.probe('producer')
    def test_invalid_arguments_preserve_outputs_and_live_objects(self): self.probe('invalid')
    def test_parallel_unique_transport_owners_retire_without_collisions(self): self.probe('parallel')
    def test_real_sm_lm_logger_clone_requests_use_only_the_original_kernel_table(self): self.probe('services')


if __name__ == '__main__': unittest.main()
