"""Finite host-only observer checks without AppKit startup or game resources."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from test_botw_mac_frontend import HOST

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
#define main botw_unused_frontend_main
#include "mac_main.m"
#undef main
#include <assert.h>
#include <unistd.h>
static pthread_t fixture_main;
static unsigned reads;
static const char *fixture_mode;
botw_graphics_lock_observation_status botw_graphics_read_lock_observation(botw_graphics_lock_observation *out) {
    assert(!pthread_equal(pthread_self(), fixture_main));
    unsigned call = ++reads;
    assert(call <= 4);
    memset(out, 0xab, sizeof *out); /* Non-payload statuses must not expose these bytes. */
    if (!strcmp(fixture_mode, "invalid")) {
        if (call == 1) return BOTW_GRAPHICS_LOCK_OBSERVATION_DISABLED;
        if (call == 2) return (botw_graphics_lock_observation_status)99;
        if (call == 3) return BOTW_GRAPHICS_LOCK_OBSERVATION_UNAVAILABLE;
        out->sampled_ns = 1; out->acquired_ns = 2;
        return BOTW_GRAPHICS_LOCK_OBSERVATION_HELD;
    }
    if (call == 2) return BOTW_GRAPHICS_LOCK_OBSERVATION_UNSTABLE;
    if (call == 4) return BOTW_GRAPHICS_LOCK_OBSERVATION_UNAVAILABLE;
    memset(out, 0, sizeof *out);
    out->lifecycle = 3; out->generation = call == 1 ? 77 : 78;
    out->sampled_ns = 160000100;
    if (call == 3) return BOTW_GRAPHICS_LOCK_OBSERVATION_IDLE;
    out->owner_thread_id = 4242; out->acquired_ns = 100;
    snprintf(out->outer_entry, sizeof out->outer_entry, "host_fixture_outer");
    snprintf(out->active_entry, sizeof out->active_entry, "host_fixture_active");
    return BOTW_GRAPHICS_LOCK_OBSERVATION_HELD;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    fixture_main = pthread_self(); fixture_mode = argv[1];
    if (!strcmp(fixture_mode, "disabled")) {
        start_host_lock_observations();
        for (unsigned i = 0; i < 12; ++i) log_host_lock_observation();
        assert(!lock_observation_started && !lock_observation_queue);
        assert(!reads && !lock_observations_emitted);
        puts("observer_disabled_without_snapshot_or_queue=true"); return 0;
    }
    observe_graphics_lock = YES;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    if (!strcmp(fixture_mode, "independent")) {
        start_host_lock_observations();
        assert(lock_observation_started && lock_observation_queue);
        start_host_lock_observations(); /* Reentry must not schedule another set. */
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 10500 * NSEC_PER_MSEC), lock_observation_queue, ^{
            assert(reads == 1 && lock_observations_emitted == 1);
            dispatch_semaphore_signal(done);
        });
    } else {
        dispatch_queue_t queue = dispatch_queue_create("local.botw.observer-fixture", DISPATCH_QUEUE_SERIAL);
        dispatch_async(queue, ^{
            for (unsigned i = 0; i < 12; ++i) log_host_lock_observation();
            assert(reads == 4 && lock_observations_emitted == 4);
            dispatch_semaphore_signal(done);
        });
    }
    /* The main thread remains blocked: neither UI nor a display callback drives this. */
    assert(!dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 12 * NSEC_PER_SEC)));
    puts("observer_fixture_complete=true"); return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin', 'AppKit linkage requires macOS')
class MacLockObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-mac-lock-observer-')
        folder = Path(cls.build.name)
        (folder / 'probe.m').write_text(PROBE)
        (folder / 'host.m').write_text(HOST)
        cls.binary = folder / 'probe'
        command = ['xcrun', 'clang', '-std=c11', '-mmacosx-version-min=14.0',
                   '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                   '-I', str(ROOT / 'native/botw_host'), '-I', str(ROOT / 'native/botw_graphics'),
                   '-I', str(ROOT / 'native/botw_app'), str(folder / 'probe.m'), str(folder / 'host.m'),
                   '-framework', 'AppKit', '-framework', 'QuartzCore', '-framework', 'Metal',
                   '-o', str(cls.binary)]
        compiled = subprocess.run(command, text=True, capture_output=True)
        if compiled.returncode:
            cls.build.cleanup()
            raise AssertionError(compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_probe(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_disabled_observer_does_not_create_queue_or_read_snapshot(self):
        result = self.run_probe('disabled')
        self.assertIn('observer_disabled_without_snapshot_or_queue=true', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_four_sample_cap_and_unavailable_payload_is_not_printed(self):
        result = self.run_probe('bounded')
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 4, result.stderr)
        for i, status in enumerate(('held', 'unstable', 'idle', 'unavailable'), 1):
            self.assertIn(f'sample={i} status={status}', lines[i - 1])
            self.assertTrue(lines[i - 1].endswith('gameplay_verified=false'))
        self.assertIn('generation=77 owner=4242', lines[0])
        self.assertIn('held_ns=160000000', lines[0])
        self.assertIn('outer=host_fixture_outer active=host_fixture_active', lines[0])
        for index in (1, 3):
            self.assertNotIn('lifecycle=', lines[index])
            self.assertNotIn('outer=', lines[index])
        invalid = self.run_probe('invalid').stderr.splitlines()
        self.assertEqual(len(invalid), 4)
        for index, line in enumerate(invalid):
            self.assertIn('status=disabled' if index == 0 else 'status=unavailable', line)
            self.assertNotIn('acquired_ns=', line)
            self.assertNotIn('outer=', line)

    def test_scheduled_observer_runs_while_main_thread_is_blocked(self):
        result = self.run_probe('independent')
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn('sample=1 status=held', result.stderr)
        self.assertIn('observer_fixture_complete=true', result.stdout)


if __name__ == '__main__':
    unittest.main()
