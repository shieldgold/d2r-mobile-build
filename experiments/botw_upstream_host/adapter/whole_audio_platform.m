#include "whole_audio_platform.h"
#include <AudioToolbox/AudioToolbox.h>
#include <TargetConditionals.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct audio_registration {
    struct botw_pcm_sink *sink;
    whole_audio_sink_apply apply;
    int requested_active;
    struct audio_registration *next;
} audio_registration;
static pthread_mutex_t g_audio_lock = PTHREAD_MUTEX_INITIALIZER;
static audio_registration *g_sinks;
#if TARGET_OS_IPHONE
static int g_platform_active;
#else
static int g_platform_active = 1;
#endif

uint32_t whole_audio_platform_output_subtype(void) {
#if TARGET_OS_IPHONE
    return kAudioUnitSubType_RemoteIO;
#else
    return kAudioUnitSubType_DefaultOutput;
#endif
}
void whole_audio_platform_lock(void) { pthread_mutex_lock(&g_audio_lock); }
void whole_audio_platform_unlock(void) { pthread_mutex_unlock(&g_audio_lock); }
int whole_audio_platform_register_locked(struct botw_pcm_sink *sink, whole_audio_sink_apply apply) {
    if (!sink || !apply) return -1;
    for (audio_registration *entry = g_sinks; entry; entry = entry->next)
        if (entry->sink == sink) return -1;
    audio_registration *entry = calloc(1, sizeof *entry);
    if (!entry) return -1;
    entry->sink = sink; entry->apply = apply; entry->next = g_sinks; g_sinks = entry;
    return 0;
}
int whole_audio_platform_request_locked(struct botw_pcm_sink *sink, int active) {
    for (audio_registration *entry = g_sinks; entry; entry = entry->next) {
        if (entry->sink != sink) continue;
        /* A failed stop still retires the logical request; propagate its error.
         * A failed start never changes the previously accepted request. */
        if (!active) entry->requested_active = 0;
        int result = entry->apply(sink, active && g_platform_active);
        if (!result && active) entry->requested_active = 1;
        return result;
    }
    return -1;
}
void whole_audio_platform_unregister_locked(struct botw_pcm_sink *sink) {
    audio_registration **cursor = &g_sinks;
    while (*cursor) {
        audio_registration *entry = *cursor;
        if (entry->sink == sink) { *cursor = entry->next; free(entry); return; }
        cursor = &entry->next;
    }
}
int whole_audio_platform_set_active(int active, char *error, size_t capacity) {
    if (error && capacity) error[0] = 0;
    whole_audio_platform_lock();
    g_platform_active = active != 0;
    int failed = 0, rollback_failed = 0;
    for (audio_registration *entry = g_sinks; entry; entry = entry->next)
        if (entry->apply(entry->sink, g_platform_active && entry->requested_active)) failed = 1;
    if (failed && g_platform_active) {
        /* A partly resumed device set must not continue producing sound. The
         * original PCM stop clears active before AudioUnitStop and quiesces the
         * callback even if the hardware reports a stop failure. */
        g_platform_active = 0;
        for (audio_registration *entry = g_sinks; entry; entry = entry->next)
            if (entry->apply(entry->sink, 0)) rollback_failed = 1;
    }
    whole_audio_platform_unlock();
    if (failed && error && capacity) {
        const char *message = active ?
            (rollback_failed ? "PCM resume failed; rollback also reported a device error; all consumers are inactive." :
                               "PCM resume failed; all sinks were returned to inactive.") :
            "PCM stop failed; all consumers are inactive and queued PCM was discarded.";
        snprintf(error, capacity, "%s", message);
    }
    return failed ? -1 : 0;
}
