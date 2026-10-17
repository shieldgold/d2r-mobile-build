"""Actual Horizon wait/signal dispatcher on owned pthreads and owned words only."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
VENDOR = COMPONENT / 'upstream'
PATCH = COMPONENT / 'patches/horizon-address-signal.patch'
BASELINE = os.environ.get('NXNATIVE_ADDRESS_SIGNAL_BASELINE') == '1'

PROBE = r'''
#include <assert.h>
#include <stdatomic.h>
#include <unistd.h>
/* The whole actual dispatcher and original wait-list/CAS/block/wake code.
   No CreateThread/Guest entry, diagnostics, signal handler or VM path is run. */
#include "actual_hle_thread.c"
#define CHECK(c) do { if (!(c)) { fprintf(stderr,"owned address contract line %d\n",__LINE__);return 91; } } while (0)
typedef struct {
    thr_t thread;
    pthread_t pthread;
    _Atomic int done;
    int64_t result;
    _Atomic int32_t *address;
} owned_waiter;
static _Atomic int32_t word, other_word;
static owned_waiter waiters[4];
static int waiter_count;
static int registered(void) {
    int count = 0;
    pthread_mutex_lock(&G);
    for (thr_t *t = g_wait; t; t = t->next) count++;
    pthread_mutex_unlock(&G);
    return count;
}
static int wait_registered(int count) {
    uint64_t deadline = now_ns() + 2000000000ull;
    do {
        if (registered() == count) return 1;
        struct timespec delay = {0, 1000000}; nanosleep(&delay, NULL);
    } while (now_ns() < deadline);
    return 0;
}
static void *owned_wait(void *pointer) {
    owned_waiter *waiter = pointer;
    assert(!pthread_setspecific(g_keythr, &waiter->thread));
    uint64_t regs[32] = {(uint64_t)waiter->address, 2, 41, (uint64_t)-1};
    assert(svc_thread(0x34, regs) == 1);
    waiter->result = (int64_t)regs[0];
    atomic_store(&waiter->done, 1);
    return NULL;
}
static int start_waiter(_Atomic int32_t *address, int priority) {
    owned_waiter *waiter = &waiters[waiter_count++];
    waiter->address = address;
    waiter->thread.prio = priority;
    waiter->thread.mask = 7;
    if (pthread_cond_init(&waiter->thread.cv, NULL)) return 0;
    if (pthread_create(&waiter->pthread, NULL, owned_wait, waiter)) return 0;
    return wait_registered(waiter_count);
}
static int64_t signal_word(_Atomic int32_t *address, int type, int value, int count) {
    uint64_t regs[32] = {(uint64_t)address, (uint32_t)type, (uint32_t)value, (uint32_t)count};
    assert(svc_thread(0x35, regs) == 1);
    return (int64_t)regs[0];
}
static int finish_waiters(void) {
    CHECK(!registered());
    for (int i = 0; i < waiter_count; i++) {
        CHECK(!pthread_join(waiters[i].pthread, NULL));
        CHECK(atomic_load(&waiters[i].done) && waiters[i].result == 0);
        CHECK(!pthread_cond_destroy(&waiters[i].thread.cv));
    }
    return 0;
}
int main(int argc, char **argv) {
    CHECK(argc == 2);
    CHECK(!pthread_key_create(&g_keythr, NULL));
    CHECK(!setenv("DM_PREEMPT", "0", 1));
    atomic_store(&word, 41); atomic_store(&other_word, 41);
    const char *mode = argv[1];
    if (!strcmp(mode, "all-zero") || !strcmp(mode, "all-negative")) {
        CHECK(start_waiter(&word, 30) && start_waiter(&word, 10) && start_waiter(&word, 20));
        CHECK(signal_word(&word, 2, 41, !strcmp(mode,"all-zero") ? 0 : -1) == 0);
        CHECK(atomic_load(&word) == 40 && !registered());
    } else if (!strcmp(mode, "none")) {
        CHECK(signal_word(&word, 2, 41, 0) == 0 && atomic_load(&word) == 42);
        atomic_store(&word, 41);
        CHECK(signal_word(&word, 2, 41, -1) == 0 && atomic_load(&word) == 42);
        atomic_store(&word, 41);
        CHECK(signal_word(&word, 2, 41, 2) == 0 && atomic_load(&word) == 42);
    } else if (!strcmp(mode, "bounded-fewer")) {
        CHECK(start_waiter(&word, 30) && start_waiter(&word, 10) && start_waiter(&word, 20));
        CHECK(signal_word(&word, 2, 41, 1) == 0 && atomic_load(&word) == 41 && registered() == 2);
        pthread_mutex_lock(&G);
        int first = waiters[1].thread.woke && !waiters[0].thread.woke && !waiters[2].thread.woke;
        pthread_mutex_unlock(&G); CHECK(first);
        CHECK(signal_word(&word, 2, 41, 1) == 0 && atomic_load(&word) == 41 && registered() == 1);
        pthread_mutex_lock(&G);
        int second = waiters[2].thread.woke && !waiters[0].thread.woke;
        pthread_mutex_unlock(&G); CHECK(second);
        CHECK(signal_word(&word, 2, 41, 1) == 0 && atomic_load(&word) == 40 && !registered());
    } else if (!strcmp(mode, "bounded-equal") || !strcmp(mode, "bounded-more")) {
        CHECK(start_waiter(&word, 30) && start_waiter(&word, 10));
        CHECK(signal_word(&word, 2, 41, !strcmp(mode,"bounded-equal") ? 2 : 3) == 0);
        CHECK(atomic_load(&word) == 40 && !registered());
    } else if (!strcmp(mode, "cas-all") || !strcmp(mode, "cas-bounded")) {
        CHECK(start_waiter(&word, 30) && start_waiter(&word, 10));
        CHECK(signal_word(&word, 2, 40, !strcmp(mode,"cas-all") ? 0 : 1) == R_BAD_STATE);
        CHECK(atomic_load(&word) == 41 && registered() == 2);
        CHECK(!atomic_load(&waiters[0].done) && !atomic_load(&waiters[1].done));
        CHECK(signal_word(&word, 0, 0, 0) == 0 && atomic_load(&word) == 41);
    } else if (!strcmp(mode, "other-address")) {
        CHECK(start_waiter(&word, 30) && start_waiter(&other_word, 10));
        CHECK(signal_word(&word, 2, 41, 0) == 0);
        CHECK(atomic_load(&word) == 40 && atomic_load(&other_word) == 41 && registered() == 1);
        CHECK(!atomic_load(&waiters[1].done));
        CHECK(signal_word(&other_word, 0, 0, 0) == 0);
    } else if (!strcmp(mode, "type-zero") || !strcmp(mode, "type-one")) {
        CHECK(start_waiter(&word, 30) && start_waiter(&word, 10));
        int type = !strcmp(mode,"type-zero") ? 0 : 1;
        CHECK(signal_word(&word, type, 41, 0) == 0);
        CHECK(atomic_load(&word) == (type ? 42 : 41) && !registered());
    } else return 92;
    CHECK(finish_waiters() == 0);
    CHECK(!pthread_key_delete(g_keythr));
    return 0;
}
'''


@unittest.skipUnless(sys.platform == 'darwin', 'complete upstream thread TU requires Darwin')
class NxnativeAddressSignal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='owned-nxnative-address-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        target = cls.directory / 'dm/host/hle_thread.c'
        target.parent.mkdir(parents=True)
        shutil.copyfile(VENDOR / 'dm/host/hle_thread.c', target)
        if not BASELINE:
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PATCH)],
                                    cwd=cls.directory, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        shutil.copyfile(target, cls.directory / 'actual_hle_thread.c')
        # This constant profile setting only disables unrelated scheduling
        # approximation; no wait/CAS/pick/block/wake function is replaced.
        (cls.directory / 'game_profile.h').write_text('#define DM_GAME_CORE_PREEMPT 0\n')
        (cls.directory / 'owned.c').write_text(PROBE)
        cls.binary = cls.directory / 'owned'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-unused-function', '-pthread', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-fno-omit-frame-pointer',
                   '-I', str(cls.directory), '-I', str(VENDOR / 'dm/host'),
                   str(cls.directory / 'owned.c'),
                   str(VENDOR / 'dm/host/tests/t_condvar_stub.c'), '-o', str(cls.binary)]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def mode(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True,
                                timeout=10, env={**os.environ,
                                    'ASAN_OPTIONS': 'detect_leaks=0:halt_on_error=1',
                                    'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=0'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')

    def test_wake_all_with_waiters_decrements_once(self):
        for name in ('all-zero', 'all-negative'):
            with self.subTest(name=name): self.mode(name)

    def test_no_waiters_increments_for_all_count_forms(self): self.mode('none')

    def test_bounded_count_preserves_value_until_last_waiter_and_priority_order(self):
        self.mode('bounded-fewer')

    def test_count_equal_or_larger_than_waiters_decrements_once(self):
        for name in ('bounded-equal', 'bounded-more'):
            with self.subTest(name=name): self.mode(name)

    def test_failed_compare_exchange_neither_updates_nor_wakes(self):
        for name in ('cas-all', 'cas-bounded'):
            with self.subTest(name=name): self.mode(name)

    def test_waiters_at_other_address_are_not_counted_or_woken(self): self.mode('other-address')

    def test_plain_wake_and_increment_modes_keep_original_behavior(self):
        for name in ('type-zero', 'type-one'):
            with self.subTest(name=name): self.mode(name)

    def test_patch_inverse_restores_complete_upstream_thread_source(self):
        target = self.directory / 'dm/host/hle_thread.c'
        if not BASELINE:
            subprocess.run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-i', str(PATCH)],
                           cwd=self.directory, check=True, capture_output=True)
        self.assertEqual(target.read_bytes(), (VENDOR / 'dm/host/hle_thread.c').read_bytes())
        if not BASELINE:
            subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PATCH)],
                           cwd=self.directory, check=True, capture_output=True)


if __name__ == '__main__': unittest.main()
