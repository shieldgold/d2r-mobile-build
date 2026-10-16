// Host-owned PCM16 output for the native BotW host: a bounded single-producer / single-consumer
// ring of 48000 Hz stereo frames drained by a CoreAudio DefaultOutput AudioUnit, or by nothing at
// all in the explicit muted mode. The producer (the audio renderer worker) converts its mono,
// stereo or 5.1 PCM16 into stereo while copying into the ring; the realtime render callback drains
// the ring into the device buffer — a plain memcpy at unity gain, otherwise an exact saturating
// multiply by the current per-sink gain — and zero-fills whatever is missing. Because the gain is
// applied when frames are consumed, not when they are queued, frames already in the ring when the
// volume changes come out at the new gain, and the channel conversion stays a producer-side
// concern (the callback always scales fully converted stereo). Nothing in this file touches SDK
// or guest state, and the realtime callback never allocates, locks, logs or calls out.
//
// Thread ownership (annotated per field below):
//   producer  = the caller of botw_pcm_write / botw_pcm_set_active / botw_pcm_set_volume /
//               botw_pcm_close, externally serialized by contract (the header guarantees a single
//               producer and no concurrent API use);
//   callback  = the AudioUnit render thread (pcm_render), a concurrent consumer that is only ever
//               reachable while the unit is started and only reads the ring while `active` is set.
//
// Lifecycle invariants:
//   1. The sink pointer passed to the AudioUnit as the render context stays valid, and the ring
//      allocation stays in place, from AudioComponentInstanceNew until after
//      AudioComponentInstanceDispose has returned and the in-flight callback count is zero.
//   2. Stop/restart never resets ring indices while a callback may be inside the ring: the
//      producer first clears `active`, then stops the unit, then waits for `in_flight` to drop to
//      zero (store-active / load-in_flight versus increment-in_flight / load-active, all seq_cst,
//      so one side always observes the other), and only then rewrites the indices.
//   3. Close additionally uninitializes and disposes the unit (the AudioUnit shutdown contract
//      guarantees no further callbacks once Dispose has succeeded) and waits for in_flight == 0
//      again before free(). Failed disposal retains storage rather than risking late-callback use after free.
//      The in-flight count alone is never relied on to stop new callbacks.
//   4. Device failures are explicit: open returns NULL and set_active returns -1 after an English
//      diagnostic; an audible sink never silently degrades into a muted one.
//
// Counters (unsigned 64-bit, per sink; arithmetic wraps modulo 2^64):
//   submitted_frames = frames accepted by botw_pcm_write (valid input, written into the ring or,
//                      when muted, consumed immediately);
//   consumed_frames  = frames the consumer actually took out of the ring (inserted silence is
//                      never counted; muted sinks count accepted frames here as well);
//   dropped_frames   = frames refused because the ring was full plus frames still queued when
//                      the sink was stopped;
//   underrun_frames  = stereo frames the device asked for while active that had to be zero-filled.
// The gain never touches this accounting: frames the consumer takes out of the ring are consumed
// regardless of gain (a zero gain renders silence but is not an underrun), and only genuinely
// missing frames are underruns. Stop/restart retains the selected gain; it starts at 1.0.
// Writes while stopped are rejected (return 0) and touch no counter.
//
// Test seam: -DBOTW_PCM_MOCK_AU replaces the six AudioToolbox calls with functions a test defines
// (declared below), so tests can drive pcm_render and the lifecycle with no audio device.

#include "botw_pcm.h"

#include <math.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <AudioToolbox/AudioToolbox.h>

#define PCM_LOG(...) do { fprintf(stderr, "[dm] BotW PCM: " __VA_ARGS__); fputc('\n', stderr); } while (0)

enum {
    PCM_RATE = 48000,
    PCM_OUT_CH = 2,
    PCM_FRAME_BYTES = PCM_OUT_CH * (int)sizeof(int16_t),
    // Ring capacity in stereo frames: power of two so indices can wrap with a mask. 32768 frames
    // is ~683 ms at 48 kHz (128 KiB); the producer's own pacing, not this size, sets the latency.
    PCM_RING_FRAMES = 32768,
    PCM_RING_MASK = PCM_RING_FRAMES - 1,
};
_Static_assert((PCM_RING_FRAMES & PCM_RING_MASK) == 0, "ring capacity must be a power of two");
_Static_assert(PCM_RING_FRAMES < (1u << 30), "index difference arithmetic assumes capacity < 2^30");
_Static_assert(ATOMIC_LLONG_LOCK_FREE == 2 && ATOMIC_INT_LOCK_FREE == 2, "callback counters must be lock-free");
_Static_assert(sizeof(float) == sizeof(uint32_t), "the gain is stored as its raw 32-bit object representation");

struct botw_pcm_sink {
    // Immutable after open.
    int audible;                 // 1 = AudioUnit backed, 0 = explicit muted sink (no device, no ring use).
    int16_t *ring;               // PCM_RING_FRAMES interleaved stereo frames; owned by the sink.
    AudioUnit au;                // NULL for muted sinks.
    // Producer-owned, read by the callback.
    _Atomic uint32_t tail;       // Next frame slot to write; advances monotonically, wraps modulo 2^32.
    _Atomic int active;          // 1 while started; callback consumes only when it observes 1.
    // Output gain, producer-owned (botw_pcm_set_volume) and read by the callback. Stored as the
    // raw 32-bit object representation of the float in a uint32 atomic: lock-freeness is then
    // compiler-checkable through ATOMIC_INT_LOCK_FREE == 2 above (C11 offers no such macro for
    // _Atomic float), and the memcpy round-trip preserves the value on any representation.
    _Atomic uint32_t gain_bits;  // IEEE-754 bits of the current gain; 1.0f initially and after open.
    // Callback-owned, read by the producer.
    _Atomic uint32_t head;       // Next frame slot to read; the producer rewrites it only while quiescent.
    _Atomic int in_flight;       // Number of callbacks currently inside the consume path (0 or 1).
    // Counters: submitted/dropped are written by the producer only, consumed/underrun by the
    // callback only; all are atomic because botw_pcm_get_counters reads them from the producer side.
    _Atomic uint64_t submitted, consumed, dropped, underrun;
    // Producer-owned explicit error state: last failing AudioToolbox status, 0 when healthy.
    OSStatus last_error;
};

// ---------------------------------------------------------------------------------------------
// AudioToolbox seam. Real builds call the framework; -DBOTW_PCM_MOCK_AU builds get these symbols
// from the test, which may fail any step on demand and drive pcm_render itself.
#ifdef BOTW_PCM_MOCK_AU
OSStatus botw_pcm_mock_new(const AudioComponentDescription *desc, AudioUnit *out);
OSStatus botw_pcm_mock_set_property(AudioUnit au, AudioUnitPropertyID id, AudioUnitScope scope,
                                    AudioUnitElement element, const void *data, UInt32 size);
OSStatus botw_pcm_mock_initialize(AudioUnit au);
OSStatus botw_pcm_mock_uninitialize(AudioUnit au);
OSStatus botw_pcm_mock_start(AudioUnit au);
OSStatus botw_pcm_mock_stop(AudioUnit au);
OSStatus botw_pcm_mock_dispose(AudioUnit au);
#define AU_NEW botw_pcm_mock_new
#define AU_SET_PROPERTY botw_pcm_mock_set_property
#define AU_INITIALIZE botw_pcm_mock_initialize
#define AU_UNINITIALIZE botw_pcm_mock_uninitialize
#define AU_START botw_pcm_mock_start
#define AU_STOP botw_pcm_mock_stop
#define AU_DISPOSE botw_pcm_mock_dispose
#else
static OSStatus au_real_new(const AudioComponentDescription *desc, AudioUnit *out) {
    AudioComponent c = AudioComponentFindNext(NULL, desc);
    if (!c) { PCM_LOG("no default output AudioUnit component on this system"); return kAudioUnitErr_InvalidElement; }
    return AudioComponentInstanceNew(c, out);
}
#define AU_NEW au_real_new
#define AU_SET_PROPERTY AudioUnitSetProperty
#define AU_INITIALIZE AudioUnitInitialize
#define AU_UNINITIALIZE AudioUnitUninitialize
#define AU_START AudioOutputUnitStart
#define AU_STOP AudioOutputUnitStop
#define AU_DISPOSE AudioComponentInstanceDispose
#endif

// ---------------------------------------------------------------------------------------------
// Gain plumbing (see the ownership notes above). The gain is a per-sink scalar in [0, 128]; the
// native device layer above may layer its own endpoint gain (the builtin speaker defaults to 2.0)
// on top, so this backend only ever scales by the value selected through botw_pcm_set_volume.
static void pcm_store_gain(botw_pcm_sink *s, float g) {
    uint32_t bits;
    memcpy(&bits, &g, sizeof bits);
    atomic_store_explicit(&s->gain_bits, bits, memory_order_relaxed);
}

// Scale one int16 sample by a finite gain. Rounding policy: the product is computed in double and
// is always exact — an int16 carries at most 15 significant bits and a float at most 24, so the
// product needs at most 39 of double's 53 significand bits and no rounding occurs anywhere. The
// exact product is then saturated to the int16 range and converted with the C cast, which
// truncates toward zero (C11 6.3.1.4): 32767 * 0.5 = 16383.5 -> 16383, -1 * 0.5 = -0.5 -> 0.
// There is no float-to-int range hazard: after saturation the value lies in [-32768, 32767].
static inline int16_t pcm_gain16(int16_t v, float gain) {
    double d = (double)v * (double)gain;
    if (d > 32767.0) return 32767;
    if (d < -32768.0) return -32768;
    return (int16_t)d;
}

// ---------------------------------------------------------------------------------------------
// Realtime consumer. Runs on the AudioUnit render thread: no allocation, locks, logging or calls
// outside this file. Fills exactly one interleaved stereo int16 buffer. Takes one lock-free gain
// snapshot per invocation and scales the consumed frames with it after the copy out of the ring,
// so every frame in one buffer is scaled by a single gain value (a concurrent volume change can
// never split one buffer across two gains) and already queued frames reflect the new gain. Unity
// gain keeps the plain memcpy path and therefore the historical bit-exact output.
static OSStatus pcm_render(void *ctx, AudioUnitRenderActionFlags *flags, const AudioTimeStamp *ts,
                           UInt32 bus, UInt32 nframes, AudioBufferList *bl) {
    (void)ts; (void)bus;
    botw_pcm_sink *s = ctx;
    if (!bl || bl->mNumberBuffers < 1 || !bl->mBuffers[0].mData) return noErr;
    int16_t *dst = bl->mBuffers[0].mData;
    uint32_t want = bl->mBuffers[0].mDataByteSize / PCM_FRAME_BYTES;
    if (want > nframes) want = nframes;
    uint32_t got = 0;

    atomic_fetch_add_explicit(&s->in_flight, 1, memory_order_seq_cst);
    if (atomic_load_explicit(&s->active, memory_order_seq_cst)) {
        uint32_t head = atomic_load_explicit(&s->head, memory_order_relaxed);
        uint32_t tail = atomic_load_explicit(&s->tail, memory_order_acquire);
        uint32_t gain_bits = atomic_load_explicit(&s->gain_bits, memory_order_relaxed);
        float gain;
        memcpy(&gain, &gain_bits, sizeof gain);
        uint32_t avail = tail - head;
        got = avail < want ? avail : want;
        if (got) {
            uint32_t pos = head & PCM_RING_MASK;
            uint32_t first = PCM_RING_FRAMES - pos;
            if (first > got) first = got;
            memcpy(dst, s->ring + (size_t)pos * PCM_OUT_CH, (size_t)first * PCM_FRAME_BYTES);
            if (got > first) memcpy(dst + (size_t)first * PCM_OUT_CH, s->ring, (size_t)(got - first) * PCM_FRAME_BYTES);
            if (gain != 1.0f) {
                // Non-unity gain: scale the copied stereo frames in place. Bounded by `got`, pure
                // bounded arithmetic on the destination buffer — no allocation, locks or calls.
                // The gain never zeroes consumption: `got` frames are consumed whatever it is.
                for (uint32_t i = 0; i < got * PCM_OUT_CH; i++) dst[i] = pcm_gain16(dst[i], gain);
            }
            atomic_store_explicit(&s->head, head + got, memory_order_release);
            atomic_fetch_add_explicit(&s->consumed, got, memory_order_relaxed);
        }
        if (want > got) atomic_fetch_add_explicit(&s->underrun, want - got, memory_order_relaxed);
    }
    atomic_fetch_sub_explicit(&s->in_flight, 1, memory_order_seq_cst);

    // Zero-fill the whole remainder of the buffer as handed to us, not just `want` frames.
    size_t filled = (size_t)got * PCM_FRAME_BYTES;
    if (bl->mBuffers[0].mDataByteSize > filled) memset((uint8_t *)dst + filled, 0, bl->mBuffers[0].mDataByteSize - filled);
    if (got == 0 && flags) *flags |= kAudioUnitRenderAction_OutputIsSilence;
    return noErr;
}

// Producer side: wait until no callback is inside the consume path. Only meaningful after
// `active` has been cleared with seq_cst (see invariant 2); bounded by one callback duration.
static void pcm_quiesce(botw_pcm_sink *s) {
    while (atomic_load_explicit(&s->in_flight, memory_order_seq_cst) != 0) sched_yield();
}

// ---------------------------------------------------------------------------------------------
// Source channel mapping into the ring (producer side).
//   1 channel : dual mono, L = R = sample.
//   2 channels: copied unchanged.
//   6 channels: declared order front L, front R, centre, LFE, rear L, rear R. ITU-R BS.775 Lo/Ro
//               in Q15: L = 0.707*FL + 0.5*C + 0.5*RL, R symmetric, which is the standard
//               (FL + 0.707*C + 0.707*RL) scaled by 0.707 for 3 dB of headroom. LFE is excluded
//               by policy (a plain Lo/Ro downmix does not carry it). Results beyond int16 are
//               saturated. Same coefficients as the retired hle_audio.c path.
static inline int16_t pcm_sat16(int32_t v) { return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }

static void pcm_store_frames(int16_t *ring, uint32_t tail, const int16_t *src, uint32_t n, unsigned channels) {
    if (channels == PCM_OUT_CH) {
        uint32_t pos = tail & PCM_RING_MASK;
        uint32_t first = PCM_RING_FRAMES - pos;
        if (first > n) first = n;
        memcpy(ring + (size_t)pos * PCM_OUT_CH, src, (size_t)first * PCM_FRAME_BYTES);
        if (n > first) memcpy(ring, src + (size_t)first * PCM_OUT_CH, (size_t)(n - first) * PCM_FRAME_BYTES);
        return;
    }
    for (uint32_t i = 0; i < n; i++, tail++, src += channels) {
        int16_t *o = ring + (size_t)(tail & PCM_RING_MASK) * PCM_OUT_CH;
        if (channels == 1) { o[0] = o[1] = src[0]; continue; }
        // 23170 = 0.707 in Q15, 16384 = 0.5 in Q15. Worst case |sum| = 55938 * 32768 < 2^31.
        int32_t l = (23170 * src[0] + 16384 * src[2] + 16384 * src[4]) >> 15;
        int32_t r = (23170 * src[1] + 16384 * src[2] + 16384 * src[5]) >> 15;
        o[0] = pcm_sat16(l);
        o[1] = pcm_sat16(r);
    }
}

// ---------------------------------------------------------------------------------------------
// Public API (producer side, not realtime-safe).

static int pcm_open_unit(botw_pcm_sink *s) {
    AudioComponentDescription desc = {
        .componentType = kAudioUnitType_Output,
        .componentSubType = kAudioUnitSubType_DefaultOutput,   // Output only; input element 1 is never enabled.
        .componentManufacturer = kAudioUnitManufacturer_Apple,
    };
    OSStatus st = AU_NEW(&desc, &s->au);
    if (st || !s->au) { s->au = NULL; s->last_error = st ? st : -1; PCM_LOG("cannot create the default output AudioUnit (status %d)", (int)s->last_error); return -1; }

    AudioStreamBasicDescription fmt = {
        .mSampleRate = PCM_RATE,
        .mFormatID = kAudioFormatLinearPCM,
        .mFormatFlags = kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked,
        .mBytesPerPacket = PCM_FRAME_BYTES,
        .mFramesPerPacket = 1,
        .mBytesPerFrame = PCM_FRAME_BYTES,
        .mChannelsPerFrame = PCM_OUT_CH,
        .mBitsPerChannel = 16,
    };
    AURenderCallbackStruct cb = { pcm_render, s };
    const char *step = "set stream format";
    st = AU_SET_PROPERTY(s->au, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &fmt, sizeof fmt);
    if (!st) { step = "set render callback"; st = AU_SET_PROPERTY(s->au, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &cb, sizeof cb); }
    if (!st) { step = "initialize"; st = AU_INITIALIZE(s->au); }
    if (st) {
        s->last_error = st;
        PCM_LOG("cannot %s on the default output AudioUnit (status %d); no output device?", step, (int)st);
        OSStatus disposed = AU_DISPOSE(s->au);
        if (disposed) {
            s->last_error = disposed;
            PCM_LOG("AudioComponentInstanceDispose failed during open cleanup (status %d); retaining callback storage", (int)disposed);
            return -2;   // The live unit may still invoke its callback: the caller must retain s and the ring.
        }
        s->au = NULL;
        pcm_quiesce(s);
        return -1;
    }
    return 0;
}

botw_pcm_sink *botw_pcm_open(int audible) {
    botw_pcm_sink *s = calloc(1, sizeof *s);
    if (!s) { PCM_LOG("cannot allocate the sink"); return NULL; }
    s->audible = audible != 0;
    pcm_store_gain(s, 1.0f);   // calloc would leave 0.0f; the documented initial gain is unity.
    if (s->audible) {
        s->ring = calloc(PCM_RING_FRAMES, PCM_FRAME_BYTES);
        if (!s->ring) { PCM_LOG("cannot allocate the %u-frame ring", (unsigned)PCM_RING_FRAMES); free(s); return NULL; }
        int opened = pcm_open_unit(s);
        if (opened) {
            if (opened != -2) { free(s->ring); free(s); }
            return NULL;
        }
    }
    return s;
}

int botw_pcm_set_active(botw_pcm_sink *s, int active) {
    if (!s) return -1;
    int was = atomic_load_explicit(&s->active, memory_order_relaxed);
    if (active) {
        if (was) return 0;
        // The ring is empty here: open starts empty and stop flushes. Start the device first so a
        // start failure leaves the sink exactly as it was (stopped, no callback ever consuming).
        if (s->audible) {
            OSStatus st = AU_START(s->au);
            if (st) { s->last_error = st; PCM_LOG("cannot start the output AudioUnit (status %d); sink stays stopped", (int)st); return -1; }
        }
        atomic_store_explicit(&s->active, 1, memory_order_seq_cst);
        return 0;
    }
    if (!was) return 0;
    atomic_store_explicit(&s->active, 0, memory_order_seq_cst);
    int rc = 0;
    if (s->audible) {
        OSStatus st = AU_STOP(s->au);
        if (st) { s->last_error = st; PCM_LOG("cannot stop the output AudioUnit (status %d); queued PCM is discarded anyway", (int)st); rc = -1; }
    }
    pcm_quiesce(s);
    // Consumer quiescent and `active` clear: flush by moving the read index up to the write index.
    uint32_t head = atomic_load_explicit(&s->head, memory_order_relaxed);
    uint32_t tail = atomic_load_explicit(&s->tail, memory_order_relaxed);
    if (tail != head) {
        atomic_fetch_add_explicit(&s->dropped, tail - head, memory_order_relaxed);
        atomic_store_explicit(&s->head, tail, memory_order_seq_cst);
    }
    return rc;
}

size_t botw_pcm_write(botw_pcm_sink *s, const int16_t *samples, size_t frames, unsigned channels) {
    if (!s || !samples || frames == 0) return 0;
    if (channels != 1 && channels != 2 && channels != 6) return 0;
    if (frames > SIZE_MAX / sizeof(*samples) / channels) return 0;   // No valid source span can represent this request.
    if (!atomic_load_explicit(&s->active, memory_order_relaxed)) return 0;   // Stopped: rejected, uncounted.
    if (!s->audible) {
        // Muted: accepted frames are consumed on the spot; no ring, no conversion, no device.
        atomic_fetch_add_explicit(&s->submitted, frames, memory_order_relaxed);
        atomic_fetch_add_explicit(&s->consumed, frames, memory_order_relaxed);
        return frames;
    }
    uint32_t tail = atomic_load_explicit(&s->tail, memory_order_relaxed);
    uint32_t head = atomic_load_explicit(&s->head, memory_order_acquire);
    uint32_t space = PCM_RING_FRAMES - (tail - head);
    // Bounded: at most `space` <= PCM_RING_FRAMES frames are read from `samples`, so the source
    // index never exceeds space * 6 and no size_t multiplication of the caller's count happens.
    uint32_t n = frames < space ? (uint32_t)frames : space;
    if (n) {
        pcm_store_frames(s->ring, tail, samples, n, channels);
        atomic_store_explicit(&s->tail, tail + n, memory_order_release);
        atomic_fetch_add_explicit(&s->submitted, n, memory_order_relaxed);
    }
    if (frames > n) atomic_fetch_add_explicit(&s->dropped, frames - n, memory_order_relaxed);
    return n;
}

int botw_pcm_set_volume(botw_pcm_sink *s, float volume) {
    if (!s) return -1;
    if (!isfinite(volume)) return -1;   // NaN and both infinities: rejected, the stored gain is untouched.
    // Finite out-of-range values clamp into [0, 128] (the BotW 1.9 SDK device-volume range);
    // negative values clamp to silence rather than being rejected. Also valid for muted sinks:
    // the gain is stored and validated, but the muted path still makes no AudioToolbox calls and
    // never reads the input. Stop/restart retains the stored value; nothing resets it but open.
    float g = volume < 0.0f ? 0.0f : (volume > 128.0f ? 128.0f : volume);
    pcm_store_gain(s, g);
    return 0;
}

void botw_pcm_get_counters(const botw_pcm_sink *s, botw_pcm_counters *out) {
    if (!out) return;
    if (!s) { memset(out, 0, sizeof *out); return; }
    out->submitted_frames = atomic_load_explicit(&s->submitted, memory_order_relaxed);
    out->consumed_frames = atomic_load_explicit(&s->consumed, memory_order_relaxed);
    out->dropped_frames = atomic_load_explicit(&s->dropped, memory_order_relaxed);
    out->underrun_frames = atomic_load_explicit(&s->underrun, memory_order_relaxed);
}

void botw_pcm_close(botw_pcm_sink *s) {
    if (!s) return;
    botw_pcm_set_active(s, 0);   // Clears `active`, stops the unit, waits for the consumer, flushes.
    if (s->au) {
        // Only successful disposal excludes future callbacks. The quiesce below covers a
        // callback that was already past the active check; it cannot substitute for disposal.
        OSStatus st = AU_UNINITIALIZE(s->au);
        if (st) PCM_LOG("AudioUnitUninitialize failed (status %d); disposing anyway", (int)st);
        st = AU_DISPOSE(s->au);
        if (st) {
            s->last_error = st;
            PCM_LOG("AudioComponentInstanceDispose failed (status %d); retaining inactive callback storage", (int)st);
            return;   // Fail-safe retention: a failed dispose does not exclude future callbacks.
        }
        s->au = NULL;
    }
    pcm_quiesce(s);
    free(s->ring);
    free(s);
}
