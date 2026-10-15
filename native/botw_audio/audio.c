/* Base-1.0 SDK facade over the reused MIT AudioOut module. See LICENSE and
 * upstream_audio.inc for source provenance and the retained queue/renderer. */
#include "audio.h"
#include <inttypes.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(__APPLE__) && !defined(BOTW_AUDIO_TEST_BACKEND)
#include <AudioToolbox/AudioToolbox.h>
#include <TargetConditionals.h>
#include <dispatch/dispatch.h>
typedef dispatch_semaphore_t audio_semaphore;
static audio_semaphore semaphore_create(void) { return dispatch_semaphore_create(0); }
static void semaphore_signal(audio_semaphore s) { dispatch_semaphore_signal(s); }
static void semaphore_wait(audio_semaphore s) { dispatch_semaphore_wait(s, DISPATCH_TIME_FOREVER); }
static void semaphore_destroy(audio_semaphore s) { dispatch_release(s); }
#else
/* The non-Apple product rejects context creation. This portable semaphore is
 * used only by deterministic transport tests, never a product audio callback. */
typedef struct audio_test_semaphore { pthread_mutex_t lock; pthread_cond_t cond; unsigned count; } *audio_semaphore;
static audio_semaphore semaphore_create(void) {
    audio_semaphore s = calloc(1, sizeof *s); if (!s) return NULL;
    if (pthread_mutex_init(&s->lock, NULL)) { free(s); return NULL; }
    if (pthread_cond_init(&s->cond, NULL)) { pthread_mutex_destroy(&s->lock); free(s); return NULL; }
    return s;
}
static void semaphore_signal(audio_semaphore s) {
    pthread_mutex_lock(&s->lock); s->count++; pthread_cond_signal(&s->cond); pthread_mutex_unlock(&s->lock);
}
static void semaphore_wait(audio_semaphore s) {
    pthread_mutex_lock(&s->lock); while (!s->count) pthread_cond_wait(&s->cond, &s->lock);
    s->count--; pthread_mutex_unlock(&s->lock);
}
static void semaphore_destroy(audio_semaphore s) {
    pthread_cond_destroy(&s->cond); pthread_mutex_destroy(&s->lock); free(s);
}
#endif
struct aout;
struct botw_audio {
    botw_audio_callbacks cb;
    struct aout *outputs[8];
    int active, closing;
    uint64_t queued_bytes;
    pthread_mutex_t error_lock;
    char deferred[512];
    _Atomic int rt_error;
    _Atomic uint64_t render_callbacks, rendered_frames, completed_buffers;
};
static pthread_mutex_t api_lock = PTHREAD_MUTEX_INITIALIZER;
static botw_audio *g_audio;
static int problem(char *error, size_t cap, const char *format, ...) {
    if (error && cap) { va_list args; va_start(args, format); vsnprintf(error, cap, format, args); va_end(args); }
    return -1;
}
static void clear_error(char *error, size_t cap) { if (error && cap) *error = 0; }
static void save_deferred(botw_audio *ctx, const char *message) {
    pthread_mutex_lock(&ctx->error_lock);
    if (!*ctx->deferred) snprintf(ctx->deferred, sizeof ctx->deferred, "%s", message);
    pthread_mutex_unlock(&ctx->error_lock);
}
#include "upstream_audio.inc"

typedef struct { botw_audio *ctx; botw_audio_callbacks cb; char error[512], log[256]; } audio_call;
static audio_call begin_call(void) {
    audio_call call = {0}; pthread_mutex_lock(&api_lock); call.ctx = g_audio;
    if (!call.ctx) { problem(call.error, sizeof call.error, "AudioOut host context is not configured"); return call; }
    call.cb = call.ctx->cb;
    if (call.ctx->closing) { problem(call.error, sizeof call.error, "AudioOut host context is closing"); return call; }
    pthread_mutex_lock(&call.ctx->error_lock);
    if (*call.ctx->deferred) {
        snprintf(call.error, sizeof call.error, "%s", call.ctx->deferred); *call.ctx->deferred = 0;
    }
    pthread_mutex_unlock(&call.ctx->error_lock);
    int rt = atomic_exchange_explicit(&call.ctx->rt_error, 0, memory_order_acq_rel);
    if (!*call.error && rt) {
        const char *reason = rt == 1 ? "released queue overflow" : rt == 2 ? "PCM frame alignment" :
            rt == 3 ? "output buffer list/data" : rt == 4 ? "output capacity below requested frames" :
            rt == 5 ? "output channel count mismatch" : "unknown callback state";
        problem(call.error, sizeof call.error, "AudioOut real-time callback rejected %s (%d)", reason, rt);
    }
    return call;
}
static void end_call(audio_call *call) {
    pthread_mutex_unlock(&api_lock);
    /* These may reenter or longjmp. Never invoke them with an audio lock held. */
    if (*call->error) { if (call->cb.fault) call->cb.fault(call->cb.opaque, call->error); }
    else if (*call->log && call->cb.log) call->cb.log(call->cb.opaque, call->log);
}
static int read_guest(audio_call *call, uint64_t source, void *dest, size_t bytes, const char *what) {
    if (*call->error) return -1;
    if (!source || bytes > UINT64_MAX - source)
        return problem(call->error, sizeof call->error, "AudioOut %s guest span overflow/null", what);
    if (call->cb.read_guest(call->cb.opaque, source, dest, bytes, call->error, sizeof call->error)) {
        if (!*call->error) problem(call->error, sizeof call->error, "AudioOut %s guest read rejected", what); return -1;
    }
    return 0;
}
static int write_guest(audio_call *call, uint64_t target, const void *source, size_t bytes, const char *what) {
    if (*call->error) return -1;
    if (!target || bytes > UINT64_MAX - target)
        return problem(call->error, sizeof call->error, "AudioOut %s guest span overflow/null", what);
    if (call->cb.write_guest(call->cb.opaque, target, source, bytes, call->error, sizeof call->error)) {
        if (!*call->error) problem(call->error, sizeof call->error, "AudioOut %s guest write rejected", what); return -1;
    }
    return 0;
}
static aout *lookup(audio_call *call, const void *guest) {
    if (*call->error) return NULL;
    for (unsigned i = 0; i < AOMAX; i++) {
        aout *a = call->ctx->outputs[i];
        if (a && a->guest == (uint64_t)(uintptr_t)guest && !a->retiring) return a;
    }
    problem(call->error, sizeof call->error, "AudioOut object %#" PRIxPTR " is not a live host output", (uintptr_t)guest); return NULL;
}
static int destroy_output(botw_audio *ctx, unsigned index, char *error, size_t cap) {
    aout *a = ctx->outputs[index]; if (!a) return 0;
    a->retiring = 1; atomic_store_explicit(&a->enabled, 0, memory_order_release);
    atomic_store_explicit(&a->state, STATE_STOPPED, memory_order_release);
    if (a->running) {
        if (platform_stop(a, error, cap)) return -1;
        a->running = 0;
    }
    if (a->au && platform_dispose(a, error, cap)) return -1;
    if (a->notify_created && !a->notify_joined) {
        atomic_store_explicit(&a->notify_run, 0, memory_order_release); semaphore_signal(a->notify_sem);
        int result = pthread_join(a->notify_th, NULL);
        if (result) return problem(error, cap, "AudioOut notifier join failed (%d)", result);
        a->notify_joined = 1;
    }
    if (a->unattached_reader) {
        if (ctx->cb.close_handle(ctx->cb.opaque, a->unattached_reader, error, cap)) {
            if (!error || !cap || !*error) problem(error, cap, "AudioOut unattached event reader close rejected"); return -1;
        }
        a->unattached_reader = 0;
    }
    if (a->writer) {
        if (ctx->cb.close_handle(ctx->cb.opaque, a->writer, error, cap)) {
            if (!error || !cap || !*error) problem(error, cap, "AudioOut kernel event writer close rejected"); return -1;
        }
        a->writer = 0;
    }
    pcm_node *node = a->nodes;
    while (node) { pcm_node *next = node->next; ctx->queued_bytes -= node->buffer.size; free(node->buffer.ptr); free(node); node = next; }
    if (a->notify_sem) semaphore_destroy(a->notify_sem);
    free(a->mixbuf); pthread_mutex_destroy(&a->m); free(a); ctx->outputs[index] = NULL; return 0;
}

botw_audio *botw_audio_create(const botw_audio_callbacks *cb, int initial_active, char *error, size_t cap) {
    clear_error(error, cap);
#if !defined(__APPLE__) && !defined(BOTW_AUDIO_TEST_BACKEND)
    (void)cb; (void)initial_active; problem(error, cap, "AudioOut requires Apple CoreAudio/AudioUnit; no silent backend"); return NULL;
#else
    if (!cb || !cb->read_guest || !cb->write_guest || !cb->create_event || !cb->signal_event ||
        !cb->close_handle || !cb->attach_system_event || !cb->fault || (initial_active != 0 && initial_active != 1)) {
        problem(error, cap, "AudioOut callbacks and explicit active state are required"); return NULL;
    }
    pthread_mutex_lock(&api_lock);
    botw_audio *ctx = NULL;
    if (g_audio) problem(error, cap, "AudioOut host context already exists");
    else {
        ctx = calloc(1, sizeof *ctx);
        if (!ctx) problem(error, cap, "AudioOut context allocation failed");
        else {
            int result = pthread_mutex_init(&ctx->error_lock, NULL);
            if (result) { problem(error, cap, "AudioOut error mutex initialization failed (%d)", result); free(ctx); ctx = NULL; }
            else {
                ctx->cb = *cb; ctx->active = initial_active;
                atomic_init(&ctx->rt_error, 0); atomic_init(&ctx->render_callbacks, 0);
                atomic_init(&ctx->rendered_frames, 0); atomic_init(&ctx->completed_buffers, 0); g_audio = ctx;
            }
        }
    }
    pthread_mutex_unlock(&api_lock); return ctx;
#endif
}
int botw_audio_get_stats(botw_audio *ctx, botw_audio_stats *out) {
    if (!out) return -1;
    pthread_mutex_lock(&api_lock);
    if (!ctx || ctx != g_audio) { pthread_mutex_unlock(&api_lock); return -1; }
    *out = (botw_audio_stats){.render_callbacks = atomic_load_explicit(&ctx->render_callbacks, memory_order_relaxed),
        .rendered_frames = atomic_load_explicit(&ctx->rendered_frames, memory_order_relaxed),
        .completed_buffers = atomic_load_explicit(&ctx->completed_buffers, memory_order_relaxed), .queued_bytes = ctx->queued_bytes};
    for (unsigned i = 0; i < AOMAX; i++) if (ctx->outputs[i] && !ctx->outputs[i]->retiring) out->live_outputs++;
    pthread_mutex_unlock(&api_lock); return 0;
}
int botw_audio_set_active(botw_audio *ctx, int active, char *error, size_t cap) {
    clear_error(error, cap); pthread_mutex_lock(&api_lock); int result = 0;
    if (!ctx || ctx != g_audio || ctx->closing || (active != 0 && active != 1))
        result = problem(error, cap, "AudioOut lifecycle requires live context and active=0/1");
    else if (ctx->active != active || !active) {
        if (!active) {
            ctx->active = 0;
            for (unsigned i = 0; i < AOMAX; i++) if (ctx->outputs[i]) atomic_store(&ctx->outputs[i]->enabled, 0);
            for (unsigned i = 0; i < AOMAX; i++) {
                aout *a = ctx->outputs[i]; if (!a || !a->running) continue;
                char item[512] = {0};
                if (platform_stop(a, item, sizeof item)) { if (!result) problem(error, cap, "AudioOut pause failed: %s", item); result = -1; }
                else a->running = 0;
            }
        } else {
            for (unsigned i = 0; i < AOMAX; i++) {
                aout *a = ctx->outputs[i];
                if (!a || a->retiring || atomic_load(&a->state) != STATE_STARTED) continue;
                if (!a->running) {
                    char item[512] = {0};
                    if (platform_start(a, item, sizeof item)) { result = problem(error, cap, "AudioOut resume failed: %s", item); break; }
                    a->running = 1;
                }
            }
            if (!result) {
                ctx->active = 1;
                for (unsigned i = 0; i < AOMAX; i++) if (ctx->outputs[i]) atomic_store(&ctx->outputs[i]->enabled, 1);
            } else {
                /* A multi-output resume is all-or-paused; never drain half a session. */
                for (unsigned i = 0; i < AOMAX; i++) {
                    aout *a = ctx->outputs[i]; if (!a) continue; atomic_store(&a->enabled, 0);
                    if (a->running) { char ignored[512]; if (!platform_stop(a, ignored, sizeof ignored)) a->running = 0; }
                }
            }
        }
        if (result) save_deferred(ctx, error && cap && *error ? error : "AudioOut lifecycle AudioUnit operation failed");
    }
    pthread_mutex_unlock(&api_lock); return result;
}
int botw_audio_shutdown(botw_audio *ctx, char *error, size_t cap) {
    clear_error(error, cap); if (!ctx) return 0;
    pthread_mutex_lock(&api_lock);
    if (ctx != g_audio) { pthread_mutex_unlock(&api_lock); return problem(error, cap, "AudioOut shutdown context is not live"); }
    ctx->closing = 1;
    for (unsigned i = 0; i < AOMAX; i++) if (destroy_output(ctx, i, error, cap)) { pthread_mutex_unlock(&api_lock); return -1; }
    g_audio = NULL; pthread_mutex_destroy(&ctx->error_lock); free(ctx); pthread_mutex_unlock(&api_lock); return 0;
}

static void sdk_init_param(void *guest) {
    audio_call call = begin_call();
    const uint32_t words[] = {0, 0xcafe0000u}; /* Original sdk+1b1f24 STP. */
    if (!*call.error) write_guest(&call, (uintptr_t)guest, words, sizeof words, "parameter"); end_call(&call);
}
static int sdk_list(char *guest, int count) {
    audio_call call = begin_call(); int result = 0;
    if (!*call.error && count > 0 && platform_has_output()) {
        char info[256] = "DeviceOut";
        if (!write_guest(&call, (uintptr_t)guest, info, sizeof info, "device list")) result = 1;
    }
    end_call(&call); return result;
}
static uint32_t open_output(void *guest, void *event, const char *name, const void *param, int needs_event) {
    audio_call call = begin_call(); unsigned slot = AOMAX; aout *a = NULL;
    uint32_t parameter[2] = {0}; uint8_t object_probe[8]; char device[256] = {0};
    if (!*call.error && needs_event && !event) problem(call.error, sizeof call.error, "AudioOut eventful open requires a SystemEvent output");
    if (!*call.error) {
        for (unsigned i = 0; i < AOMAX; i++) {
            aout *current = call.ctx->outputs[i];
            if (!current && slot == AOMAX) slot = i;
            if (current && current->guest == (uintptr_t)guest) problem(call.error, sizeof call.error, "AudioOut object already open/retiring");
            if (current && event && current->guest_event == (uintptr_t)event) problem(call.error, sizeof call.error, "AudioOut SystemEvent already attached");
        }
        if (slot == AOMAX && !*call.error) problem(call.error, sizeof call.error, "AudioOut open output limit %d reached", AOMAX);
    }
    if (!*call.error) read_guest(&call, (uintptr_t)param, parameter, sizeof parameter, "parameter");
    int channels = (int)(parameter[1] & 0xffffu); if (!channels) channels = 2;
    if (!*call.error && (parameter[0] && parameter[0] != RATE)) problem(call.error, sizeof call.error, "AudioOut sample rate %u unsupported (48000 required)", parameter[0]);
    if (!*call.error && channels != 1 && channels != 2 && channels != 6) problem(call.error, sizeof call.error, "AudioOut channel count %d unsupported (1/2/6)", channels);
    if (!*call.error && (parameter[1] >> 16) != 0xcafeu && (parameter[1] >> 16) != 0)
        problem(call.error, sizeof call.error, "AudioOut parameter reserved marker is invalid");
    if (!*call.error && name) {
        for (size_t i = 0; i < sizeof device; i++) {
            if (read_guest(&call, (uintptr_t)name + i, device + i, 1, "device name") || !device[i]) break;
            if (i + 1 == sizeof device) problem(call.error, sizeof call.error, "AudioOut device name is not terminated");
        }
        if (!*call.error && *device && strcmp(device, "DeviceOut")) problem(call.error, sizeof call.error, "AudioOut device '%s' is not the host default output", device);
    }
    /* Opaque side-table identity. All hooked consumers avoid guessing SDK object
     * layout; this verifies an accessible output token without writing fields. */
    if (!*call.error && !read_guest(&call, (uintptr_t)guest, object_probe, sizeof object_probe, "output object"))
        write_guest(&call, (uintptr_t)guest, object_probe, sizeof object_probe, "output object");
    if (!*call.error) {
        a = calloc(1, sizeof *a);
        if (!a) problem(call.error, sizeof call.error, "AudioOut output allocation failed");
        else {
            int result = pthread_mutex_init(&a->m, NULL);
            if (result) { free(a); a = NULL; problem(call.error, sizeof call.error, "AudioOut output mutex failed (%d)", result); }
        }
    }
    if (a) {
        a->owner = call.ctx; a->guest = (uintptr_t)guest; a->guest_event = (uintptr_t)event; a->ch = channels;
        a->mix = channels == 6; atomic_init(&a->state, STATE_STOPPED); atomic_init(&a->enabled, call.ctx->active);
        atomic_init(&a->q_head, 0); atomic_init(&a->q_tail, 0); atomic_init(&a->rel_head, 0); atomic_init(&a->rel_tail, 0);
        atomic_init(&a->notify_run, 1); atomic_init(&a->notify_pending, 0); atomic_init(&a->notify_armed, 0);
        call.ctx->outputs[slot] = a;
        if (a->mix && !(a->mixbuf = malloc(MIXMAX * 12))) problem(call.error, sizeof call.error, "AudioOut downmix buffer allocation failed");
        if (!*call.error && !(a->notify_sem = semaphore_create())) problem(call.error, sizeof call.error, "AudioOut notifier semaphore allocation failed");
        if (!*call.error) platform_open(a, call.error, sizeof call.error);
        if (!*call.error) {
            int result = pthread_create(&a->notify_th, NULL, event_notify, a);
            if (result) problem(call.error, sizeof call.error, "AudioOut notifier thread creation failed (%d)", result);
            else a->notify_created = 1;
        }
        if (!*call.error && event) {
            if (call.cb.create_event(call.cb.opaque, 0, &a->writer, &a->unattached_reader, call.error, sizeof call.error)) {
                if (!*call.error) problem(call.error, sizeof call.error, "AudioOut kernel event creation rejected");
            } else if (!a->writer || !a->unattached_reader) problem(call.error, sizeof call.error, "AudioOut kernel returned invalid event handles");
            else if (call.cb.attach_system_event(call.cb.opaque, (uintptr_t)event, a->unattached_reader, call.error, sizeof call.error)) {
                if (!*call.error) problem(call.error, sizeof call.error, "AudioOut original SDK event attachment rejected");
            } else a->unattached_reader = 0; /* guest now owns its readable endpoint */
        }
        if (*call.error) {
            char cleanup_error[512] = {0};
            if (destroy_output(call.ctx, slot, cleanup_error, sizeof cleanup_error)) save_deferred(call.ctx, cleanup_error);
        } else snprintf(call.log, sizeof call.log, "audio_output=AudioUnit initialized=true sample_rate=48000 channels=%d owned_pcm=true", channels);
    }
    uint32_t result = *call.error ? UINT32_MAX : 0; end_call(&call); return result;
}
static uint32_t sdk_open_default(void *guest, const void *param) { return open_output(guest, NULL, NULL, param, 0); }
static uint32_t sdk_open(void *guest, const char *name, const void *param) { return open_output(guest, NULL, name, param, 0); }
static uint32_t sdk_open_default_event(void *guest, void *event, const void *param) { return open_output(guest, event, NULL, param, 1); }
static uint32_t sdk_open_event(void *guest, void *event, const char *name, const void *param) { return open_output(guest, event, name, param, 1); }
static uint32_t sdk_start(void *guest) {
    audio_call call = begin_call(); aout *a = lookup(&call, guest);
    if (a && atomic_load(&a->state) != STATE_STARTED) {
        if (call.ctx->active && platform_start(a, call.error, sizeof call.error)) { /* state remains stopped */ }
        else { a->running = call.ctx->active; atomic_store(&a->state, STATE_STARTED); atomic_store(&a->enabled, call.ctx->active); }
    }
    uint32_t result = *call.error ? UINT32_MAX : 0; end_call(&call); return result;
}
static void sdk_stop(void *guest) {
    audio_call call = begin_call(); aout *a = lookup(&call, guest);
    if (a) {
        atomic_store(&a->enabled, 0); atomic_store(&a->state, STATE_STOPPED);
        if (a->running && !platform_stop(a, call.error, sizeof call.error)) a->running = 0;
    }
    end_call(&call);
}
static void sdk_close(void *guest) {
    audio_call call = begin_call(); aout *a = lookup(&call, guest);
    if (a) for (unsigned i = 0; i < AOMAX; i++) if (call.ctx->outputs[i] == a) { destroy_output(call.ctx, i, call.error, sizeof call.error); break; }
    end_call(&call);
}
static int scalar(const void *guest, int kind) {
    audio_call call = begin_call(); aout *a = lookup(&call, guest); int value = 0;
    if (a) value = kind == 0 ? atomic_load(&a->state) : kind == 1 ? RATE : kind == 2 ? a->ch : FMT_PCM16;
    end_call(&call); return value;
}
static int sdk_state(const void *g) { return scalar(g, 0); }
static int sdk_rate(const void *g) { return scalar(g, 1); }
static int sdk_channels(const void *g) { return scalar(g, 2); }
static int sdk_format(const void *g) { return scalar(g, 3); }
static void sdk_set_buffer(void *guest, void *pointer, uint64_t capacity, uint64_t bytes) {
    audio_call call = begin_call(); uint64_t words[] = {(uintptr_t)pointer, capacity, bytes};
    if (!*call.error && (bytes > capacity || (bytes && (!pointer || bytes > UINT64_MAX - (uintptr_t)pointer))))
        problem(call.error, sizeof call.error, "AudioOut buffer size exceeds capacity or address range");
    uint64_t target = (uintptr_t)guest;
    if (!*call.error && (!target || target > UINT64_MAX - 8)) problem(call.error, sizeof call.error, "AudioOut buffer descriptor overflow");
    if (!*call.error) write_guest(&call, target + 8, words, sizeof words, "buffer descriptor"); end_call(&call);
}
static void *sdk_buffer_pointer(const void *guest) {
    audio_call call = begin_call(); uint64_t words[4] = {0};
    read_guest(&call, (uintptr_t)guest, words, sizeof words, "buffer descriptor"); void *result = *call.error ? NULL : (void *)(uintptr_t)words[1];
    end_call(&call); return result;
}
static uint64_t sdk_buffer_size(const void *guest) {
    audio_call call = begin_call(); uint64_t words[4] = {0};
    read_guest(&call, (uintptr_t)guest, words, sizeof words, "buffer descriptor"); uint64_t result = *call.error ? 0 : words[3]; end_call(&call); return result;
}
static unsigned sdk_append(void *guest, void *descriptor) {
    audio_call call = begin_call(); aout *a = lookup(&call, guest); uint64_t raw[4] = {0}; unsigned result = 0;
    if (a) read_guest(&call, (uintptr_t)descriptor, raw, sizeof raw, "buffer descriptor");
    if (!*call.error && (!raw[1] || (raw[1] & 1) || !raw[3] || raw[3] > raw[2] || raw[3] > SIZE_MAX ||
        raw[3] > UINT64_MAX - raw[1] || raw[3] % ((unsigned)a->ch * 2u)))
        problem(call.error, sizeof call.error, "AudioOut buffer invalid pointer/capacity/size/frame alignment");
    if (!*call.error) for (unsigned i = 0; i < AOMAX; i++) {
        aout *other = call.ctx->outputs[i]; if (!other) continue;
        for (pcm_node *node = other->nodes; node; node = node->next) if (node->guest == (uintptr_t)descriptor)
            problem(call.error, sizeof call.error, "AudioOut buffer descriptor is already queued");
    }
    if (!*call.error && raw[3] > PCM_LIMIT - call.ctx->queued_bytes)
        problem(call.error, sizeof call.error, "AudioOut buffer PCM budget exceeded (%u bytes)", PCM_LIMIT);
    if (!*call.error && a->node_count < QMAX) {
        pcm_node *node = calloc(1, sizeof *node);
        if (!node || !(node->buffer.ptr = malloc((size_t)raw[3]))) { free(node); problem(call.error, sizeof call.error, "AudioOut owned PCM allocation failed"); }
        else if (read_guest(&call, raw[1], node->buffer.ptr, (size_t)raw[3], "buffer PCM")) { free(node->buffer.ptr); free(node); }
        else {
            node->buffer.cap = node->buffer.size = raw[3]; node->guest = (uintptr_t)descriptor;
            pthread_mutex_lock(&a->m);
            uint32_t tail = atomic_load_explicit(&a->q_tail, memory_order_relaxed);
            uint32_t head = atomic_load_explicit(&a->q_head, memory_order_acquire);
            if (tail - head < QMAX) {
                node->next = a->nodes; a->nodes = node; a->node_count++; call.ctx->queued_bytes += raw[3];
                a->q[tail & (QMAX - 1)] = &node->buffer;
                atomic_store_explicit(&a->q_tail, tail + 1, memory_order_release); result = 1;
            }
            pthread_mutex_unlock(&a->m);
            if (!result) { free(node->buffer.ptr); free(node); }
        }
    }
    end_call(&call); return result;
}
static void *sdk_released(void *guest) {
    audio_call call = begin_call(); aout *a = lookup(&call, guest); pcm_node *node = NULL; void *result = NULL;
    if (a) {
        pthread_mutex_lock(&a->m);
        uint32_t head = atomic_load_explicit(&a->rel_head, memory_order_relaxed);
        uint32_t tail = atomic_load_explicit(&a->rel_tail, memory_order_acquire);
        if (head != tail) { node = (pcm_node *)a->rel[head & (RELMAX - 1)]; atomic_store_explicit(&a->rel_head, head + 1, memory_order_release); }
        pthread_mutex_unlock(&a->m);
        if (node) {
            pcm_node **link = &a->nodes; while (*link && *link != node) link = &(*link)->next;
            if (!*link) problem(call.error, sizeof call.error, "AudioOut released buffer ownership invariant failed");
            else {
                *link = node->next; a->node_count--; call.ctx->queued_bytes -= node->buffer.size;
                result = (void *)(uintptr_t)node->guest; free(node->buffer.ptr); free(node);
            }
        }
    }
    end_call(&call); return result;
}

typedef struct { const char *name; uint64_t offset, size; void *function; } audio_export;
#define EXPORT(name, off, size, fn) {name, off, size, (void *)(uintptr_t)fn}
static const audio_export audio_profile[] = {
    EXPORT("_ZN2nn5audio27InitializeAudioOutParameterEPNS0_17AudioOutParameterE",0x1b1f24,0xc,sdk_init_param),
    EXPORT("_ZN2nn5audio13ListAudioOutsEPNS0_12AudioOutInfoEi",0x1c6588,0x7c,sdk_list),
    EXPORT("_ZN2nn5audio19OpenDefaultAudioOutEPNS0_8AudioOutERKNS0_17AudioOutParameterE",0x1c6604,0x28,sdk_open_default),
    EXPORT("_ZN2nn5audio12OpenAudioOutEPNS0_8AudioOutEPKcRKNS0_17AudioOutParameterE",0x1c662c,0x198,sdk_open),
    EXPORT("_ZN2nn5audio19OpenDefaultAudioOutEPNS0_8AudioOutEPNS_2os11SystemEventERKNS0_17AudioOutParameterE",0x1c67c4,0xf0,sdk_open_default_event),
    EXPORT("_ZN2nn5audio12OpenAudioOutEPNS0_8AudioOutEPNS_2os11SystemEventEPKcRKNS0_17AudioOutParameterE",0x1c68b4,0xf0,sdk_open_event),
    EXPORT("_ZN2nn5audio13CloseAudioOutEPNS0_8AudioOutE",0x1c69a4,0xd8,sdk_close),
    EXPORT("_ZN2nn5audio13StartAudioOutEPNS0_8AudioOutE",0x1c6a7c,0x80,sdk_start),
    EXPORT("_ZN2nn5audio12StopAudioOutEPNS0_8AudioOutE",0x1c6afc,0x78,sdk_stop),
    EXPORT("_ZN2nn5audio16GetAudioOutStateEPKNS0_8AudioOutE",0x1c6b74,0xe4,sdk_state),
    EXPORT("_ZN2nn5audio21GetAudioOutSampleRateEPKNS0_8AudioOutE",0x1c6c60,0x8,sdk_rate),
    EXPORT("_ZN2nn5audio23GetAudioOutChannelCountEPKNS0_8AudioOutE",0x1c6c68,0x8,sdk_channels),
    EXPORT("_ZN2nn5audio23GetAudioOutSampleFormatEPKNS0_8AudioOutE",0x1c6c70,0x8,sdk_format),
    EXPORT("_ZN2nn5audio21SetAudioOutBufferInfoEPNS0_14AudioOutBufferEPvmm",0x1b1f3c,0xc,sdk_set_buffer),
    EXPORT("_ZN2nn5audio28GetAudioOutBufferDataPointerEPKNS0_14AudioOutBufferE",0x1b1f48,0x8,sdk_buffer_pointer),
    EXPORT("_ZN2nn5audio25GetAudioOutBufferDataSizeEPKNS0_14AudioOutBufferE",0x1b1f50,0x8,sdk_buffer_size),
    EXPORT("_ZN2nn5audio20AppendAudioOutBufferEPNS0_8AudioOutEPNS0_14AudioOutBufferE",0x1c6c78,0xa8,sdk_append),
    EXPORT("_ZN2nn5audio25GetReleasedAudioOutBufferEPNS0_8AudioOutE",0x1c6d20,0xa0,sdk_released)
};
#undef EXPORT
int botw_audio_resolve_symbol(const char *module, unsigned index, const char *name,
    uint64_t value, uint64_t size, size_t text_end, void **function, char *error, size_t cap) {
    clear_error(error, cap); if (!name) return 0;
    for (size_t i = 0; i < sizeof audio_profile / sizeof *audio_profile; i++) {
        const audio_export *entry = &audio_profile[i]; if (strcmp(name, entry->name)) continue;
        if (!module || strcmp(module, "sdk") || index != 5 || !function || value != entry->offset || size != entry->size ||
            value > text_end || size > text_end - value)
            return problem(error, cap, "AudioOut fixed base-1.0 profile mismatch: %s module=%s index=%u offset=%#" PRIx64 " size=%#" PRIx64,
                name, module ? module : "(null)", index, value, size);
        *function = entry->function; return 1;
    }
    return 0;
}
