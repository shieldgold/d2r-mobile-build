"""Native startup allocation failures must stop before unchecked guest use."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'native recovery requires Apple ARM64')
class StartupMemoryTests(unittest.TestCase):
    def test_address_policy_is_explicit_and_query_memory_uses_its_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'address-policy.c'
            source.write_text('#include "' + str(ROOT / 'native/botw_host/runtime.c') + '"\n' + r'''
int main(void) {
    if (botw_host_set_address_space(48) != 1) return 2;
    if (configured_address_bits != 36) return 3;
    if (botw_host_set_address_space(39) || configured_address_bits != 39) return 4;
    pthread_mutex_lock(&run_lock);
    if (botw_host_set_address_space(36) != 2 || configured_address_bits != 39) return 5;
    pthread_mutex_unlock(&run_lock);
    if (botw_host_set_address_space(36) || configured_address_bits != 36) return 6;
    state.owner = pthread_self();
    state.kernel = botw_kernel_create(NULL, NULL);
    if (!state.kernel) return 7;
    state.gates[0] = (Gate){.ret = 0x100, .arg = 6}; state.gate_count = 1;
    botw_kernel_memory_info info = {0};
    uint64_t regs[31] = {(uint64_t)&info, 0, (1ull << 36) + PAGE};
    botw_gate_dispatch(0x100, regs);
    if (regs[0] || info.type != 0x10 || info.base != (1ull << 36)) return 8;
    char error[512];
    if (botw_kernel_configure_address_space(state.kernel, 39, error, sizeof error)) return 9;
    regs[0] = (uint64_t)&info; regs[2] = (1ull << 36) + PAGE;
    botw_gate_dispatch(0x100, regs);
    if (regs[0] || info.type || info.base > regs[2] ||
            info.size > (1ull << 39) || regs[2] - info.base >= info.size) return 10;
    botw_kernel_destroy(state.kernel); return 0;
}
''')
            executable = Path(folder) / 'address-policy'
            sources = [source, ROOT / 'native/botw_host/entry.S',
                       ROOT / 'native/botw_host/services.c', ROOT / 'native/botw_host/kernel.c',
                       ROOT / 'native/botw_host/hipc_address.c',
                       ROOT / 'native/botw_host/address_space.c',
                       ROOT / 'native/botw_input/input.c', ROOT / 'native/botw_resources/romfs.c']
            compiled = subprocess.run(['xcrun', 'clang', '-O2', '-Wall', '-Wextra', '-Werror',
                                       *(str(s) for s in sources), '-o', str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_initial_heap_failure_stops_without_corrupting_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'startup.c'
            source.write_text('#include "' + str(ROOT / 'native/botw_host/runtime.c') + '"\n' + r'''
static void event(const char *message) { puts(message); }
int main(void) {
    state.owner = pthread_self(); state.active = 1; state.guest_started = 1;
    state.event = event;
    state.current_entry = "nninitStartup";
    const botw_kernel_callbacks callbacks = {.memory = external_memory};
    state.kernel = botw_kernel_create(&callbacks, NULL);
    if (!state.kernel) return 2;
    char error[512]; const size_t unit = 0x200000;
    if (botw_kernel_configure_memory(state.kernel, 4 * unit, unit, unit,
            4 * unit, 0, error, sizeof error)) return 3;
    state.gates[0] = (Gate){.ret = 0x100, .arg = 1}; state.gate_count = 1;
    int result;
    if (!sigsetjmp(state.recovery, 1)) {
        uint64_t regs[31] = {0, 8 * unit};
        botw_gate_dispatch(0x100, regs);
        result = 10; /* Do not return to an unchecked guest output pointer. */
    } else result = state.guest_started && !state.fault_signal ? 0 : 11;
    if (cleanup()) return 12;
    return result;
}
''')
            executable = Path(folder) / 'startup'
            sources = [source, ROOT / 'native/botw_host/entry.S',
                       ROOT / 'native/botw_host/services.c', ROOT / 'native/botw_host/kernel.c',
                       ROOT / 'native/botw_host/hipc_address.c',
                       ROOT / 'native/botw_host/address_space.c',
                       ROOT / 'native/botw_input/input.c', ROOT / 'native/botw_resources/romfs.c']
            compiled = subprocess.run(['xcrun', 'clang', '-O2', '-Wall', '-Wextra', '-Werror',
                                       *(str(s) for s in sources), '-o', str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('initial game heap allocation failed: requested=16777216 result=0xd001', result.stdout)
