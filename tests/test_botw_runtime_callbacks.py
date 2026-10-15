"""Guest callback recovery with real ARM64 stubs; graphics linkage is simulated."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#define BOTW_WITH_GRAPHICS 1
#include "RUNTIME_SOURCE"
#include <assert.h>

/* These only satisfy unused graphics linkage. No Metal result is claimed. */
int botw_graphics_init(void *p, botw_graphics_error e, botw_graphics_memory_range m) { (void)p; (void)e; (void)m; return 1; }
void botw_graphics_shutdown(void) {}
void *botw_nvn_bootstrap_loader(const char *n) { (void)n; return NULL; }
const char *botw_graphics_last_error(void) { return "unused simulated graphics"; }
void botw_graphics_set_memory_access_validator(botw_graphics_memory_access a) { (void)a; }
void botw_graphics_set_memory_tracking_registration(botw_graphics_memory_tracking_registration a) { (void)a; }
void botw_graphics_set_command_memory_invoker(botw_graphics_command_memory_invoker a) { (void)a; }
int botw_graphics_platform_initialize(void) { return 0; }
int botw_graphics_platform_open_default_display(uintptr_t *o) { (void)o; return 0; }
int botw_graphics_platform_create_layer(uintptr_t d, uintptr_t *o) { (void)d; (void)o; return 0; }
int botw_graphics_platform_set_scaling_mode(uintptr_t l, int m) { (void)l; (void)m; return 0; }
int botw_graphics_platform_get_native_window(uintptr_t l, uintptr_t *o) { (void)l; (void)o; return 0; }
int botw_graphics_platform_display_size(uintptr_t d, uint32_t *w, uint32_t *h) { (void)d; (void)w; (void)h; return 0; }
int botw_graphics_platform_display_is_live(uintptr_t d) { (void)d; return 0; }
void botw_graphics_set_capture_enabled(int e) { (void)e; }
int botw_graphics_load_shader_bundle(const char *d, botw_graphics_shader_bundle_result *r, char *e, size_t n) {
    (void)d; (void)r; (void)e; (void)n; return 0;
}

typedef struct { NVNcommandBuffer *command; int event, calls; size_t minimum; } Arguments;
static NVNcommandBuffer command_object;
static PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC good, bad, trap, nested, exiting, host_null, guest_null, host_tail_null;
static void success(NVNcommandBuffer *c, NVNcommandBufferMemoryEvent event, size_t minimum, void *opaque) {
    Arguments *a = opaque; assert(c == a->command && (int)event == a->event && minimum == a->minimum); a->calls++;
}
static void rejected(NVNcommandBuffer *c, NVNcommandBufferMemoryEvent event, size_t n, void *p) {
    (void)c; (void)event; (void)n; (void)p; fail("synthetic guest allocation failure");
}
static void nested_rejection(NVNcommandBuffer *c, NVNcommandBufferMemoryEvent event, size_t n, void *p) {
    char error[512];
    assert(!graphics_invoke_memory_callback(bad, c, event, n, p, error, sizeof error));
    assert(strstr(error, "synthetic guest allocation failure"));
    fail("outer callback rejected after nested recovery");
}
static void guest_exit(NVNcommandBuffer *c, NVNcommandBufferMemoryEvent event, size_t n, void *p) {
    (void)c; (void)event; (void)n; (void)p;
    uint64_t registers[31] = {0}; botw_gate_dispatch(0x100, registers);
    assert(!"ExitThread must reject without terminating its callback caller");
}
static void host_null_helper(NVNcommandBuffer *c, NVNcommandBufferMemoryEvent event, size_t n, void *p) {
    (void)c; (void)event; (void)n; (void)p;
    void (*volatile missing)(void) = NULL; missing();
}
static void previous_signal(int number) { _exit(number == SIGSEGV || number == SIGBUS ? 88 : 89); }
static void emit_jump(uint8_t *where, void *target) {
    const uint32_t code[] = {0x58000050, 0xd61f0200};
    memcpy(where, code, sizeof code); memcpy(where + 8, &target, sizeof target);
}
static void setup(void) {
    state.owner = pthread_self(); state.active = 1; state.guest_executing = 1;
    state.current_entry = "outer guest entry";
    Module *m = &state.modules[0]; m->name = "main"; m->size = m->text_end = m->patch_off = PAGE;
    m->base = mmap(NULL, PAGE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0); assert(m->base != MAP_FAILED);
    emit_jump(m->base + 0x100, success); emit_jump(m->base + 0x120, rejected);
    const uint32_t fault = 0xd4200000; memcpy(m->base + 0x140, &fault, sizeof fault);
    emit_jump(m->base + 0x160, nested_rejection); emit_jump(m->base + 0x180, guest_exit);
    emit_jump(m->base + 0x1a0, host_null_helper);
    const uint32_t null_call[] = {0xd2800010, 0xd63f0200, 0xd65f03c0}; /* mov x16,0; blr x16; ret */
    memcpy(m->base + 0x1c0, null_call, sizeof null_call);
    const uint32_t helper_call[] = {0x58000090, 0xd63f0200, 0xd65f03c0, 0xd503201f};
    void *helper = host_null_helper;
    memcpy(m->base + 0x1e0, helper_call, sizeof helper_call); memcpy(m->base + 0x1f0, &helper, sizeof helper);
    __builtin___clear_cache((char *)m->base, (char *)m->base + PAGE);
    assert(!mprotect(m->base, PAGE, PROT_READ | PROT_EXEC));
    good = (void *)(m->base + 0x100); bad = (void *)(m->base + 0x120); trap = (void *)(m->base + 0x140);
    nested = (void *)(m->base + 0x160); exiting = (void *)(m->base + 0x180);
    host_null = (void *)(m->base + 0x1a0); guest_null = (void *)(m->base + 0x1c0);
    host_tail_null = (void *)(m->base + 0x1e0);
    state.gates[0] = (Gate){.ret = 0x100, .arg = 0xa}; state.gate_count = 1;
    install_signal_boundary();
}
static void check_outer(void) {
    assert(state.guest_executing == 1 && !state.fault_signal);
    assert(!strcmp(state.current_entry, "outer guest entry"));
    assert(!callback_context);
}
static void check_good(void) {
    char error[512] = {0};
    for (unsigned i = 0; i < 2; i++) {
        Arguments args = {&command_object, (int)i, 0, 512 + i};
        assert(graphics_invoke_memory_callback(good, &command_object, i, args.minimum, &args, error, sizeof error));
        assert(args.calls == 1); check_outer();
    }
}
static void check_rejected(PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC fn, const char *reason) {
    Arguments args = {&command_object, 0, 0, 512}; char error[512] = {0};
    assert(!graphics_invoke_memory_callback(fn, &command_object, 0, 512, &args, error, sizeof error));
    if (!strstr(error, reason)) fprintf(stderr, "expected=%s actual=%s\n", reason, error);
    assert(strstr(error, reason)); assert(!args.calls); check_outer(); check_good();
}
typedef struct { int attached; } Worker;
static void *worker(void *opaque) {
    Worker *w = opaque; ChildContext child = {0};
    if (w->attached) { child.active = 1; strcpy(child.error, "outer worker untouched"); child_context = &child; }
    char error[512]; Arguments args = {&command_object, 1, 0, 1024};
    if (!w->attached) {
        assert(!graphics_invoke_memory_callback(good, &command_object, 1, 1024, &args, error, sizeof error));
        assert(strstr(error, "guest thread"));
    } else for (unsigned i = 0; i < 32; i++) {
        assert(!graphics_invoke_memory_callback(bad, &command_object, 1, 1024, &args, error, sizeof error));
        assert(strstr(error, "synthetic guest allocation failure"));
        assert(!strcmp(child.error, "outer worker untouched") && child.active && !child.fault_signal && !callback_context);
        assert(graphics_invoke_memory_callback(good, &command_object, 1, 1024, &args, error, sizeof error));
    }
    assert(args.calls == (w->attached ? 32 : 0)); child_context = NULL; return NULL;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (strstr(argv[1], "null")) { signal(SIGSEGV, previous_signal); signal(SIGBUS, previous_signal); }
    setup();
    if (sigsetjmp(state.recovery, 1)) { fprintf(stderr, "Callback escaped into outer guest recovery\n"); return 2; }
    if (!strcmp(argv[1], "arguments")) check_good();
    else if (!strcmp(argv[1], "failure")) check_rejected(bad, "synthetic guest allocation failure");
    else if (!strcmp(argv[1], "trap")) check_rejected(trap, "signal=");
    else if (!strcmp(argv[1], "guest-null")) check_rejected(guest_null, "signal=");
    else if (!strcmp(argv[1], "host-null")) check_rejected(host_null, "signal=");
    else if (!strcmp(argv[1], "host-tail-null")) check_rejected(host_tail_null, "signal=");
    else if (!strcmp(argv[1], "nested")) check_rejected(nested, "outer callback rejected after nested recovery");
    else if (!strcmp(argv[1], "exit")) check_rejected(exiting, "ExitThread");
    else if (!strcmp(argv[1], "pointers")) {
        check_rejected(NULL, "entry"); check_rejected((void *)((uintptr_t)good + 1), "unaligned");
        check_rejected((void *)success, "signed code");
    } else if (!strcmp(argv[1], "workers") || !strcmp(argv[1], "unrelated")) {
        Worker work[2] = {{strcmp(argv[1], "unrelated") != 0}, {1}}; pthread_t threads[2];
        for (unsigned i = 0; i < 2; i++) assert(!pthread_create(&threads[i], NULL, worker, &work[i]));
        for (unsigned i = 0; i < 2; i++) assert(!pthread_join(threads[i], NULL));
        check_outer(); check_good();
    } else assert(!"unknown fixture");
    assert(!cleanup()); puts("NATIVE_CALLBACK_RETURN_AND_OUTER_CONTEXT_PRESERVED"); return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'native callback recovery requires Apple ARM64')
class RuntimeCallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-native-callbacks-')
        cls.addClassCleanup(cls.build.cleanup)
        source = Path(cls.build.name) / 'callbacks.c'
        source.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = Path(cls.build.name) / 'callbacks'
        sources = [source, *(ROOT / 'native/botw_host' / name for name in
                   ('entry.S', 'services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name
                   for name in ('romfs.c', 'savefs.c', 'local_user.c'))]
        result = subprocess.run(['xcrun', 'clang', '-framework', 'Security', '-O2',
                                 '-Wall', '-Wextra', '-Werror', *(str(p) for p in sources),
                                 '-o', str(cls.exe)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def check_case(self, name):
        result = subprocess.run([str(self.exe), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('NATIVE_CALLBACK_RETURN_AND_OUTER_CONTEXT_PRESERVED', result.stdout)

    def test_native_callback_receives_all_original_arguments(self): self.check_case('arguments')
    def test_callback_failure_returns_without_escaping_into_outer_run(self): self.check_case('failure')
    def test_guest_signal_returns_without_overwriting_outer_fault(self): self.check_case('trap')
    def check_unattributed_null(self, name):
        result = subprocess.run([str(self.exe), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 88, result.stdout + result.stderr)
    def test_ambiguous_null_call_keeps_the_previous_signal_disposition(self): self.check_unattributed_null('guest-null')
    def test_host_null_helper_keeps_the_previous_signal_disposition(self): self.check_unattributed_null('host-null')
    def test_host_tail_call_does_not_use_a_guest_return_address_as_fault_proof(self): self.check_unattributed_null('host-tail-null')
    def test_nested_callback_failure_restores_both_boundaries(self): self.check_case('nested')
    def test_callback_cannot_terminate_its_caller_thread(self): self.check_case('exit')
    def test_callback_entry_requires_aligned_signed_guest_code(self): self.check_case('pointers')
    def test_concurrent_guest_workers_have_independent_callback_recovery(self): self.check_case('workers')
    def test_unrelated_host_thread_is_rejected(self): self.check_case('unrelated')
