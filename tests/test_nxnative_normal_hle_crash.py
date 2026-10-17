"""Owned CPU calls into actual patched HLE/crash code; no Guest or host runs."""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
ADAPTER = COMPONENT / 'adapter'
PATCH = COMPONENT / 'patches/normal-hle-crash.patch'
MEMBERS = ('dm/host/dm.h', 'dm/host/hle.c', 'dm/host/hle_svc.c',
           'dm/host/hle_sdk16.c', 'dm/host/crash.c', 'dm/ios/Sources/main.m')


def function_span(source, signature):
    """Find a C body, ignoring braces in literals/comments (owned extraction only)."""
    start = source.index(signature)
    opening = source.index('{', start)
    depth, state, i = 0, 'code', opening
    while i < len(source):
        c, pair = source[i], source[i:i + 2]
        if state == 'code':
            if pair in ('/*', '//'):
                state = 'block' if pair == '/*' else 'line'
                i += 2
                continue
            if c in ('"', "'"):
                state = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if not depth:
                    return start, i + 1
        elif state == 'block' and pair == '*/':
            state = 'code'
            i += 2
            continue
        elif state == 'line' and c == '\n':
            state = 'code'
        elif state in ('"', "'"):
            if c == '\\':
                i += 2
                continue
            if c == state:
                state = 'code'
        i += 1
    raise AssertionError('unterminated actual C function: ' + signature)


PROBE = r'''
#include "dm.h"
#include <errno.h>
#include <signal.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include <sys/ucontext.h>
#include <unistd.h>
/* New helpers are only referenced after the production patch exists. */
#if __has_include("normal_failures.h")
#include "normal_failures.h"
#endif
module_t g_mod[MAX_MOD]; int g_nmod;
struct dm_flags g_flag;
static int owned_mem_init_calls, owned_thread_calls, owned_enter, owned_leave;
static int owned_host_calls, owned_fault_calls, owned_write_calls, owned_actions;
static int owned_dirty_handled;
static void *owned_forbidden, *owned_expected_uc;
uint64_t guest_ns(void) { return 62500; }
uint64_t dm_entropy64(void) { return 5; }
module_t *module_of(const void *p) { (void)p; _exit(96); }
int svc_thread(int n, uint64_t *r) { (void)n; (void)r; owned_thread_calls++; return 0; }
void hlecount_svc(int n) { (void)n; }
uint64_t svc_host_call(int n, uint64_t *r) { (void)n; (void)r; owned_host_calls++; _exit(96); }
void thr_svc_enter(uint64_t *r, uint64_t pc) { (void)r; (void)pc; owned_enter++; }
void thr_svc_leave(void) { owned_leave++; }
/* ACTUAL_HLE */
/* ACTUAL_SVC_WITH_OWNED_MEM_INIT */
#define HLE_FORCE(symbol, why, result, name, ...) result name(__VA_ARGS__)
/* ACTUAL_DIAG_WRAPPERS */
#undef HLE_FORCE
static int owned_sigaction(int sig, const struct sigaction *action, struct sigaction *old) {
    (void)old;
    if (sig == SIGALRM || !action || !(action->sa_flags & SA_ONSTACK) ||
        !(action->sa_flags & SA_SIGINFO)) _exit(97);
    owned_actions++;
    return 0;
}
#define sigaction(...) owned_sigaction(__VA_ARGS__)
#include "crash.c"
#undef sigaction

static void forbidden_read(int signal_number) { (void)signal_number; _exit(95); }
static void install_owned_tripwire(void) {
    struct sigaction sa = {.sa_handler = forbidden_read};
    if (sigaction(SIGSEGV, &sa, NULL) || sigaction(SIGBUS, &sa, NULL)) _exit(98);
    owned_forbidden = mmap(NULL, (size_t)getpagesize(), PROT_NONE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (owned_forbidden == MAP_FAILED) _exit(99);
}
static int owned_fault(void *addr, int write, void *uc) {
    if (addr != owned_forbidden || write != 1 || uc != owned_expected_uc) _exit(100);
    owned_fault_calls++;
    return owned_dirty_handled;
}
static void owned_write(void *addr, size_t size) {
    if (addr != owned_forbidden || size != 7) _exit(101);
    owned_write_calls++;
}
static uint64_t *one_accessible_word(void) {
    size_t page = (size_t)getpagesize();
    char *p = mmap(NULL, page * 2, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (p == MAP_FAILED || mprotect(p + page, page, PROT_NONE)) _exit(102);
    return (uint64_t *)(p + page - sizeof(uint64_t));
}
static void owned_signal_call(int sig, int code, int handled) {
    size_t page = (size_t)getpagesize();
    char *p = mmap(NULL, page * 2, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (p == MAP_FAILED || mprotect(p + page, page, PROT_NONE)) _exit(103);
    /* Only the real dirty-fault ESR field is accessible. __ss is PROT_NONE. */
    mcontext_t mc = (mcontext_t)(p + page - offsetof(struct __darwin_mcontext64, __ss));
    mc->__es.__esr = 1u << 6;
    ucontext_t context = {0}; context.uc_mcontext = mc;
    siginfo_t info = {0}; info.si_code = code; info.si_addr = owned_forbidden;
    owned_expected_uc = &context; owned_dirty_handled = handled;
    dm_fault_hook_register(owned_fault);
    dm_guest_write_prep_register(owned_write);
    dm_guest_write_prepare(owned_forbidden, 7);
    on_crash(sig, &info, &context);
    if (!handled || owned_fault_calls != 1 || owned_write_calls != 1) _exit(104);
}
int main(int argc, char **argv) {
    if (argc != 2) return 90;
    install_owned_tripwire();
    const char *mode = argv[1];
    if (!strcmp(mode, "unimplemented")) { unimpl_c(0, owned_forbidden); return 91; }
    if (!strcmp(mode, "unknown-svc")) { svc_dispatch(0x7f, owned_forbidden, 0); return 91; }
    if (!strcmp(mode, "private-svc")) { svc_dispatch(0x80, owned_forbidden, 0); return 91; }
    if (!strcmp(mode, "put")) { h_diag_Put(owned_forbidden, owned_forbidden, 1); return 0; }
    if (!strcmp(mode, "abort0")) { h_diag_Abort0(); return 91; }
    if (!strcmp(mode, "abort-result")) { h_diag_AbortR(owned_forbidden); return 91; }
    if (!strcmp(mode, "obs-register")) { gate_register(0x4000, "obs", 1, 0); return 91; }
    if (!strcmp(mode, "private-register")) { gate_register(0x4000, "svc", 0x80, 0); return 91; }
    if (!strcmp(mode, "obs-dispatch")) {
        g_gate[gate_h(0x4000)] = (gate_t){0x4000, G_OBS, 1, 0};
        gate_dispatch(0x4000, owned_forbidden); return 91;
    }
    if (!strcmp(mode, "obs-attach")) { gate_obs_set(1, owned_forbidden); return 91; }
    if (!strcmp(mode, "notification") || !strcmp(mode, "fatal-break")) {
        uint64_t *r = one_accessible_word();
        *r = !strcmp(mode, "notification") ? 0x80000000ull : 0;
        uint64_t before = *r;
        svc_dispatch(0x26, r, 0);
        return *r == before && owned_thread_calls == 1 && owned_mem_init_calls == 1 ? 0 : 105;
    }
    if (!strcmp(mode, "debug-string")) {
        uint64_t *r = one_accessible_word(); *r = (uint64_t)(uintptr_t)owned_forbidden;
        g_flag.dbg = 1;
        svc_dispatch(0x27, r, 0);
        return *r == 0 && owned_thread_calls == 1 ? 0 : 106;
    }
    if (!strcmp(mode, "horizon-and-counter")) {
        uint64_t r[31] = {0};
        gate_register(0x4000, "svc", 0x29, 0x1234);
        r[1] = 0;
        if (gate_dispatch(0x4000, r) || r[0] || r[1] != 7 ||
            owned_enter != 1 || owned_leave != 1 || owned_mem_init_calls != 1 ||
            owned_thread_calls != 1 || owned_host_calls) return 107;
        gate_register(0x4004, "cntpct", 4, 0);
        gate_register(0x4008, "cntvct", 5, 0);
        if (gate_dispatch(0x4004, r) != 1200 || r[4] != 1200 ||
            gate_dispatch(0x4008, r) != 1200 || r[5] != 1200 || !gate_svc_present(0x29)) return 108;
        return 0;
    }
    if (!strcmp(mode, "dirty-handled")) { owned_signal_call(SIGSEGV, SEGV_ACCERR, 1); return 0; }
    if (!strcmp(mode, "mapping")) { owned_signal_call(SIGSEGV, SEGV_MAPERR, 0); return 91; }
    if (!strcmp(mode, "protection")) { owned_signal_call(SIGSEGV, SEGV_ACCERR, 0); return 91; }
    if (!strcmp(mode, "bus")) { owned_signal_call(SIGBUS, SEGV_ACCERR, 0); return 91; }
    if (!strcmp(mode, "unknown-code")) { owned_signal_call(SIGSEGV, 0, 0); return 91; }
    if (!strcmp(mode, "other-signal")) {
        siginfo_t info = {0}; on_crash(SIGILL, &info, owned_forbidden); return 91;
    }
    if (!strcmp(mode, "trap")) {
        crash_set_trap_hook(owned_forbidden);
        siginfo_t info = {0}; on_crash(SIGTRAP, &info, owned_forbidden); return 91;
    }
    if (!strcmp(mode, "backtrace")) { dm_backtrace((uint64_t)(uintptr_t)owned_forbidden); return 91; }
    if (!strcmp(mode, "offset")) { crash_off(owned_forbidden, 0); return 91; }
    if (!strcmp(mode, "dump")) { crash_dump_now(owned_forbidden); return 91; }
    if (!strcmp(mode, "watch-and-altstack")) {
        crash_set_dump_hook(owned_forbidden);
        crash_set_wait_dump(owned_forbidden);
        crash_set_trap_hook(owned_forbidden);
        crash_watch_progress(7, owned_forbidden);
        crash_watch_frame(); crash_watch_pregame_done(); crash_watch_pregame_again();
        if (crash_watch_progress_count() || gate_obs_present(1)) return 109;
        crash_install();
        void *first = pthread_getspecific(g_altstack_key);
        crash_thread_init();
        stack_t ss = {0};
        if (!first || first != pthread_getspecific(g_altstack_key) ||
            sigaltstack(NULL, &ss) || ss.ss_size != 65536 || ss.ss_sp != first || owned_actions != 4) return 110;
        return 0;
    }
    return 111;
}
'''


class NormalHleCrash(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='nxnative-owned-normal-hle-crash-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.base = Path(cls.tmp.name)
        cls.stage = cls.base / 'staged'
        cls.source_before = {}
        for name in MEMBERS:
            raw = (COMPONENT / 'upstream' / name).read_bytes()
            cls.source_before[name] = hashlib.sha256(raw).hexdigest()
            target = cls.stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        cls.applied = []
        for patch in (COMPONENT / 'patches/normal-entry.patch', PATCH):
            if not patch.exists():
                if patch != PATCH:
                    raise AssertionError('the frozen normal-entry predecessor is required')
                continue
            applied = subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(patch)],
                                     cwd=cls.stage, capture_output=True, text=True)
            if applied.returncode:
                raise AssertionError('actual patch failed: ' + applied.stdout + applied.stderr)
            cls.applied.append(patch)
        cls.patched_hashes = {name: hashlib.sha256((cls.stage / name).read_bytes()).hexdigest()
                              for name in MEMBERS}
        host = cls.stage / 'dm/host'
        # Compile-only owned constants. mem_init is the one explicit fixture seam;
        # no AS reservation or Guest instruction is executed by these tests.
        (host / 'game_profile.h').write_text('#pragma once\n#define DM_GAME_MEM_RESERVE (5ull << 30)\n'
            '#define DM_GAME_MEM_ALIAS (512ull << 20)\n#define DM_GAME_MEM_STACK (256ull << 20)\n'
            '#define DM_GAME_APP_POOL (3285ull << 20)\n#define DM_GAME_PROGRAM_ID 0\n')
        hle = (host / 'hle.c').read_text()
        stubs = hle[hle.index('#define MAX_STUB'):hle.index('// Per module, one cache')]
        gates = hle[hle.index('enum { G_SVC,'):]
        svc = (host / 'hle_svc.c').read_text()
        a, b = function_span(svc, 'static void mem_init(void)')
        svc = svc[:a] + 'static void mem_init(void) { owned_mem_init_calls++; }' + svc[b:]
        sdk = (host / 'hle_sdk16.c').read_text()
        diag = sdk[sdk.index('// ---------- diag ----------'):sdk.index('// ---------- settings:')]
        owned = cls.base / 'owned-normal-hle-crash.c'
        owned.write_text(PROBE.replace('/* ACTUAL_HLE */', stubs + gates)
            .replace('/* ACTUAL_SVC_WITH_OWNED_MEM_INIT */', svc)
            .replace('/* ACTUAL_DIAG_WRAPPERS */', diag))
        cls.probe_sha256 = hashlib.sha256(owned.read_bytes()).hexdigest()
        cls.compile_commands = []
        cls.runtime_cases = []
        clang = subprocess.check_output(['xcrun', '--find', 'clang'], text=True).strip()
        sdk_path = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
        common = [clang, '-isysroot', sdk_path, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                  '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-parameter',
                  '-Wno-unused-but-set-variable', '-Wno-frame-address',
                  '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-DNXNATIVE_NORMAL_POLICY=1',
                  '-I', str(host), '-I', str(ADAPTER)]
        # The unused warnings concern unexercised upstream branches whose LOG macro
        # is deliberately removed in normal mode; all other enabled warnings are errors.
        cls.executable = cls.base / 'owned-normal-hle-crash'
        sources = [str(owned), str(ADAPTER / 'normal_policy.c')]
        if (ADAPTER / 'normal_failures.c').exists():
            sources.append(str(ADAPTER / 'normal_failures.c'))
        command = common + sources + ['-o', str(cls.executable)]
        built = subprocess.run(command, capture_output=True, text=True)
        cls.compile_commands.append({'argv': command, 'exit_code': built.returncode})
        if built.returncode:
            raise AssertionError(built.stderr)

    def owned(self, mode, expected=0, message=b''):
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'ASAN_OPTIONS': 'detect_leaks=0:handle_segv=0:handle_sigbus=0',
               'DM_WATCH': '1', 'DM_WATCH_STALL': '1', 'DM_WATCH_SAMPLE': '/owned/never-created'}
        result = subprocess.run([str(self.executable), mode], capture_output=True, env=env, timeout=5)
        self.runtime_cases.append({'mode': mode, 'exit_code': result.returncode,
                                   'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr),
                                   'stderr_sha256': hashlib.sha256(result.stderr).hexdigest()})
        # Never publish unexpected bytes, even if testing an old diagnostic producer.
        self.assertEqual(result.returncode, expected,
                         f'{mode}: return={result.returncode}, stderr_sha256={hashlib.sha256(result.stderr).hexdigest()}')
        self.assertEqual(result.stdout, b'')
        self.assertEqual(hashlib.sha256(result.stderr).digest(), hashlib.sha256(message).digest(), mode)

    def test_unimplemented_import_fails_before_register_access(self):
        self.owned('unimplemented', 3, b'[nxnative normal] unimplemented import\n')

    def test_unknown_svc_fails_before_frame_access(self):
        self.owned('unknown-svc', 3, b'[nxnative normal] unimplemented svc\n')

    def test_sdk_put_discards_without_string_access(self):
        self.owned('put')

    def test_observation_and_private_gates_are_closed(self):
        for mode in ('obs-register', 'private-register', 'obs-dispatch', 'obs-attach', 'private-svc'):
            with self.subTest(mode=mode):
                self.owned(mode, 77, b'[nxnative normal] diagnostic entry disabled\n')

    def test_sdk_abort_does_not_read_result_or_call_site(self):
        for mode in ('abort0', 'abort-result'):
            with self.subTest(mode=mode):
                self.owned(mode, 70, b'[nxnative normal] sdk abort\n')

    def test_notification_break_debug_string_and_real_horizon_counter_contracts(self):
        for mode in ('notification', 'debug-string', 'horizon-and-counter'):
            with self.subTest(mode=mode):
                self.owned(mode)
        self.owned('fatal-break', 42, b'[nxnative normal] guest requested fatal break\n')

    def test_dirty_fault_and_host_write_hooks_remain_first(self):
        self.owned('dirty-handled')

    def test_unhandled_signal_reads_no_full_context(self):
        for mode, expected, literal in (
                ('mapping', 128 + 11, b'mapping fault'),
                ('protection', 128 + 11, b'protection fault'),
                ('bus', 128 + 10, b'signal'),
                ('unknown-code', 128 + 11, b'signal'),
                ('other-signal', 128 + 4, b'signal'),
                ('trap', 128 + 5, b'signal')):
            with self.subTest(mode=mode):
                self.owned(mode, expected, b'[nxnative normal] unhandled host ' + literal + b'\n')

    def test_explicit_dump_entries_are_fixed_failures(self):
        for mode in ('backtrace', 'offset', 'dump'):
            with self.subTest(mode=mode):
                self.owned(mode, 77, b'[nxnative normal] diagnostic entry disabled\n')

    def test_watch_callbacks_are_inert_and_altstack_service_is_preserved(self):
        self.owned('watch-and-altstack')

    def test_actual_patch_inverse_restores_every_original_byte(self):
        if not PATCH.exists():
            self.fail('normal HLE/crash implementation has not been applied')
        reverse = self.base / 'reverse'
        shutil.copytree(self.stage, reverse)
        for patch in reversed(self.applied):
            result = subprocess.run(['patch', '-R', '-p1', '--batch', '--fuzz=0', '-i', str(patch)],
                                    cwd=reverse, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in MEMBERS:
            self.assertEqual((reverse / name).read_bytes(), (COMPONENT / 'upstream' / name).read_bytes(), name)


if __name__ == '__main__':
    unittest.main()
