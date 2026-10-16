// SPDX-License-Identifier: GPL-3.0-or-later
// Owned mock hardware only. Real patched botw_pcm.c supplies ring/gain/callback.
#include <AudioToolbox/AudioToolbox.h>
#include <stdlib.h>
#include <string.h>
typedef struct {
    AURenderCallbackStruct callback;
    int initialized, started, disposed;
} owned_unit;
static owned_unit units[16];
static unsigned count;
static int fail_new, fail_start, fail_stop;
void owned_pcm_fail(int allocation, int start, int stop) {
    fail_new = allocation; fail_start = start; fail_stop = stop;
}
unsigned owned_pcm_unit_count(void) { return count; }
int owned_pcm_started(void) { return count && units[count - 1].started; }
OSStatus botw_pcm_mock_new(const AudioComponentDescription* description, AudioUnit* out) {
    if (description->componentSubType != kAudioUnitSubType_DefaultOutput || count == 16) abort();
    if (fail_new) return -50;
    *out = (AudioUnit)&units[count++]; return noErr;
}
OSStatus botw_pcm_mock_set_property(AudioUnit au, AudioUnitPropertyID id, AudioUnitScope scope,
                                   AudioUnitElement element, const void* data, UInt32 size) {
    if (scope != kAudioUnitScope_Input || element != 0) abort();
    if (id == kAudioUnitProperty_SetRenderCallback) {
        if (size != sizeof(AURenderCallbackStruct)) abort();
        memcpy(&((owned_unit*)au)->callback, data, size);
    } else if (id != kAudioUnitProperty_StreamFormat) abort();
    return noErr;
}
OSStatus botw_pcm_mock_initialize(AudioUnit au) { ((owned_unit*)au)->initialized = 1; return noErr; }
OSStatus botw_pcm_mock_uninitialize(AudioUnit au) { ((owned_unit*)au)->initialized = 0; return noErr; }
OSStatus botw_pcm_mock_start(AudioUnit au) {
    owned_unit* unit = (owned_unit*)au;
    if (!unit->initialized || unit->disposed) abort();
    if (fail_start) return -51;
    unit->started = 1; return noErr;
}
OSStatus botw_pcm_mock_stop(AudioUnit au) {
    if (fail_stop) return -52;
    ((owned_unit*)au)->started = 0; return noErr;
}
OSStatus botw_pcm_mock_dispose(AudioUnit au) {
    owned_unit* unit = (owned_unit*)au; unit->disposed = 1; unit->started = 0; return noErr;
}
int owned_pcm_render(int16_t* samples, unsigned frames) {
    if (!count) return -1;
    owned_unit* unit = &units[count - 1];
    AudioTimeStamp timestamp = {0}; AudioUnitRenderActionFlags flags = 0;
    AudioBufferList buffers = {.mNumberBuffers = 1};
    buffers.mBuffers[0] = (AudioBuffer){2, frames * 4, samples};
    return unit->callback.inputProc(unit->callback.inputProcRefCon, &flags, &timestamp, 0, frames, &buffers);
}
