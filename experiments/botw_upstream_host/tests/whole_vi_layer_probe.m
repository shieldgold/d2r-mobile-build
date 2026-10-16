/* Self-owned layer tokens and actual native viewport arithmetic, never a GPU. */
#import <Metal/Metal.h>
#include <assert.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>
#include "dm/host/botw_graphics.c"
#include "actual_vi_viewport.inc"

void *nvn_lookup(const char *name) { (void)name; assert(!"unexpected owned lookup"); return NULL; }
void hle_bind_system_event(void *event, int autoclear) { (void)event; (void)autoclear; assert(!"unexpected event bind"); }
void hle_signal_system_event(void *event) { (void)event; assert(!"unexpected event signal"); }
void botw_front_set_vsync_callback(void (*callback)(void)) { (void)callback; assert(!"unexpected vsync callback"); }

static void require(int condition) {
    if (!condition) { fputs("owned VI layer contract failed\n", stderr); _Exit(91); }
}
static hle_force_ent const *entry(const char *name) {
    unsigned long size = 0;
    const hle_force_ent *rows = (const hle_force_ent *)getsectiondata(&_mh_execute_header, "__DATA", "swforce", &size);
    require(rows && size % sizeof *rows == 0);
    for (unsigned i = 0; i < size / sizeof *rows; ++i) if (!strcmp(rows[i].name, name)) return &rows[i];
    return NULL;
}
typedef Result (*scale_fn)(void *, int);
static scale_fn scaling;
static void *layer;
static void setup(void) {
    void *display = NULL;
    require(botw_vi_open(&display) == RES_OK && display);
    require(botw_vi_layer(&layer, display) == RES_OK && layer);
    const hle_force_ent *row = entry("_ZN2nn2vi19SetLayerScalingModeEPNS0_5LayerENS0_11ScalingModeE");
    require(row && row->titles == DM_TM_BOTW && row->fn &&
        !strcmp(row->why, "base-v0 VI layer scaling is consumed by native presentation"));
    scaling = (scale_fn)row->fn;
}
static int equal(MTLViewport a, MTLViewport b) {
    return a.originX == b.originX && a.originY == b.originY && a.width == b.width &&
        a.height == b.height && a.znear == b.znear && a.zfar == b.zfar;
}
static void *writer(void *unused) {
    (void)unused;
    for (unsigned i = 0; i < 20000; ++i) require(scaling(layer, i & 1 ? 2 : 4) == RES_OK);
    return NULL;
}
static void *reader(void *unused) {
    (void)unused;
    for (unsigned i = 0; i < 20000; ++i) {
        MTLViewport viewport = owned_viewport(1000, 1000, 2000, 1000);
        require(equal(viewport, (MTLViewport){0, 0, 2000, 1000, 0, 1}) ||
                equal(viewport, (MTLViewport){500, 0, 1000, 1000, 0, 1}));
    }
    return NULL;
}
int main(int argc, char **argv) {
    require(argc == 2);
    if (!strcmp(argv[1], "default")) {
        require(equal(owned_viewport(1000, 1000, 2000, 1000), (MTLViewport){500, 0, 1000, 1000, 0, 1}));
    } else if (!strcmp(argv[1], "fit")) {
#if !defined(BOTW_VI_LAYER_BASELINE)
        setup(); require(scaling(layer, 2) == RES_OK);
#endif
        require(equal(owned_viewport(1000, 1000, 2000, 1000), (MTLViewport){0, 0, 2000, 1000, 0, 1}));
        require(equal(owned_viewport(1280, 720, 1170, 2532), (MTLViewport){0, 0, 1170, 2532, 0, 1}));
    } else if (!strcmp(argv[1], "preserve")) {
        setup(); require(scaling(layer, 4) == RES_OK);
        require(equal(owned_viewport(1280, 720, 2000, 1000), (MTLViewport){111, 0, 1778, 1000, 0, 1}));
        require(equal(owned_viewport(1952, 900, 2868, 1320), (MTLViewport){0, 0, 2868, 1320, 0, 1}));
    } else if (!strcmp(argv[1], "invalid")) {
        setup(); require(scaling(layer, 4) == RES_OK);
        const int invalid[] = {0, 1, 3, 5, -1, INT32_MIN, INT32_MAX};
        for (unsigned i = 0; i < sizeof invalid / sizeof *invalid; ++i) require(scaling(layer, invalid[i]) == RES_FAIL);
        unsigned fake = 0x4c415952;
        require(scaling(NULL, 2) == RES_FAIL && scaling((void *)(uintptr_t)1, 2) == RES_FAIL && scaling(&fake, 2) == RES_FAIL);
        require(equal(owned_viewport(1000, 1000, 2000, 1000), (MTLViewport){500, 0, 1000, 1000, 0, 1}));
    } else if (!strcmp(argv[1], "concurrent")) {
        setup(); pthread_t threads[4];
        require(!pthread_create(&threads[0], NULL, writer, NULL));
        for (unsigned i = 1; i < 4; ++i) require(!pthread_create(&threads[i], NULL, reader, NULL));
        for (unsigned i = 0; i < 4; ++i) require(!pthread_join(threads[i], NULL));
    } else {
        require(!strcmp(argv[1], "registry")); setup();
    }
    return 0;
}
