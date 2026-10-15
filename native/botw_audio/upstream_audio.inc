/* Derived from jhaizhou's MIT AudioOut module, dm/host/hle_audio.c:
 * https://github.com/TommyLau/botw-mobile
 * commit 5f673ae2ea4f90ad46403aa6fbe13537fb557271 (297 lines).
 * The same SPSC/AudioUnit/notifier implementation is present in d2r-mobile
 * commit 9de307e. Copyright (c) 2026 jhaizhou; see adjacent LICENSE.
 *
 * Local adaptations: host-owned PCM nodes, checked base-1.0 ABI facade,
 * strict AudioUnit errors, pause without consumption, no silent sink, no RT
 * file dump, staged destruction. The queue publication, coalesced notifier,
 * pull/downmix and CoreAudio stream/callback architecture remain upstream's.
 */
enum { FMT_PCM16 = 2, STATE_STARTED = 0, STATE_STOPPED = 1, RATE = 48000 };
typedef struct { void *ptr; uint64_t cap, size; } gbuf; /* Host-only, never guest ABI. */
#define QMAX 64
#define RELMAX QMAX
#define AOMAX 8
#define MIXMAX 4096
#define PCM_LIMIT (64u * 1024u * 1024u)
typedef struct pcm_node { gbuf buffer; uint64_t guest; struct pcm_node *next; } pcm_node;
typedef struct aout {
    struct botw_audio *owner;
    uint64_t guest, guest_event;
    uint32_t writer, unattached_reader;
    int ch, running, retiring, notify_created, notify_joined;
    _Atomic int state, enabled;
    pthread_mutex_t m;
    gbuf *q[QMAX]; _Atomic uint32_t q_head, q_tail;
    gbuf *rel[RELMAX]; _Atomic uint32_t rel_head, rel_tail;
    uint64_t pos;
    void *au;
    int mix;
    int16_t *mixbuf;
    pcm_node *nodes;
    unsigned node_count;
    audio_semaphore notify_sem;
    pthread_t notify_th;
    _Atomic int notify_run, notify_pending, notify_armed;
} aout;

static void notify_release(aout *a) {
    atomic_store_explicit(&a->notify_pending, 1, memory_order_release);
    if (atomic_exchange_explicit(&a->notify_armed, 1, memory_order_acq_rel) == 0)
        semaphore_signal(a->notify_sem);
}
static void *event_notify(void *p) {
    aout *a = p;
    for (;;) {
        semaphore_wait(a->notify_sem);
        if (atomic_exchange_explicit(&a->notify_pending, 0, memory_order_acq_rel) && a->writer) {
            char error[512] = {0};
            if (a->owner->cb.signal_event(a->owner->cb.opaque, a->writer, error, sizeof error))
                save_deferred(a->owner, *error ? error : "AudioOut kernel event signal rejected");
        }
        atomic_store_explicit(&a->notify_armed, 0, memory_order_release);
        if (!atomic_load_explicit(&a->notify_run, memory_order_acquire)) break;
        if (atomic_load_explicit(&a->notify_pending, memory_order_acquire) &&
            atomic_exchange_explicit(&a->notify_armed, 1, memory_order_acq_rel) == 0)
            semaphore_signal(a->notify_sem);
    }
    return NULL;
}

static void pull(aout *a, uint8_t *dst, uint64_t n) {
    int released = 0;
    uint32_t qh = atomic_load_explicit(&a->q_head, memory_order_relaxed);
    while (n) {
        uint32_t qt = atomic_load_explicit(&a->q_tail, memory_order_acquire);
        if (qh == qt || atomic_load_explicit(&a->state, memory_order_acquire) != STATE_STARTED ||
            !atomic_load_explicit(&a->enabled, memory_order_acquire)) { memset(dst, 0, (size_t)n); break; }
        gbuf *b = a->q[qh & (QMAX - 1)];
        uint64_t k = b->size - a->pos; if (k > n) k = n;
        memcpy(dst, (uint8_t *)b->ptr + a->pos, (size_t)k);
        dst += k; n -= k; a->pos += k;
        if (a->pos >= b->size) {
            uint32_t rw = atomic_load_explicit(&a->rel_tail, memory_order_relaxed);
            uint32_t rr = atomic_load_explicit(&a->rel_head, memory_order_acquire);
            /* facade bounds pending + released, so this cannot occur on valid input. */
            if (rw - rr >= RELMAX) {
                atomic_store_explicit(&a->owner->rt_error, 1, memory_order_release);
                memset(dst, 0, (size_t)n); break;
            }
            a->rel[rw & (RELMAX - 1)] = b;
            atomic_store_explicit(&a->rel_tail, rw + 1, memory_order_release);
            qh++;
            atomic_store_explicit(&a->q_head, qh, memory_order_release);
            a->pos = 0; released = 1;
            atomic_fetch_add_explicit(&a->owner->completed_buffers, 1, memory_order_relaxed);
        }
    }
    if (released) notify_release(a);
}

/* Upstream Lo/Ro downmix: input FL, FR, C, LFE, RL, RR. The D2R game's
 * measured layout motivated this implementation; no BOTW mix was measured. */
static void downmix51(const int16_t *in, int16_t *out, unsigned frames) {
    for (unsigned i = 0; i < frames; i++, in += 6, out += 2) {
        int32_t l = (23170 * in[0] + 16384 * in[2] + 16384 * in[4]) >> 15;
        int32_t r = (23170 * in[1] + 16384 * in[2] + 16384 * in[5]) >> 15;
        out[0] = (int16_t)(l > 32767 ? 32767 : l < -32768 ? -32768 : l);
        out[1] = (int16_t)(r > 32767 ? 32767 : r < -32768 ? -32768 : r);
    }
}
static void __attribute__((unused)) render_pcm(aout *a, void *data, size_t bytes) {
    atomic_fetch_add_explicit(&a->owner->render_callbacks, 1, memory_order_relaxed);
    unsigned stride = a->mix ? 4u : (unsigned)a->ch * 2u;
    atomic_fetch_add_explicit(&a->owner->rendered_frames, bytes / stride, memory_order_relaxed);
    if (bytes % stride) { memset(data, 0, bytes); atomic_store(&a->owner->rt_error, 2); return; }
    if (!a->mix) pull(a, data, bytes);
    else {
        int16_t *out = data; size_t left = bytes / 4;
        while (left) {
            unsigned k = left < MIXMAX ? (unsigned)left : MIXMAX;
            pull(a, (uint8_t *)a->mixbuf, (uint64_t)k * 12);
            downmix51(a->mixbuf, out, k); out += 2 * k; left -= k;
        }
    }
}

#if defined(BOTW_AUDIO_TEST_BACKEND)
/* Deterministic contract harness only. Product builds have no injectable sink. */
extern int botw_audio_test_transport(void *output, int operation);
static int platform_open(aout *a, char *error, size_t cap) {
    int result = botw_audio_test_transport(a, 0);
    if (result) return problem(error, cap, "AudioOut AudioUnit open failed (%d)", result);
    a->au = a; return 0;
}
static int platform_start(aout *a, char *error, size_t cap) {
    int result = botw_audio_test_transport(a, 1);
    return result ? problem(error, cap, "AudioOut AudioUnit start failed (%d)", result) : 0;
}
static int platform_stop(aout *a, char *error, size_t cap) {
    int result = botw_audio_test_transport(a, 2);
    return result ? problem(error, cap, "AudioOut AudioUnit stop failed (%d)", result) : 0;
}
static int platform_dispose(aout *a, char *error, size_t cap) {
    int result = botw_audio_test_transport(a, 3);
    if (result) return problem(error, cap, "AudioOut AudioUnit dispose failed (%d)", result);
    a->au = NULL; return 0;
}
static int platform_has_output(void) { return 1; }
#elif defined(__APPLE__)
static OSStatus au_render(void *ctx, AudioUnitRenderActionFlags *flags, const AudioTimeStamp *timestamp,
    UInt32 bus, UInt32 frames, AudioBufferList *buffers) {
    (void)flags; (void)timestamp; (void)bus;
    aout *a = ctx; unsigned channels = a->mix ? 2u : (unsigned)a->ch;
    uint64_t required = (uint64_t)frames * channels * 2u;
    int invalid = !buffers || buffers->mNumberBuffers != 1 ? 3 :
        buffers->mBuffers[0].mNumberChannels != channels ? 5 :
        required > buffers->mBuffers[0].mDataByteSize ? 4 :
        required && !buffers->mBuffers[0].mData ? 3 : 0;
    if (invalid) {
        if (buffers) for (UInt32 i = 0; i < buffers->mNumberBuffers; i++) {
            if (buffers->mBuffers[i].mData) memset(buffers->mBuffers[i].mData, 0, buffers->mBuffers[i].mDataByteSize);
            buffers->mBuffers[i].mDataByteSize = 0;
        }
        atomic_store_explicit(&a->owner->rt_error, invalid, memory_order_release); return kAudio_ParamError;
    }
    /* mDataByteSize is capacity on entry. Only the requested frames are PCM
     * consumption, even when the host supplies a larger reusable allocation. */
    render_pcm(a, buffers->mBuffers[0].mData, (size_t)required);
    buffers->mBuffers[0].mDataByteSize = (UInt32)required; return noErr;
}
static AudioComponent output_component(void) {
    AudioComponentDescription description = {.componentType = kAudioUnitType_Output,
#if TARGET_OS_IPHONE
        .componentSubType = kAudioUnitSubType_RemoteIO,
#else
        .componentSubType = kAudioUnitSubType_DefaultOutput,
#endif
        .componentManufacturer = kAudioUnitManufacturer_Apple};
    return AudioComponentFindNext(NULL, &description);
}
static int platform_has_output(void) {
    if (!output_component()) return 0;
#if !TARGET_OS_IPHONE
    AudioObjectID device = kAudioObjectUnknown; UInt32 size = sizeof device;
    AudioObjectPropertyAddress address = {kAudioHardwarePropertyDefaultOutputDevice,
        kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &address, 0, NULL, &size, &device) || !device) return 0;
#endif
    return 1;
}
static int platform_open(aout *a, char *error, size_t cap) {
    AudioComponent component = output_component();
    if (!component) return problem(error, cap, "AudioOut AudioUnit output component unavailable");
    AudioUnit unit = NULL; OSStatus status = AudioComponentInstanceNew(component, &unit); a->au = unit;
    if (status || !unit) return problem(error, cap, "AudioOut AudioComponentInstanceNew failed (%d)", (int)status);
    unsigned channels = a->mix ? 2u : (unsigned)a->ch;
    AudioStreamBasicDescription format = {.mSampleRate = RATE, .mFormatID = kAudioFormatLinearPCM,
        .mFormatFlags = kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked,
        .mBytesPerPacket = 2u * channels, .mFramesPerPacket = 1, .mBytesPerFrame = 2u * channels,
        .mChannelsPerFrame = channels, .mBitsPerChannel = 16};
    AURenderCallbackStruct callback = {au_render, a};
    status = AudioUnitSetProperty(unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &format, sizeof format);
    if (!status) status = AudioUnitSetProperty(unit, kAudioUnitProperty_SetRenderCallback,
        kAudioUnitScope_Input, 0, &callback, sizeof callback);
    if (!status) status = AudioUnitInitialize(unit);
    return status ? problem(error, cap, "AudioOut AudioUnit format/callback/initialize failed (%d)", (int)status) : 0;
}
static int platform_start(aout *a, char *error, size_t cap) {
    OSStatus status = AudioOutputUnitStart((AudioUnit)a->au);
    return status ? problem(error, cap, "AudioOut AudioUnit start failed (%d)", (int)status) : 0;
}
static int platform_stop(aout *a, char *error, size_t cap) {
    OSStatus status = AudioOutputUnitStop((AudioUnit)a->au);
    return status ? problem(error, cap, "AudioOut AudioUnit stop failed (%d)", (int)status) : 0;
}
static int platform_dispose(aout *a, char *error, size_t cap) {
    OSStatus uninit = AudioUnitUninitialize((AudioUnit)a->au);
    OSStatus dispose = AudioComponentInstanceDispose((AudioUnit)a->au);
    if (dispose) return problem(error, cap, "AudioOut AudioUnit dispose failed (%d)", (int)dispose);
    a->au = NULL; /* Successful disposal is the callback quiescence boundary. */
    return uninit ? problem(error, cap, "AudioOut AudioUnit uninitialize failed (%d), instance disposed", (int)uninit) : 0;
}
#else
static int platform_open(aout *a, char *e, size_t n) { (void)a; return problem(e,n,"AudioOut requires Apple AudioUnit"); }
static int platform_start(aout *a, char *e, size_t n) { return platform_open(a,e,n); }
static int platform_stop(aout *a, char *e, size_t n) { return platform_open(a,e,n); }
static int platform_dispose(aout *a, char *e, size_t n) { return platform_open(a,e,n); }
static int platform_has_output(void) { return 0; }
#endif
