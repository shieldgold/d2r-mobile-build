"""Shared finite observer and real iOS entry projections, without UI or GPU startup."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
#include "BOTWEventLog.m"
#include <pthread.h>
#include <string.h>
#include <assert.h>
#include <unistd.h>
static pthread_t fixture_main;
static unsigned reads, enable_calls;
static const char *fixture_mode;
int botw_graphics_enable_lock_observation(void) {
    ++enable_calls;
    return strcmp(fixture_mode, "late") != 0;
}
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
    if (!strcmp(fixture_mode, "closed-disabled")) {
        botw_frontend_start_graphics_lock_observations();
        int accepted = botw_frontend_enable_graphics_lock_observation();
        if (accepted || enable_calls) {
            fprintf(stderr, "disabled_start_closes_configuration accepted=%d producer_calls=%u expected=0/0\n", accepted, enable_calls);
            return 91;
        }
        botw_frontend_start_graphics_lock_observations();
        assert(lock_observation_started && !lock_observation_queue && !reads && !lock_observations_emitted);
        puts("disabled_start_closes_configuration_without_queue_or_producer=true"); return 0;
    }
    if (!strcmp(fixture_mode, "disabled")) {
        botw_frontend_start_graphics_lock_observations();
        for (unsigned i = 0; i < 12; ++i) log_host_lock_observation();
        assert(lock_observation_started && !lock_observation_queue);
        assert(!reads && !lock_observations_emitted);
        puts("observer_disabled_without_snapshot_or_queue=true"); return 0;
    }
    if (!strcmp(fixture_mode, "late")) {
        assert(!botw_frontend_enable_graphics_lock_observation());
        botw_frontend_start_graphics_lock_observations();
        assert(lock_observation_started && !lock_observation_queue && !reads);
        puts("observer_late_enable_rejected_without_queue=true"); return 0;
    }
    assert(botw_frontend_enable_graphics_lock_observation());
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    if (!strcmp(fixture_mode, "independent")) {
        botw_frontend_start_graphics_lock_observations();
        assert(lock_observation_started && lock_observation_queue);
        assert(!botw_frontend_enable_graphics_lock_observation()); /* Queued state is immutable. */
        botw_frontend_start_graphics_lock_observations(); /* Reentry must not schedule another set. */
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


@unittest.skipUnless(platform.system() == 'Darwin', 'Foundation/GCD requires macOS')
class MacLockObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-mac-lock-observer-')
        folder = Path(cls.build.name)
        (folder / 'probe.m').write_text(PROBE)
        cls.binary = folder / 'probe'
        command = ['xcrun', 'clang', '-std=c11', '-mmacosx-version-min=14.0',
                   '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                   '-I', str(ROOT / 'native/botw_host'), '-I', str(ROOT / 'native/botw_graphics'),
                   '-I', str(ROOT / 'native/botw_app'), str(folder / 'probe.m'),
                   '-framework', 'Foundation',
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

    def test_disabled_start_closes_configuration_without_calling_producer(self):
        result = self.run_probe('closed-disabled')
        self.assertEqual(result.stderr, '')
        self.assertIn('closes_configuration_without_queue_or_producer=true', result.stdout)

    def test_late_enable_rejection_does_not_queue_or_read(self):
        result = self.run_probe('late')
        self.assertEqual(result.stderr, '')
        self.assertIn('late_enable_rejected_without_queue=true', result.stdout)

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


def source_definition(text, marker):
    """Project a complete production definition without loading UIKit/Metal."""
    start = text.index(marker)
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


IOS_PROJECTION = r'''
#import <Foundation/Foundation.h>
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
static unsigned enables, starts, ui_calls, errors;
static int enable_result = 1, create_result, attr_result;
static BOOL lock_observation_enable_rejected;
static BOOL audioSessionPrepared = YES, audioInterrupted, audioServicesLost;
static BOOL audio_result = YES;
static int expected_argc;
static char **expected_argv;
int botw_frontend_enable_graphics_lock_observation(void) {
    assert(!ui_calls); ++enables; return enable_result;
}
void botw_frontend_start_graphics_lock_observations(void) { ++starts; }
@interface BOTWDelegate : NSObject @end
@implementation BOTWDelegate @end
int UIApplicationMain(int argc, char **argv, NSString *principal, NSString *delegate) {
    assert(argc == expected_argc && argv == expected_argv && !principal);
    assert([delegate isEqualToString:@"BOTWDelegate"]);
    ++ui_calls; return 73;
}
static BOOL activateHostAudioSession(void) { return audio_result; }
static void hostEvent(const char *message) { assert(message); ++errors; }
static void *runGuest(void *unused) { return unused; }
static int fixture_attr_init(pthread_attr_t *attr) { (void)attr; return attr_result; }
static int fixture_attr_stack(pthread_attr_t *attr, size_t size) {
    (void)attr; assert(size == 64u * 1024u * 1024u); return 0;
}
static int fixture_attr_detach(pthread_attr_t *attr, int value) {
    (void)attr; assert(value == PTHREAD_CREATE_DETACHED); return 0;
}
static int fixture_create(pthread_t *thread, const pthread_attr_t *attr, void *(*entry)(void *), void *argument) {
    (void)thread; (void)attr; assert(entry == runGuest && !argument); return create_result;
}
static int fixture_attr_destroy(pthread_attr_t *attr) { (void)attr; return 0; }
#define pthread_attr_init fixture_attr_init
#define pthread_attr_setstacksize fixture_attr_stack
#define pthread_attr_setdetachstate fixture_attr_detach
#define pthread_create fixture_create
#define pthread_attr_destroy fixture_attr_destroy
@interface BOTWController : NSObject
@property BOOL bootstrapReady;
@property BOOL started;
- (void)startGuestIfReady;
@end
@implementation BOTWController
__START_METHOD__
@end
__REJECTION_LOG__
#define main actual_ios_main
__ACTUAL_MAIN__
#undef main
int main(int argc, char **argv) { @autoreleasepool {
    assert(argc == 2);
    if (!strcmp(argv[1], "start")) {
        BOTWController *controller = [BOTWController new];
        [controller startGuestIfReady]; assert(!starts && !controller.started);
        controller.bootstrapReady = YES;
        audioInterrupted = YES; [controller startGuestIfReady]; assert(!starts); audioInterrupted = NO;
        audioServicesLost = YES; [controller startGuestIfReady]; assert(!starts); audioServicesLost = NO;
        audioSessionPrepared = NO; audio_result = NO;
        [controller startGuestIfReady]; assert(!starts); audio_result = YES; audioSessionPrepared = YES;
        attr_result = 1; [controller startGuestIfReady]; assert(!starts && !controller.started); attr_result = 0;
        create_result = 1; [controller startGuestIfReady]; assert(!starts && !controller.started); create_result = 0;
        [controller startGuestIfReady];
        if (starts != 1 || !controller.started) {
            fprintf(stderr, "start_after_pthread_success actual_starts=%u expected=1 started=%d\n", starts, controller.started); return 91;
        }
        [controller startGuestIfReady]; assert(starts == 1 && !enables && errors == 2);
        puts("actual_start_method_success_once=true failure_does_not_schedule=true"); return 0;
    }
    char *with_flag[] = {"BOTWNative", "-UIKitPrivateArgument", "--observe-graphics-lock", "--observe-graphics-lock", NULL};
    char *without_flag[] = {"BOTWNative", "-UIKitPrivateArgument", "prefix--observe-graphics-lock", "--observe-graphics-lock=true", NULL};
    BOOL enabled = strcmp(argv[1], "unset") != 0;
    if (!strncmp(argv[1], "late", 4)) enable_result = 0;
    expected_argc = 4; expected_argv = enabled ? with_flag : without_flag;
    int result = actual_ios_main(expected_argc, expected_argv);
    if (result != 73 || ui_calls != 1 || enables != (unsigned)enabled || starts ||
        lock_observation_enable_rejected != (enabled && !enable_result)) {
        fprintf(stderr, "actual_main_projection enables=%u expected=%u ui_calls=%u result=%d rejected=%d\n",
            enables, enabled, ui_calls, result, lock_observation_enable_rejected); return 91;
    }
    if (!strcmp(argv[1], "late-log")) {
        logLockObservationEnableRejection(); logLockObservationEnableRejection();
        assert(!lock_observation_enable_rejected && !starts);
    }
    puts("actual_main_preUIKit_exact_argv_preserved=true"); return 0;
} }
'''


@unittest.skipUnless(platform.system() == 'Darwin', 'Foundation requires macOS')
class IOSLockObserverIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-ios-lock-projection-')
        folder = Path(cls.build.name)
        actual = (ROOT / 'native/botw_app/main.m').read_text()
        projection = IOS_PROJECTION.replace('__ACTUAL_MAIN__', source_definition(actual, 'int main(int argc, char **argv)'))
        projection = projection.replace('__START_METHOD__', source_definition(actual, '- (void)startGuestIfReady {'))
        projection = projection.replace('__REJECTION_LOG__', source_definition(actual, 'static void logLockObservationEnableRejection(void)'))
        (folder / 'probe.m').write_text(projection)
        cls.binary = folder / 'probe'
        command = ['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                   '-Wall', '-Wextra', '-Werror', str(folder / 'probe.m'), '-framework', 'Foundation', '-o', str(cls.binary)]
        compiled = subprocess.run(command, text=True, capture_output=True)
        if compiled.returncode:
            cls.build.cleanup()
            raise AssertionError(compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_mode(self, mode, expected_stderr=''):
        result = subprocess.run([str(self.binary), mode], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, expected_stderr)
        return result

    def test_actual_main_enables_exact_token_before_UIKit_and_preserves_argv(self):
        self.assertIn('exact_argv_preserved=true', self.run_mode('enabled').stdout)

    def test_actual_main_unset_or_similar_tokens_do_not_enable(self):
        self.run_mode('unset')

    def test_actual_main_late_enable_rejection_still_enters_UIKit(self):
        self.run_mode('late')

    def test_rejected_enable_reports_once_after_original_log_connection(self):
        self.run_mode('late-log', 'host_graphics_lock_observation enabled=false reason=pre_start_enable_rejected\n')
        actual = (ROOT / 'native/botw_app/main.m').read_text()
        appear = source_definition(actual, '- (void)viewDidAppear:(BOOL)animated')
        self.assertLess(appear.index('if (dup2('), appear.index('logLockObservationEnableRejection();'))
        self.assertLess(appear.index('logLockObservationEnableRejection();'), appear.index('[self startGuestIfReady];'))
        self.assertNotIn('logLockObservationEnableRejection', source_definition(actual, 'int main(int argc, char **argv)'))

    def test_actual_guest_start_method_only_schedules_after_thread_creation_succeeds(self):
        self.assertIn('success_once=true', self.run_mode('start').stdout)


if __name__ == '__main__':
    unittest.main()
