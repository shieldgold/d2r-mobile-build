"""Run the packaged iOS policy selector with real Foundation plist values."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
#import <Foundation/Foundation.h>
#include "runtime.h"
static unsigned calls;
static enum botw_host_memory_policy selected;
static int setter_result;
int botw_host_set_memory_policy(enum botw_host_memory_policy policy) {
    ++calls; selected = policy; return setter_result;
}
#include "memory_policy.h"
int main(void) { @autoreleasepool {
    if (configurePackagedHostMemoryPolicy(nil) || calls != 1 || selected != BOTW_HOST_MEMORY_BOUNDED) return 1;
    if (configurePackagedHostMemoryPolicy(@"bounded-v1") || calls != 2 || selected != BOTW_HOST_MEMORY_BOUNDED) return 2;
    NSError *error = nil;
    NSData *plist = [NSPropertyListSerialization dataWithPropertyList:@{@"BOTWHostMemoryPolicy": @"original-logical-v1"}
        format:NSPropertyListBinaryFormat_v1_0 options:0 error:&error];
    NSDictionary *decoded = [NSPropertyListSerialization propertyListWithData:plist options:0 format:NULL error:&error];
    if (error || !decoded || configurePackagedHostMemoryPolicy(decoded[@"BOTWHostMemoryPolicy"]) ||
        calls != 3 || selected != BOTW_HOST_MEMORY_ORIGINAL_LOGICAL) return 3;
    NSArray *invalid = @[@"", @"original-logical-v2", @" original-logical-v1", @"original-logical-v1\n", @1,
        @YES, @[], @{}, NSNull.null, [@"original-logical-v1" dataUsingEncoding:NSUTF8StringEncoding]];
    for (id value in invalid) {
        if (!configurePackagedHostMemoryPolicy(value) || calls != 3) return 4;
    }
    setter_result = 1;
    if (!configurePackagedHostMemoryPolicy(@"original-logical-v1") || calls != 4) return 5;
    setter_result = 2;
    if (!configurePackagedHostMemoryPolicy(nil) || calls != 5) return 6;
    puts("packaged_memory_policy_selector=verified default=bounded explicit=original-logical-v1 invalid=fail-closed");
    return 0;
} }
'''

@unittest.skipUnless(platform.system() == 'Darwin', 'Foundation requires macOS')
class IOSMemoryPolicyTests(unittest.TestCase):
    def test_actual_selector_validates_plist_types_and_propagates_runtime_rejection(self):
        with tempfile.TemporaryDirectory(prefix='botw-ios-memory-policy-') as temporary:
            work = Path(temporary); source = work / 'probe.m'; binary = work / 'probe'
            source.write_text(PROBE)
            command = ['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                       '-I', str(ROOT / 'native/botw_host'), '-I', str(ROOT / 'native/botw_app'),
                       str(source), '-framework', 'Foundation', '-o', str(binary)]
            compiled = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('packaged_memory_policy_selector=verified', result.stdout)

if __name__ == '__main__':
    unittest.main()
