// BotW 1.9.0 CPU AudioRenderer bring-up, selected only by DM_BOTW_AUDIO_CPU=1.
// The native SDK 20.5.22 renderer runs in its CPU/manual execution mode. The host
// selects that mode in copied parameters, drives native Execute from an SDK-created
// worker every 240 frames at 48 kHz, serializes every renderer-state call against
// rendering, tracks CPU audio memory for FlushDataCache and forwards enabled
// DeviceSink commands to the host PCM sink. Voices, mixes, effects, pools and update
// serialization stay native. With the variable unset every entry forwards unchanged.
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include "botw_audio_renderer.h"
#include "botw_pcm.h"
#include <stdarg.h>
#include <math.h>
#include <stdatomic.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>

enum {
    NAT_WORK_SIZE, NAT_CONFIG_SIZE, NAT_CONFIG_INIT, NAT_OPEN, NAT_UPDATE, NAT_START, NAT_STOP, NAT_CLOSE,
    NAT_VOICE_DROP, NAT_POOL_ACQUIRE, NAT_POOL_RELEASE, NAT_FLUSH, NAT_DISPATCH, NAT_EXECUTE, NAT_REVISION,
    NAT_THREAD_CREATE, NAT_THREAD_START, NAT_THREAD_WAIT, NAT_THREAD_DESTROY, NAT_THREAD_CURRENT, NAT_COUNT
};
// Module-relative export anchors of the BotW 1.9.0 SDK (ELF sha256 200ed035...9cd9),
// read from its dynamic symbol table. CPU mode refuses any other layout.
static const struct { const char *name; uint32_t offset; } k_native[NAT_COUNT] = {
    [NAT_WORK_SIZE] = {"_ZN2nn5audio30GetAudioRendererWorkBufferSizeERKNS0_22AudioRendererParameterE", 0x2214a0},
    [NAT_CONFIG_SIZE] = {"_ZN2nn5audio36GetAudioRendererConfigWorkBufferSizeERKNS0_22AudioRendererParameterE", 0x218890},
    [NAT_CONFIG_INIT] = {"_ZN2nn5audio29InitializeAudioRendererConfigEPNS0_19AudioRendererConfigERKNS0_22AudioRendererParameterEPvm", 0x2189b0},
    [NAT_OPEN] = {"_ZN2nn5audio17OpenAudioRendererEPNS0_19AudioRendererHandleEPNS_2os11SystemEventERKNS0_22AudioRendererParameterEPvm", 0x2218c0},
    [NAT_UPDATE] = {"_ZN2nn5audio26RequestUpdateAudioRendererENS0_19AudioRendererHandleEPKNS0_19AudioRendererConfigE", 0x221ae0},
    [NAT_START] = {"_ZN2nn5audio18StartAudioRendererENS0_19AudioRendererHandleE", 0x221a20},
    [NAT_STOP] = {"_ZN2nn5audio17StopAudioRendererENS0_19AudioRendererHandleE", 0x221a60},
    [NAT_CLOSE] = {"_ZN2nn5audio18CloseAudioRendererENS0_19AudioRendererHandleE", 0x2219d0},
    [NAT_VOICE_DROP] = {"_ZN2nn5audio34SetAudioRendererVoiceDropParameterENS0_19AudioRendererHandleEf", 0x221f20},
    [NAT_POOL_ACQUIRE] = {"_ZN2nn5audio17AcquireMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeEPvm", 0x21f270},
    [NAT_POOL_RELEASE] = {"_ZN2nn5audio17ReleaseMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeE", 0x21f2c0},
    [NAT_FLUSH] = {"_ZN2nn2os14FlushDataCacheEPKvm", 0x13bbb0},
    [NAT_DISPATCH] = {"_ZN2nn5audio3dsp20CommandListProcessor15DispatchCommandEPKNS0_13CommandHeaderE", 0x24b550},
    [NAT_EXECUTE] = {"_ZN2nn5audio29ExecuteAudioRendererRenderingENS0_19AudioRendererHandleE", 0x221e90},
    [NAT_REVISION] = {"_ZN2nn5audio6common18GetCurrentRevisionEv", 0x2281b0},
    [NAT_THREAD_CREATE] = {"_ZN2nn2os12CreateThreadEPNS0_10ThreadTypeEPFvPvES3_S3_mi", 0x140920},
    [NAT_THREAD_START] = {"_ZN2nn2os11StartThreadEPNS0_10ThreadTypeE", 0x140970},
    [NAT_THREAD_WAIT] = {"_ZN2nn2os10WaitThreadEPNS0_10ThreadTypeE", 0x140d00},
    [NAT_THREAD_DESTROY] = {"_ZN2nn2os13DestroyThreadEPNS0_10ThreadTypeE", 0x140950},
    [NAT_THREAD_CURRENT] = {"_ZN2nn2os16GetCurrentThreadEv", 0x140c20},
};
static void *_Atomic g_native[NAT_COUNT];

// AudioRendererHandle is 16 bytes passed by value in X0/X1; bool returns are W0 bit 0.
typedef size_t (*size_fn)(const void *parameter);
typedef void (*config_init_fn)(void *config, const void *parameter, void *work, size_t size);
typedef Result (*open_fn)(void *handle, void *event, const void *parameter, void *work, size_t size);
typedef Result (*update_fn)(uint64_t session, uint64_t client, const void *config);
typedef uint64_t (*handle_fn)(uint64_t session, uint64_t client);
typedef Result (*execute_fn)(uint64_t session, uint64_t client);
typedef void (*voice_drop_fn)(uint64_t session, uint64_t client, float value);
typedef uint64_t (*pool_acquire_fn)(void *config, void *pool, void *address, size_t size);
typedef uint64_t (*pool_release_fn)(void *config, void *pool);
typedef void (*flush_fn)(const void *address, size_t size);
typedef uint64_t (*dispatch_fn)(void *processor, const void *header);
typedef uint32_t (*revision_fn)(void);
typedef Result (*thread_create_fn)(void *thread, void (*entry)(void *), void *arg, void *stack, size_t size, int priority);
typedef void (*thread_fn)(void *thread);
typedef void *(*thread_current_fn)(void);
#define NATIVE(which, type) ((type)native(which))

enum {
    PARAM_SIZE = 56, PARAM_RATE = 0x00, PARAM_FRAMES = 0x04, PARAM_DEVICE = 0x2c, PARAM_MODE = 0x30, PARAM_REVISION = 0x34,
    AUDIO_RATE = 48000, AUDIO_FRAMES = 240, MAX_CHANNELS = 6,
    CMD_DEVICE_SINK = 20, CMD_DEVICE_SINK_SIZE = 328, CMD_NAME = 0x20, CMD_SESSION = 0x120, CMD_SAMPLES = 0x128,
    CMD_CHANNELS = 0x130, CMD_INDICES = 0x134, PROC_FRAMES = 0x20, PROC_RATE = 0x28, PROC_MIX = 0x30, PROC_MIX_COUNT = 0x38,
    WORKER_THREAD_TYPE = 0x200, WORKER_STACK = 1 << 20, WORKER_PRIORITY = 16,
    MAX_SESSIONS = 2, MAX_RANGES = 256, HEARTBEAT_FRAMES = 2000,
};
#define AUDIO_REVISION 0x3f564552u   // "REV?" (REV15), returned by native GetCurrentRevision
#define CMD_MAGIC 0xcafebabeu
#define FRAME_NS (1000000000ull * AUDIO_FRAMES / AUDIO_RATE)

static _Noreturn __attribute__((format(printf, 1, 2))) void audio_stop(const char *format, ...) {
    char text[512];
    va_list args; va_start(args, format); vsnprintf(text, sizeof text, format, args); va_end(args);
    LOG("[STOP] BotW AudioRenderer CPU: %s", text);
    fflush(stderr);
    const char *stop = getenv("DM_STOP_ON_UNIMPL");
    if (stop && *stop && strcmp(stop, "0")) _exit(3);
    abort();
}

static uint32_t rd32(const void *p, size_t offset) { uint32_t v; memcpy(&v, (const uint8_t *)p + offset, sizeof v); return v; }
static uint64_t rd64(const void *p, size_t offset) { uint64_t v; memcpy(&v, (const uint8_t *)p + offset, sizeof v); return v; }
static void wr32(void *p, size_t offset, uint32_t v) { memcpy((uint8_t *)p + offset, &v, sizeof v); }
static int within(uintptr_t base, size_t size, uintptr_t address, size_t bytes) {
    return address >= base && bytes <= size && address - base <= size - bytes;
}
static uint64_t mono_ns(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}
static uint64_t fpcr_read(void) { uint64_t v; __asm__ volatile("mrs %0, fpcr" : "=r"(v)); return v; }
static uint64_t fpsr_read(void) { uint64_t v; __asm__ volatile("mrs %0, fpsr" : "=r"(v)); return v; }
static void fpcr_write(uint64_t v) { __asm__ volatile("msr fpcr, %0" :: "r"(v)); }

// ---------- Mode, SDK identity and native resolution ----------
enum { MODE_UNSET, MODE_NATIVE, MODE_CPU };
static _Atomic int g_mode;
static int g_audible;
static pthread_mutex_t g_init_lock = PTHREAD_MUTEX_INITIALIZER;

static module_t *sdk_module(void) {
    for (int i = 0; i < g_nmod; i++) if (!strcmp(g_mod[i].name, "sdk")) return &g_mod[i];
    return NULL;
}
static void *native(int which) {
    void *fn = atomic_load_explicit(&g_native[which], memory_order_acquire);
    if (fn) return fn;
    module_t *sdk = sdk_module();
    if (!sdk || !(fn = module_sym(sdk, k_native[which].name))) die("BotW AudioRenderer: SDK export %s is missing", k_native[which].name);
    atomic_store_explicit(&g_native[which], fn, memory_order_release);
    return fn;
}
// Returns NULL and the selection, or the reason the environment is not a supported profile.
static const char *audio_env_parse(int *cpu, int *audible) {
    *cpu = *audible = 0;
    const char *flag = getenv("DM_BOTW_AUDIO_CPU");
    if (!flag || !strcmp(flag, "0")) return NULL;
    if (strcmp(flag, "1")) return "DM_BOTW_AUDIO_CPU must be unset, 0 or 1";
    const char *version = getenv("DM_GAME_VERSION");
    if (!version || strcmp(version, "1.9.0")) return "DM_BOTW_AUDIO_CPU=1 supports only DM_GAME_VERSION=1.9.0 (SDK 20.5.22)";
    const char *audio = getenv("DM_AUDIO");
    if (!audio || (strcmp(audio, "0") && strcmp(audio, "1")))
        return "DM_BOTW_AUDIO_CPU=1 requires DM_AUDIO=0 (explicit muted sink) or DM_AUDIO=1 (host output)";
    *cpu = 1; *audible = audio[0] == '1';
    return NULL;
}
// Resolves every native export and checks its verified module-relative offset.
// Returns the first mismatching index, or -1 when the whole layout matches.
static int sdk_layout_check(module_t *sdk, void *resolved[NAT_COUNT]) {
    for (int i = 0; i < NAT_COUNT; i++) {
        resolved[i] = module_sym(sdk, k_native[i].name);
        if (!resolved[i] || (uint8_t *)resolved[i] != sdk->base + k_native[i].offset) return i;
    }
    return -1;
}
static int audio_mode(void) {
    int mode = atomic_load_explicit(&g_mode, memory_order_acquire);
    if (mode) return mode;
    pthread_mutex_lock(&g_init_lock);
    if (!(mode = atomic_load_explicit(&g_mode, memory_order_relaxed))) {
        int cpu, audible;
        const char *bad = audio_env_parse(&cpu, &audible);
        if (bad) audio_stop("%s", bad);
        if (cpu) {
            module_t *sdk = sdk_module();
            if (!sdk) audio_stop("the sdk module is not loaded");
            void *resolved[NAT_COUNT];
            int mismatch = sdk_layout_check(sdk, resolved);
            if (mismatch >= 0)
                audio_stop("SDK export %s is not at sdk+%#x; this is not the verified BotW 1.9.0 SDK 20.5.22 layout",
                           k_native[mismatch].name, k_native[mismatch].offset);
            uint32_t revision = ((revision_fn)resolved[NAT_REVISION])();
            if (revision != AUDIO_REVISION) audio_stop("native renderer revision %#x, expected %#x", revision, AUDIO_REVISION);
            for (int i = 0; i < NAT_COUNT; i++) atomic_store_explicit(&g_native[i], resolved[i], memory_order_release);
            g_audible = audible;
            LOG("BotW AudioRenderer CPU: SDK 20.5.22 layout verified (%d exports, revision %#x); PCM output %s", NAT_COUNT,
                revision, audible ? "routed to the host audio device (DM_AUDIO=1)" : "MUTED by DM_AUDIO=0; rendering still runs");
        }
        mode = cpu ? MODE_CPU : MODE_NATIVE;
        atomic_store_explicit(&g_mode, mode, memory_order_release);
    }
    pthread_mutex_unlock(&g_init_lock);
    return mode;
}

// Copies the game's 56-byte parameter and selects the CPU device with manual execution.
// Returns NULL or the reason the parameter is outside the verified 48000/240 REV15 profile.
static const char *cpu_parameter(const void *parameter, uint8_t copy[PARAM_SIZE]) {
    if (!parameter) return "null AudioRendererParameter";
    memcpy(copy, parameter, PARAM_SIZE);
    if (rd32(copy, PARAM_RATE) != AUDIO_RATE || rd32(copy, PARAM_FRAMES) != AUDIO_FRAMES) return "only 48000 Hz with 240-sample frames is supported";
    if (rd32(copy, PARAM_REVISION) != AUDIO_REVISION) return "parameter revision is not REV15";
    uint32_t device = rd32(copy, PARAM_DEVICE), mode = rd32(copy, PARAM_MODE);
    if (!((device == 0 && mode == 0) || (device == 1 && mode == 1))) return "unknown rendering device / execution mode pair";
    wr32(copy, PARAM_DEVICE, 1);
    wr32(copy, PARAM_MODE, 1);
    return NULL;
}
static void cpu_parameter_or_stop(const void *parameter, uint8_t copy[PARAM_SIZE], const char *op) {
    const char *bad = cpu_parameter(parameter, copy);
    if (bad) audio_stop("%s: %s", op, bad);
}

// ---------- CPU audio memory owned by renderers, configs and memory pools ----------
enum { RANGE_WORK = 1, RANGE_CONFIG, RANGE_POOL };
static const char *const k_range_kind[] = {"?", "renderer work", "config work", "memory pool"};
typedef struct { uintptr_t base; size_t size; const void *key, *owner; int kind; } cpu_range;
static cpu_range g_range[MAX_RANGES];
static unsigned g_nrange;
static pthread_mutex_t g_range_lock = PTHREAD_MUTEX_INITIALIZER;

static int range_index(int kind, const void *key) {   // Holding g_range_lock
    for (unsigned i = 0; i < g_nrange; i++) if (g_range[i].kind == kind && g_range[i].key == key) return (int)i;
    return -1;
}
static void range_drop(unsigned i) { g_range[i] = g_range[--g_nrange]; }   // Holding g_range_lock
// Returns NULL or the reason the range cannot be tracked; overlaps are never merged.
static const char *range_add(int kind, const void *key, const void *owner, const void *address, size_t size) {
    uintptr_t base = (uintptr_t)address;
    if (!base || !size) return "empty range";
    if (size > UINTPTR_MAX - base) return "range wraps the address space";
    const char *bad = NULL;
    pthread_mutex_lock(&g_range_lock);
    if (range_index(kind, key) >= 0) bad = "object is already tracked";
    for (unsigned i = 0; !bad && i < g_nrange; i++)
        if (base < g_range[i].base + g_range[i].size && g_range[i].base < base + size) {
            LOG("BotW AudioRenderer CPU: %s %#lx+%#zx overlaps tracked %s %#lx+%#zx", k_range_kind[kind], (unsigned long)base, size,
                k_range_kind[g_range[i].kind], (unsigned long)g_range[i].base, g_range[i].size);
            bad = "range overlaps tracked audio memory";
        }
    if (!bad && g_nrange == MAX_RANGES) bad = "tracked audio range table is full";
    if (!bad) g_range[g_nrange++] = (cpu_range){base, size, key, owner, kind};
    pthread_mutex_unlock(&g_range_lock);
    return bad;
}
static int range_remove(int kind, const void *key) {
    pthread_mutex_lock(&g_range_lock);
    int i = range_index(kind, key);
    if (i >= 0) range_drop((unsigned)i);
    pthread_mutex_unlock(&g_range_lock);
    return i >= 0;
}
// Returns 1 and the owner when the object is tracked.
static int range_owner(int kind, const void *key, const void **owner) {
    pthread_mutex_lock(&g_range_lock);
    int i = range_index(kind, key);
    if (i >= 0) *owner = g_range[i].owner;
    pthread_mutex_unlock(&g_range_lock);
    return i >= 0;
}
static int range_contains(const void *address, size_t size) {
    int hit = 0;
    pthread_mutex_lock(&g_range_lock);
    for (unsigned i = 0; !hit && i < g_nrange; i++) hit = within(g_range[i].base, g_range[i].size, (uintptr_t)address, size);
    pthread_mutex_unlock(&g_range_lock);
    return hit;
}
// Binds a config to the renderer that first updates it. Returns NULL or the reason.
static const char *range_bind_config(const void *config, const void *session) {
    const char *bad = NULL;
    pthread_mutex_lock(&g_range_lock);
    int i = range_index(RANGE_CONFIG, config);
    if (i < 0) bad = "config was not initialized in CPU mode";
    else if (!g_range[i].owner) g_range[i].owner = session;
    else if (g_range[i].owner != session) bad = "config already belongs to another renderer";
    pthread_mutex_unlock(&g_range_lock);
    return bad;
}
// Retires the renderer work, its bound configs and their memory pools.
static void range_retire_session(const void *session) {
    pthread_mutex_lock(&g_range_lock);
    for (unsigned i = 0; i < g_nrange;) {
        if (g_range[i].kind != RANGE_CONFIG || g_range[i].owner != session) { i++; continue; }
        const void *config = g_range[i].key;
        for (unsigned j = 0; j < g_nrange;)
            if (g_range[j].kind == RANGE_POOL && g_range[j].owner == config) range_drop(j); else j++;
        i = 0;   // Pool removal may have moved this entry; rescan from the start
        int c = range_index(RANGE_CONFIG, config);
        if (c >= 0) range_drop((unsigned)c);
    }
    int w = range_index(RANGE_WORK, session);
    if (w >= 0) range_drop((unsigned)w);
    pthread_mutex_unlock(&g_range_lock);
}

// ---------- Renderer sessions and the SDK-created worker ----------
enum { SESSION_FREE, SESSION_OPENING, SESSION_OPEN, SESSION_CLOSING };
typedef struct {
    pthread_mutex_t call_lock;   // Serializes every native call that touches renderer state (lock order: call, wake, range)
    pthread_mutex_t wake_lock;
    pthread_cond_t wake;
    _Atomic int state, active, closing, halted;
    uint64_t session, client;    // Native AudioRendererHandle words
    void *thread_type, *stack;
    uint64_t owner_fpcr;
    botw_pcm_sink *sink;
    uintptr_t work; size_t work_size;
    unsigned sink_commands;      // DeviceSink commands in the current Execute (worker, under call_lock)
    int sink_logged;
    uint64_t updates, executions, output_frames, dropped_frames, drop_events, silent_frames;   // Under call_lock
    _Atomic uint64_t late_wakes;  // Worker-owned; read by heartbeats
} audio_session;
static audio_session g_session[MAX_SESSIONS];
static pthread_mutex_t g_session_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_once_t g_session_once = PTHREAD_ONCE_INIT;
static _Atomic int g_shutdown;
static _Atomic float g_output_volume = 1.0f;
static pthread_mutex_t g_output_volume_lock = PTHREAD_MUTEX_INITIALIZER;
static _Thread_local audio_session *t_rendering;   // Renderer whose native Execute runs on this thread

static void session_init_locks(void) {
    for (unsigned i = 0; i < MAX_SESSIONS; i++) {
        pthread_mutex_init(&g_session[i].call_lock, NULL);
        pthread_mutex_init(&g_session[i].wake_lock, NULL);
        pthread_cond_init(&g_session[i].wake, NULL);
    }
}
static unsigned session_id(const audio_session *s) { return (unsigned)(s - g_session); }
// Reserves a free slot; the explicit session limit never replaces a live renderer.
static audio_session *session_reserve(void) {
    pthread_once(&g_session_once, session_init_locks);
    audio_session *s = NULL;
    pthread_mutex_lock(&g_session_lock);
    for (unsigned i = 0; !s && i < MAX_SESSIONS; i++)
        if (atomic_load(&g_session[i].state) == SESSION_FREE) { s = &g_session[i]; atomic_store(&s->state, SESSION_OPENING); }
    pthread_mutex_unlock(&g_session_lock);
    if (!s) audio_stop("OpenAudioRenderer: all %d host renderer sessions are open", MAX_SESSIONS);
    return s;
}
static void session_release(audio_session *s) {   // Holding s->call_lock
    pthread_mutex_lock(&g_session_lock);
    s->session = s->client = 0; s->thread_type = s->stack = NULL; s->sink = NULL; s->work = 0; s->work_size = 0;
    s->owner_fpcr = 0; s->sink_commands = 0; s->sink_logged = 0;
    s->updates = s->executions = s->output_frames = s->dropped_frames = s->drop_events = s->silent_frames = 0;
    atomic_store(&s->late_wakes, 0);
    atomic_store(&s->active, 0); atomic_store(&s->closing, 0); atomic_store(&s->halted, 0);
    atomic_store(&s->state, SESSION_FREE);
    pthread_mutex_unlock(&g_session_lock);
}
// Finds an open renderer and returns it with call_lock held; any other handle stops.
static audio_session *session_enter(uint64_t session, uint64_t client, const char *op) {
    pthread_once(&g_session_once, session_init_locks);
    audio_session *s = NULL;
    pthread_mutex_lock(&g_session_lock);
    for (unsigned i = 0; !s && i < MAX_SESSIONS; i++) {
        int state = atomic_load(&g_session[i].state);
        if ((state == SESSION_OPEN || state == SESSION_CLOSING) && g_session[i].session == session && g_session[i].client == client) s = &g_session[i];
    }
    pthread_mutex_unlock(&g_session_lock);
    if (!s) audio_stop("%s on unknown renderer handle %#llx/%#llx", op, (unsigned long long)session, (unsigned long long)client);
    pthread_mutex_lock(&s->call_lock);
    if (atomic_load(&s->state) != SESSION_OPEN || s->session != session || s->client != client)
        audio_stop("%s on renderer %u while it is closing or closed", op, session_id(s));
    return s;
}
static void session_wake(audio_session *s, _Atomic int *flag, int value) {
    pthread_mutex_lock(&s->wake_lock);
    atomic_store(flag, value);
    pthread_cond_broadcast(&s->wake);
    pthread_mutex_unlock(&s->wake_lock);
}
static void session_heartbeat(audio_session *s, const char *why) {
    botw_pcm_counters c = {0};
    if (s->sink) botw_pcm_get_counters(s->sink, &c);
    LOG("BotW AudioRenderer CPU %u %s: %llu updates, %llu frames rendered (%llu without DeviceSink), %llu PCM frames accepted, "
        "%llu dropped, %llu late wakes; sink submitted %llu consumed %llu dropped %llu underrun %llu",
        session_id(s), why, (unsigned long long)s->updates, (unsigned long long)s->executions, (unsigned long long)s->silent_frames,
        (unsigned long long)s->output_frames, (unsigned long long)s->dropped_frames, (unsigned long long)atomic_load(&s->late_wakes),
        (unsigned long long)c.submitted_frames, (unsigned long long)c.consumed_frames, (unsigned long long)c.dropped_frames,
        (unsigned long long)c.underrun_frames);
}

// The native decoder shares scratch across sessions. Order: session call lock, then global Execute lock.
static pthread_mutex_t g_execute_lock = PTHREAD_MUTEX_INITIALIZER;
static void render_frame(audio_session *s) {
    pthread_mutex_lock(&s->call_lock);
    if (!atomic_load(&s->active) || atomic_load(&s->closing) || atomic_load(&s->halted)) { pthread_mutex_unlock(&s->call_lock); return; }
    s->sink_commands = 0;
    pthread_mutex_lock(&g_execute_lock);
    t_rendering = s;
    Result result = NATIVE(NAT_EXECUTE, execute_fn)(s->session, s->client);
    t_rendering = NULL;
    pthread_mutex_unlock(&g_execute_lock);
    if (result) audio_stop("native ExecuteAudioRendererRendering returned %#x on renderer %u", result, session_id(s));
    s->executions++;
    if (!s->sink_commands) s->silent_frames++;
    if (s->executions == 1 || s->executions % HEARTBEAT_FRAMES == 0) session_heartbeat(s, "progress");
    pthread_mutex_unlock(&s->call_lock);
}
// SDK thread callback. The SDK bootstrap has already installed TLR/TLS and the thread
// handle; guest ELF TLS may legitimately be NULL and is never invented here.
static void audio_worker(void *opaque) {
    audio_session *s = opaque;
    uint64_t entry_fpcr = fpcr_read(), entry_fpsr = fpsr_read();
    void *current = NATIVE(NAT_THREAD_CURRENT, thread_current_fn)();
    LOG("BotW AudioRenderer CPU %u: worker entered (handle %#x id %llu), entry FPCR/FPSR %#llx/%#llx, owner FPCR %#llx",
        session_id(s), rd32(s->thread_type, 0x1b0), (unsigned long long)rd64(s->thread_type, 0x1b8),
        (unsigned long long)entry_fpcr, (unsigned long long)entry_fpsr, (unsigned long long)s->owner_fpcr);
    if (current != s->thread_type) audio_stop("worker %u: SDK GetCurrentThread does not match its ThreadType", session_id(s));
    fpcr_write(s->owner_fpcr);   // Owner arithmetic mode for rendering; FPSR stays this thread's own
    uint64_t next = 0;
    pthread_mutex_lock(&s->wake_lock);
    while (!atomic_load(&s->closing) && !atomic_load(&s->halted)) {
        if (!atomic_load(&s->active)) { next = 0; pthread_cond_wait(&s->wake, &s->wake_lock); continue; }   // Parked while stopped
        uint64_t now = mono_ns();
        if (!next) next = now + FRAME_NS;
        if (now < next) {
            struct timespec rel = {(time_t)((next - now) / 1000000000ull), (long)((next - now) % 1000000000ull)};
            pthread_cond_timedwait_relative_np(&s->wake, &s->wake_lock, &rel);   // Deadline stays absolute CLOCK_MONOTONIC
            continue;
        }
        pthread_mutex_unlock(&s->wake_lock);
        render_frame(s);
        pthread_mutex_lock(&s->wake_lock);
        next += FRAME_NS;
        now = mono_ns();
        if (next <= now) { atomic_fetch_add(&s->late_wakes, 1); next = now + FRAME_NS; }   // Late: resume cadence without a catch-up burst
    }
    pthread_mutex_unlock(&s->wake_lock);
    LOG("BotW AudioRenderer CPU %u: worker leaving after %llu frames, FPCR/FPSR %#llx/%#llx", session_id(s),
        (unsigned long long)s->executions, (unsigned long long)fpcr_read(), (unsigned long long)fpsr_read());
    fpcr_write(entry_fpcr);
}
static void worker_create(audio_session *s) {
    void *thread = NULL, *stack = NULL;
    if (posix_memalign(&thread, 16, WORKER_THREAD_TYPE) || posix_memalign(&stack, 4096, WORKER_STACK))
        audio_stop("cannot allocate the renderer %u worker", session_id(s));
    memset(thread, 0, WORKER_THREAD_TYPE);
    s->thread_type = thread; s->stack = stack;
    Result result = NATIVE(NAT_THREAD_CREATE, thread_create_fn)(thread, audio_worker, s, stack, WORKER_STACK, WORKER_PRIORITY);
    if (result) audio_stop("native CreateThread for renderer %u returned %#x", session_id(s), result);
    NATIVE(NAT_THREAD_START, thread_fn)(thread);
}

// ---------- DeviceSink output ----------
static int16_t clamp16(int32_t v) { return v > INT16_MAX ? INT16_MAX : v < INT16_MIN ? INT16_MIN : (int16_t)v; }
// Copies one enabled DeviceSink command into interleaved PCM16. Returns NULL or the reason.
static int audio_read_exact(const void *source, void *out, size_t size) {
    mach_vm_size_t got = 0;
    return source && mach_vm_read_overwrite(mach_task_self(), (mach_vm_address_t)(uintptr_t)source, size,
                                            (mach_vm_address_t)(uintptr_t)out, &got) == KERN_SUCCESS && got == size;
}
static const char *device_sink(audio_session *s, const uint8_t *processor_input, const uint8_t *command_input) {
    uint8_t processor[0x40], prefix[8], cmd[CMD_DEVICE_SINK_SIZE];
    if (!audio_read_exact(processor_input, processor, sizeof processor)) return "unreadable command processor";
    if (!audio_read_exact(command_input, prefix, sizeof prefix)) return "unreadable command header";
    uint16_t declared_size; memcpy(&declared_size, prefix + 6, sizeof declared_size);
    if (declared_size != CMD_DEVICE_SINK_SIZE) return "unexpected DeviceSink command size";
    uint64_t list = rd64(processor, 0), current = rd64(processor, 8), list_bytes = rd64(processor, 0x10);
    if (s->work_size < 4096 || list_bytes < 32 ||
        !within(s->work, s->work_size - 4096, (uintptr_t)list, (size_t)list_bytes))
        return "command list lies outside native renderer work (client-state page excluded)";
    if (current != (uintptr_t)command_input || current < list + 32)
        return "DeviceSink is not the processor current command";
    if (!within((uintptr_t)list, (size_t)list_bytes, (uintptr_t)current, sizeof cmd))
        return "DeviceSink command extends beyond the command list";
    if (!audio_read_exact(command_input, cmd, sizeof cmd)) return "unreadable DeviceSink command";
    if (rd32(cmd, 0) != CMD_MAGIC) return "bad command magic";
    if (!(cmd[4] & 1)) return "command is disabled";
    uint16_t size; memcpy(&size, cmd + 6, sizeof size);
    if (size != CMD_DEVICE_SINK_SIZE) return "unexpected DeviceSink command size";
    uint32_t frames = rd32(processor, PROC_FRAMES), rate = rd32(processor, PROC_RATE), mix_count = rd32(processor, PROC_MIX_COUNT);
    if (frames != AUDIO_FRAMES || rate != AUDIO_RATE) return "processor is not rendering 240 frames at 48000 Hz";
    uint32_t channels = rd32(cmd, CMD_CHANNELS);
    if (channels != 1 && channels != 2 && channels != 6) return "unsupported channel count";
    uint64_t samples = rd64(cmd, CMD_SAMPLES);
    if (!samples || samples != rd64(processor, PROC_MIX)) return "sample storage is not the processor mix buffer (upsampler path unsupported)";
    if (!mix_count) return "processor has no mix buffers";   // A 32-bit count times 960 bytes cannot overflow size_t
    if (!within(s->work, s->work_size - 4096, (uintptr_t)samples, (size_t)mix_count * 4u * AUDIO_FRAMES)) return "mix buffers lie outside the renderer work buffer";
    int16_t index[MAX_CHANNELS];
    for (uint32_t c = 0; c < channels; c++) {
        memcpy(&index[c], cmd + CMD_INDICES + 2 * c, sizeof index[c]);
        if (index[c] < 0 || (uint32_t)index[c] >= mix_count) return "channel index outside the mix buffers";
    }
    if (++s->sink_commands > 1) return "more than one DeviceSink command in one frame";
    if (!s->sink_logged) {
        char name[64]; size_t n = 0;
        for (; n < sizeof name - 1 && cmd[CMD_NAME + n]; n++) name[n] = cmd[CMD_NAME + n] >= 0x20 && cmd[CMD_NAME + n] < 0x7f ? (char)cmd[CMD_NAME + n] : '?';
        name[n] = 0;
        LOG("BotW AudioRenderer CPU %u: first DeviceSink \"%s\" session field %u, %u channels from mix buffers %d,%d,%d,%d,%d,%d of %u",
            session_id(s), name, rd32(cmd, CMD_SESSION), channels, index[0], channels > 1 ? index[1] : -1, channels > 2 ? index[2] : -1,
            channels > 2 ? index[3] : -1, channels > 2 ? index[4] : -1, channels > 2 ? index[5] : -1, mix_count);
        s->sink_logged = 1;
    }
    if (!s->sink) return NULL;   // Host audio shut down: the frame is rendered but not output
    int16_t pcm[AUDIO_FRAMES * MAX_CHANNELS];
    const int32_t *mix = (const int32_t *)(uintptr_t)samples;
    for (uint32_t f = 0; f < AUDIO_FRAMES; f++)
        for (uint32_t c = 0; c < channels; c++) pcm[f * channels + c] = clamp16(mix[(size_t)index[c] * AUDIO_FRAMES + f]);
    size_t accepted = botw_pcm_write(s->sink, pcm, AUDIO_FRAMES, channels);
    s->output_frames += accepted;
    if (accepted < AUDIO_FRAMES) {
        s->dropped_frames += AUDIO_FRAMES - accepted;
        if (!(s->drop_events++ & 1023))
            LOG("BotW AudioRenderer CPU %u: PCM queue full, dropped %zu frames (%llu total, %llu drop events)", session_id(s),
                AUDIO_FRAMES - accepted, (unsigned long long)s->dropped_frames, (unsigned long long)s->drop_events);
    }
    return NULL;
}

// ---------- Intercepted SDK exports ----------
// Process ignores DispatchCommand's return value; DeviceSink (type 20) is a native no-op.
HLE_FORCE("_ZN2nn5audio3dsp20CommandListProcessor15DispatchCommandEPKNS0_13CommandHeaderE",
          "BotW CPU audio: enabled DeviceSink commands feed the host PCM sink; others stay native",
          uint64_t, botw_audio_dispatch, void *processor, const void *header) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_DISPATCH, dispatch_fn)(processor, header);
    uint8_t prefix[8];
    if (!audio_read_exact(header, prefix, sizeof prefix)) audio_stop("unreadable audio command header");
    if (prefix[5] != CMD_DEVICE_SINK) return NATIVE(NAT_DISPATCH, dispatch_fn)(processor, header);
    audio_session *s = t_rendering;
    if (!s) audio_stop("DeviceSink command outside a host-owned Execute");
    const char *bad = device_sink(s, processor, header);
    if (bad) audio_stop("renderer %u DeviceSink: %s", session_id(s), bad);
    return 0;
}

// CPU-only storage needs ordering, not cache maintenance; anything else stops.
HLE_FORCE("_ZN2nn2os14FlushDataCacheEPKvm", "BotW CPU audio: fence tracked CPU audio buffers only",
          void, botw_audio_flush, const void *address, size_t size) {
    if (audio_mode() != MODE_CPU) { NATIVE(NAT_FLUSH, flush_fn)(address, size); return; }
    if (!size) return;   // Native returns before any cache maintenance
    if (!range_contains(address, size))
        audio_stop("FlushDataCache(%p, %#zx) is outside tracked CPU audio memory; GPU/device coherence is not emulated", address, size);
    atomic_thread_fence(memory_order_seq_cst);
}

static void log_parameter(const uint8_t *p) {
    LOG("BotW AudioRenderer CPU: parameter rate %u frames %u +08 %u +0c %u voices %u +14 %u +18 %u +1c %u +20 %u splitters %u/%u "
        "device/mode %u/%u -> 1/1 revision %#x", rd32(p, 0), rd32(p, 4), rd32(p, 8), rd32(p, 12), rd32(p, 16), rd32(p, 20),
        rd32(p, 24), rd32(p, 28), p[32], rd32(p, 36), rd32(p, 40), rd32(p, 44), rd32(p, 48), rd32(p, 52));
}
HLE_FORCE("_ZN2nn5audio30GetAudioRendererWorkBufferSizeERKNS0_22AudioRendererParameterE",
          "BotW CPU audio: size the CPU/manual renderer", size_t, botw_audio_work_size, const void *parameter) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_WORK_SIZE, size_fn)(parameter);
    uint8_t copy[PARAM_SIZE];
    cpu_parameter_or_stop(parameter, copy, "GetAudioRendererWorkBufferSize");
    log_parameter(parameter);
    size_t size = NATIVE(NAT_WORK_SIZE, size_fn)(copy);
    LOG("BotW AudioRenderer CPU: native work buffer size %#zx", size);
    return size;
}
HLE_FORCE("_ZN2nn5audio36GetAudioRendererConfigWorkBufferSizeERKNS0_22AudioRendererParameterE",
          "BotW CPU audio: size the CPU/manual config", size_t, botw_audio_config_size, const void *parameter) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_CONFIG_SIZE, size_fn)(parameter);
    uint8_t copy[PARAM_SIZE];
    cpu_parameter_or_stop(parameter, copy, "GetAudioRendererConfigWorkBufferSize");
    size_t size = NATIVE(NAT_CONFIG_SIZE, size_fn)(copy);
    LOG("BotW AudioRenderer CPU: native config work size %#zx", size);
    return size;
}
HLE_FORCE("_ZN2nn5audio29InitializeAudioRendererConfigEPNS0_19AudioRendererConfigERKNS0_22AudioRendererParameterEPvm",
          "BotW CPU audio: CPU/manual config with tracked work memory",
          void, botw_audio_config_init, void *config, const void *parameter, void *work, size_t size) {
    if (audio_mode() != MODE_CPU) { NATIVE(NAT_CONFIG_INIT, config_init_fn)(config, parameter, work, size); return; }
    uint8_t copy[PARAM_SIZE];
    cpu_parameter_or_stop(parameter, copy, "InitializeAudioRendererConfig");
    if (!config) audio_stop("InitializeAudioRendererConfig: null config");
    const char *bad = range_add(RANGE_CONFIG, config, NULL, work, size);
    if (bad) audio_stop("InitializeAudioRendererConfig %p work %p+%#zx: %s", config, work, size, bad);
    NATIVE(NAT_CONFIG_INIT, config_init_fn)(config, copy, work, size);
    LOG("BotW AudioRenderer CPU: config %p initialized with work %p+%#zx", config, work, size);
}
HLE_FORCE("_ZN2nn5audio17OpenAudioRendererEPNS0_19AudioRendererHandleEPNS_2os11SystemEventERKNS0_22AudioRendererParameterEPvm",
          "BotW CPU audio: open the native CPU/manual renderer and its SDK worker",
          Result, botw_audio_open, void *handle, void *event, const void *parameter, void *work, size_t size) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_OPEN, open_fn)(handle, event, parameter, work, size);
    uint8_t copy[PARAM_SIZE];
    cpu_parameter_or_stop(parameter, copy, "OpenAudioRenderer");
    if (!handle || !event) audio_stop("OpenAudioRenderer: null handle or SystemEvent");
    audio_session *s = session_reserve();
    pthread_mutex_lock(&s->call_lock);
    const char *bad = range_add(RANGE_WORK, s, s, work, size);
    if (bad) audio_stop("OpenAudioRenderer work %p+%#zx: %s", work, size, bad);
    s->work = (uintptr_t)work; s->work_size = size;
    s->owner_fpcr = fpcr_read();
    int halted = atomic_load(&g_shutdown);
    atomic_store(&s->halted, halted);
    if (!halted && !(s->sink = botw_pcm_open(g_audible)))
        audio_stop("OpenAudioRenderer: the %s PCM sink could not be opened", g_audible ? "audible" : "muted");
    if (s->sink && botw_pcm_set_volume(s->sink, atomic_load(&g_output_volume)))
        audio_stop("OpenAudioRenderer: PCM output gain was rejected");
    Result result = NATIVE(NAT_OPEN, open_fn)(handle, event, copy, work, size);
    if (result) {
        LOG("BotW AudioRenderer CPU: native OpenAudioRenderer returned %#x", result);
        if (s->sink) botw_pcm_close(s->sink);
        range_remove(RANGE_WORK, s);
        session_release(s);
        pthread_mutex_unlock(&s->call_lock);
        return result;
    }
    uint64_t words[2]; memcpy(words, handle, sizeof words);
    if (!words[0] || !words[1]) audio_stop("native OpenAudioRenderer succeeded without a handle");
    pthread_mutex_lock(&g_session_lock);
    for (unsigned i = 0; i < MAX_SESSIONS; i++)
        if (&g_session[i] != s && atomic_load(&g_session[i].state) != SESSION_FREE && g_session[i].session == words[0] && g_session[i].client == words[1])
            audio_stop("native OpenAudioRenderer returned the live handle of renderer %u", i);
    s->session = words[0]; s->client = words[1];
    pthread_mutex_unlock(&g_session_lock);
    worker_create(s);
    atomic_store(&s->state, SESSION_OPEN);
    LOG("BotW AudioRenderer CPU %u: opened handle %#llx/%#llx, work %p+%#zx, event %p, worker priority %d stack %#x, owner FPCR %#llx",
        session_id(s), (unsigned long long)s->session, (unsigned long long)s->client, work, size, event, WORKER_PRIORITY, WORKER_STACK,
        (unsigned long long)s->owner_fpcr);
    pthread_mutex_unlock(&s->call_lock);
    return 0;
}
HLE_FORCE("_ZN2nn5audio26RequestUpdateAudioRendererENS0_19AudioRendererHandleEPKNS0_19AudioRendererConfigE",
          "BotW CPU audio: serialize native updates with rendering",
          Result, botw_audio_update, uint64_t session, uint64_t client, const void *config) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_UPDATE, update_fn)(session, client, config);
    audio_session *s = session_enter(session, client, "RequestUpdateAudioRenderer");
    const char *bad = range_bind_config(config, s);
    if (bad) audio_stop("RequestUpdateAudioRenderer config %p: %s", config, bad);
    Result result = NATIVE(NAT_UPDATE, update_fn)(session, client, config);
    if (!s->updates++ || result) LOG("BotW AudioRenderer CPU %u: RequestUpdate #%llu returned %#x", session_id(s), (unsigned long long)s->updates, result);
    pthread_mutex_unlock(&s->call_lock);
    return result;
}
// Start/Stop/Close return W0 only on success (native failure aborts); X0 passes through.
HLE_FORCE("_ZN2nn5audio18StartAudioRendererENS0_19AudioRendererHandleE", "BotW CPU audio: start rendering cadence",
          uint64_t, botw_audio_start, uint64_t session, uint64_t client) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_START, handle_fn)(session, client);
    audio_session *s = session_enter(session, client, "StartAudioRenderer");
    uint64_t result = NATIVE(NAT_START, handle_fn)(session, client);
    if (s->sink && botw_pcm_set_active(s->sink, 1)) audio_stop("renderer %u: PCM sink failed to start", session_id(s));
    session_wake(s, &s->active, 1);
    LOG("BotW AudioRenderer CPU %u: started", session_id(s));
    pthread_mutex_unlock(&s->call_lock);
    return result;
}
HLE_FORCE("_ZN2nn5audio17StopAudioRendererENS0_19AudioRendererHandleE", "BotW CPU audio: park rendering cadence",
          uint64_t, botw_audio_stop_renderer, uint64_t session, uint64_t client) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_STOP, handle_fn)(session, client);
    audio_session *s = session_enter(session, client, "StopAudioRenderer");
    uint64_t result = NATIVE(NAT_STOP, handle_fn)(session, client);
    session_wake(s, &s->active, 0);
    if (s->sink && botw_pcm_set_active(s->sink, 0)) audio_stop("renderer %u: PCM sink failed to stop", session_id(s));
    session_heartbeat(s, "stopped");
    pthread_mutex_unlock(&s->call_lock);
    return result;
}
HLE_FORCE("_ZN2nn5audio34SetAudioRendererVoiceDropParameterENS0_19AudioRendererHandleEf",
          "BotW CPU audio: serialize native voice-drop changes with rendering",
          void, botw_audio_voice_drop, uint64_t session, uint64_t client, float value) {
    if (audio_mode() != MODE_CPU) { NATIVE(NAT_VOICE_DROP, voice_drop_fn)(session, client, value); return; }
    audio_session *s = session_enter(session, client, "SetAudioRendererVoiceDropParameter");
    NATIVE(NAT_VOICE_DROP, voice_drop_fn)(session, client, value);
    pthread_mutex_unlock(&s->call_lock);
}
HLE_FORCE("_ZN2nn5audio18CloseAudioRendererENS0_19AudioRendererHandleE", "BotW CPU audio: join the worker before native Close",
          uint64_t, botw_audio_close, uint64_t session, uint64_t client) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_CLOSE, handle_fn)(session, client);
    audio_session *s = session_enter(session, client, "CloseAudioRenderer");
    atomic_store(&s->state, SESSION_CLOSING);
    session_wake(s, &s->closing, 1);
    pthread_mutex_unlock(&s->call_lock);
    NATIVE(NAT_THREAD_WAIT, thread_fn)(s->thread_type);      // Joined outside the call lock
    NATIVE(NAT_THREAD_DESTROY, thread_fn)(s->thread_type);
    pthread_mutex_lock(&s->call_lock);
    uint64_t result = NATIVE(NAT_CLOSE, handle_fn)(session, client);
    session_heartbeat(s, "closed");
    if (s->sink) botw_pcm_close(s->sink);
    s->sink = NULL;
    range_retire_session(s);
    free(s->thread_type);
    free(s->stack);
    session_release(s);
    pthread_mutex_unlock(&s->call_lock);
    return result;
}
HLE_FORCE("_ZN2nn5audio17AcquireMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeEPvm",
          "BotW CPU audio: track memory-pool CPU ranges",
          uint64_t, botw_audio_pool_acquire, void *config, void *pool, void *address, size_t size) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_POOL_ACQUIRE, pool_acquire_fn)(config, pool, address, size);
    const void *owner;
    if (!pool || !range_owner(RANGE_CONFIG, config, &owner)) audio_stop("AcquireMemoryPool on config %p not initialized in CPU mode", config);
    const char *bad = range_add(RANGE_POOL, pool, config, address, size);
    if (bad) audio_stop("AcquireMemoryPool %p+%#zx: %s", address, size, bad);
    uint64_t result = NATIVE(NAT_POOL_ACQUIRE, pool_acquire_fn)(config, pool, address, size);
    if (!(result & 1)) {
        range_remove(RANGE_POOL, pool);
        LOG("BotW AudioRenderer CPU: native AcquireMemoryPool refused %p+%#zx", address, size);
    }
    return result;
}
HLE_FORCE("_ZN2nn5audio17ReleaseMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeE",
          "BotW CPU audio: retire memory-pool CPU ranges",
          uint64_t, botw_audio_pool_release, void *config, void *pool) {
    if (audio_mode() != MODE_CPU) return NATIVE(NAT_POOL_RELEASE, pool_release_fn)(config, pool);
    const void *owner = NULL;
    if (!range_owner(RANGE_POOL, pool, &owner) || owner != config) audio_stop("ReleaseMemoryPool on untracked pool %p", pool);
    uint64_t result = NATIVE(NAT_POOL_RELEASE, pool_release_fn)(config, pool);
    range_remove(RANGE_POOL, pool);
    return result;
}

void botw_audio_renderer_shutdown(void) {
    if (atomic_load_explicit(&g_mode, memory_order_acquire) != MODE_CPU) return;
    atomic_store(&g_shutdown, 1);
    pthread_once(&g_session_once, session_init_locks);
    for (unsigned i = 0; i < MAX_SESSIONS; i++) {
        audio_session *s = &g_session[i];
        pthread_mutex_lock(&s->call_lock);
        if (atomic_load(&s->state) != SESSION_FREE) {
            session_wake(s, &s->halted, 1);
            if (s->sink) { session_heartbeat(s, "halted by host shutdown"); botw_pcm_close(s->sink); }
            s->sink = NULL;
        }
        pthread_mutex_unlock(&s->call_lock);
    }
}

int botw_audio_renderer_cpu_enabled(void) { return audio_mode() == MODE_CPU; }
void botw_audio_renderer_set_output_volume(float volume) {
    if (audio_mode() != MODE_CPU) return;
    if (!isfinite(volume)) audio_stop("non-finite output volume is unsupported");
    if (volume < 0) volume = 0;
    if (volume > 128) volume = 128;
    pthread_mutex_lock(&g_output_volume_lock);
    atomic_store(&g_output_volume, volume);
    pthread_once(&g_session_once, session_init_locks);
    for (unsigned i = 0; i < MAX_SESSIONS; i++) {
        audio_session *s = &g_session[i];
        pthread_mutex_lock(&s->call_lock);
        if (s->sink && botw_pcm_set_volume(s->sink, volume)) audio_stop("renderer %u rejected output volume", i);
        pthread_mutex_unlock(&s->call_lock);
    }
    pthread_mutex_unlock(&g_output_volume_lock);
}
