"""Generic macOS frontend checks with a synthetic host, without game resources."""
from pathlib import Path
import os
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST = r'''
#import <AppKit/AppKit.h>
#import <QuartzCore/CAMetalLayer.h>
#include "runtime.h"
#include "graphics.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static unsigned ticks;
static unsigned input_ticks;
int botw_graphics_enable_lock_observation(void) {
    if (getenv("BOTW_MAC_TEST_OBSERVATION_ENABLE_FAIL")) return 0;
    puts("synthetic_lock_observation_enabled=true");
    return 1;
}
__attribute__((weak))
botw_graphics_lock_observation_status botw_graphics_read_lock_observation(botw_graphics_lock_observation *out) {
    (void)out; return BOTW_GRAPHICS_LOCK_OBSERVATION_DISABLED;
}
static void diagnose_window(const char *phase) {
    assert(NSThread.isMainThread);
    NSApplication *app = NSApplication.sharedApplication;
    NSWindow *window = app.windows.firstObject;
    fprintf(stdout, "synthetic_window_diagnostic phase=%s windows=%lu visible=%d key=%d "
        "occlusion=%lu screen=%s app_active=%d app_hidden=%d ticks=%u run_loop=%s\n",
        phase, (unsigned long)app.windows.count, window.visible, window.keyWindow,
        (unsigned long)window.occlusionState, window.screen.localizedName.UTF8String ?: "(none)",
        app.active, app.hidden, ticks, NSRunLoop.currentRunLoop.currentMode.UTF8String ?: "(none)");
    fflush(stdout);
}
int botw_host_set_address_space(unsigned bits) {
    assert(bits == 39);
    return getenv("BOTW_MAC_TEST_CONFIG_FAIL") ? 1 : 0;
}
int botw_host_set_graphics_layer(void *layer) {
    assert([(__bridge id)layer isKindOfClass:CAMetalLayer.class]);
    diagnose_window("layer_configured");
    if (getenv("BOTW_MAC_TEST_CLOSE")) {
        /* Observation only: this timer never supplies display ticks, changes
         * focus or closes the window. It distinguishes a live main run loop
         * without display callbacks from a stalled main run loop. */
        NSTimer *probe = [NSTimer timerWithTimeInterval:1 repeats:YES block:^(NSTimer *timer) {
            (void)timer; diagnose_window("waiting_for_display");
        }];
        [NSRunLoop.mainRunLoop addTimer:probe forMode:NSRunLoopCommonModes];
    }
    return 0;
}
void botw_host_refresh_input(void) {}
void botw_host_sample_input(void) {
    assert(NSThread.isMainThread);
    ++input_ticks;
    assert(input_ticks == ticks);
}
void botw_host_set_applet_state(int foreground, int focused) { (void)foreground; (void)focused; }
void botw_host_display_tick(double target) {
    assert(NSThread.isMainThread && target > 0);
    ++ticks;
    if (ticks == 1) {
        diagnose_window("first_real_display_tick");
        fprintf(stdout, "synthetic_first_display_target=%.9f\n", target); fflush(stdout);
    }
    if (ticks == 2 && getenv("BOTW_MAC_TEST_CLOSE")) {
        assert(input_ticks == ticks - 1); /* This callback's input phase follows VI. */
        assert(NSApplication.sharedApplication.windows.count == 1);
        fprintf(stdout, "synthetic_host_real_display_ticks=%u\n", ticks); fflush(stdout);
        [NSApplication.sharedApplication.windows.firstObject performClose:nil];
    }
}
int botw_host_run_mode(const char *root, const char *documents, botw_host_event event, enum botw_host_mode mode) {
    assert(!NSThread.isMainThread && root[0] == '/' && !strcmp(root, documents));
    assert(mode == BOTW_HOST_BOOT_GAME && pthread_get_stacksize_np(pthread_self()) >= 64u*1024u*1024u);
    event("synthetic_host_entered=true");
    if (getenv("BOTW_MAC_TEST_CLOSE")) for (;;) pause();
    return 1;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin', 'AppKit requires macOS')
class MacFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-mac-frontend-')
        folder = Path(cls.build.name)
        stub = folder / 'host.m'; stub.write_text(HOST)
        cls.binary = folder / 'botw-mac'
        command = ['xcrun', 'clang', '-std=c11', '-mmacosx-version-min=14.0', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                   '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'native/botw_host'),
                   '-I', str(ROOT / 'native/botw_graphics'),
                   str(ROOT / 'native/botw_app/mac_main.m'),
                   str(ROOT / 'native/botw_app/BOTWEventLog.m'), str(stub), '-framework', 'AppKit',
                   '-framework', 'QuartzCore', '-framework', 'Metal', '-o', str(cls.binary)]
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            cls.build.cleanup()
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_frontend(self, *arguments, **env):
        environment = dict(os.environ)
        environment.pop('BOTW_MAC_TEST_CLOSE', None)
        environment.pop('BOTW_MAC_TEST_CONFIG_FAIL', None)
        environment.pop('BOTW_MAC_TEST_OBSERVATION_ENABLE_FAIL', None)
        environment.update(env)
        try:
            return subprocess.run([str(self.binary), *arguments], capture_output=True, text=True,
                                  timeout=15, env=environment)
        except subprocess.TimeoutExpired as error:
            # TimeoutExpired may retain bytes even when text=True was requested.
            def partial_text(value):
                if isinstance(value, bytes):
                    return value.decode('utf-8', errors='replace')
                return value or '(empty)'

            self.fail(f'macOS frontend timed out after {error.timeout} seconds: {error.cmd!r}\n'
                      f'Partial stdout:\n{partial_text(error.stdout)}\n'
                      f'Partial stderr:\n{partial_text(error.stderr)}')

    def test_usage_and_invalid_paths_exit_before_appkit_or_guest(self):
        help_result = self.run_frontend('--help')
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('ROOT', help_result.stdout)
        for arguments, code in [((), 64), (('--unknown',), 64),
                                (('/nonexistent-botw-mac-fixture',), 66), ((str(self.binary),), 66)]:
            with self.subTest(arguments=arguments):
                result = self.run_frontend(*arguments)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertNotIn('synthetic_host_entered', result.stdout)

    def test_address_policy_failure_stops_before_creating_a_window(self):
        result = self.run_frontend(self.build.name, BOTW_MAC_TEST_CONFIG_FAIL='1')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('address_space_configuration_failed', result.stderr)
        self.assertNotIn('synthetic_host_entered', result.stdout)
        self.assertNotIn('synthetic_lock_observation_enabled', result.stdout)

    def test_explicit_lock_observation_is_enabled_before_host_setup(self):
        result = self.run_frontend(self.build.name, '--observe-graphics-lock',
                                   BOTW_MAC_TEST_CONFIG_FAIL='1')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('synthetic_lock_observation_enabled=true', result.stdout)
        self.assertNotIn('mac_window_ready', result.stdout)
        self.assertNotIn('synthetic_host_entered', result.stdout)
        for arguments in [(self.build.name, '--unknown'),
                          (self.build.name, '--observe-graphics-lock', '--extra')]:
            result = self.run_frontend(*arguments)
            self.assertEqual(result.returncode, 64, result.stdout + result.stderr)
            self.assertNotIn('synthetic_lock_observation_enabled', result.stdout)
        rejected = self.run_frontend(self.build.name, '--observe-graphics-lock',
                                     BOTW_MAC_TEST_CONFIG_FAIL='1',
                                     BOTW_MAC_TEST_OBSERVATION_ENABLE_FAIL='1')
        self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
        self.assertIn('address_space_configuration_failed', rejected.stderr)
        self.assertIn('enabled=false reason=pre_start_enable_rejected', rejected.stderr)
        self.assertNotIn('mac_window_ready', rejected.stdout)

    @unittest.skipUnless(os.environ.get('BOTW_RUN_WINDOW_TESTS') == '1', 'requires an active WindowServer session')
    def test_startup_failure_and_window_close_exit_without_destroying_live_guest(self):
        failure = self.run_frontend(self.build.name)
        self.assertEqual(failure.returncode, 1, failure.stdout + failure.stderr)
        self.assertIn('synthetic_host_entered=true', failure.stdout)
        self.assertIn('mac_host_return=1', failure.stdout)
        closed = self.run_frontend(self.build.name, BOTW_MAC_TEST_CLOSE='1')
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertIn('synthetic_host_real_display_ticks=2', closed.stdout)
        self.assertIn('mac_process_exit=0 frontend_resources_released=false', closed.stdout)


if __name__ == '__main__':
    unittest.main()
