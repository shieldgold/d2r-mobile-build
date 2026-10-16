// BotW NV driver and VI service boundaries feeding the native NVN/Metal backend.
// Only main/subsdk0 imports confirmed in BotW 1.9.0 are registered here.
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include <stdatomic.h>

void *nvn_lookup(const char *name);
void hle_bind_system_event(void *event, int autoclear);
void hle_signal_system_event(void *event);
void botw_front_set_vsync_callback(void (*callback)(void));

typedef void *(*alloc_fn)(size_t, size_t, void *);
typedef void (*free_fn)(void *, void *);
typedef void *(*realloc_fn)(void *, size_t, void *);
static struct allocator { alloc_fn alloc; free_fn free; realloc_fn realloc; void *user; } g_allocator, g_debug_allocator;
HLE_FORCE("_ZN2nv20SetGraphicsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_", "BotW: record graphics allocator callbacks", void, botw_nv_allocator, alloc_fn a, free_fn f, realloc_fn r, void *u) {
    g_allocator = (struct allocator){a, f, r, u};
}
HLE_FORCE("_ZN2nv28SetGraphicsDevtoolsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_", "BotW: record graphics diagnostic allocator callbacks", void, botw_nv_debug_allocator, alloc_fn a, free_fn f, realloc_fn r, void *u) {
    g_debug_allocator = (struct allocator){a, f, r, u};
}
HLE_FORCE("_ZN2nv18InitializeGraphicsEPvm", "BotW: native Metal replaces nvdrv IPC", void, botw_nv_initialize, void *memory, size_t size) {
    (void)memory;
    LOG("BotW graphics driver: native NVN/Metal, guest transfer memory=%zu", size);
}
HLE_FORCE("nvnBootstrapLoader", "BotW: resolve native NVN entry points", void *, botw_nvn_bootstrap, const char *name) {
    return nvn_lookup(name);
}

static struct { unsigned magic; } g_display = {0x4453504c}, g_layer = {0x4c415952}, g_window = {0x57494e44};
static _Atomic(void *) g_vsync_event;
static void vsync_tick(void) {
    void *event = atomic_load(&g_vsync_event);
    if (event) hle_signal_system_event(event);
}
HLE_FORCE("_ZN2nn2vi10InitializeEv", "BotW: native host display", void, botw_vi_initialize, void) {}
HLE_FORCE("_ZN2nn2vi18OpenDefaultDisplayEPPNS0_7DisplayE", "BotW: default host display", Result, botw_vi_open, void **out) {
    if (!out) return RES_FAIL;
    *out = &g_display; return RES_OK;
}
HLE_FORCE("_ZN2nn2vi11CreateLayerEPPNS0_5LayerEPNS0_7DisplayE", "BotW: native Metal layer", Result, botw_vi_layer, void **out, void *display) {
    if (!out || display != &g_display) return RES_FAIL;
    *out = &g_layer; return RES_OK;
}
HLE_FORCE("_ZN2nn2vi15GetNativeWindowEPPvPNS0_5LayerE", "BotW: NVN window token", Result, botw_vi_window, void **out, void *layer) {
    if (!out || layer != &g_layer) return RES_FAIL;
    *out = &g_window; return RES_OK;
}
HLE_FORCE("_ZN2nn2vi20GetDisplayVsyncEventEPNS_2os15SystemEventTypeEPNS0_7DisplayE", "BotW: host display refresh event", Result, botw_vi_vsync, void *event, void *display) {
    if (!event || display != &g_display) return RES_FAIL;
    hle_bind_system_event(event, 1);
    atomic_store(&g_vsync_event, event);
    botw_front_set_vsync_callback(vsync_tick);
    return RES_OK;
}
