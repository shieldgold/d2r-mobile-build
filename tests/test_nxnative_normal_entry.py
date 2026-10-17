"""Actual entry fragments on owned Foundation paths; no UIKit, host or Guest runs."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
PATCH = COMPONENT / 'patches/normal-entry.patch'
ADAPTER = COMPONENT / 'adapter'

PROBE = r'''
#import <Foundation/Foundation.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#if __has_include("normal_policy.h")
#include "normal_policy.h"
#define OWNED_POLICY_PRESENT 1
#else
#define OWNED_POLICY_PRESENT 0
#endif
static NSString *owned_documents, *owned_resources;
static int owned_preload_calls, owned_set_calls, owned_fail_at;
int owned_setenv(const char *key, const char *value, int overwrite) {
    if (++owned_set_calls == owned_fail_at) { errno = EIO; return -1; }
    return setenv(key, value, overwrite);
}
int owned_unsetenv(const char *key) { return unsetenv(key); }
static NSArray<NSString *> *owned_search_paths(NSSearchPathDirectory d,
        NSSearchPathDomainMask domain, BOOL expand) {
    (void)d; (void)domain; (void)expand; return @[owned_documents];
}
@interface OwnedBundle : NSObject
+ (instancetype)mainBundle;
- (NSString *)resourcePath;
@end
@implementation OwnedBundle
+ (instancetype)mainBundle { static OwnedBundle *b; if (!b) b = [OwnedBundle new]; return b; }
- (NSString *)resourcePath { return owned_resources; }
@end
#define NSSearchPathForDirectoriesInDomains owned_search_paths
#define NSBundle OwnedBundle
#define DM_GAME_ROMFS_MARKER "OWNED_MARKER"
static void dm_host_preload_start(int argc, char **argv) {
    if (argc != 2 || !argv[0] || !argv[1]) _exit(93);
    owned_preload_calls++;
}
/* ACTUAL_LOG_MACROS */
/* ACTUAL_ENTRY_FRAGMENT */
static int argument_effect(void) {
    NSString *p = [owned_documents stringByAppendingPathComponent:@"argument-evaluated"];
    return [@"OWNED" writeToFile:p atomically:YES encoding:NSUTF8StringEncoding error:NULL];
}
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 4) return 90;
        owned_documents = [NSString stringWithUTF8String:argv[2]];
        owned_resources = [NSString stringWithUTF8String:argv[3]];
        const char *mode = argv[1];
        if (!strcmp(mode, "log")) { int side = 0; LOG("owned %d", ++side); return side ? 91 : 0; }
        if (!strcmp(mode, "die")) { die("owned %d", argument_effect()); return 94; }
#if OWNED_POLICY_PRESENT
        if (!strcmp(mode, "typed")) {
            char first[] = "/owned/romfs";
            nxnative_normal_environment_entry entries[] = {
                {NXNATIVE_NORMAL_ROMFS, first}, {NXNATIVE_NORMAL_SAVE, "/owned/save"},
                {NXNATIVE_NORMAL_TEMP, "/owned/temp"}, {NXNATIVE_NORMAL_SHMAP, "/owned/map"},
                {NXNATIVE_NORMAL_MSL_DIR, "/owned/msl"}, {NXNATIVE_NORMAL_FONT_DIR, "/owned/font"}
            };
            if (nxnative_normal_environment_apply(NULL, 0)) return 106;
            entries[0].key = (nxnative_normal_environment_key)-1;
            if (nxnative_normal_environment_apply(entries, 6)) return 107;
            entries[0].key = NXNATIVE_NORMAL_SAVE;
            if (nxnative_normal_environment_apply(entries, 6)) return 108;
            entries[0].key = NXNATIVE_NORMAL_ROMFS;
            if (nxnative_normal_environment_apply(entries, 5)) return 109;
            entries[0].value = NULL;
            if (nxnative_normal_environment_apply(entries, 6)) return 110;
            entries[0].value = "relative/path";
            if (nxnative_normal_environment_apply(entries, 6)) return 111;
            if (owned_set_calls || getenv("DM_ROMFS")) return 112;
            entries[0].value = first;
            errno = ERANGE;
            if (!nxnative_normal_environment_apply(entries, 6) || errno != ERANGE) return 113;
            first[1] = 'X';
            if (strcmp(getenv("DM_ROMFS"), "/owned/romfs")) return 114;
            int calls = owned_set_calls;
            if (nxnative_normal_environment_apply(entries, 6) || errno != EALREADY || calls != owned_set_calls) return 115;
            return 0;
        }
#endif
        if (!strcmp(mode, "fail-set")) owned_fail_at = 3;
        int result = boot_env();
        if (!strcmp(mode, "missing") || !strcmp(mode, "fail-set")) {
            if (result || owned_preload_calls) return 95;
#if OWNED_POLICY_PRESENT
            if (getenv("DM_ROMFS")) return 96;
            if (!strcmp(mode, "fail-set") && (owned_set_calls != 3 || errno != EIO)) return 116;
#endif
            return 0;
        }
        if (!result || owned_preload_calls != 1 || g_argc != 2) return 97;
        if (strcmp(g_argv[1], [owned_resources stringByAppendingPathComponent:@"dm"].fileSystemRepresentation)) return 98;
        if ([[NSFileManager defaultManager] fileExistsAtPath:[owned_documents stringByAppendingPathComponent:@"log.txt"]]) return 99;
        if ((getenv)("DM_PROBE")) return 100;
#if OWNED_POLICY_PRESENT
        if (strcmp(getenv("DM_ROMFS"), [owned_documents stringByAppendingPathComponent:@"romfs"].fileSystemRepresentation)) return 101;
        if (getenv("DM_NVN_CBDUMP") || getenv("DM_DBG") || getenv("DM_MEMSTAT") || getenv("DM_WATCH_STALL")) return 102;
        if (!strcmp(mode, "aoc")) {
            if (!getenv("DM_AOC_DIR") || strcmp(getenv("DM_AOC_DIR"), [owned_resources stringByAppendingPathComponent:@"aoc"].fileSystemRepresentation)) return 117;
        } else if (getenv("DM_AOC_DIR")) return 118;
        if (strcmp(getenv("HOME"), argv[2]) || strcmp(getenv("TMPDIR"), argv[2])) return 103;
        if (setenv("DM_ROMFS", "/owned/later-mutation", 1)) return 104;
        if (strcmp(getenv("DM_ROMFS"), [owned_documents stringByAppendingPathComponent:@"romfs"].fileSystemRepresentation)) return 105;
#endif
        return 0;
    }
}
'''


class NormalEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='nxnative-owned-normal-entry-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.base = Path(cls.tmp.name)
        cls.stage = cls.base / 'staged'
        for name in ('dm/host/dm.h', 'dm/ios/Sources/main.m'):
            target = cls.stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(COMPONENT / 'upstream' / name, target)
        if PATCH.exists():
            applied = subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(PATCH)],
                                     cwd=cls.stage, capture_output=True, text=True)
            if applied.returncode:
                raise AssertionError('normal entry patch failed: ' + applied.stdout + applied.stderr)
        header = (cls.stage / 'dm/host/dm.h').read_text()
        if '/* NXNATIVE_NORMAL_LOG_BEGIN */' in header:
            macros = header.split('/* NXNATIVE_NORMAL_LOG_BEGIN */', 1)[1].split('/* NXNATIVE_NORMAL_LOG_END */', 1)[0]
        else:
            macros = '\n'.join(re.findall(r'^#define (?:LOG|die)\([^\n]+', header, re.M))
        source = (cls.stage / 'dm/ios/Sources/main.m').read_text()
        fragment = source[source.index('static char *g_argv[4]'):source.index('static int g_boot_ok;')]
        owned = cls.base / 'owned-entry.m'
        owned.write_text(PROBE.replace('/* ACTUAL_LOG_MACROS */', macros).replace('/* ACTUAL_ENTRY_FRAGMENT */', fragment))
        cls.probe_sha256 = hashlib.sha256(owned.read_bytes()).hexdigest()
        cls.compile_commands = []
        clang = subprocess.check_output(['xcrun', '--find', 'clang'], text=True).strip()
        sdk = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
        common = [clang, '-isysroot', sdk, '-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                  '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-DNXNATIVE_NORMAL_POLICY=1', '-I', str(ADAPTER)]
        objects = []
        if (ADAPTER / 'normal_policy.c').exists():
            obj = cls.base / 'normal-policy.o'
            command = common + ['-std=gnu11', '-Dsetenv=owned_setenv', '-Dunsetenv=owned_unsetenv',
                                '-c', str(ADAPTER / 'normal_policy.c'), '-o', str(obj)]
            built = subprocess.run(command, capture_output=True, text=True)
            cls.compile_commands.append({'argv': command, 'exit_code': built.returncode})
            if built.returncode: raise AssertionError(built.stderr)
            objects.append(str(obj))
        cls.executable = cls.base / 'owned-normal-entry'
        command = common + ['-fobjc-arc', str(owned), *objects, '-framework', 'Foundation', '-o', str(cls.executable)]
        built = subprocess.run(command, capture_output=True, text=True)
        cls.compile_commands.append({'argv': command, 'exit_code': built.returncode})
        if built.returncode: raise AssertionError(built.stderr)

    def run_owned(self, mode='boot', marker=True, aoc=False):
        base = Path(tempfile.mkdtemp(prefix='owned-paths-', dir=self.base))
        documents, resources = base / 'Documents', base / 'Resources'
        documents.mkdir(); resources.mkdir(); (resources / 'dm').mkdir()
        if aoc: (resources / 'aoc').mkdir()
        (documents / 'romfs').mkdir()
        if marker: (documents / 'romfs/OWNED_MARKER').write_bytes(b'OWNED metadata only')
        (documents / 'env.txt').write_text('DM_PROBE=OWNED_ENV_FILE\n')
        (resources / 'dm/env.txt').write_text('DM_PROBE=OWNED_BUNDLE_ENV\n')
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(documents), 'TMPDIR': str(documents),
               'DM_ROMFS': '/owned/inherited-must-be-overwritten', 'DM_NVN_CBDUMP': '/owned/not-used',
               'DM_AOC_DIR': '/owned/not-present', 'DM_DBG': '1', 'DM_MEMSTAT': 'owned',
               'DM_WATCH_STALL': '1', 'ASAN_OPTIONS': 'detect_leaks=0'}
        result = subprocess.run([str(self.executable), mode, str(documents), str(resources)], env=env,
                                capture_output=True, text=True, timeout=5)
        return result, documents

    def test_actual_log_macro_does_not_evaluate_varargs(self):
        result, _ = self.run_owned('log')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((result.stdout, result.stderr), ('', ''))

    def test_actual_boot_preserves_owned_paths_without_raw_log_or_env_file(self):
        result, _ = self.run_owned()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((result.stdout, result.stderr), ('', ''))

    def test_actual_die_macro_does_not_evaluate_varargs_and_is_a_failure(self):
        result, documents = self.run_owned('die')
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertFalse((documents / 'argument-evaluated').exists())
        self.assertEqual(result.stderr, '[nxnative normal] host failure\n')

    def test_actual_setter_failure_prevents_publish_and_preload(self):
        result, _ = self.run_owned('fail-set')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '[nxnative normal] entry preparation failed\n')

    def test_actual_missing_romfs_prevents_preload(self):
        result, _ = self.run_owned('missing', marker=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '[nxnative normal] entry preparation failed\n')

    def test_real_typed_environment_rejects_invalid_keys_before_mutation_and_publishes_owned_values_once(self):
        result, _ = self.run_owned('typed')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '[nxnative normal] entry preparation failed\n' * 7)

    def test_actual_optional_aoc_path_is_the_owned_bundle_path(self):
        result, _ = self.run_owned('aoc', aoc=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((result.stdout, result.stderr), ('', ''))

    def test_patch_inverse_restores_both_original_upstream_files(self):
        inverse = self.base / 'inverse'
        shutil.copytree(self.stage, inverse)
        run = subprocess.run(['patch', '-R', '-p1', '--batch', '--fuzz=0', '-i', str(PATCH)],
                             cwd=inverse, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        for name in ('dm/host/dm.h', 'dm/ios/Sources/main.m'):
            self.assertEqual((inverse / name).read_bytes(), (COMPONENT / 'upstream' / name).read_bytes())


if __name__ == '__main__':
    unittest.main()
