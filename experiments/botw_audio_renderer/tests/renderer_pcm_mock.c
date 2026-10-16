// SPDX-License-Identifier: GPL-3.0-or-later
// Extend the frozen owned AU seam for actual ADSP session-index callbacks.
// Complete original PCM supplies every ring/gain/render/stop operation.
#define botw_pcm_mock_start owned_start_impl
#define botw_pcm_mock_stop owned_stop_impl
#define botw_pcm_mock_dispose owned_dispose_impl
#include "pcm_mock_au.c"
#undef botw_pcm_mock_start
#undef botw_pcm_mock_stop
#undef botw_pcm_mock_dispose
#include <pthread.h>
static pthread_mutex_t owned_observer_lock = PTHREAD_MUTEX_INITIALIZER;
OSStatus botw_pcm_mock_start(AudioUnit unit) {
    pthread_mutex_lock(&owned_observer_lock);
    OSStatus result = owned_start_impl(unit);
    pthread_mutex_unlock(&owned_observer_lock); return result;
}
OSStatus botw_pcm_mock_stop(AudioUnit unit) {
    pthread_mutex_lock(&owned_observer_lock);
    OSStatus result = owned_stop_impl(unit);
    pthread_mutex_unlock(&owned_observer_lock); return result;
}
OSStatus botw_pcm_mock_dispose(AudioUnit unit) {
    pthread_mutex_lock(&owned_observer_lock);
    OSStatus result = owned_dispose_impl(unit);
    pthread_mutex_unlock(&owned_observer_lock); return result;
}
int owned_pcm_started_at(unsigned index) {
    pthread_mutex_lock(&owned_observer_lock);
    const int started = index < count && units[index].started && !units[index].disposed;
    pthread_mutex_unlock(&owned_observer_lock); return started;
}
int owned_pcm_render_at(unsigned index, int16_t* samples, unsigned frames) {
    pthread_mutex_lock(&owned_observer_lock);
    if (index >= count || !units[index].started || units[index].disposed) {
        pthread_mutex_unlock(&owned_observer_lock); return -1;
    }
    const AURenderCallbackStruct callback = units[index].callback;
    pthread_mutex_unlock(&owned_observer_lock);
    AudioTimeStamp timestamp = {0}; AudioUnitRenderActionFlags flags = 0;
    AudioBufferList buffers = {.mNumberBuffers = 1};
    buffers.mBuffers[0] = (AudioBuffer){2, frames * 4, samples};
    return callback.inputProc(callback.inputProcRefCon, &flags, &timestamp, 0, frames, &buffers);
}
