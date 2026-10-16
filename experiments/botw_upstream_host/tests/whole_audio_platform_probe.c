#include <AudioToolbox/AudioToolbox.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "botw_pcm.h"
#include "whole_audio_platform.h"

#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "owned audio behavior failed at %d: %s\n", __LINE__, #expr); return 1; } } while (0)
typedef struct {
    AURenderCallbackStruct callback;
    int alive, initialized, started, starts, stops, disposes;
    OSStatus fail_start, fail_stop, fail_dispose;
} owned_unit;
static owned_unit units[16];
static int unit_count;
static owned_unit *unit(AudioUnit au) { return (owned_unit *)au; }
OSStatus botw_pcm_mock_new(const AudioComponentDescription *desc, AudioUnit *out) {
    if (unit_count == 16 || desc->componentSubType != kAudioUnitSubType_DefaultOutput) abort();
    owned_unit *u = &units[unit_count++]; u->alive = 1; *out = (AudioUnit)u; return noErr;
}
OSStatus botw_pcm_mock_set_property(AudioUnit au, AudioUnitPropertyID id, AudioUnitScope scope,
                                  AudioUnitElement element, const void *data, UInt32 size) {
    if (scope != kAudioUnitScope_Input || element != 0) abort();
    if (id == kAudioUnitProperty_SetRenderCallback) {
        if (size != sizeof(AURenderCallbackStruct)) abort();
        memcpy(&unit(au)->callback, data, size);
    } else if (id != kAudioUnitProperty_StreamFormat) abort();
    return noErr;
}
OSStatus botw_pcm_mock_initialize(AudioUnit au) { unit(au)->initialized = 1; return noErr; }
OSStatus botw_pcm_mock_uninitialize(AudioUnit au) { unit(au)->initialized = 0; return noErr; }
OSStatus botw_pcm_mock_start(AudioUnit au) {
    owned_unit *u = unit(au); ++u->starts;
    if (!u->alive || !u->initialized) abort();
    if (u->fail_start) return u->fail_start;
    u->started = 1; return noErr;
}
OSStatus botw_pcm_mock_stop(AudioUnit au) {
    owned_unit *u = unit(au); ++u->stops;
    if (u->fail_stop) return u->fail_stop;
    u->started = 0; return noErr;
}
OSStatus botw_pcm_mock_dispose(AudioUnit au) {
    owned_unit *u = unit(au); ++u->disposes;
    if (u->fail_dispose) return u->fail_dispose;
    u->alive = u->started = 0; return noErr;
}
static int render(owned_unit *u, int16_t *out) {
    AudioTimeStamp time = {0}; AudioUnitRenderActionFlags flags = 0;
    AudioBufferList buffer = {.mNumberBuffers = 1};
    buffer.mBuffers[0] = (AudioBuffer){2, 4, out};
    out[0] = out[1] = 1234;
    return u->callback.inputProc(u->callback.inputProcRefCon, &flags, &time, 0, 1, &buffer);
}
static const int16_t samples[] = {200, -200, 100, -100};
static void *producer(void *opaque) {
    botw_pcm_sink *sink = opaque;
    for (int i = 0; i < 5000; ++i) {
        if (botw_pcm_set_volume(sink, (i & 1) ? 0.5f : 1.0f)) abort();
        botw_pcm_write(sink, samples, 2, 2);
    }
    return NULL;
}
int main(int argc, char **argv) {
    CHECK(argc == 2);
    char error[128] = {0}; int16_t out[2];
    CHECK(whole_audio_platform_output_subtype() == kAudioUnitSubType_DefaultOutput);
    CHECK(!whole_audio_platform_set_active(1, error, sizeof error));
    botw_pcm_sink *a = botw_pcm_open(1), *b = botw_pcm_open(1);
    CHECK(a && b && !botw_pcm_set_active(a, 1) && !botw_pcm_set_active(b, 1));
    if (!strcmp(argv[1], "lifecycle")) {
        botw_pcm_sink *stopped = botw_pcm_open(1); CHECK(stopped);
        CHECK(botw_pcm_write(a, samples, 2, 2) == 2);
        CHECK(!whole_audio_platform_set_active(0, error, sizeof error));
        CHECK(!units[0].started && !units[1].started);
        CHECK(botw_pcm_write(a, samples, 2, 2) == 0);
        botw_pcm_counters c; botw_pcm_get_counters(a, &c);
        CHECK(c.submitted_frames == 2 && c.dropped_frames == 2);
        CHECK(!render(&units[0], out) && out[0] == 0 && out[1] == 0);
        botw_pcm_sink *fresh = botw_pcm_open(1); CHECK(fresh && !botw_pcm_set_active(fresh, 1));
        CHECK(units[3].starts == 0 && botw_pcm_write(fresh, samples, 2, 2) == 0);
        CHECK(!botw_pcm_set_active(b, 0));
        CHECK(!botw_pcm_set_volume(a, 0.5f));
        CHECK(!whole_audio_platform_set_active(1, error, sizeof error));
        CHECK(units[0].started && !units[1].started && !units[2].started && units[3].started);
        CHECK(!render(&units[0], out) && out[0] == 0 && out[1] == 0);
        CHECK(botw_pcm_write(a, samples, 1, 2) == 1);
        CHECK(!render(&units[0], out) && out[0] == 100 && out[1] == -100);
        botw_pcm_close(stopped); botw_pcm_close(fresh);
    } else if (!strcmp(argv[1], "failures")) {
        CHECK(!whole_audio_platform_set_active(0, error, sizeof error));
        units[1].fail_start = -50;
        CHECK(whole_audio_platform_set_active(1, error, sizeof error) != 0 && error[0]);
        CHECK(!units[0].started && !units[1].started);
        CHECK(botw_pcm_write(a, samples, 1, 2) == 0 && botw_pcm_write(b, samples, 1, 2) == 0);
        units[1].fail_start = 0;
        CHECK(!whole_audio_platform_set_active(1, error, sizeof error));
        units[0].fail_stop = -51;
        CHECK(whole_audio_platform_set_active(0, error, sizeof error) != 0 && error[0]);
        CHECK(botw_pcm_write(a, samples, 1, 2) == 0 && botw_pcm_write(b, samples, 1, 2) == 0);
        CHECK(!render(&units[0], out) && out[0] == 0 && out[1] == 0);
        units[0].fail_stop = 0;
    } else if (!strcmp(argv[1], "retired")) {
        CHECK(!whole_audio_platform_set_active(0, error, sizeof error));
        units[0].fail_dispose = -50;
        botw_pcm_close(a);
        int starts = units[0].starts;
        CHECK(units[0].alive && !render(&units[0], out) && out[0] == 0 && out[1] == 0);
        CHECK(!whole_audio_platform_set_active(1, error, sizeof error));
        CHECK(units[0].starts == starts && !units[0].started);
        units[0].fail_dispose = 0;
        botw_pcm_close(a); a = NULL;
        CHECK(!units[0].alive);
    } else if (!strcmp(argv[1], "concurrency")) {
        pthread_t writer; CHECK(!pthread_create(&writer, NULL, producer, a));
        for (int i = 0; i < 1000; ++i) CHECK(!whole_audio_platform_set_active(i & 1, error, sizeof error));
        CHECK(!pthread_join(writer, NULL));
        CHECK(!whole_audio_platform_set_active(0, error, sizeof error));
        botw_pcm_counters c; botw_pcm_get_counters(a, &c);
        CHECK(c.submitted_frames == c.consumed_frames + c.dropped_frames);
    } else return 64;
    botw_pcm_close(a); botw_pcm_close(b);
    return 0;
}
