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
#include <stdatomic.h>

static const char *const init_name = "_ZN2nv18InitializeGraphicsEPvm";
static const char *const alloc_name = "_ZN2nv20SetGraphicsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_";
static char observed_error[2048];
static unsigned shutdown_calls;
static uint64_t heap;
static const size_t unit = 0x200000;
#if defined(BOTW_WITH_GRAPHICS)
static const uintptr_t fake_display = 0x22000000, fake_layer = 0x33000000, fake_native = 0x44000000;
static int fake_vi_ready, fake_display_live, fake_scaling;
static unsigned attach_calls;
static uint32_t attached_reader;
static const char *fake_backend_error = "simulated backend rejection";
static int (*registered_access)(const void *, size_t, int);
static _Atomic int ticker_running;
static _Atomic unsigned ticker_ticks;
#endif

static void event_log(const char *message) {
    if (!strncmp(message, "error=", 6)) snprintf(observed_error, sizeof observed_error, "%s", message);
    puts(message);
}
int botw_graphics_init(void *layer, void (*error)(const char *), int (*memory)(const void *, size_t)) {
    (void)layer; (void)error; (void)memory; return 1; /* Deliberately simulated. */
}
void *botw_nvn_bootstrap_loader(const char *name) { (void)name; return NULL; }
int botw_graphics_shutdown_checked(char *error, size_t capacity) {
    (void)error; (void)capacity;
    /* Model shutdown's GPU wait boundary while all borrowed memory is live. */
    assert(state.graphics_workspace == (void *)(uintptr_t)heap);
    assert(state.graphics_workspace_size == PAGE && state.graphics_started);
    assert(botw_kernel_memory(state.kernel, heap, PAGE, 1));
    assert(*(const uint8_t *)(uintptr_t)heap == 0x5a);
    assert(state.modules[1].base && state.modules[1].base[0x1dff30] == 0xa7);
    shutdown_calls++;
#if defined(BOTW_WITH_GRAPHICS)
    fake_display_live = fake_vi_ready = 0;
#endif
    return 1;
}
#if defined(BOTW_WITH_GRAPHICS)
const char *botw_graphics_last_error(void) { return fake_backend_error; }
void botw_graphics_set_memory_access_validator(int (*access)(const void *, size_t, int)) { registered_access = access; }
void botw_graphics_set_memory_tracking_registration(botw_graphics_memory_tracking_registration registration) { (void)registration; }
void botw_graphics_set_command_memory_invoker(botw_graphics_command_memory_invoker invoke) { (void)invoke; }
int botw_graphics_platform_initialize(void) { if (fake_vi_ready) return 0; fake_vi_ready = 1; return 1; }
int botw_graphics_platform_open_default_display(uintptr_t *out) {
    if (!fake_vi_ready || fake_display_live) return 0; *out = fake_display; fake_display_live = 1; return 1;
}
int botw_graphics_platform_create_layer(uintptr_t display, uintptr_t *out) {
    if (!fake_display_live || display != fake_display) return 0; *out = fake_layer; return 1;
}
int botw_graphics_platform_set_scaling_mode(uintptr_t layer, int mode) {
    if (layer != fake_layer || mode != 2) return 0; fake_scaling = mode; return 1;
}
int botw_graphics_platform_get_native_window(uintptr_t layer, uintptr_t *out) {
    if (layer != fake_layer || !fake_display_live) return 0; *out = fake_native; return 1;
}
int botw_graphics_platform_display_size(uintptr_t display, uint32_t *width, uint32_t *height) {
    if (display != fake_display || !fake_display_live) return 0; *width = 1234; *height = 567; return 1;
}
int botw_graphics_platform_display_is_live(uintptr_t display) { return fake_display_live && display == fake_display; }
static void simulated_sdk_attach(void *out, uint32_t reader, _Bool managed, int mode) {
    /* A synthetic callee proves the ABI and invocation, not SDK byte contents. */
    assert(managed && mode == 1 && reader && reader != state.vi_vsync_read);
    attached_reader = reader; attach_calls++; memset(out, 0x39, 48); ((uint8_t *)out)[0x28] = 2;
}
static void *feed_display_ticks(void *unused) {
    (void)unused; double timestamp = 10.0;
    while (atomic_load(&ticker_running)) {
        botw_host_display_tick(timestamp); timestamp += 0.001; atomic_fetch_add(&ticker_ticks, 1);
    }
    return NULL;
}
static void setup_vi_symbols(void) {
    static const struct { const char *name; uint64_t offset, size; } fixtures[] = {
        {"_ZN2nn2vi10InitializeEv", 0x288c, 0x9b0},
        {"_ZN2nn2vi18OpenDefaultDisplayEPPNS0_7DisplayE", 0xce70, 0x20},
        {"_ZN2nn2vi11CreateLayerEPPNS0_5LayerEPNS0_7DisplayE", 0xdda0, 0x288},
        {"_ZN2nn2vi19SetLayerScalingModeEPNS0_5LayerENS0_11ScalingModeE", 0xe20c, 0x64},
        {"_ZN2nn2vi20GetDisplayVsyncEventEPNS_2os15SystemEventTypeEPNS0_7DisplayE", 0xd118, 0xa0},
        {"_ZN2nn2vi15GetNativeWindowEPPvPNS0_5LayerE", 0xe270, 0x2c}
    };
    Module *m = &state.modules[2]; m->name = "subsdk1"; m->size = m->patch_off = 0x40000;
    m->text_end = 0x20000; m->sym_off = 0x1000; m->str_off = 0x2000; m->sym_count = 7;
    m->base = mmap(NULL, m->size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0); assert(m->base != MAP_FAILED);
    m->str_size = 1;
    for (unsigned i = 0; i < 6; i++) {
        Sym *s = (Sym *)(m->base + m->sym_off) + i + 1;
        *s = (Sym){.name = (uint32_t)m->str_size, .info = 0x12, .section = 1,
            .value = fixtures[i].offset, .size = fixtures[i].size};
        strcpy((char *)m->base + m->str_off + m->str_size, fixtures[i].name);
        m->str_size += strlen(fixtures[i].name) + 1;
    }
    m = &state.modules[5]; m->name = "sdk"; m->size = m->patch_off = 0x90000;
    m->text_end = 0x88000; m->sym_off = 0x1000; m->str_off = 0x2000; m->sym_count = 2;
    m->base = mmap(NULL, m->size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0); assert(m->base != MAP_FAILED);
    const char *name = "_ZN2nn2os33AttachReadableHandleToSystemEventEPNS0_15SystemEventTypeEjbNS0_14EventClearModeE";
    strcpy((char *)m->base + m->str_off + 1, name); m->str_size = strlen(name) + 2;
    ((Sym *)(m->base + m->sym_off))[1] = (Sym){.name = 1, .info = 0x12, .section = 1, .value = 0x851c8, .size = 0x3c};
    /* Generic A64 jump to our synthetic callee, not original SDK instructions. */
    uint32_t jump[] = {0x58000050, 0xd61f0200}; uintptr_t target = (uintptr_t)simulated_sdk_attach;
    memcpy(m->base + 0x851c8, jump, sizeof jump); memcpy(m->base + 0x851d0, &target, sizeof target);
    __builtin___clear_cache((char *)m->base + 0x851c8, (char *)m->base + 0x851d8);
    assert(!mprotect(m->base + 0x84000, PAGE, PROT_READ | PROT_EXEC));
}
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
#if defined(BOTW_WITH_GRAPHICS)
    setup_vi_symbols();
#endif
    const botw_kernel_callbacks callbacks = {.memory = external_memory};
    state.kernel = botw_kernel_create(&callbacks, NULL); assert(state.kernel);
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
    graphics_backend_init = botw_graphics_init; graphics_backend_shutdown = botw_graphics_shutdown_checked;
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
        Module saved = *m, saved_vi = state.modules[2]; state.modules[2] = *m; m->base = NULL; reject_lookup("profile");
        *m = saved; state.modules[2] = saved_vi;
        Module shadow = *m; shadow.name = "main";
        shadow.base = mmap(NULL, m->size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        assert(shadow.base != MAP_FAILED); memcpy(shadow.base, m->base, m->size);
        state.modules[0] = shadow; reject_lookup("profile");
        memset(&state.modules[0], 0, sizeof state.modules[0]); munmap(shadow.base, shadow.size);
    } else if (!strcmp(argv[1], "backend")) {
        reject_call((void *)(uintptr_t)heap, PAGE, "Metal");
        state.graphics_started = 1;
        graphics_backend_shutdown = NULL; reject_call((void *)(uintptr_t)heap, PAGE, "Metal");
        graphics_backend_shutdown = botw_graphics_shutdown_checked;
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
    } else if (!strcmp(argv[1], "memory-access")) {
        botw_graphics_set_memory_access_validator(graphics_memory_access);
        assert(registered_access);
        assert(registered_access((void *)(uintptr_t)heap, PAGE, 0));
        assert(registered_access((void *)(uintptr_t)heap, PAGE, 1));
        assert(!registered_access((void *)(uintptr_t)(heap + unit - 8), PAGE, 0));
        assert(!registered_access((void *)(uintptr_t)(heap + unit - 8), PAGE, 1));
        assert(registered_access(m->base + 0xc794, 4, 0));
        assert(!registered_access(m->base + 0xc794, 4, 1));
        uint64_t r[31] = {0, heap, PAGE, 1}; char error[512];
        assert(botw_kernel_dispatch(state.kernel, 0x15, r, error, sizeof error) == BOTW_KERNEL_HANDLED && !r[0]);
        uint32_t transfer = (uint32_t)r[1];
        assert(registered_access((void *)(uintptr_t)heap, PAGE, 0));
        assert(!registered_access((void *)(uintptr_t)heap, PAGE, 1));
        assert(!botw_kernel_close_handle(state.kernel, transfer, error, sizeof error));
        assert(registered_access((void *)(uintptr_t)heap, PAGE, 1));
    } else if (!strcmp(argv[1], "borrowed-readonly")) {
        state.graphics_started = 1;
        uint64_t r[31] = {0, heap, PAGE, 1}; char error[512];
        assert(botw_kernel_dispatch(state.kernel, 0x15, r, error, sizeof error) == BOTW_KERNEL_HANDLED && !r[0]);
        uint32_t transfer = (uint32_t)r[1]; assert(transfer);
        assert(botw_kernel_memory(state.kernel, heap, PAGE, 0) && !botw_kernel_memory(state.kernel, heap, PAGE, 1));
        reject_call((void *)(uintptr_t)heap, PAGE, "writable");
        assert(!botw_kernel_close_handle(state.kernel, transfer, error, sizeof error));
        assert(botw_kernel_memory(state.kernel, heap, PAGE, 1)); state.graphics_started = 0;
    } else if (!strcmp(argv[1], "display-tick")) {
        state.graphics_started = 1; invoke((void *)(uintptr_t)heap, PAGE);
        char error[512];
        assert(!botw_kernel_create_event(state.kernel, 0, &state.vi_vsync_write,
            &state.vi_vsync_read, error, sizeof error));
        uint32_t read = state.vi_vsync_read;
        uint64_t wait[31] = {0, (uint64_t)&read, 1, 0};
        #define UNSIGNALED() do { \
            wait[0] = 0; wait[1] = (uint64_t)&read; wait[2] = 1; wait[3] = 0; \
            assert(botw_kernel_dispatch(state.kernel, 0x18, wait, error, sizeof error) == BOTW_KERNEL_HANDLED); \
            assert(wait[0] == BOTW_KERNEL_RESULT_TIMED_OUT); \
        } while (0)
        botw_host_display_tick(1.0); UNSIGNALED();
        state.vi_initialized = 1; state.vi_display = (void *)fake_display; state.vi_display_live = 1; fake_display_live = 1;
        botw_host_display_tick(NAN); botw_host_display_tick(INFINITY); botw_host_display_tick(0);
        botw_host_display_tick(-1); UNSIGNALED();
        botw_host_display_tick(1.0);
        wait[0] = 0; wait[1] = (uint64_t)&read; wait[2] = 1; wait[3] = 0;
        assert(botw_kernel_dispatch(state.kernel, 0x18, wait, error, sizeof error) == BOTW_KERNEL_HANDLED && !wait[0]);
        assert(!botw_kernel_clear_event(state.kernel, read, error, sizeof error));
        botw_host_display_tick(1.0); botw_host_display_tick(0.9); UNSIGNALED();
        fake_display_live = 0; botw_host_display_tick(1.5); UNSIGNALED(); fake_display_live = 1;
        state.vi_display_live = 0; botw_host_display_tick(2.0); UNSIGNALED();
        #undef UNSIGNALED
    } else if (!strcmp(argv[1], "vi-profile")) {
        Module *vi = &state.modules[2]; Sym *symbols = (Sym *)(vi->base + vi->sym_off);
        const uint64_t expected[] = {(uint64_t)host_vi_initialize, (uint64_t)host_vi_open_default_display,
            (uint64_t)host_vi_create_layer, (uint64_t)host_vi_set_scaling_mode,
            (uint64_t)host_vi_get_vsync_event, (uint64_t)host_vi_get_native_window};
        for (unsigned i = 1; i < 7; i++) {
            const char *name = symbol_name(vi, &symbols[i]); assert(find_symbol(name) == expected[i - 1]);
            symbols[i].value += 4; observed_error[0] = 0;
            if (!sigsetjmp(state.recovery, 1)) { (void)find_symbol(name); assert(!"accepted wrong VI address"); }
            assert(strstr(observed_error, "profile")); symbols[i].value -= 4;
            symbols[i].size--; observed_error[0] = 0;
            if (!sigsetjmp(state.recovery, 1)) { (void)find_symbol(name); assert(!"accepted wrong VI size"); }
            assert(strstr(observed_error, "profile")); symbols[i].size++;
            state.address_bits = 36; assert(find_symbol(name) == (uint64_t)vi->base + symbols[i].value); state.address_bits = 39;
        }
    } else if (!strcmp(argv[1], "vi-chain") || !strcmp(argv[1], "vi-race")) {
        #define VI_REJECT(call, reason) do { \
            observed_error[0] = 0; \
            if (!sigsetjmp(state.recovery, 1)) { (void)(call); assert(!"unexpected accepted VI call"); } \
            assert(strstr(observed_error, reason)); \
        } while (0)
        VI_REJECT(host_vi_initialize(), "workspace");
        state.graphics_started = 1; invoke((void *)(uintptr_t)heap, PAGE);
        void **out = (void **)(uintptr_t)(heap + 2 * PAGE);
        VI_REJECT(host_vi_open_default_display(out), "not initialized");
        host_vi_initialize(); assert(state.vi_initialized && fake_vi_ready);
        VI_REJECT(host_vi_initialize(), "already");
        VI_REJECT(host_vi_open_default_display(NULL), "output");
        assert(!host_vi_open_default_display(out) && *out == (void *)fake_display);
        VI_REJECT(host_vi_create_layer(out, (void *)0x55), "live host display");
        fake_display_live = 0; fake_backend_error = "";
        VI_REJECT(host_vi_create_layer(out, (void *)fake_display), "rejected");
        assert(!state.vi_layer && *out == (void *)fake_display);
        fake_display_live = 1; fake_backend_error = "simulated backend rejection";
        assert(!host_vi_create_layer(out, (void *)fake_display) && *out == (void *)fake_layer);
        VI_REJECT(host_vi_set_scaling_mode((void *)fake_layer, 1), "backend rejection");
        assert(!host_vi_set_scaling_mode((void *)fake_layer, 2) && fake_scaling == 2);
        VI_REJECT(host_vi_get_native_window(out, (void *)0x66), "host display layer");
        assert(!host_vi_get_native_window(out, (void *)fake_layer) && *out == (void *)fake_native);
        void *event = (void *)(uintptr_t)(heap + PAGE);
        VI_REJECT(host_vi_get_vsync_event(event, (void *)0x77), "live host display");
        VI_REJECT(host_vi_get_vsync_event((void *)(uintptr_t)(heap + unit - 40), (void *)fake_display), "output");
        Sym *attach_symbol = (Sym *)(state.modules[5].base + state.modules[5].sym_off) + 1;
        attach_symbol->size--; VI_REJECT(host_vi_get_vsync_event(event, (void *)fake_display), "profile"); attach_symbol->size++;
        assert(!state.vi_vsync_write && !attach_calls);
        memset(event, 0xa5, 48); /* An output initializer must not interpret prior POD contents. */
        assert(!host_vi_get_vsync_event(event, (void *)fake_display) && attach_calls == 1);
        uint32_t first = attached_reader;
        char error[512]; uint64_t wait[31] = {0, (uintptr_t)&first, 1, 0};
        assert(botw_kernel_dispatch(state.kernel, 0x18, wait, error, sizeof error) == BOTW_KERNEL_HANDLED &&
               wait[0] == BOTW_KERNEL_RESULT_TIMED_OUT);
        botw_host_display_tick(1.0); wait[0] = 0; wait[1] = (uintptr_t)&first; wait[2] = 1; wait[3] = 0;
        assert(botw_kernel_dispatch(state.kernel, 0x18, wait, error, sizeof error) == BOTW_KERNEL_HANDLED && !wait[0]);
        assert(!botw_kernel_clear_event(state.kernel, first, error, sizeof error));
        void *second_event = (void *)(uintptr_t)(heap + PAGE + 64);
        assert(!host_vi_get_vsync_event(second_event, (void *)fake_display) && attach_calls == 2);
        uint32_t second = attached_reader; assert(first != second && first != state.vi_vsync_read);
        assert(!botw_kernel_close_handle(state.kernel, first, error, sizeof error));
        botw_host_display_tick(2.0); wait[0] = 0; wait[1] = (uintptr_t)&second; wait[2] = 1; wait[3] = 0;
        assert(botw_kernel_dispatch(state.kernel, 0x18, wait, error, sizeof error) == BOTW_KERNEL_HANDLED && !wait[0]);
        assert(!botw_kernel_close_handle(state.kernel, second, error, sizeof error));
        #undef VI_REJECT
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
    pthread_t ticker = {0};
#if defined(BOTW_WITH_GRAPHICS)
    int race = !strcmp(argv[1], "vi-race");
    if (race) {
        atomic_store(&ticker_running, 1); assert(!pthread_create(&ticker, NULL, feed_display_ticks, NULL));
        while (atomic_load(&ticker_ticks) < 4) sched_yield();
    }
#else
    (void)ticker;
#endif
    assert(!cleanup());
#if defined(BOTW_WITH_GRAPHICS)
    if (race) { atomic_store(&ticker_running, 0); assert(!pthread_join(ticker, NULL)); }
#endif
    botw_host_display_tick(3.0); /* A frontend callback may still arrive after cleanup. */
    if (!strcmp(argv[1], "lifecycle") || !strcmp(argv[1], "display-tick") ||
        !strcmp(argv[1], "vi-chain") || !strcmp(argv[1], "vi-race")) assert(shutdown_calls == 1);
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

    def test_graphics_memory_access_distinguishes_readable_and_writable_kernel_ranges(self):
        self.check_case('memory-access')

    def test_kernel_transfer_owner_read_permission_cannot_be_workspace(self):
        self.check_case('borrowed-readonly')

    def test_workspace_borrow_survives_until_simulated_gpu_shutdown_and_is_not_written(self):
        self.check_case('lifecycle')

    def test_only_finite_new_timestamps_signal_live_display_events(self):
        self.check_case('display-tick')

    def test_vi_chain_calls_backend_and_audited_sdk_with_real_independent_kernel_events(self):
        self.check_case('vi-chain')

    def test_all_vi_exports_require_their_exact_audited_profile(self):
        self.check_case('vi-profile')

    def test_display_callbacks_can_race_cleanup_without_using_destroyed_kernel(self):
        self.check_case('vi-race')
