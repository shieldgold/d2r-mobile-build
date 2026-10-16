// Unit and stress test for the host PCM output backend (botw_pcm.c) with the AudioToolbox calls
// replaced by mocks (-DBOTW_PCM_MOCK_AU). The source is included directly so the static realtime
// callback pcm_render can be driven from the test, which plays the role of the AudioUnit render
// thread. No audio device is opened and nothing is played. Expected sample values are hand-written
// from the documented mapping policy, never computed by the implementation under test.
//
// Volume coverage (botw_pcm_set_volume): frames queued before a gain change come out at the new
// gain; exact half/double/128 products with truncate-toward-zero and int16 clipping; negative and
// above-128 finite clamps; NaN/+Inf/-Inf rejected with the gain unchanged; zero gain consumes
// frames without counting underruns; gain applies after mono/stereo/5.1 conversion; retention
// across stop/restart and on muted sinks; and a concurrent invariant stress (constant tone under
// exactly one gain per rendered buffer) for races between volume changes and the render thread.
//
// Build (from dm/host/tests; outputs under build/ or any scratch directory):
//   clang -O1 -g -Wall -Wextra -Werror -Wno-unused-function -DBOTW_PCM_MOCK_AU -I.. -o build/t_botw_pcm t_botw_pcm.c
//   clang -O1 -g -Wall -Wextra -Werror -Wno-unused-function -DBOTW_PCM_MOCK_AU -I.. -fsanitize=address,undefined -fno-sanitize-recover=all -o build/t_botw_pcm_asan t_botw_pcm.c
//   clang -O1 -g -Wall -Wextra -Werror -Wno-unused-function -DBOTW_PCM_MOCK_AU -I.. -fsanitize=thread -o build/t_botw_pcm_tsan t_botw_pcm.c
// Run: build/t_botw_pcm   (exit 0 = pass). T_BOTW_PCM_STRESS_FRAMES=<n> scales the sequence stress
// test; T_BOTW_PCM_GAIN_STRESS_FRAMES=<n> scales the gain stress test.
#include "../botw_pcm.c"
#include <inttypes.h>
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>

// ---------------------------------------------------------------------------------------------
// Mock AudioToolbox. Records the call sequence and the render callback, fails steps on demand,
// and models the Dispose contract: no callback may run once AudioComponentInstanceDispose returns.
static struct {
    int new_calls, set_calls, init_calls, uninit_calls, start_calls, stop_calls, dispose_calls;
    OSStatus fail_new, fail_set, fail_init, fail_start, fail_stop, fail_dispose;
    int fail_set_index;                 // Which AudioUnitSetProperty call fails (1-based), 0 = none.
    AudioStreamBasicDescription fmt;    // Format handed to kAudioUnitProperty_StreamFormat.
    AURenderCallbackStruct cb;          // Callback handed to kAudioUnitProperty_SetRenderCallback.
    int unit_alive, unit_started, unit_initialized;
    char seq[64];                       // Letter per call: N S S I A O U D.
    int seq_len;
    pthread_t *consumer_to_join;        // Stress: Dispose joins the render thread before returning.
    _Atomic int *consumer_stop;
} g_au;
static int g_dummy_unit;
static void au_seq(char c) { if (g_au.seq_len < (int)sizeof g_au.seq - 1) g_au.seq[g_au.seq_len++] = c; g_au.seq[g_au.seq_len] = 0; }
static void au_reset(void) { memset(&g_au, 0, sizeof g_au); }

OSStatus botw_pcm_mock_new(const AudioComponentDescription *desc, AudioUnit *out) {
    au_seq('N'); g_au.new_calls++;
    if (desc->componentType != kAudioUnitType_Output || desc->componentSubType != kAudioUnitSubType_DefaultOutput || desc->componentManufacturer != kAudioUnitManufacturer_Apple) abort();
    if (g_au.fail_new) { *out = NULL; return g_au.fail_new; }
    g_au.unit_alive = 1; *out = (AudioUnit)&g_dummy_unit; return noErr;
}
OSStatus botw_pcm_mock_set_property(AudioUnit au, AudioUnitPropertyID id, AudioUnitScope scope, AudioUnitElement element, const void *data, UInt32 size) {
    au_seq('S'); g_au.set_calls++;
    if (au != (AudioUnit)&g_dummy_unit || !g_au.unit_alive || g_au.unit_initialized) abort();
    if (scope != kAudioUnitScope_Input || element != 0) abort();   // Only the output bus's input side; never element 1 (the microphone).
    if (id == kAudioUnitProperty_StreamFormat) { if (size != sizeof g_au.fmt) abort(); memcpy(&g_au.fmt, data, size); }
    else if (id == kAudioUnitProperty_SetRenderCallback) { if (size != sizeof g_au.cb) abort(); memcpy(&g_au.cb, data, size); }
    else abort();
    return g_au.fail_set && g_au.fail_set_index == g_au.set_calls ? g_au.fail_set : noErr;
}
OSStatus botw_pcm_mock_initialize(AudioUnit au) { au_seq('I'); g_au.init_calls++; if (au != (AudioUnit)&g_dummy_unit || !g_au.unit_alive) abort(); if (g_au.fail_init) return g_au.fail_init; g_au.unit_initialized = 1; return noErr; }
OSStatus botw_pcm_mock_uninitialize(AudioUnit au) { au_seq('U'); g_au.uninit_calls++; if (au != (AudioUnit)&g_dummy_unit || !g_au.unit_alive) abort(); if (g_au.unit_started) return -10863; g_au.unit_initialized = 0; return noErr; }
OSStatus botw_pcm_mock_start(AudioUnit au) { au_seq('A'); g_au.start_calls++; if (au != (AudioUnit)&g_dummy_unit || !g_au.unit_initialized) abort(); if (g_au.fail_start) return g_au.fail_start; g_au.unit_started = 1; return noErr; }
OSStatus botw_pcm_mock_stop(AudioUnit au) { au_seq('O'); g_au.stop_calls++; if (au != (AudioUnit)&g_dummy_unit || !g_au.unit_initialized) abort(); if (g_au.fail_stop) return g_au.fail_stop; g_au.unit_started = 0; return noErr; }
OSStatus botw_pcm_mock_dispose(AudioUnit au) {
    au_seq('D'); g_au.dispose_calls++;
    if (au != (AudioUnit)&g_dummy_unit || !g_au.unit_alive) abort();
    if (g_au.fail_dispose) return g_au.fail_dispose;   // No lifetime guarantee: callback registration and consumer stay live.
    if (g_au.consumer_to_join) { atomic_store(g_au.consumer_stop, 1); pthread_join(*g_au.consumer_to_join, NULL); g_au.consumer_to_join = NULL; }
    g_au.unit_alive = 0; g_au.unit_initialized = 0; g_au.unit_started = 0; return noErr;
}

// ---------------------------------------------------------------------------------------------
static int g_fail;
#define CHECK(cond) do { if (!(cond)) { fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); g_fail = 1; } } while (0)
#define CHECK_EQ(a, b) do { uint64_t a_ = (uint64_t)(a), b_ = (uint64_t)(b); if (a_ != b_) { fprintf(stderr, "%s:%d: %s = %" PRIu64 ", expected %s = %" PRIu64 "\n", __FILE__, __LINE__, #a, a_, #b, b_); g_fail = 1; } } while (0)

// Drive the registered render callback like the AudioUnit would: one interleaved stereo buffer.
static AudioUnitRenderActionFlags render(botw_pcm_sink *s, int16_t *dst, uint32_t frames) {
    AudioUnitRenderActionFlags flags = 0;
    AudioTimeStamp ts = {0};
    AudioBufferList bl = { .mNumberBuffers = 1 };
    bl.mBuffers[0].mNumberChannels = 2; bl.mBuffers[0].mDataByteSize = frames * 4; bl.mBuffers[0].mData = dst;
    memset(dst, 0x5a, (size_t)frames * 4);   // Poison so missing zero-fill shows.
    AURenderCallback proc = g_au.cb.inputProc ? g_au.cb.inputProc : pcm_render;
    void *ctx = g_au.cb.inputProc ? g_au.cb.inputProcRefCon : s;
    CHECK(proc(ctx, &flags, &ts, 0, frames, &bl) == noErr);
    return flags;
}
static botw_pcm_counters counters(const botw_pcm_sink *s) { botw_pcm_counters c; botw_pcm_get_counters(s, &c); return c; }
static void check_counters(const botw_pcm_sink *s, uint64_t sub, uint64_t con, uint64_t drop, uint64_t under) {
    botw_pcm_counters c = counters(s);
    CHECK_EQ(c.submitted_frames, sub); CHECK_EQ(c.consumed_frames, con); CHECK_EQ(c.dropped_frames, drop); CHECK_EQ(c.underrun_frames, under);
}
static int16_t seq_l(uint32_t seq) { return (int16_t)(1 + seq % 30000); }
static int16_t seq_r(uint32_t seq) { return (int16_t)(1 + seq / 30000); }
static int64_t seq_decode(int16_t l, int16_t r) { return (l == 0 && r == 0) ? -1 : (int64_t)(r - 1) * 30000 + (l - 1); }
static void fill_seq(int16_t *buf, uint32_t first, uint32_t n) { for (uint32_t i = 0; i < n; i++) { buf[2 * i] = seq_l(first + i); buf[2 * i + 1] = seq_r(first + i); } }

static void test_lifecycle_and_failures(void) {
    au_reset();
    botw_pcm_sink *s = botw_pcm_open(1);
    CHECK(s != NULL);
    CHECK(strcmp(g_au.seq, "NSSI") == 0);
    CHECK(g_au.fmt.mSampleRate == 48000.0 && g_au.fmt.mFormatID == kAudioFormatLinearPCM);
    CHECK(g_au.fmt.mFormatFlags == (kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked));
    CHECK(g_au.fmt.mChannelsPerFrame == 2 && g_au.fmt.mBitsPerChannel == 16 && g_au.fmt.mBytesPerFrame == 4 && g_au.fmt.mBytesPerPacket == 4 && g_au.fmt.mFramesPerPacket == 1);
    CHECK(g_au.cb.inputProc == pcm_render && g_au.cb.inputProcRefCon == s);   // Context is the sink itself.
    int16_t buf[8] = {1, 2, 3, 4, 5, 6, 7, 8}, out[16];
    CHECK_EQ(botw_pcm_write(s, buf, 4, 2), 0);           // Initially stopped: rejected.
    check_counters(s, 0, 0, 0, 0);
    render(s, out, 4);                                    // Callback before start: silence, no underrun counted.
    CHECK(out[0] == 0 && out[7] == 0);
    check_counters(s, 0, 0, 0, 0);
    CHECK_EQ(botw_pcm_set_active(s, 1), 0);
    CHECK_EQ(botw_pcm_set_active(s, 1), 0);               // Idempotent, no second start.
    CHECK(g_au.start_calls == 1);
    CHECK_EQ(botw_pcm_write(s, buf, 4, 2), 4);
    CHECK_EQ(botw_pcm_set_active(s, 0), 0);
    CHECK_EQ(botw_pcm_set_active(s, 0), 0);
    CHECK(g_au.stop_calls == 1);
    check_counters(s, 4, 0, 4, 0);                        // Stop discarded the 4 queued frames.
    botw_pcm_close(s);
    CHECK(strcmp(g_au.seq, "NSSIAOUD") == 0);             // Stop precedes uninitialize precedes dispose.
    CHECK(!g_au.unit_alive);

    // Close while active: close itself stops the unit first.
    au_reset(); s = botw_pcm_open(1); CHECK(s && !botw_pcm_set_active(s, 1)); botw_pcm_close(s);
    CHECK(strcmp(g_au.seq, "NSSIAOUD") == 0);

    // Open failures: NULL, English diagnostic, unit disposed when it had been created.
    au_reset(); g_au.fail_new = -1; CHECK(botw_pcm_open(1) == NULL); CHECK(strcmp(g_au.seq, "N") == 0);
    au_reset(); g_au.fail_set = -10868; g_au.fail_set_index = 1; CHECK(botw_pcm_open(1) == NULL); CHECK(strcmp(g_au.seq, "NSD") == 0);
    au_reset(); g_au.fail_set = -10868; g_au.fail_set_index = 2; CHECK(botw_pcm_open(1) == NULL); CHECK(strcmp(g_au.seq, "NSSD") == 0);
    au_reset(); g_au.fail_init = -10851; CHECK(botw_pcm_open(1) == NULL); CHECK(strcmp(g_au.seq, "NSSID") == 0);

    // Start failure: -1, sink stays stopped (writes rejected), retry can succeed later.
    au_reset(); s = botw_pcm_open(1); CHECK(s != NULL);
    g_au.fail_start = -10863;
    CHECK_EQ(botw_pcm_set_active(s, 1), (uint64_t)-1);
    CHECK_EQ(botw_pcm_write(s, buf, 4, 2), 0);
    CHECK(s->last_error == -10863);
    g_au.fail_start = 0;
    CHECK_EQ(botw_pcm_set_active(s, 1), 0);
    CHECK_EQ(botw_pcm_write(s, buf, 4, 2), 4);
    g_au.fail_stop = -1;                                  // Stop failure: -1 but the sink is stopped and flushed anyway.
    CHECK_EQ(botw_pcm_set_active(s, 0), (uint64_t)-1);
    g_au.fail_stop = 0;   // Close must handle a unit that the failed stop left running.
    CHECK_EQ(botw_pcm_write(s, buf, 4, 2), 0);
    check_counters(s, 4, 0, 4, 0);
    botw_pcm_close(s);
    CHECK(g_au.dispose_calls == 1);

    // Failed disposal cannot justify freeing the callback context. A late callback must remain safe and silent.
    au_reset(); s = botw_pcm_open(1); CHECK(s && !botw_pcm_set_active(s, 1));
    g_au.fail_dispose = -50;
    botw_pcm_close(s);
    CHECK(g_au.unit_alive && s->au && s->last_error == -50);
    render(s, out, 4); CHECK(out[0] == 0 && out[7] == 0);
    g_au.fail_dispose = 0;
    botw_pcm_close(s);   // Test-only recovery of known retained storage, avoiding an intentional test leak.
    CHECK(!g_au.unit_alive);

    // The same lifetime rule applies when initialize fails after callback registration.
    au_reset(); g_au.fail_init = -10851; g_au.fail_dispose = -50;
    CHECK(botw_pcm_open(1) == NULL);
    s = g_au.cb.inputProcRefCon;
    CHECK(s && g_au.unit_alive && s->au);
    render(s, out, 4); CHECK(out[0] == 0 && out[7] == 0);
    g_au.fail_init = 0; g_au.fail_dispose = 0;
    botw_pcm_close(s);
    CHECK(!g_au.unit_alive);

    // NULL handling.
    CHECK_EQ(botw_pcm_set_active(NULL, 1), (uint64_t)-1);
    botw_pcm_counters c = {1, 1, 1, 1}; botw_pcm_get_counters(NULL, &c); CHECK(c.submitted_frames == 0 && c.underrun_frames == 0);
    botw_pcm_get_counters(NULL, NULL);
    botw_pcm_close(NULL);
}

static void test_ring_order_partial_wrap_underrun(void) {
    au_reset();
    botw_pcm_sink *s = botw_pcm_open(1);
    CHECK(s && !botw_pcm_set_active(s, 1));
    enum { CAP = PCM_RING_FRAMES };
    int16_t *in = malloc((size_t)(CAP + 100) * 4), *out = malloc((size_t)CAP * 4);
    CHECK(in && out);

    // Empty ring: full underrun, zero-filled, silence flag.
    AudioUnitRenderActionFlags f = render(s, out, 16);
    CHECK(f & kAudioUnitRenderAction_OutputIsSilence);
    for (int i = 0; i < 32; i++) CHECK(out[i] == 0);
    check_counters(s, 0, 0, 0, 16);

    // Partial underrun: 10 queued, 16 asked -> 10 data then 6 zero frames.
    fill_seq(in, 0, 10);
    CHECK_EQ(botw_pcm_write(s, in, 10, 2), 10);
    f = render(s, out, 16);
    CHECK(!(f & kAudioUnitRenderAction_OutputIsSilence));
    for (uint32_t i = 0; i < 10; i++) CHECK(seq_decode(out[2 * i], out[2 * i + 1]) == (int64_t)i);
    for (int i = 20; i < 32; i++) CHECK(out[i] == 0);
    check_counters(s, 10, 10, 0, 22);

    // Overflow: CAP + 100 offered -> CAP accepted (prefix), 100 dropped.
    fill_seq(in, 10, CAP + 100);
    CHECK_EQ(botw_pcm_write(s, in, CAP + 100, 2), CAP);
    CHECK_EQ(botw_pcm_write(s, in, 1, 2), 0);             // Full: nothing accepted, 1 more dropped.
    check_counters(s, 10 + CAP, 10, 101, 22);
    // Drain in odd-sized chunks, checking order and that no silence is inserted.
    uint32_t next = 10, left = CAP;
    for (uint32_t n = 1; left; n = (n * 7 + 3) % 1000 + 1) {
        uint32_t k = n < left ? n : left;
        render(s, out, k);
        for (uint32_t i = 0; i < k; i++) CHECK(seq_decode(out[2 * i], out[2 * i + 1]) == (int64_t)(next + i));
        next += k; left -= k;
    }
    check_counters(s, 10 + CAP, 10 + CAP, 101, 22);
    // Wrap: the write index is now at 10 + CAP, so a CAP-sized write straddles the ring boundary.
    fill_seq(in, next, CAP);
    CHECK_EQ(botw_pcm_write(s, in, CAP, 2), CAP);
    render(s, out, CAP);
    for (uint32_t i = 0; i < CAP; i++) if (seq_decode(out[2 * i], out[2 * i + 1]) != (int64_t)(next + i)) { CHECK(!"wrap order"); break; }
    next += CAP;
    // An unrepresentable declared source span is rejected without reading input or changing counters.
    botw_pcm_counters before_invalid = counters(s);
    CHECK_EQ(botw_pcm_write(s, in, SIZE_MAX, 2), 0);
    botw_pcm_counters c = counters(s);
    CHECK(memcmp(&c, &before_invalid, sizeof c) == 0);
    CHECK_EQ(botw_pcm_write(s, in, CAP, 2), CAP);
    c = counters(s);
    CHECK_EQ(c.dropped_frames, 101);
    CHECK_EQ(c.submitted_frames, 10 + 3 * (uint64_t)CAP);

    // Stop flushes queued PCM; restart begins empty and the old data never reappears.
    CHECK_EQ(botw_pcm_set_active(s, 0), 0);
    c = counters(s);
    CHECK_EQ(c.dropped_frames, 101 + CAP);
    render(s, out, 8);                                    // Stopped: zero-fill, no counter movement.
    for (int i = 0; i < 16; i++) CHECK(out[i] == 0);
    botw_pcm_counters c2 = counters(s); CHECK(memcmp(&c, &c2, sizeof c) == 0);
    CHECK_EQ(botw_pcm_set_active(s, 1), 0);
    f = render(s, out, 8);
    CHECK(f & kAudioUnitRenderAction_OutputIsSilence);
    c2 = counters(s); CHECK_EQ(c2.underrun_frames, c.underrun_frames + 8); CHECK_EQ(c2.consumed_frames, c.consumed_frames);
    fill_seq(in, 7, 3);
    CHECK_EQ(botw_pcm_write(s, in, 3, 2), 3);
    render(s, out, 3);
    CHECK(seq_decode(out[0], out[1]) == 7 && seq_decode(out[4], out[5]) == 9);

    // Buffer list smaller than the requested frame count: only the buffer size is written.
    { AudioUnitRenderActionFlags fl = 0; AudioTimeStamp ts = {0}; AudioBufferList bl = { .mNumberBuffers = 1 };
      memset(out, 0x5a, 64); bl.mBuffers[0].mDataByteSize = 4 * 4; bl.mBuffers[0].mData = out;
      fill_seq(in, 0, 6); CHECK_EQ(botw_pcm_write(s, in, 6, 2), 6);
      CHECK(pcm_render(s, &fl, &ts, 0, 8, &bl) == noErr);
      CHECK(seq_decode(out[6], out[7]) == 3 && out[8] == 0x5a5a);
      c = counters(s); CHECK_EQ(c.consumed_frames, c2.consumed_frames + 3 + 4);
      AudioBufferList empty = { .mNumberBuffers = 0 }; CHECK(pcm_render(s, &fl, &ts, 0, 8, &empty) == noErr);
      CHECK(pcm_render(s, &fl, &ts, 0, 8, NULL) == noErr); }
    botw_pcm_close(s);
    free(in); free(out);
}

static void test_channel_mapping(void) {
    au_reset();
    botw_pcm_sink *s = botw_pcm_open(1);
    CHECK(s && !botw_pcm_set_active(s, 1));
    int16_t out[64];
    // Mono: dual mono.
    const int16_t mono[4] = {5, -7, 32767, -32768};
    CHECK_EQ(botw_pcm_write(s, mono, 4, 1), 4);
    render(s, out, 4);
    const int16_t mono_exp[8] = {5, 5, -7, -7, 32767, 32767, -32768, -32768};
    CHECK(memcmp(out, mono_exp, sizeof mono_exp) == 0);
    // Stereo: unchanged.
    const int16_t st[6] = {1, -1, 32767, -32768, 0, 100};
    CHECK_EQ(botw_pcm_write(s, st, 3, 2), 3);
    render(s, out, 3);
    CHECK(memcmp(out, st, sizeof st) == 0);
    // 5.1 in declared order FL FR C LFE RL RR -> Lo/Ro Q15: L = (23170 FL + 16384 C + 16384 RL) >> 15.
    const int16_t six[9][6] = {
        {1000, 0, 0, 0, 0, 0},          // 23170000 >> 15 = 707
        {0, -1000, 0, 0, 0, 0},         // -23170000 >> 15 = -708 (arithmetic shift rounds toward -inf)
        {0, 0, 1000, 0, 0, 0},          // centre -> both at 500
        {0, 0, 0, 32767, 0, 0},         // LFE is not mixed in
        {0, 0, 0, 0, 1000, 0},          // rear L -> L 500, R 0
        {0, 0, 0, 0, 0, -1000},         // rear R -> L 0, R -500
        {32767, 32767, 32767, 32767, 32767, 32767},   // 55936 -> clipped 32767
        {-32768, -32768, -32768, -32768, -32768, -32768}, // -55938 -> clipped -32768
        {32767, 0, -32768, 0, 0, 32767},                 // L = 23170*32767 - 16384*32768 = 222,340,478 >> 15 = 6785; R = 16384*(-32768+32767) = -16384 >> 15 = -1
    };
    const int16_t six_exp[18] = {707, 0, 0, -708, 500, 500, 0, 0, 500, 0, 0, -500, 32767, 32767, -32768, -32768, 6785, -1};
    CHECK_EQ(botw_pcm_write(s, &six[0][0], 9, 6), 9);
    render(s, out, 9);
    for (int i = 0; i < 18; i++) if (out[i] != six_exp[i]) { fprintf(stderr, "5.1 sample %d: got %d expected %d\n", i, out[i], six_exp[i]); g_fail = 1; }
    // Unsupported inputs: rejected, no counters, no ring change.
    botw_pcm_counters before = counters(s);
    const unsigned bad[] = {0, 3, 4, 5, 7, 8, 16, 0xffffffffu};
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) CHECK_EQ(botw_pcm_write(s, st, 1, bad[i]), 0);
    CHECK_EQ(botw_pcm_write(s, NULL, 1, 2), 0);
    CHECK_EQ(botw_pcm_write(s, st, 0, 2), 0);
    CHECK_EQ(botw_pcm_write(NULL, st, 1, 2), 0);
    botw_pcm_counters after = counters(s);
    CHECK(memcmp(&before, &after, sizeof before) == 0);
    CHECK_EQ(after.submitted_frames, 16); CHECK_EQ(after.consumed_frames, 16);
    botw_pcm_close(s);
}

static void test_muted(void) {
    au_reset();
    botw_pcm_sink *s = botw_pcm_open(0);
    CHECK(s != NULL);
    CHECK(g_au.seq_len == 0);                             // No AudioToolbox call at all.
    int16_t buf[12] = {0};
    CHECK_EQ(botw_pcm_write(s, buf, 2, 6), 0);            // Stopped: rejected.
    check_counters(s, 0, 0, 0, 0);
    CHECK_EQ(botw_pcm_set_active(s, 1), 0);
    CHECK_EQ(botw_pcm_write(s, buf, 2, 6), 2);
    CHECK_EQ(botw_pcm_write(s, buf, 6, 2), 6);
    CHECK_EQ(botw_pcm_write(s, buf, 12, 1), 12);
    CHECK_EQ(botw_pcm_write(s, buf, 1, 3), 0);            // Invalid channel count still rejected.
    CHECK_EQ(botw_pcm_write(s, NULL, 1, 2), 0);
    CHECK_EQ(botw_pcm_write(s, buf, SIZE_MAX, 1), 0);     // Muting does not excuse an unrepresentable source span.
    check_counters(s, 20, 20, 0, 0);                      // Consumed on the spot, nothing dropped.
    CHECK_EQ(botw_pcm_set_active(s, 0), 0);
    CHECK_EQ(botw_pcm_write(s, buf, 2, 2), 0);
    check_counters(s, 20, 20, 0, 0);
    CHECK_EQ(botw_pcm_set_active(s, 1), 0);
    CHECK_EQ(botw_pcm_write(s, buf, 2, 2), 2);
    check_counters(s, 22, 22, 0, 0);
    botw_pcm_close(s);
    CHECK(g_au.seq_len == 0);
}

static void test_volume(void) {
    au_reset();
    botw_pcm_sink *s = botw_pcm_open(1);
    CHECK(s && !botw_pcm_set_active(s, 1));
    int16_t out[16];

    // Initial gain is 1: bit-exact passthrough (the memcpy fast path).
    const int16_t pas[8] = {1, -1, 32767, -32768, 0, 100, -20000, 20000};
    CHECK_EQ(botw_pcm_write(s, pas, 4, 2), 4);
    render(s, out, 4);
    CHECK(memcmp(out, pas, sizeof pas) == 0);

    // Non-finite values are rejected without changing the gain (still unity here).
    CHECK_EQ(botw_pcm_set_volume(s, NAN), (uint64_t)-1);
    CHECK_EQ(botw_pcm_set_volume(s, INFINITY), (uint64_t)-1);
    CHECK_EQ(botw_pcm_set_volume(s, -INFINITY), (uint64_t)-1);
    CHECK_EQ(botw_pcm_set_volume(NULL, 1.0f), (uint64_t)-1);
    CHECK_EQ(botw_pcm_write(s, pas, 4, 2), 4);
    render(s, out, 4);
    CHECK(memcmp(out, pas, sizeof pas) == 0);

    // Frames queued BEFORE the gain change come out at the NEW gain. Rounding is truncate toward
    // zero: 1*0.5=0.5->0, -1*0.5=-0.5->0, 32767*0.5=16383.5->16383, -32768*0.5=-16384, 3*0.5=1.5->1,
    // -3*0.5=-1.5->-1, 7*0.5=3.5->3, -9*0.5=-4.5->-4.
    const int16_t half_in[8] = {1, -1, 32767, -32768, 3, -3, 7, -9};
    const int16_t half_exp[8] = {0, 0, 16383, -16384, 1, -1, 3, -4};
    CHECK_EQ(botw_pcm_write(s, half_in, 4, 2), 4);
    CHECK_EQ(botw_pcm_set_volume(s, 0.5f), 0);
    render(s, out, 4);
    CHECK(memcmp(out, half_exp, sizeof half_exp) == 0);

    // Double with clipping: -20000*2=-40000 -> -32768; 20000*2=40000 -> 32767.
    const int16_t dbl_in[8] = {100, -100, -20000, 20000, 32767, -32768, 1, -1};
    const int16_t dbl_exp[8] = {200, -200, -32768, 32767, 32767, -32768, 2, -2};
    CHECK_EQ(botw_pcm_set_volume(s, 2.0f), 0);
    CHECK_EQ(botw_pcm_write(s, dbl_in, 4, 2), 4);
    render(s, out, 4);
    CHECK(memcmp(out, dbl_exp, sizeof dbl_exp) == 0);

    // Rejection at non-unity gain leaves the working gain exactly as it was.
    CHECK_EQ(botw_pcm_set_volume(s, NAN), (uint64_t)-1);
    CHECK_EQ(botw_pcm_write(s, dbl_in, 4, 2), 4);
    render(s, out, 4);
    CHECK(memcmp(out, dbl_exp, sizeof dbl_exp) == 0);

    // Extreme 128: 256*128=32768 -> 32767; -256*128=-32768 exact; 255*128=32640; -257*128=-32896 -> -32768.
    const int16_t x128_in[8] = {256, -256, 255, -257, 0, 1, -1, 2};
    const int16_t x128_exp[8] = {32767, -32768, 32640, -32768, 0, 128, -128, 256};
    CHECK_EQ(botw_pcm_set_volume(s, 128.0f), 0);
    CHECK_EQ(botw_pcm_write(s, x128_in, 4, 2), 4);
    render(s, out, 4);
    CHECK(memcmp(out, x128_exp, sizeof x128_exp) == 0);

    // A finite value above 128 clamps to 128; it is not rejected.
    CHECK_EQ(botw_pcm_set_volume(s, 1000.0f), 0);
    CHECK_EQ(botw_pcm_write(s, x128_in, 4, 2), 4);
    render(s, out, 4);
    CHECK(memcmp(out, x128_exp, sizeof x128_exp) == 0);

    // Zero volume renders silence but still consumes: with 2 queued and 4 asked, exactly the 2
    // genuinely missing frames are underruns, and the silence flag is not set (data was served).
    const int16_t nz[4] = {12345, -12345, 32767, -32768};
    CHECK_EQ(botw_pcm_set_volume(s, 0.0f), 0);
    botw_pcm_counters b = counters(s);
    CHECK_EQ(botw_pcm_write(s, nz, 2, 2), 2);
    AudioUnitRenderActionFlags f = render(s, out, 4);
    CHECK(!(f & kAudioUnitRenderAction_OutputIsSilence));
    for (int i = 0; i < 8; i++) CHECK(out[i] == 0);
    botw_pcm_counters a = counters(s);
    CHECK_EQ(a.consumed_frames - b.consumed_frames, 2);   // Consumed even though rendered as zeros.
    CHECK_EQ(a.underrun_frames - b.underrun_frames, 2);   // Only the 2 missing frames count.
    b = a;
    CHECK_EQ(botw_pcm_write(s, nz, 2, 2), 2);             // Fully served buffer at zero gain.
    f = render(s, out, 2);
    CHECK(!(f & kAudioUnitRenderAction_OutputIsSilence));
    a = counters(s);
    CHECK_EQ(a.consumed_frames - b.consumed_frames, 2);
    CHECK_EQ(a.underrun_frames, b.underrun_frames);       // Zero gain is never an underrun.
    for (int i = 0; i < 4; i++) CHECK(out[i] == 0);

    // Negative finite clamps to 0 (accepted, not rejected).
    CHECK_EQ(botw_pcm_set_volume(s, -5.0f), 0);
    b = counters(s);
    CHECK_EQ(botw_pcm_write(s, nz, 1, 2), 1);
    render(s, out, 1);
    a = counters(s);
    CHECK_EQ(a.consumed_frames - b.consumed_frames, 1);
    CHECK_EQ(a.underrun_frames, b.underrun_frames);
    CHECK(out[0] == 0 && out[1] == 0);

    // The gain applies after channel conversion: mono is dual-mono'd by the producer, then one
    // gain pass in the callback scales both converted channels.
    CHECK_EQ(botw_pcm_set_volume(s, 2.0f), 0);
    const int16_t mono[2] = {100, -50};
    CHECK_EQ(botw_pcm_write(s, mono, 2, 1), 2);
    render(s, out, 2);
    const int16_t mono_exp[4] = {200, 200, -100, -100};
    CHECK(memcmp(out, mono_exp, sizeof mono_exp) == 0);
    // 5.1 centre -> stored (500, 500) by the producer downmix -> 2.0 gain -> (1000, 1000).
    const int16_t six[6] = {0, 0, 1000, 0, 0, 0};
    CHECK_EQ(botw_pcm_write(s, six, 1, 6), 1);
    render(s, out, 1);
    CHECK(out[0] == 1000 && out[1] == 1000);

    // Stop/restart retains the selected gain (still 2.0): 12345*2=24690, 32767*2 -> 32767.
    CHECK_EQ(botw_pcm_set_active(s, 0), 0);
    CHECK_EQ(botw_pcm_set_active(s, 1), 0);
    CHECK_EQ(botw_pcm_write(s, nz, 2, 2), 2);
    render(s, out, 2);
    CHECK(out[0] == 24690 && out[1] == -24690 && out[2] == 32767 && out[3] == -32768);
    botw_pcm_close(s);
}

static void test_volume_muted(void) {
    au_reset();
    botw_pcm_sink *s = botw_pcm_open(0);
    CHECK(s != NULL);
    // Gain is stored and validated on muted sinks too, with no AudioToolbox involvement.
    CHECK_EQ(botw_pcm_set_volume(s, 2.0f), 0);
    CHECK_EQ(botw_pcm_set_volume(s, NAN), (uint64_t)-1);
    CHECK_EQ(botw_pcm_set_volume(s, INFINITY), (uint64_t)-1);
    CHECK_EQ(botw_pcm_set_volume(s, -INFINITY), (uint64_t)-1);
    CHECK(g_au.seq_len == 0);
    int16_t buf[4] = {0};
    CHECK_EQ(botw_pcm_set_active(s, 1), 0);
    CHECK_EQ(botw_pcm_write(s, buf, 2, 2), 2);   // Consumed on the spot; input still never read.
    check_counters(s, 2, 2, 0, 0);
    CHECK_EQ(botw_pcm_set_active(s, 0), 0);
    CHECK_EQ(botw_pcm_set_active(s, 1), 0);      // Restart retains the stored gain, muted or not.
    CHECK_EQ(botw_pcm_write(s, buf, 2, 2), 2);
    check_counters(s, 4, 4, 0, 0);
    botw_pcm_close(s);
    CHECK(g_au.seq_len == 0);                    // Never a single AudioToolbox call.
}

// ---------------------------------------------------------------------------------------------
// Concurrent stress: one producer thread (writes, periodic stop/restart, finally close) against one
// render thread that keeps calling pcm_render with random sizes, including across stop/restart and
// right up to Dispose (which, like the real contract, returns only once the render thread is gone).
typedef struct {
    botw_pcm_sink *sink;
    _Atomic int stop;
    uint32_t rng;
    uint64_t nonzero_frames, zero_frames, gap_total, backwards, renders, silent_flags_wrong, order_errors;
    int64_t expect;
} consumer_ctx;
static uint32_t rnd(uint32_t *st) { *st = *st * 1664525u + 1013904223u; return *st >> 8; }

static void *consumer_main(void *p) {
    consumer_ctx *c = p;
    enum { MAXN = 2048 };
    int16_t *out = malloc(MAXN * 4);
    while (!atomic_load_explicit(&c->stop, memory_order_acquire)) {
        uint32_t n = rnd(&c->rng) % MAXN + 1;
        AudioUnitRenderActionFlags flags = 0; AudioTimeStamp ts = {0}; AudioBufferList bl = { .mNumberBuffers = 1 };
        bl.mBuffers[0].mNumberChannels = 2; bl.mBuffers[0].mDataByteSize = n * 4; bl.mBuffers[0].mData = out;
        memset(out, 0x5a, (size_t)n * 4);
        pcm_render(c->sink, &flags, &ts, 0, n, &bl);
        c->renders++;
        int seen_zero = 0; uint32_t got = 0;
        for (uint32_t i = 0; i < n; i++) {
            int64_t v = seq_decode(out[2 * i], out[2 * i + 1]);
            if (v < 0) { seen_zero = 1; c->zero_frames++; continue; }
            if (seen_zero) { c->order_errors++; continue; }          // Data after silence inside one buffer.
            c->nonzero_frames++; got++;
            if (v == c->expect) { c->expect++; continue; }
            if (v < c->expect) { c->backwards++; c->expect = v + 1; continue; }
            c->gap_total += (uint64_t)(v - c->expect); c->expect = v + 1;   // Gap: only a stop flush may cause it.
        }
        if ((got == 0) != ((flags & kAudioUnitRenderAction_OutputIsSilence) != 0)) c->silent_flags_wrong++;
        if (rnd(&c->rng) % 64 == 0) sched_yield();
    }
    free(out);
    return NULL;
}

static void test_stress(void) {
    const char *env = getenv("T_BOTW_PCM_STRESS_FRAMES");
    uint64_t target = env ? strtoull(env, NULL, 10) : 4000000;
    au_reset();
    botw_pcm_sink *s = botw_pcm_open(1);
    CHECK(s && !botw_pcm_set_active(s, 1));
    consumer_ctx c = { .sink = s, .rng = 12345 };
    pthread_t th; CHECK(pthread_create(&th, NULL, consumer_main, &c) == 0);
    g_au.consumer_to_join = &th; g_au.consumer_stop = &c.stop;

    enum { MAXW = 1500 };
    int16_t *in = malloc(MAXW * 4);
    uint32_t rng = 777, next = 0;
    uint64_t overflow_dropped = 0, flush_dropped = 0, restarts = 0, iter = 0;
    while (next < target) {
        uint32_t n = rnd(&rng) % MAXW + 1;
        fill_seq(in, next, n);
        size_t acc = botw_pcm_write(s, in, n, 2);
        CHECK(acc <= n);
        next += (uint32_t)acc; overflow_dropped += n - acc;
        iter++;
        if (acc < n) sched_yield();                     // Ring full: let the render thread drain.
        if (iter % 97 == 0) usleep(300);                // Starve the render thread on purpose: underruns.
        if (iter % 400 == 0) {                          // Pause / restart while the render thread keeps running.
            botw_pcm_counters b = counters(s);
            CHECK_EQ(botw_pcm_set_active(s, 0), 0);
            botw_pcm_counters a = counters(s);
            flush_dropped += a.dropped_frames - b.dropped_frames;
            CHECK_EQ(a.submitted_frames, a.consumed_frames + a.dropped_frames - overflow_dropped);   // Ring empty after a stop.
            if (rnd(&rng) % 8 == 0) sched_yield();
            CHECK_EQ(botw_pcm_set_active(s, 1), 0);
            restarts++;
        }
    }
    // Final stop (observable here, unlike the one inside close) so the last flush is accounted for.
    botw_pcm_counters b = counters(s);
    CHECK_EQ(botw_pcm_set_active(s, 0), 0);
    botw_pcm_counters a = counters(s);
    uint64_t final_flush = a.dropped_frames - b.dropped_frames;   // Never observable by the render thread.
    flush_dropped += final_flush;
    CHECK_EQ(a.submitted_frames, next);
    CHECK_EQ(a.dropped_frames, overflow_dropped + flush_dropped);
    CHECK_EQ(a.consumed_frames, next - flush_dropped);
    botw_pcm_close(s);                                   // Disposes (joining the render thread) and frees.
    CHECK(g_au.dispose_calls == 1 && atomic_load(&c.stop) == 1);
    // Every submitted frame was either consumed in order or flushed by a stop; silence is never
    // mistaken for data, data never follows silence inside a buffer, and the flag matches.
    CHECK_EQ(c.nonzero_frames, next - flush_dropped);
    CHECK_EQ(c.gap_total, flush_dropped - final_flush);
    CHECK_EQ(c.backwards, 0);
    CHECK_EQ(c.order_errors, 0);
    CHECK_EQ(c.silent_flags_wrong, 0);
    CHECK(c.zero_frames > 0 && c.renders > 0);
    free(in);
    fprintf(stderr, "stress: %" PRIu64 " frames submitted, %" PRIu64 " consumed, %" PRIu64 " overflow-dropped, %" PRIu64 " flush-dropped, %" PRIu64 " restarts, %" PRIu64 " renders, %" PRIu64 " silent frames, gap total %" PRIu64 "\n",
            (uint64_t)next, c.nonzero_frames, overflow_dropped, flush_dropped, restarts, c.renders, c.zero_frames, c.gap_total);
}

// ---------------------------------------------------------------------------------------------
// Concurrent gain stress: the sequence check above only works at unity gain, so this companion runs
// one API thread (the header's serialized producer role: writes, volume changes, stop/restart,
// close) against one render thread and verifies values by invariant instead. Every frame written
// is the constant tone (1000, -1000) and each pcm_render invocation applies exactly one gain
// snapshot, so every data frame in one rendered buffer must be exactly one of (500,-500),
// (1000,-1000), (2000,-2000) — one single pair per buffer — while underruns and stop flushes stay
// all-zero. T_BOTW_PCM_GAIN_STRESS_FRAMES=<n> scales this test.
typedef struct {
    botw_pcm_sink *sink;
    _Atomic int stop;
    uint32_t rng;
    uint64_t renders, data_frames, silent_frames, flag_wrong, bad_value, mixed_gain, order_errors;
} gain_ctx;

static void *gain_consumer_main(void *p) {
    gain_ctx *c = p;
    enum { MAXN = 2048 };
    static const int16_t pair[3][2] = {{500, -500}, {1000, -1000}, {2000, -2000}};   // tone * {0.5, 1, 2}.
    int16_t *out = malloc(MAXN * 4);
    while (!atomic_load_explicit(&c->stop, memory_order_acquire)) {
        uint32_t n = rnd(&c->rng) % MAXN + 1;
        AudioUnitRenderActionFlags flags = 0; AudioTimeStamp ts = {0}; AudioBufferList bl = { .mNumberBuffers = 1 };
        bl.mBuffers[0].mNumberChannels = 2; bl.mBuffers[0].mDataByteSize = n * 4; bl.mBuffers[0].mData = out;
        memset(out, 0x5a, (size_t)n * 4);
        pcm_render(c->sink, &flags, &ts, 0, n, &bl);
        c->renders++;
        int seen_silence = 0, which = -1;
        for (uint32_t i = 0; i < n; i++) {
            int16_t l = out[2 * i], r = out[2 * i + 1];
            if (l == 0 && r == 0) { seen_silence = 1; c->silent_frames++; continue; }
            if (seen_silence) { c->order_errors++; continue; }       // Data after silence inside one buffer.
            int k = 0;
            while (k < 3 && !(l == pair[k][0] && r == pair[k][1])) k++;
            if (k == 3) { c->bad_value++; continue; }                 // Not the tone under any used gain.
            if (which == -1) which = k;
            else if (which != k) c->mixed_gain++;                     // One gain snapshot per buffer.
            c->data_frames++;
        }
        if ((which != -1) == ((flags & kAudioUnitRenderAction_OutputIsSilence) != 0)) c->flag_wrong++;
        if (rnd(&c->rng) % 64 == 0) sched_yield();
    }
    free(out);
    return NULL;
}

static void test_gain_stress(void) {
    const char *env = getenv("T_BOTW_PCM_GAIN_STRESS_FRAMES");
    uint64_t target = env ? strtoull(env, NULL, 10) : 2000000;
    static const float gains[3] = {0.5f, 1.0f, 2.0f};
    au_reset();
    botw_pcm_sink *s = botw_pcm_open(1);
    CHECK(s && !botw_pcm_set_active(s, 1));
    gain_ctx c = { .sink = s, .rng = 90210 };
    pthread_t th; CHECK(pthread_create(&th, NULL, gain_consumer_main, &c) == 0);
    g_au.consumer_to_join = &th; g_au.consumer_stop = &c.stop;

    enum { MAXW = 1500 };
    int16_t *in = malloc(MAXW * 4);
    for (uint32_t i = 0; i < MAXW; i++) { in[2 * i] = 1000; in[2 * i + 1] = -1000; }
    uint32_t rng = 777;
    uint64_t submitted = 0, overflow_dropped = 0, flush_dropped = 0, volume_changes = 0, restarts = 0, iter = 0;
    while (submitted < target) {
        uint32_t n = rnd(&rng) % MAXW + 1;
        size_t acc = botw_pcm_write(s, in, n, 2);
        CHECK(acc <= n);
        submitted += acc; overflow_dropped += n - acc;
        iter++;
        if (acc < n) sched_yield();                     // Ring full: let the render thread drain.
        if (rnd(&rng) % 37 == 0) { CHECK_EQ(botw_pcm_set_volume(s, gains[rnd(&rng) % 3]), 0); volume_changes++; }
        if (iter % 97 == 0) usleep(300);                // Starve the render thread on purpose: underruns.
        if (iter % 400 == 0) {                          // Stop/restart under concurrency; gain is retained.
            botw_pcm_counters b = counters(s);
            CHECK_EQ(botw_pcm_set_active(s, 0), 0);
            botw_pcm_counters a = counters(s);
            flush_dropped += a.dropped_frames - b.dropped_frames;
            CHECK_EQ(a.submitted_frames, a.consumed_frames + a.dropped_frames - overflow_dropped);   // Ring empty after a stop.
            CHECK_EQ(botw_pcm_set_active(s, 1), 0);
            restarts++;
        }
    }
    botw_pcm_counters b = counters(s);
    CHECK_EQ(botw_pcm_set_active(s, 0), 0);
    botw_pcm_counters a = counters(s);
    uint64_t final_flush = a.dropped_frames - b.dropped_frames;   // Never observable by the render thread.
    flush_dropped += final_flush;
    CHECK_EQ(a.submitted_frames, submitted);
    CHECK_EQ(a.dropped_frames, overflow_dropped + flush_dropped);
    CHECK_EQ(a.consumed_frames, submitted - flush_dropped);
    botw_pcm_close(s);                                  // Disposes (joining the render thread) and frees.
    CHECK(g_au.dispose_calls == 1 && atomic_load(&c.stop) == 1);
    // Every consumed frame was the tone scaled by exactly one gain per buffer; silence was only
    // real silence (underrun or flush), never a wrong value; the flag always matched the data.
    CHECK_EQ(c.data_frames, submitted - flush_dropped);
    CHECK_EQ(c.bad_value, 0);
    CHECK_EQ(c.mixed_gain, 0);
    CHECK_EQ(c.order_errors, 0);
    CHECK_EQ(c.flag_wrong, 0);
    CHECK(c.renders > 0 && c.silent_frames > 0 && volume_changes > 0 && restarts > 0);
    free(in);
    fprintf(stderr, "gain stress: %" PRIu64 " frames submitted, %" PRIu64 " consumed, %" PRIu64 " overflow-dropped, %" PRIu64 " flush-dropped, %" PRIu64 " volume changes, %" PRIu64 " restarts, %" PRIu64 " renders, %" PRIu64 " silent frames\n",
            submitted, c.data_frames, overflow_dropped, flush_dropped, volume_changes, restarts, c.renders, c.silent_frames);
}

int main(void) {
    test_lifecycle_and_failures();
    test_ring_order_partial_wrap_underrun();
    test_channel_mapping();
    test_muted();
    test_volume();
    test_volume_muted();
    test_stress();
    test_gain_stress();
    printf("t_botw_pcm %s\n", g_fail ? "FAILED" : "ok");
    return g_fail;
}
