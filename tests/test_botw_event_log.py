"""Stress the actual Foundation logger and real main queue with synthetic events."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
#import <Foundation/Foundation.h>
#include <dispatch/dispatch.h>
#include <mach/mach.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "BOTWEventLog.h"
#include "graphics.h"
/* UI history must never enable or sample the independent host observer. */
int botw_graphics_enable_lock_observation(void) { abort(); }
botw_graphics_lock_observation_status botw_graphics_read_lock_observation(botw_graphics_lock_observation *out) {
    (void)out; abort();
}
static unsigned callbacks;
static int heartbeat;
static uint64_t footprint(void) {
    task_vm_info_data_t info = {0}; mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&info, &count) || count < TASK_VM_INFO_REV1_COUNT) abort();
    return info.phys_footprint;
}
int main(int argc, const char **argv) { @autoreleasepool {
    if (argc != 2) return 64;
    if (!strcmp(argv[1], "invalid")) {
        void (^handler)(NSString *, BOOL) = ^(NSString *text, BOOL reveal) { (void)text; (void)reveal; };
        if ([[BOTWEventLog alloc] initWithCharacterLimit:0 handler:handler] ||
            [[BOTWEventLog alloc] initWithCharacterLimit:NSUIntegerMax handler:handler] ||
            [[BOTWEventLog alloc] initWithCharacterLimit:32 handler:nil]) return 1;
        puts("invalid_config=fail_closed"); return 0;
    }
    BOOL concurrent = !strcmp(argv[1], "concurrent");
    BOOL tiny = !strcmp(argv[1], "unicode");
    if (!concurrent && !tiny && strcmp(argv[1], "burst")) return 65;
    const NSUInteger limit = tiny ? 9 : 65536;
    __block BOTWEventLog *log;
    log = [[BOTWEventLog alloc] initWithCharacterLimit:limit handler:^(NSString *text, BOOL reveal) {
        if (!NSThread.isMainThread || text.length > limit || ![text dataUsingEncoding:NSUTF8StringEncoding]) exit(2);
        ++callbacks;
        if (callbacks == 1) {
            if (!reveal || ![text hasSuffix:tiny ? @"尾🧪\n" : @"END🧪\n"]) exit(3);
            if (!tiny && ([text containsString:@"000000:initial"] ||
                [text componentsSeparatedByString:@"\n"].count > 513)) exit(4);
            [log appendMessage:@"NEXT" reveal:NO]; /* Reentry must not retain the logger lock. */
        } else if (callbacks == 2) {
            if (reveal || ![text hasSuffix:@"NEXT\n"]) exit(5);
        } else exit(6);
    }];
    if (!log) return 7;
    uint64_t before = footprint();
    [log appendMessage:@"000000:initial" reveal:YES];
    if (tiny) {
        NSMutableString *large = [NSMutableString string];
        for (unsigned i=0; i<10000; ++i) [large appendString:@"👨‍👩‍👧‍👦é"];
        [log appendMessage:large reveal:NO];
        [log appendMessage:@"尾🧪" reveal:NO];
        [large setString:@"caller-mutated"];
    } else {
        void (^produce)(unsigned) = ^(unsigned producer) {
            for (unsigned i=0; i<25000; ++i) { @autoreleasepool {
                NSString *message = [NSString stringWithFormat:@"%06u:producer=%u synthetic condition signal requested=-1 waiters=0 woken=0", i+1, producer];
                [log appendMessage:message reveal:NO];
            } }
        };
        if (concurrent) {
            dispatch_group_t group=dispatch_group_create();
            for (unsigned i=0; i<4; ++i) dispatch_group_async(group, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0), ^{ produce(i); });
            if (dispatch_group_wait(group, dispatch_time(DISPATCH_TIME_NOW, 10*NSEC_PER_SEC))) return 8;
        } else for (unsigned i=0; i<4; ++i) produce(i);
        [log appendMessage:@"END🧪" reveal:NO];
    }
    uint64_t after = footprint();
    if (after > before && after-before > 64u*1024u*1024u) return 9;
    printf("producer_footprint_before=%llu after=%llu callbacks_before_main=%u\n", (unsigned long long)before, (unsigned long long)after, callbacks);
    if (callbacks) return 10;
    dispatch_async(dispatch_get_main_queue(), ^{ heartbeat=1; });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 400*NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
        if (!heartbeat || callbacks != 2) exit(11);
        printf("event_log_stress=verified callbacks=%u retained_text_limit=%lu\n",callbacks,(unsigned long)limit);
        exit(0);
    });
} for (;;) { @autoreleasepool { [NSRunLoop.mainRunLoop runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.02]]; } } }
'''

@unittest.skipUnless(platform.system() == 'Darwin', 'Foundation/main queue requires macOS')
class EventLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='botw-event-log-')
        folder = Path(cls.temporary.name)
        source = folder / 'probe.m'; source.write_text(PROBE)
        cls.binary = folder / 'probe'
        command = ['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fblocks', '-Wall', '-Wextra', '-Werror',
                   '-I', str(ROOT / 'native/botw_app'), '-I', str(ROOT / 'native/botw_graphics'), str(source),
                   str(ROOT / 'native/botw_app/BOTWEventLog.m'), '-framework', 'Foundation', '-o', str(cls.binary)]
        compiled = subprocess.run(command, text=True, capture_output=True)
        if compiled.returncode:
            cls.temporary.cleanup()
            raise AssertionError(compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_mode(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_invalid_configuration_is_rejected(self):
        self.assertIn('invalid_config=fail_closed', self.run_mode('invalid'))

    def test_one_hundred_thousand_events_coalesce_before_main_and_allow_reentry(self):
        self.assertIn('event_log_stress=verified callbacks=2', self.run_mode('burst'))

    def test_concurrent_producers_keep_storage_and_main_queue_bounded(self):
        self.assertIn('event_log_stress=verified callbacks=2', self.run_mode('concurrent'))

    def test_oversized_unicode_and_mutable_input_keep_valid_bounded_tail(self):
        self.assertIn('event_log_stress=verified callbacks=2', self.run_mode('unicode'))

if __name__ == '__main__':
    unittest.main()
