/* Actual complete BOTW HID module and native input module on owned CPU memory.
 * No Guest code, hardware input, UIKit or GPU is executed. */
#include "dm.h"
#include "native/botw_input/input.h"
#include <assert.h>
#include <setjmp.h>
#include <stdatomic.h>
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>
static sigjmp_buf failure;
static _Atomic int armed, failed;
static _Noreturn void owned_failure(const char *format, ...) {
    (void)format;
    atomic_fetch_add(&failed, 1);
    if (atomic_load(&armed)) siglongjmp(failure, 1);
    abort();
}
#undef die
#define die(...) owned_failure(__VA_ARGS__)
#include "normal_registry.inc"
#include "dm/host/botw_hid.c"
void botw_keys_start(void) {} /* No scripted producer is installed. */
uint64_t now_ns(void) { return UINT64_C(1000000000); }
static const char *const debug_names[] = {
    "_ZN2nn3hid18InitializeDebugPadEv",
    "_ZN2nn3hid16GetDebugPadStateEPNS0_13DebugPadStateE"
};
static const hle_force_ent *find(const char *name) {
    unsigned long bytes = 0;
    const hle_force_ent *entries = (const void *)getsectiondata(&_mh_execute_header, "__DATA", "swforce", &bytes);
    assert(entries && !(bytes % sizeof *entries));
    for (size_t i = 0; i < bytes / sizeof *entries; ++i)
        if (!strcmp(entries[i].name, name)) return entries + i;
    return NULL;
}
static void registry(void) {
    for (unsigned i = 0; i < 2; ++i) {
        const hle_force_ent *entry = find(debug_names[i]);
        assert(entry && "fixed base-v0 DebugPad import has no normal host producer");
        assert(entry->fn && entry->titles == DM_TM_BOTW);
        assert(!strcmp(entry->why, "base-v0 DebugPad uses the complete native disconnected producer"));
    }
#ifndef BOTW_WHOLE_DEBUG_PAD_BASELINE
    void (*callback)(void) = botw_base_v0_debug_pad_tick; assert(callback);
#endif
}
typedef struct {
    uint64_t before[2];
    botw_debug_pad_state state;
    uint64_t after[2];
} guarded;
_Static_assert(sizeof(botw_debug_pad_state) == 32, "fixed base-v0 DebugPad state");
_Static_assert(offsetof(botw_debug_pad_state, attributes) == 8, "attributes ABI");
_Static_assert(offsetof(botw_debug_pad_state, buttons) == 12, "buttons ABI");
_Static_assert(offsetof(botw_debug_pad_state, rx) == 16, "right stick ABI");
_Static_assert(offsetof(botw_debug_pad_state, lx) == 24, "left stick ABI");
static void guarded_clear(guarded *g) { memset(g, 0xa5, sizeof *g); }
static void guarded_ok(const guarded *g) {
    for (unsigned i = 0; i < 2; ++i)
        assert(g->before[i] == UINT64_C(0xa5a5a5a5a5a5a5a5) && g->after[i] == UINT64_C(0xa5a5a5a5a5a5a5a5));
}
static void untouched(const void *p, size_t bytes) {
    for (size_t i = 0; i < bytes; ++i) assert(((const unsigned char *)p)[i] == 0xa5);
}
static void disconnected(const botw_debug_pad_state *state) {
    assert(state->sampling && !state->attributes && !state->buttons && !state->rx && !state->ry && !state->lx && !state->ly);
}
static void initialize(void) {
    const hle_force_ent *entry = find(debug_names[0]); assert(entry);
    ((void (*)(void))entry->fn)();
}
static void get(botw_debug_pad_state *state) {
    const hle_force_ent *entry = find(debug_names[1]); assert(entry);
    ((void (*)(botw_debug_pad_state *))entry->fn)(state);
}
static void tick(void) {
#ifdef BOTW_WHOLE_DEBUG_PAD_BASELINE
    /* The historical module really has no tick. The RED mode tests the real
     * force registry, and never substitutes a successful platform callback. */
    assert(!"the baseline has no DebugPad display-tick producer");
#else
    void (*callback)(void) = botw_base_v0_debug_pad_tick; assert(callback); callback();
#endif
}
static void *destination;
static unsigned prepares;
static int reject_output;
static int prepare(void *pointer, size_t bytes) {
    if (pointer == destination) { assert(bytes == 32); ++prepares; return !reject_output; }
    /* The old Npad producer prepares its own normal C stack temporary. */
    assert(bytes == 40); return 1;
}
static void checked_registry(void) { assert(dm_guest_write_prep_register_checked(prepare)); }
static void native_boundaries(void) {
    guarded g; guarded_clear(&g);
    assert(botw_input_sample_debug_pad(NULL, 1) == -1);
    assert(botw_input_read_debug_pad(NULL, &g.state) == -1);
    botw_input *input = botw_input_create(); assert(input);
    size_t bytes = 0; unsigned char *memory = botw_input_shared_memory(input, &bytes);
    assert(memory && bytes == BOTW_HID_SHARED_MEMORY_SIZE);
    unsigned char *saved = malloc(bytes); assert(saved); memcpy(saved, memory, bytes);
    assert(botw_input_sample_debug_pad(input, 1) == -1);
    assert(botw_input_read_debug_pad(input, &g.state) == -1);
    assert(botw_input_read_debug_pad(input, NULL) == -1);
    assert(!memcmp(saved, memory, bytes)); untouched(&g, sizeof g);
    assert(!botw_input_activate_debug_pad(input, 100));
    memcpy(saved, memory, bytes);
    assert(botw_input_sample_debug_pad(input, 0) == -1);
    assert(!memcmp(saved, memory, bytes));
    assert(botw_input_read_debug_pad(input, &g.state) == 1);
    assert(g.state.sampling == 1); disconnected(&g.state); guarded_ok(&g);
    _Atomic uint64_t *count = (_Atomic uint64_t *)(void *)(memory + 0x18);
    atomic_store(count, 0); guarded_clear(&g);
    assert(!botw_input_read_debug_pad(input, &g.state)); untouched(&g, sizeof g);
    atomic_store(count, 1);
    _Atomic uint64_t *tail = (_Atomic uint64_t *)(void *)(memory + 0x10);
    atomic_store(tail, 17);
    assert(botw_input_read_debug_pad(input, &g.state) == -1); untouched(&g, sizeof g);
    free(saved); botw_input_destroy(input);
}
static void native_isolation(void) {
    botw_input *input = botw_input_create(); assert(input);
    assert(!botw_input_set_source(input, BOTW_INPUT_TOUCH, BOTW_BUTTON_A, 123, -234, 345, -456, 1));
    botw_input_sample(input, 10);
    size_t bytes = 0; unsigned char *memory = botw_input_shared_memory(input, &bytes);
    unsigned char *npads = malloc(bytes - BOTW_HID_DEBUG_PAD_SIZE); assert(npads);
    memcpy(npads, memory + BOTW_HID_DEBUG_PAD_SIZE, bytes - BOTW_HID_DEBUG_PAD_SIZE);
    assert(!botw_input_activate_debug_pad(input, 100));
    for (uint64_t sequence = 1; sequence <= 70; ++sequence) {
        guarded a, b; guarded_clear(&a); guarded_clear(&b);
        assert(botw_input_read_debug_pad(input, &a.state) == 1);
        assert(botw_input_read_debug_pad(input, &b.state) == 1);
        assert(a.state.sampling == sequence && !memcmp(&a.state, &b.state, 32));
        disconnected(&a.state); guarded_ok(&a); guarded_ok(&b);
        assert(!botw_input_activate_debug_pad(input, 999));
        assert(!memcmp(npads, memory + BOTW_HID_DEBUG_PAD_SIZE, bytes - BOTW_HID_DEBUG_PAD_SIZE));
        assert(!botw_input_sample_debug_pad(input, sequence + 100));
    }
    free(npads); botw_input_destroy(input);
}
static void lifecycle(void) {
    checked_registry(); guarded a, b; guarded_clear(&a); guarded_clear(&b);
    tick(); /* A display tick before DebugPad initialization is inert. */
    initialize(); destination = &a.state; get(&a.state);
    assert(a.state.sampling == 1); disconnected(&a.state); guarded_ok(&a);
    initialize(); destination = &b.state; get(&b.state);
    assert(!memcmp(&a.state, &b.state, 32)); guarded_ok(&b);
    tick(); get(&b.state); assert(b.state.sampling == a.state.sampling + 1);
    botw_debug_pad_state saved = b.state; get(&b.state);
    assert(!memcmp(&saved, &b.state, 32) && prepares == 4); guarded_ok(&b);
}
static void failure_case(const char *mode) {
    checked_registry(); guarded g; guarded_clear(&g); destination = &g.state;
    if (strcmp(mode, "preinit")) initialize();
    if (!strcmp(mode, "prepare-failed")) reject_output = 1;
    atomic_store(&armed, 1);
    if (!sigsetjmp(failure, 1)) {
        get(!strcmp(mode, "null-out") ? NULL : &g.state);
        assert(!"typed DebugPad failure was accepted");
    }
    atomic_store(&armed, 0);
    assert(atomic_load(&failed) == 1); untouched(&g, sizeof g);
    if (!strcmp(mode, "prepare-failed")) {
        assert(prepares == 1); reject_output = 0; get(&g.state);
        assert(g.state.sampling == 1); disconnected(&g.state); guarded_ok(&g);
    } else assert(!prepares);
}
static _Atomic int stop;
static void *ticker(void *unused) {
    (void)unused;
    while (!atomic_load(&stop)) tick();
    return NULL;
}
static void concurrent(void) {
    checked_registry(); initialize(); botw_hid_initialize_npad();
    uint32_t id = 0; botw_hid_set_supported_styles(STYLE_FULLKEY); botw_hid_set_supported_ids(&id, 1);
    botw_pad_state source = {BOTW_BTN_A | BOTW_BTN_PLUS, 30000, -29000, 28000, -27000};
    botw_hid_pad_set(1, &source); botw_input_focus(1);
    pthread_t thread; assert(!pthread_create(&thread, NULL, ticker, NULL));
    guarded g; guarded_clear(&g); destination = &g.state; uint64_t last = 0;
    for (unsigned i = 0; i < 4000; ++i) {
        get(&g.state); disconnected(&g.state); assert(g.state.sampling >= last); last = g.state.sampling;
        NpadState npad; assert(botw_hid_get_states_fullkey(&npad, 1, &id) == 1);
        assert(npad.buttons == source.buttons && npad.lx == source.lx && npad.ly == source.ly &&
               npad.rx == source.rx && npad.ry == source.ry && npad.attributes == ATTR_CONNECTED);
    }
    atomic_store(&stop, 1); assert(!pthread_join(thread, NULL)); guarded_ok(&g);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "registry")) registry();
    else if (!strcmp(argv[1], "native-boundaries")) native_boundaries();
    else if (!strcmp(argv[1], "native-isolation")) native_isolation();
    else if (!strcmp(argv[1], "lifecycle")) lifecycle();
    else if (!strcmp(argv[1], "concurrent")) concurrent();
    else if (!strcmp(argv[1], "preinit") || !strcmp(argv[1], "null-out") || !strcmp(argv[1], "prepare-failed")) failure_case(argv[1]);
    else assert(!"unknown owned mode");
    return 0;
}
