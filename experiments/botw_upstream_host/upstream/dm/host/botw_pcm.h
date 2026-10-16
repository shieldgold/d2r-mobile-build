// Host-owned PCM output. No guest pointers or SDK calls may enter the audio callback.
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct botw_pcm_sink botw_pcm_sink;
typedef struct {
    uint64_t submitted_frames, consumed_frames, dropped_frames, underrun_frames;
} botw_pcm_counters;

// Fixed 48000 Hz stereo device output. audible=0 is an explicit muted sink.
// Returns NULL on allocation/device failure. Initially stopped. Not realtime-safe.
botw_pcm_sink *botw_pcm_open(int audible);
// Caller serializes lifecycle and writes. Stop discards queued PCM; restart starts empty.
// Returns 0 on success, -1 on device failure. Not realtime-safe.
int botw_pcm_set_active(botw_pcm_sink *sink, int active);
// Copies interleaved PCM16; supports 1, 2 or 6 source channels. Returns accepted frames.
// Single producer. Never blocks on the consumer; excess frames are counted as dropped.
// A muted active sink consumes accepted frames without opening an audio device.
size_t botw_pcm_write(botw_pcm_sink *sink, const int16_t *samples, size_t frames, unsigned channels);
// Per-sink output gain, applied when consuming queued PCM (including already queued frames).
// Finite values clamp to [0, 128]; non-finite values are rejected without changing gain.
// Initially 1.0. Returns 0 on success, -1 for invalid input. Caller serializes API use.
int botw_pcm_set_volume(botw_pcm_sink *sink, float volume);
void botw_pcm_get_counters(const botw_pcm_sink *sink, botw_pcm_counters *out);
// Stops callbacks before releasing storage. Caller guarantees no concurrent producer/API use.
void botw_pcm_close(botw_pcm_sink *sink);
