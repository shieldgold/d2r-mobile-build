"""Real runtime child TSD/TLR setup, guest stacks, rewritten ExitThread gates."""
import platform
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import test_botw_runtime as fixture

ROOT = fixture.ROOT
DRIVER = r'''
#include "RUNTIME_SOURCE"
#include <assert.h>
#include <stdatomic.h>
typedef struct {
    uint64_t tls, tlr, gate, thread_object, stack, handle, tls_value, tlr_value;
    uint32_t release, pad0, ready, pad1;
    uint64_t k18;
    uint32_t progress, pad2, gate_attempted, pad3;
} Evidence;
static _Atomic unsigned focus_stop_seen;
static void event_log(const char *message) {
    if (strstr(message, "guest execution stopped at focus boundary")) atomic_fetch_add(&focus_stop_seen, 1);
    puts(message);
}
static uint64_t host_now(void) {
    struct timespec t; assert(!clock_gettime(CLOCK_MONOTONIC, &t));
    return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
static void svc(unsigned number, uint64_t *registers) {
    char error[512]; int result = botw_kernel_dispatch(state.kernel, number, registers, error, sizeof error);
    if (result != BOTW_KERNEL_HANDLED) fprintf(stderr, "svc %x failed: %s\n", number, error);
    assert(result == BOTW_KERNEL_HANDLED && registers[0] == 0);
}
static uint64_t region(unsigned id) {
    uint64_t r[32] = {0, id, BOTW_KERNEL_CURRENT_PROCESS}; svc(0x29, r); return r[1];
}
int main(int argc, char **argv) {
    assert(argc == 3); memset(&state, 0, sizeof state);
    state.owner = pthread_self(); state.event = event_log;
    if (sigsetjmp(state.recovery, 1)) return 10;
    install_signal_boundary(); state.active = 1;
    for (unsigned i = 0; i < MODULE_COUNT; i++) {
        Module *m = &state.modules[i]; m->name = module_names[i];
        read_layout(m, argv[1]); map_module(m, argv[1]); parse_dynamic(m); register_gates(m, argv[1]);
    }
    initialize_keys();
    *(uint64_t *)state.tls = 0xdeadbeef; *(uint64_t *)state.tlr = 0xbeef;
    assert(!pthread_setspecific(state.keys[0], (void *)0x5555));
    const botw_kernel_callbacks callbacks = {.log = services_log, .memory = external_memory,
        .executable = executable_address, .execute_thread = execute_child};
    state.kernel = botw_kernel_create(&callbacks, NULL); assert(state.kernel);
    char error[512]; const size_t unit = 0x200000;
    assert(!botw_kernel_configure_memory(state.kernel, 4 * unit, 4 * unit, unit,
        4 * unit, 0, error, sizeof error));
    uint64_t heap = region(4), stack = region(14), r[32] = {0, unit * 3}; svc(1, r);
    Evidence *evidence = (Evidence *)(uintptr_t)(heap + unit * 2);
    uint32_t workers[2]; unsigned count = !strcmp(argv[2], "normal") ? 2 : 1;
    int focus_case = !strncmp(argv[2], "focus", 5);
    if (focus_case) assert(!services_focus(NULL, 0, 0, 0, 1, error, sizeof error));
    for (unsigned i = 0; i < count; i++) {
        r[0] = stack + i * unit; r[1] = heap + i * unit; r[2] = unit; svc(4, r);
        r[1] = (uintptr_t)state.modules[5].base + 0x200; r[2] = (uintptr_t)&evidence[i];
        r[3] = stack + (i + 1) * unit; r[4] = 44; r[5] = (uint64_t)-2; svc(8, r);
        workers[i] = (uint32_t)r[1]; r[0] = workers[i]; svc(9, r);
    }
    uint64_t deadline = host_now() + 2000000000;
    if (count == 2) {
        while ((!__atomic_load_n(&evidence[0].ready, __ATOMIC_ACQUIRE) ||
                !__atomic_load_n(&evidence[1].ready, __ATOMIC_ACQUIRE)) && host_now() < deadline) sched_yield();
        for (unsigned i = 0; i < count; i++) {
            Evidence *e = &evidence[i]; assert(e->ready == 1);
            assert(e->tls && e->tlr && e->tls != (uintptr_t)state.tls && e->tlr != (uintptr_t)state.tlr);
            assert(e->gate == (uintptr_t)botw_gate && e->thread_object == e->tlr + 0x200);
            assert(e->stack > stack + i * unit && e->stack < stack + (i + 1) * unit);
            assert(e->handle == workers[i] && e->tls_value == 0x31 && e->tlr_value == 0x42 && e->k18 == 0);
        }
        assert(evidence[0].tls != evidence[1].tls && evidence[0].tlr != evidence[1].tlr);
        assert(*(uint64_t *)state.tls == 0xdeadbeef && *(uint64_t *)state.tlr == 0xbeef);
        assert(pthread_getspecific(state.keys[0]) == (void *)0x5555);
        for (unsigned i = 0; i < count; i++) __atomic_store_n(&evidence[i].release, 1, __ATOMIC_RELEASE);
        for (unsigned i = 0; i < count; i++) {
            uint64_t wait[32] = {0, (uintptr_t)&workers[i], 1, 1000000000}; svc(0x18, wait);
            r[0] = stack + i * unit; r[1] = heap + i * unit; r[2] = unit; svc(5, r);
        }
        puts("runtime_child_context_isolated=true rewritten_exit_joined=true");
    } else if (focus_case) {
        while (!__atomic_load_n(&evidence[0].ready, __ATOMIC_ACQUIRE) && host_now() < deadline) sched_yield();
        assert(evidence[0].ready == 1);
        __atomic_store_n(&evidence[0].release, 1, __ATOMIC_RELEASE);
        while (!__atomic_load_n(&evidence[0].gate_attempted, __ATOMIC_ACQUIRE) && host_now() < deadline) sched_yield();
        assert(evidence[0].gate_attempted == 1);
        struct timespec paused = {.tv_nsec = 30000000}; assert(!nanosleep(&paused, NULL));
        assert(!__atomic_load_n(&evidence[0].progress, __ATOMIC_ACQUIRE));
        assert(!atomic_load(&focus_stop_seen));
        if (!strcmp(argv[2], "focus-stop")) {
            uint64_t start = host_now(); assert(!cleanup());
            assert(host_now() - start < 1000000000 && !state.kernel && !state.fault_signal);
            assert(atomic_load(&focus_stop_seen) == 1);
            puts("focus_parked_child_cleanup_joined=true main_boundary_untouched=true");
            return 0;
        }
        assert(!services_focus(NULL, 0, 1, 1, 0, error, sizeof error));
        uint64_t wait[32] = {0, (uintptr_t)&workers[0], 1, 1000000000}; svc(0x18, wait);
        assert(__atomic_load_n(&evidence[0].progress, __ATOMIC_ACQUIRE) == 1);
        assert(!atomic_load(&focus_stop_seen));
        puts("focus_gate_paused_then_resumed=true");
    } else {
        int result;
        do { result = botw_kernel_dispatch(state.kernel, 0x1e, r, error, sizeof error); }
        while (result == BOTW_KERNEL_HANDLED && host_now() < deadline);
        assert(result == BOTW_KERNEL_ERROR && strstr(error, "guest child CPU fault"));
        printf("child_fault_recovered=true detail=%s\n", error);
    }
    int stopped;
    do { stopped = botw_kernel_shutdown(state.kernel, error, sizeof error); if (stopped) sched_yield(); }
    while (stopped && host_now() < deadline);
    assert(!stopped); state.active = 0; assert(!cleanup()); return 0;
}
'''


def guest_child(raw, *, fault=False, focus=False):
    """Encode a tiny rewritten A64 thread; neither game code nor headers."""
    def ldr(rt, rn, offset=0):
        return 0xf9400000 | ((offset // 8) << 10) | (rn << 5) | rt

    def store(rt, rn, offset=0):
        return 0xf9000000 | ((offset // 8) << 10) | (rn << 5) | rt

    def add(rd, rn, rm):
        return 0x8b000000 | (rm << 16) | (rn << 5) | rd

    words = [0xaa0003f3, 0xaa0103f4, 0xd53bd069, 0x9000006a]  # x19=arg,x20=handle,TSD,key page
    for key_offset, target, evidence_offset in ((8, 14, 0), (16, 12, 8), (24, 13, 16), (0, 13, 80)):
        words += [ldr(11, 10, key_offset), add(11, 9, 11), ldr(target, 11), store(target, 19, evidence_offset)]
    words += [0xd280062f, store(15, 14), 0xd280084f, store(15, 12),
              ldr(13, 12, 0x1f8), store(13, 19, 24), 0x910003ed, store(13, 19, 32),
              store(20, 19, 40), ldr(13, 14), store(13, 19, 48), ldr(13, 12), store(13, 19, 56)]
    words += [0x91000000 | (72 << 10) | (19 << 5) | 21, 0x5280002d,
              0x889ffc00 | (21 << 5) | 13]  # release store ready=1
    words += [0x91000000 | (64 << 10) | (19 << 5) | 21,
              0x88dffc00 | (21 << 5) | 13,
              0x34000000 | (((-1) & 0x7ffff) << 5) | 13]  # acquire spin until host release
    if focus:
        # Publish attempted=1 before entering the genuine rewritten host gate.
        words += [0x91000000 | (96 << 10) | (19 << 5) | 21, 0x5280002d,
                  0x889ffc00 | (21 << 5) | 13, 0xd2800000]
        pc = 0x200 + len(words) * 4
        words += [0x94000000 | (((0x10040 - pc) // 4) & 0x3ffffff)]
        words += [0x91000000 | (88 << 10) | (19 << 5) | 21, 0x5280002d,
                  0x889ffc00 | (21 << 5) | 13]  # progress=1 only after gate returns
    if fault:
        words = [0xd4200000]
    else:
        pc = 0x200 + len(words) * 4
        words += [0x14000000 | (((0x10000 - pc) // 4) & 0x3ffffff)]
    struct.pack_into('<' + 'I' * len(words), raw, 0x200, *words)
    # Same TSD-based rewrite gate used in the existing loader/IPC fixtures.
    imm = (-4) & ((1 << 21) - 1)
    adrp = 0x9000001d | ((imm & 3) << 29) | ((imm >> 2) << 5)
    gate = (0xa9bf7bfd, 0xd53bd07e, adrp, 0xf9400fbd, 0x8b1d03de,
            0xf94003de, 0xf94003fd, 0xd63f03c0, 0xa8c17bfd, 0xd4200000)
    struct.pack_into('<' + 'I' * len(gate), raw, 0x10000, *gate)
    if focus:
        returning_gate = (*gate[:-1], 0xd65f03c0)
        struct.pack_into('<' + 'I' * len(returning_gate), raw, 0x10040, *returning_gate)


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'native child runtime tests require Apple ARM64')
class RuntimeThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-runtime-child-')
        cls.exe = Path(cls.build.name) / 'runtime-child'
        driver = Path(cls.build.name) / 'runtime-child.c'
        driver.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        sources = [driver, *(ROOT / 'native/botw_host' / name for name in ('entry.S', 'services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')),
                   ROOT / 'native/botw_resources/savefs.c', ROOT / 'native/botw_resources/local_user.c',
                   ROOT / 'native/botw_input/input.c', ROOT / 'native/botw_resources/romfs.c']
        result = subprocess.run(['xcrun', 'clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O1', '-g',
                                 *(str(source) for source in sources), '-o', str(cls.exe)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_guest(self, *, fault=False, focus=None):
        with tempfile.TemporaryDirectory(prefix='botw-synthetic-child-') as directory:
            root = Path(directory)
            for name in fixture.MODULES:
                fixture.image(root, name)
            path = root / 'botw/sdk.patched.bin'
            raw = bytearray(path.read_bytes())
            guest_child(raw, fault=fault, focus=bool(focus))
            path.write_bytes(raw)
            gates = '0x10020\tsvc\t10\t0x200\n'
            if focus:
                gates += '0x10060\tsvc\t11\t0x204\n' if focus == 'svc' else '0x10060\tcntpct\t0\t0x204\n'
            (root / 'botw/sdk.gates.tsv').write_text(gates)
            mode = 'focus-stop' if focus == 'stop' else 'focus-resume' if focus else 'fault' if fault else 'normal'
            return subprocess.run([str(self.exe), str(root), mode],
                                  capture_output=True, text=True, timeout=10)

    def test_actual_child_context_is_isolated_and_rewritten_exit_is_joined(self):
        result = self.run_guest()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('runtime_child_context_isolated=true rewritten_exit_joined=true', result.stdout)

    def test_actual_child_fault_recovers_to_its_own_boundary(self):
        result = self.run_guest(fault=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('child_fault_recovered=true', result.stdout)
        self.assertNotIn('host_stage=stopped', result.stdout)

    def test_tick_gate_pauses_guest_progress_and_resumes(self):
        result = self.run_guest(focus='tick')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('focus_gate_paused_then_resumed=true', result.stdout)

    def test_svc_gate_pauses_guest_progress_and_resumes(self):
        result = self.run_guest(focus='svc')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('focus_gate_paused_then_resumed=true', result.stdout)

    def test_cleanup_wakes_parked_guest_and_joins_without_main_longjmp(self):
        result = self.run_guest(focus='stop')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('focus_parked_child_cleanup_joined=true main_boundary_untouched=true', result.stdout)


if __name__ == '__main__':
    unittest.main()
