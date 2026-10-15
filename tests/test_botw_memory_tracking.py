"""Real ARM64 write faults and checked host writes through the existing boundary."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from test_botw_runtime_callbacks import DRIVER as CALLBACK_DRIVER

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#define BOTW_WITH_AUDIO 1
CALLBACK_FIXTURE

static uint8_t *tracked;
static int fault_calls, prepare_calls, protected_page, fail_prepare;
static int tracked_fault(void *address, int is_write, void *context) {
    (void)context; fault_calls++;
    if (!is_write || !protected_page || (uintptr_t)address < (uintptr_t)tracked ||
            (uintptr_t)address - (uintptr_t)tracked >= PAGE) return 0;
    if (mprotect(tracked, PAGE, PROT_READ | PROT_WRITE)) return 0;
    protected_page = 0; return 1;
}
static int prepare_write(void *address, size_t bytes, char *error, size_t capacity) {
    prepare_calls++;
    if (fail_prepare) {
        if (error && capacity) snprintf(error, capacity, "probe refused write preparation");
        return -1;
    }
    if ((uintptr_t)address >= (uintptr_t)tracked &&
            (uintptr_t)address - (uintptr_t)tracked < PAGE &&
            bytes <= PAGE - ((uintptr_t)address - (uintptr_t)tracked)) {
        if (mprotect(tracked, PAGE, PROT_READ | PROT_WRITE)) return -1;
        protected_page = 0;
    }
    return 0;
}
static int other_fault(void *a, int w, void *c) { (void)a; (void)w; (void)c; return 0; }
static void prior_unhandled(int number) {
    _exit((number == SIGBUS || number == SIGSEGV) && fault_calls == 0 ? 88 : 89);
}
static void protect_page(int protection) {
    assert(!mprotect(tracked, PAGE, protection)); protected_page = protection == PROT_READ;
}
static PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC write_callback(void) {
    Module *m = &state.modules[0];
    assert(!mprotect(m->base, PAGE, PROT_READ | PROT_WRITE));
    const uint32_t code[] = {0xb9000001, 0xd65f03c0}; /* str w1,[x0]; ret */
    memcpy(m->base + 0x220, code, sizeof code);
    __builtin___clear_cache((char *)m->base + 0x220, (char *)m->base + 0x228);
    assert(!mprotect(m->base, PAGE, PROT_READ | PROT_EXEC));
    return (void *)(m->base + 0x220);
}
int main(int argc, char **argv) {
    assert(argc == 2); char error[512] = {0};
    tracked = mmap(NULL, PAGE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(tracked != MAP_FAILED); tracked[0] = 0x33;
    if (!strcmp(argv[1], "read-fault")) {
        signal(SIGBUS, prior_unhandled); signal(SIGSEGV, prior_unhandled);
    } else if (!strcmp(argv[1], "unowned-host")) {
        signal(SIGBUS, previous_signal); signal(SIGSEGV, previous_signal);
    }
    setup();
    if (sigsetjmp(state.recovery, 1)) return 41;
    assert(!botw_host_prepare_guest_write(tracked, 4, error, sizeof error));
    assert(!prepare_calls);
    assert(botw_host_register_memory_tracking(NULL, prepare_write));
    assert(botw_host_register_memory_tracking(tracked_fault, NULL));
    assert(!botw_host_register_memory_tracking(tracked_fault, prepare_write));
    assert(!botw_host_register_memory_tracking(tracked_fault, prepare_write));
    assert(botw_host_register_memory_tracking(other_fault, prepare_write));
    if (!strcmp(argv[1], "host-write")) {
        protect_page(PROT_READ); *(volatile uint32_t *)tracked = 0x22334455;
        assert(*(uint32_t *)tracked == 0x22334455 && fault_calls == 1); check_outer();
    } else if (!strcmp(argv[1], "guest-write") || !strcmp(argv[1], "unowned-guest")) {
        PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC callback = write_callback();
        int owned = !strcmp(argv[1], "guest-write");
        protect_page(owned ? PROT_READ : PROT_NONE);
        int ok = graphics_invoke_memory_callback(callback, (void *)tracked, 1, 0, NULL, error, sizeof error);
        assert(ok == owned);
        if (owned) assert(*(uint32_t *)tracked == 1 && fault_calls == 1);
        else assert(strstr(error, "signal="));
        check_outer(); check_good();
    } else if (!strcmp(argv[1], "read-fault")) {
        protect_page(PROT_NONE); volatile uint8_t value = *(volatile uint8_t *)tracked; (void)value;
        assert(!"real read fault was swallowed");
    } else if (!strcmp(argv[1], "unowned-host")) {
        protect_page(PROT_NONE); *(volatile uint8_t *)tracked = 7;
        assert(!"unowned host fault was swallowed");
    } else if (!strcmp(argv[1], "prepare")) {
        protect_page(PROT_READ);
        assert(!botw_host_prepare_guest_write(tracked, 0, error, sizeof error));
        assert(!prepare_calls && protected_page && tracked[0] == 0x33);
        assert(botw_host_prepare_guest_write((void *)(UINTPTR_MAX - 1), 4, error, sizeof error));
        assert(!prepare_calls && protected_page);
        fail_prepare = 1;
        assert(botw_host_prepare_guest_write(tracked, 4, error, sizeof error));
        assert(strstr(error, "probe refused") && prepare_calls == 1 && tracked[0] == 0x33);
        fail_prepare = 0;
        assert(!botw_host_prepare_guest_write(tracked, 4, error, sizeof error));
        assert(!protected_page && prepare_calls == 2 && tracked[0] == 0x33);
    } else if (!strcmp(argv[1], "audio-vm-write")) {
        state.tls = tracked; const uint32_t value = 0x12345678; protect_page(PROT_READ);
        fail_prepare = 1;
        assert(audio_write_guest(NULL, (uint64_t)tracked, &value, sizeof value, error, sizeof error));
        assert(prepare_calls == 1 && tracked[0] == 0x33);
        fail_prepare = 0;
        assert(!audio_write_guest(NULL, (uint64_t)tracked, &value, sizeof value, error, sizeof error));
        assert(prepare_calls == 2 && *(uint32_t *)tracked == value && !fault_calls);
        state.tls = NULL;
    } else assert(!"unknown case");
    assert(!mprotect(tracked, PAGE, PROT_READ | PROT_WRITE)); assert(!munmap(tracked, PAGE));
    assert(!cleanup()); puts("MEMORY_TRACKING_BOUNDARIES_PRESERVED"); return 0;
}
'''.replace('CALLBACK_FIXTURE', CALLBACK_DRIVER.replace(
    'int main(int argc, char **argv)', 'int callback_fixture_main(int argc, char **argv)'))


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'requires Apple ARM64 signal and protected VM APIs')
class MemoryTrackingRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-memory-tracking-')
        cls.addClassCleanup(cls.build.cleanup)
        source = Path(cls.build.name) / 'memory.c'
        source.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = Path(cls.build.name) / 'memory'
        sources = [source, *(ROOT / 'native/botw_host' / n for n in
                   ('entry.S', 'services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')),
                   ROOT / 'native/botw_input/input.c', ROOT / 'native/botw_audio/audio.c',
                   *(ROOT / 'native/botw_resources' / n for n in ('romfs.c', 'savefs.c', 'local_user.c'))]
        result = subprocess.run(['xcrun', 'clang', '-O2', '-Wall', '-Wextra', '-Werror',
                                 '-framework', 'Security', '-framework', 'AudioToolbox',
                                 '-framework', 'AudioUnit', '-framework', 'CoreAudio',
                                 *(str(p) for p in sources), '-o', str(cls.exe)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def check(self, case, status=0):
        result = subprocess.run([str(self.exe), case], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        if not status:
            self.assertIn('MEMORY_TRACKING_BOUNDARIES_PRESERVED', result.stdout)

    def test_real_host_write_to_owned_readonly_page_resumes(self): self.check('host-write')
    def test_real_guest_callback_write_resumes_without_unwinding(self): self.check('guest-write')
    def test_unhandled_guest_write_keeps_callback_recovery(self): self.check('unowned-guest')
    def test_real_read_fault_keeps_prior_handler_without_calling_write_hook(self): self.check('read-fault', 88)
    def test_unowned_host_write_keeps_prior_disposition(self): self.check('unowned-host', 88)
    def test_checked_prepare_is_separate_from_validation_and_never_writes_payload(self): self.check('prepare')
    def test_protected_audio_vm_write_obeys_failed_and_successful_preparation(self): self.check('audio-vm-write')
