"""Audited platform ABI/state tests with simulated callbacks, not Metal proof.

The Apple ARM64 harness uses the real runtime and kernel heap. Only the graphics
linkage is simulated, so these checks establish rejection/lifetime semantics;
an actual Metal-backed guest probe remains a separate integration check.
"""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#include "RUNTIME_SOURCE"
#include <assert.h>

static const char *const init_name = "_ZN2nv18InitializeGraphicsEPvm";
static const char *const alloc_name = "_ZN2nv20SetGraphicsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_";
static char observed_error[2048];
static unsigned shutdown_calls;
static uint64_t heap;
static const size_t unit = 0x200000;

static void event_log(const char *message) {
    if (!strncmp(message, "error=", 6)) snprintf(observed_error, sizeof observed_error, "%s", message);
    puts(message);
}
int botw_graphics_init(void *layer, void (*error)(const char *), int (*memory)(const void *, size_t)) {
    (void)layer; (void)error; (void)memory; return 1; /* Deliberately simulated. */
}
void *botw_nvn_bootstrap_loader(const char *name) { (void)name; return NULL; }
void botw_graphics_shutdown(void) {
    /* Model shutdown's GPU wait boundary while all borrowed memory is live. */
    assert(state.graphics_workspace == (void *)(uintptr_t)heap);
    assert(state.graphics_workspace_size == PAGE && state.graphics_started);
    assert(botw_kernel_memory(state.kernel, heap, PAGE, 1));
    assert(*(const uint8_t *)(uintptr_t)heap == 0x5a);
    assert(state.modules[1].base && state.modules[1].base[0x1dff30] == 0xa7);
    shutdown_calls++;
}
#if defined(BOTW_WITH_GRAPHICS)
void botw_graphics_set_capture_enabled(int enabled) { (void)enabled; }
int botw_graphics_load_shader_bundle(const char *directory,
    botw_graphics_shader_bundle_result *result, char *error, size_t capacity) {
    (void)directory; (void)result; (void)error; (void)capacity; return 0;
}
#endif

#if defined(BOTW_WITH_GRAPHICS)
static Sym *init_symbol(void) { return (Sym *)(state.modules[1].base + state.modules[1].sym_off) + 1; }
#endif
static void setup(void) {
    memset(&state, 0, sizeof state); state.owner = pthread_self(); state.event = event_log;
    state.address_bits = 39; state.host39_transport_audited = 1;
    Module *m = &state.modules[1]; m->name = "subsdk0"; m->size = 0x1e8000;
    m->text_end = 0xe000; m->patch_off = m->size; m->sym_off = 0x1000;
    m->str_off = 0x2000; m->sym_count = 3;
    m->base = mmap(NULL, m->size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(m->base != MAP_FAILED);
    char *strings = (char *)m->base + m->str_off; strings[0] = 0;
    strcpy(strings + 1, init_name); strcpy(strings + 2 + strlen(init_name), alloc_name);
    m->str_size = 3 + strlen(init_name) + strlen(alloc_name);
    Sym *symbols = (Sym *)(m->base + m->sym_off);
    symbols[1] = (Sym){.name = 1, .info = 0x12, .section = 1, .value = 0xc794, .size = 0x94};
    symbols[2] = (Sym){.name = 2 + (uint32_t)strlen(init_name), .info = 0x12,
        .section = 1, .value = 0xc5c8, .size = 0x104};
    memset(m->base + 0x1dff30, 0xa7, 16); memset(m->base + 0x1e3f88, 0xb8, 16);
    state.kernel = botw_kernel_create(NULL, NULL); assert(state.kernel);
    char error[512];
    assert(!botw_kernel_configure_address_space(state.kernel, 39, error, sizeof error));
    assert(!botw_kernel_configure_memory(state.kernel, 4 * unit, unit, unit, 4 * unit, 0, error, sizeof error));
    uint64_t r[31] = {0, unit};
    assert(botw_kernel_dispatch(state.kernel, 1, r, error, sizeof error) == BOTW_KERNEL_HANDLED && !r[0]);
    heap = r[1]; assert(heap && !(heap % PAGE)); memset((void *)(uintptr_t)heap, 0x5a, PAGE);
}

#if defined(BOTW_WITH_GRAPHICS)
static void invoke(void *workspace, size_t bytes) {
    ((void (*)(void *, size_t))(uintptr_t)find_symbol(init_name))(workspace, bytes);
}
static void reject_call(void *workspace, size_t bytes, const char *reason) {
    void *previous = state.graphics_workspace; size_t previous_size = state.graphics_workspace_size;
    observed_error[0] = 0;
    if (!sigsetjmp(state.recovery, 1)) { invoke(workspace, bytes); assert(!"unexpected successful initialization"); }
    assert(strstr(observed_error, reason));
    assert(state.graphics_workspace == previous && state.graphics_workspace_size == previous_size);
}
static void reject_lookup(const char *reason) {
    observed_error[0] = 0;
    if (!sigsetjmp(state.recovery, 1)) { (void)find_symbol(init_name); assert(!"unexpected accepted profile"); }
    assert(strstr(observed_error, reason));
}
#endif

int main(int argc, char **argv) {
    assert(argc == 2); setup();
#if !defined(BOTW_WITH_GRAPHICS)
    assert(!strcmp(argv[1], "headless"));
    graphics_backend_init = botw_graphics_init; graphics_backend_shutdown = botw_graphics_shutdown;
    graphics_backend_loader = botw_nvn_bootstrap_loader; state.graphics_started = 1;
    assert(find_symbol(init_name) == (uint64_t)state.modules[1].base + 0xc794);
    state.graphics_started = 0;
#else
    Module *m = &state.modules[1];
    if (!strcmp(argv[1], "profile")) {
        assert(find_symbol(init_name) == (uint64_t)host_initialize_graphics);
        assert(find_symbol(alloc_name) == (uint64_t)m->base + 0xc5c8);
        assert(find_symbol("_ZN2nv18InitializeGraphicsEPvj") == 0);
        state.address_bits = 36;
        assert(find_symbol(init_name) == (uint64_t)m->base + 0xc794);
        state.address_bits = 39; state.host39_transport_audited = 0; reject_lookup("audit");
        state.host39_transport_audited = 1;
        m->name = "subsdk1"; reject_lookup("profile"); m->name = NULL; reject_lookup("profile"); m->name = "subsdk0";
        Sym original = *init_symbol();
        init_symbol()->value++; reject_lookup("profile"); *init_symbol() = original;
        init_symbol()->size--; reject_lookup("profile"); *init_symbol() = original;
        init_symbol()->info = 0x11; reject_lookup("profile"); *init_symbol() = original;
        init_symbol()->section = 0; reject_lookup("profile"); *init_symbol() = original;
        size_t end = m->text_end; m->text_end = 0xc827; reject_lookup("profile"); m->text_end = end;
        size_t size = m->size; m->size = 0xc827; reject_lookup("profile"); m->size = size;
        Module saved = *m; state.modules[2] = *m; m->base = NULL; reject_lookup("profile");
        *m = saved; memset(&state.modules[2], 0, sizeof state.modules[2]);
        Module shadow = *m; shadow.name = "main";
        shadow.base = mmap(NULL, m->size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        assert(shadow.base != MAP_FAILED); memcpy(shadow.base, m->base, m->size);
        state.modules[0] = shadow; reject_lookup("profile");
        memset(&state.modules[0], 0, sizeof state.modules[0]); munmap(shadow.base, shadow.size);
    } else if (!strcmp(argv[1], "backend")) {
        reject_call((void *)(uintptr_t)heap, PAGE, "Metal");
        state.graphics_started = 1;
        graphics_backend_shutdown = NULL; reject_call((void *)(uintptr_t)heap, PAGE, "Metal");
        graphics_backend_shutdown = botw_graphics_shutdown;
        graphics_backend_init = NULL; reject_call((void *)(uintptr_t)heap, PAGE, "Metal");
        graphics_backend_init = botw_graphics_init;
        graphics_backend_loader = NULL; reject_call((void *)(uintptr_t)heap, PAGE, "Metal");
        graphics_backend_loader = botw_nvn_bootstrap_loader; state.graphics_started = 0;
    } else if (!strcmp(argv[1], "workspace")) {
        state.graphics_started = 1;
        reject_call(NULL, PAGE, "workspace");
        reject_call((void *)(uintptr_t)(heap + 1), PAGE, "aligned");
        if (getpagesize() > 4096) reject_call((void *)(uintptr_t)(heap + 4096), PAGE, "aligned");
        reject_call((void *)(uintptr_t)heap, 0, "size");
        reject_call((void *)(uintptr_t)heap, PAGE - 1, "size");
        reject_call((void *)(uintptr_t)heap, UINT64_C(0x100000000), "size");
        reject_call((void *)(uintptr_t)heap, SIZE_MAX, "size");
        reject_call((void *)(uintptr_t)(heap + unit - PAGE), 2 * PAGE, "writable");
        void *foreign = mmap(NULL, PAGE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        assert(foreign != MAP_FAILED); reject_call(foreign, PAGE, "writable"); munmap(foreign, PAGE);
        state.graphics_started = 0;
    } else if (!strcmp(argv[1], "borrowed-readonly")) {
        state.graphics_started = 1;
        uint64_t r[31] = {0, heap, PAGE, 1}; char error[512];
        assert(botw_kernel_dispatch(state.kernel, 0x15, r, error, sizeof error) == BOTW_KERNEL_HANDLED && !r[0]);
        uint32_t transfer = (uint32_t)r[1]; assert(transfer);
        assert(botw_kernel_memory(state.kernel, heap, PAGE, 0) && !botw_kernel_memory(state.kernel, heap, PAGE, 1));
        reject_call((void *)(uintptr_t)heap, PAGE, "writable");
        assert(!botw_kernel_close_handle(state.kernel, transfer, error, sizeof error));
        assert(botw_kernel_memory(state.kernel, heap, PAGE, 1)); state.graphics_started = 0;
    } else {
        assert(!strcmp(argv[1], "lifecycle")); state.graphics_started = 1;
        invoke((void *)(uintptr_t)heap, PAGE);
        assert(state.graphics_workspace == (void *)(uintptr_t)heap && state.graphics_workspace_size == PAGE);
        for (size_t i = 0; i < PAGE; i++) assert(((uint8_t *)(uintptr_t)heap)[i] == 0x5a);
        for (size_t i = 0; i < 16; i++) assert(m->base[0x1dff30 + i] == 0xa7 && m->base[0x1e3f88 + i] == 0xb8);
        reject_call((void *)(uintptr_t)heap, PAGE, "already");
        reject_call((void *)(uintptr_t)(heap + PAGE), PAGE, "already");
    }
#endif
    assert(!cleanup());
    if (!strcmp(argv[1], "lifecycle")) assert(shutdown_calls == 1);
    else assert(!shutdown_calls);
    assert(!state.graphics_started && !state.graphics_workspace && !state.graphics_workspace_size);
    assert(!state.kernel); return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'runtime/kernel platform ABI tests require Apple ARM64')
class GraphicsPlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-platform-abi-')
        source = Path(cls.build.name) / 'platform.c'
        source.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        sources = [source, ROOT / 'native/botw_host/entry.S']
        sources += [ROOT / 'native/botw_host' / name for name in
                    ('services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')]
        sources += [ROOT / 'native/botw_resources' / name for name in
                    ('romfs.c', 'savefs.c', 'local_user.c')]
        sources.append(ROOT / 'native/botw_input/input.c')
        cls.executables = {}
        for graphics in (False, True):
            executable = Path(cls.build.name) / ('enabled' if graphics else 'headless')
            command = ['xcrun', 'clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                       '-framework', 'Security']
            if graphics:
                command.append('-DBOTW_WITH_GRAPHICS')
            compiled = subprocess.run([*command, *(str(s) for s in sources), '-o', str(executable)],
                                      capture_output=True, text=True)
            assert compiled.returncode == 0, compiled.stderr
            cls.executables[graphics] = executable

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def check_case(self, case, *, graphics=True):
        result = subprocess.run([str(self.executables[graphics]), case], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_headless_build_does_not_interpose_even_with_simulated_callbacks(self):
        self.check_case('headless', graphics=False)

    def test_exact_profile_symbol_text_and_shadow_definition_checks(self):
        self.check_case('profile')

    def test_backend_must_be_started_and_linked(self):
        self.check_case('backend')

    def test_workspace_alignment_size_and_complete_guest_range(self):
        self.check_case('workspace')

    def test_kernel_transfer_owner_read_permission_cannot_be_workspace(self):
        self.check_case('borrowed-readonly')

    def test_workspace_borrow_survives_until_simulated_gpu_shutdown_and_is_not_written(self):
        self.check_case('lifecycle')
