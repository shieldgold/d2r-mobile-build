// SPDX-License-Identifier: GPL-3.0-or-later
// Owned CPU PCM only. No renderer commands, guest, device, or GPU are executed.
#include "audio_core/sink/sink.h"
#include "renderer_host.h"
#include "pcm_output.h"
#ifndef OWNED_EAGER_BASELINE
#include "native_sink_stream.h"
#endif
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <future>
#include <limits>
#include <string_view>
#include <thread>
#include <vector>
extern "C" {
#include "botw_pcm.h"
#include "whole_audio_platform.h"
void owned_pcm_fail(int, int, int);
unsigned owned_pcm_unit_count(void);
int owned_pcm_started(void);
int owned_pcm_render(std::int16_t*, unsigned);
}
using namespace botw::audio;
using namespace AudioCore::Sink;
using namespace std::chrono_literals;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned native sink failed %d: %s\n", __LINE__, #x); return 78; } } while (0)
static RendererHost* owned_master_host;
static unsigned owned_master_reads;
// The real Host getter's private test copy invokes this AFTER its real atomic
// read. Publish a legitimate concurrent setting before any second getter call.
extern "C" void owned_master_read_after(float value) {
    if (!owned_master_host) return;
    ++owned_master_reads;
    if (owned_master_reads == 1 && value == 0.0f)
        require(owned_master_host->set_master_volume(128.0f));
}

#ifdef OWNED_EAGER_BASELINE
// Reproduce the tempting but incorrect push bridge with the complete upstream
// Append/Process algorithms. Its real PCM write succeeds before any AU callback.
class OwnedEagerBridge final : public SinkStream {
public:
    OwnedEagerBridge(RendererHost& host) : SinkStream(host, StreamType::Render) { require(pcm.open()); }
    void Start(bool = false) override { require(pcm.set_active(true)); paused = false; }
    void Stop() override { require(pcm.set_active(false)); paused = true; }
    void Finalize() override { Stop(); pcm.close(); }
    void push_to_pcm(unsigned frames) {
        std::vector<s16> samples(frames * 2);
        ProcessAudioOutAndRender(samples, frames);
        require(pcm.write(samples, 2).status);
    }
    PcmOutput pcm;
};
int main(int, char**) {
    RendererHost host(true);
    OwnedEagerBridge stream(host);
    std::array<s16, 4> samples{100, -100, 200, -200};
    SinkBuffer buffer{2, 0, 19, false};
    stream.AppendBuffer(buffer, samples);
    stream.Start();
    stream.push_to_pcm(2);
    PcmCounters counters{}; require(stream.pcm.counters(counters));
    CHECK(counters.submitted_frames == 2 && counters.consumed_frames == 0);
    if (stream.GetQueueSize() != 1) {
        std::fprintf(stderr, "queue retired before real callback: submitted=%llu consumed=%llu queued=%u\n",
                     counters.submitted_frames, counters.consumed_frames, stream.GetQueueSize());
        return 78;
    }
    stream.Finalize();
    return 0;
}
#else
// Owned allocator scheduling seam: force a real lifecycle flush/resume exactly
// when Append copies a 2-frame stereo stack buffer, after its earlier counters
// snapshot but before its real PCM write. No production callback is modified.
static std::atomic<bool> flush_on_copy{false};
static std::atomic<int> flush_error{0};
void* operator new(std::size_t size) {
    void* pointer = std::malloc(size ? size : 1);
    if (!pointer) throw std::bad_alloc();
    if (size == 4 * sizeof(s16) && flush_on_copy.exchange(false)) {
        char error[128]{};
        if (whole_audio_platform_set_active(0, error, sizeof error) ||
            whole_audio_platform_set_active(1, error, sizeof error)) flush_error = 1;
    }
    return pointer;
}
void operator delete(void* pointer) noexcept { std::free(pointer); }

template<class F> bool fails(F&& fn, Status expected) {
    try { fn(); } catch (const PlatformError& error) { return error.status == expected; }
    return false;
}
struct OwnedSession {
    std::unique_ptr<Sink> sink = make_pcm_sink();
    RendererHost host{*sink, true};
    SinkStream* stream = nullptr;
    explicit OwnedSession(unsigned channels = 2) {
        stream = sink->AcquireSinkStream(host, channels, "owned", StreamType::Render);
        stream->SetRingSize(1);
    }
    ~OwnedSession() { owned_pcm_fail(0, 0, 0); try { sink->CloseStreams(); } catch (...) {} }
    void append(std::span<s16> samples, std::uint64_t tag = 7) {
        SinkBuffer buffer{samples.size() / stream->GetSystemChannels(), 0, tag, false};
        stream->AppendBuffer(buffer, samples);
    }
};
int main(int argc, char** argv) {
    CHECK(argc == 2); const std::string_view name{argv[1]};
    if (name == "consumption") {
        CHECK(owned_pcm_unit_count() == 0);
        auto sink = make_pcm_sink(); CHECK(owned_pcm_unit_count() == 0);
        RendererHost host(*sink, true);
        auto* stream = sink->AcquireSinkStream(host, 2, "owned", StreamType::Render);
        std::array<s16, 4> samples{100, -100, 200, -200}, out{};
        SinkBuffer first{2, 0, 19, false}; stream->AppendBuffer(first, samples);
        samples.fill(999); // DeviceSinkCommand's original stack span may now disappear.
        auto progress = pcm_sink_progress(*stream);
        CHECK(stream->IsPaused() && progress.queued_buffers == 1 && progress.submitted_frames == 0);
        stream->Start(); progress = pcm_sink_progress(*stream);
        CHECK(!stream->IsPaused() && owned_pcm_started());
        CHECK(progress.submitted_frames == 2 && progress.consumed_frames == 0);
        CHECK(stream->GetQueueSize() == 1 && stream->GetExpectedPlayedSampleCount() == 0);
        CHECK(!owned_pcm_render(out.data(), 1) && out[0] == 100 && out[1] == -100);
        progress = pcm_sink_progress(*stream);
        CHECK(progress.consumed_frames == 1 && progress.front_frames_played == 1 && progress.completed_buffers == 0);
        CHECK(progress.front_tag == 19 && progress.front_sequence == 1 && stream->GetQueueSize() == 1);
        CHECK(!owned_pcm_render(out.data(), 2) && out[0] == 200 && out[1] == -200 && out[2] == 0 && out[3] == 0);
        progress = pcm_sink_progress(*stream);
        CHECK(progress.consumed_frames == 2 && progress.completed_buffers == 1 && progress.last_completed_tag == 19);
        CHECK(progress.last_completed_sequence == 1 && progress.queued_buffers == 0);
        CHECK(stream->GetExpectedPlayedSampleCount() == 2);
        sink->CloseStreams();
    } else if (name == "fifo") {
        OwnedSession owned; std::array<s16, 4> a{11, -11, 12, -12}, b{21, -21, 22, -22};
        std::array<s16, 6> out{};
        owned.append(a, 33); owned.append(b, 33); // Normal renderer stack tags repeat.
        owned.stream->Start(); CHECK(!owned_pcm_render(out.data(), 3));
        auto p = pcm_sink_progress(*owned.stream);
        CHECK(p.completed_buffers == 1 && p.last_completed_sequence == 1 && p.last_completed_tag == 33);
        CHECK(p.front_sequence == 2 && p.front_tag == 33 && p.front_frames_played == 1 && p.queued_buffers == 1);
        CHECK(!owned_pcm_render(out.data(), 1)); p = pcm_sink_progress(*owned.stream);
        CHECK(p.completed_buffers == 2 && p.last_completed_sequence == 2 && p.queued_buffers == 0);
    } else if (name == "wait") {
        OwnedSession owned; std::array<s16, 4> a{1, 2, 3, 4}, out{};
        owned.append(a); owned.stream->Start();
        std::promise<void> entered; auto ready = entered.get_future();
        auto waiter = std::async(std::launch::async, [&] { entered.set_value(); owned.stream->WaitFreeSpace({}); });
        ready.wait(); CHECK(waiter.wait_for(25ms) == std::future_status::timeout);
        CHECK(!owned_pcm_render(out.data(), 1));
        CHECK(waiter.wait_for(15ms) == std::future_status::timeout); // Partial consumption is not a freed buffer.
        CHECK(!owned_pcm_render(out.data(), 1));
        CHECK(waiter.wait_for(1s) == std::future_status::ready); waiter.get();
        owned.append(a); std::stop_source stop;
        auto cancelled = std::async(std::launch::async, [&] { owned.stream->WaitFreeSpace(stop.get_token()); });
        CHECK(cancelled.wait_for(20ms) == std::future_status::timeout);
        stop.request_stop(); CHECK(cancelled.wait_for(1s) == std::future_status::ready); cancelled.get();
        CHECK(pcm_sink_progress(*owned.stream).consumed_frames == 2 && owned.stream->GetQueueSize() == 1);
        auto failed = std::async(std::launch::async, [&] { return fails([&] { owned.stream->WaitFreeSpace({}); }, Status::DeviceError); });
        CHECK(failed.wait_for(20ms) == std::future_status::timeout);
        owned.host.fail(Status::DeviceError);
        CHECK(failed.wait_for(1s) == std::future_status::ready && failed.get());
    } else if (name == "clear") {
        OwnedSession owned; std::array<s16, 4> a{9, 10, 11, 12}, out{};
        owned.append(a, 4); owned.stream->Start(); CHECK(!owned_pcm_render(out.data(), 1));
        owned.stream->ClearQueue(); auto p = pcm_sink_progress(*owned.stream);
        CHECK(p.consumed_frames == 1 && p.cancelled_frames == 1 && p.cancelled_buffers == 1 && p.completed_buffers == 0);
        CHECK(p.queued_buffers == 0 && !owned.stream->IsPaused());
        CHECK((!owned_pcm_render(out.data(), 2) && out == std::array<s16, 4>{}));
        owned.append(a, 4); CHECK(!owned_pcm_render(out.data(), 2));
        p = pcm_sink_progress(*owned.stream); CHECK(p.consumed_frames == 3 && p.completed_buffers == 1 && p.last_completed_sequence == 2);
        owned.append(a); owned.stream->Stop(); p = pcm_sink_progress(*owned.stream);
        CHECK(owned.stream->IsPaused() && p.queued_buffers == 0 && p.consumed_frames == 3 && p.cancelled_frames == 3);
        owned.append(a); owned.stream->ClearQueue(); p = pcm_sink_progress(*owned.stream);
        CHECK(p.queued_buffers == 0 && p.cancelled_frames == 5 && p.submitted_frames == 6);
    } else if (name == "lifecycle") {
        OwnedSession owned; std::array<s16, 4> a{9, 10, 11, 12}, out{}; char error[128]{};
        owned.append(a); owned.stream->Start();
        CHECK(!whole_audio_platform_set_active(0, error, sizeof error));
        auto p = pcm_sink_progress(*owned.stream);
        CHECK(p.queued_buffers == 0 && p.consumed_frames == 0 && p.cancelled_frames == 2 && p.completed_buffers == 0);
        CHECK(!whole_audio_platform_set_active(1, error, sizeof error));
        owned.append(a); CHECK(!owned_pcm_render(out.data(), 2) && out == a);
        CHECK(owned.stream->GetExpectedPlayedSampleCount() == 2);
    } else if (name == "lifecycle-interleave") {
        OwnedSession owned; std::array<s16, 4> a{1, 2, 3, 4}, b{11, 12, 13, 14}, out{};
        owned.append(a, 71); owned.stream->Start();
        flush_on_copy = true;
        owned.append(b, 72);
        CHECK(!flush_on_copy && !flush_error);
        CHECK(!owned_pcm_render(out.data(), 2) && out == b);
        const auto p = pcm_sink_progress(*owned.stream);
        CHECK(p.consumed_frames == 2 && p.cancelled_frames == 2 && p.completed_buffers == 1 && p.cancelled_buffers == 1);
        if (p.last_completed_sequence != 2 || p.last_completed_tag != 72) {
            std::fprintf(stderr, "flush/write interleave misattributed real playback: completed_sequence=%llu completed_tag=%llu\n",
                         p.last_completed_sequence, p.last_completed_tag);
            return 78;
        }
    } else if (name == "observed-refusal") {
        // Real bounded PCM, no renderer queue. Distinguish a refused suffix from
        // the later lifecycle flush; neither can become callback consumption.
        auto* raw = botw_pcm_open(1); CHECK(raw && !botw_pcm_set_active(raw, 1));
        std::vector<s16> full(32767 * 2, 1); botw_pcm_counters before{}, after{};
        CHECK(botw_pcm_write_observed(raw, full.data(), 32767, 2, &before) == 32767);
        CHECK(before.submitted_frames == 0 && before.consumed_frames == 0 && before.dropped_frames == 0);
        std::array<s16, 4> tail{2, 3, 4, 5};
        CHECK(botw_pcm_write_observed(raw, tail.data(), 2, 2, &before) == 1);
        CHECK(before.submitted_frames == 32767 && before.consumed_frames == 0 && before.dropped_frames == 0);
        botw_pcm_get_counters(raw, &after);
        CHECK(after.submitted_frames == 32768 && after.consumed_frames == 0 && after.dropped_frames == 1);
        CHECK(botw_pcm_write_observed(raw, tail.data(), 2, 2, nullptr) == 0);
        botw_pcm_get_counters(raw, &before); CHECK(before.dropped_frames == 1);
        CHECK(!botw_pcm_set_active(raw, 0)); botw_pcm_get_counters(raw, &after);
        CHECK(after.submitted_frames == 32768 && after.consumed_frames == 0 && after.dropped_frames == 32769);
        botw_pcm_close(raw);
    } else if (name == "conversion") {
        OwnedSession owned(6); std::array<s16, 12> samples{100, 200, 300, 400, 500, 600, -100, -200, -300, -400, -500, -600};
        std::array<s16, 4> out{}; owned.sink->SetSystemVolume(0.5f); owned.sink->SetDeviceVolume(0.5f);
        require(owned.host.set_master_volume(0.5f)); owned.append(samples); owned.stream->Start();
        CHECK(!owned_pcm_render(out.data(), 2));
        // Full original Eden coefficient order and original float->s32->s16 truncation.
        const s16 left = s16(s32((100.f + 300.f * .596f + 400.f * .354f + 500.f * .707f) * .125f));
        const s16 right = s16(s32((200.f + 300.f * .596f + 400.f * .354f + 600.f * .707f) * .125f));
        CHECK((out == std::array<s16, 4>{left, right, s16(-left), s16(-right)}));
        owned.stream->SetSystemChannels(1); std::array<s16, 2> mono{80, -80}; owned.append(mono);
        CHECK(!owned_pcm_render(out.data(), 2)); CHECK((out == std::array<s16, 4>{10, 0, -10, 0}));
    } else if (name == "validation") {
        auto sink = make_pcm_sink(); RendererHost host(*sink, true);
        CHECK(fails([&] { sink->AcquireSinkStream(host, 2, "in", StreamType::In); }, Status::Unsupported));
        CHECK(host.failure() == Status::Unsupported && owned_pcm_unit_count() == 0);
        RendererHost other(*sink, true);
        CHECK(fails([&] { sink->AcquireSinkStream(other, 3, "bad", StreamType::Render); }, Status::InvalidArgument));
        CHECK(other.failure() == Status::InvalidArgument && owned_pcm_unit_count() == 0);
    } else if (name == "invalid-buffer") {
        OwnedSession owned; std::array<s16, 3> bad{1, 2, 3}; SinkBuffer b{1, 0, 1, false};
        CHECK(fails([&] { owned.stream->AppendBuffer(b, bad); }, Status::InvalidArgument));
        CHECK(owned.host.failure() == Status::InvalidArgument && !owned_pcm_started());
    } else if (name == "master-snapshot") {
        OwnedSession owned; std::array<s16, 2> samples{32767, -32768}, output{1, 1};
        owned.sink->SetSystemVolume(128.f); owned.sink->SetDeviceVolume(128.f);
        require(owned.host.set_master_volume(0.f));
        owned_master_host = &owned.host; owned_master_reads = 0;
        owned.append(samples);
        owned_master_host = nullptr;
        CHECK(owned_master_reads == 1); // Exact same finite setting was checked and mixed.
        CHECK(owned.host.master_volume() == 128.f && owned.host.failure() == Status::Ok);
        owned.stream->Start(); CHECK(!owned_pcm_render(output.data(), 1));
        CHECK((output == std::array<s16, 2>{0, 0}));
        CHECK(pcm_sink_progress(*owned.stream).consumed_frames == 1);
    } else if (name == "invalid-volume") {
        OwnedSession owned;
        CHECK(fails([&] { owned.stream->SetSystemVolume(std::numeric_limits<float>::quiet_NaN()); }, Status::InvalidArgument));
        CHECK(owned.host.failure() == Status::InvalidArgument);
    } else if (name == "open-failure") {
        auto sink = make_pcm_sink(); RendererHost host(*sink, true); owned_pcm_fail(1, 0, 0);
        CHECK(fails([&] { sink->AcquireSinkStream(host, 2, "owned", StreamType::Render); }, Status::DeviceError));
        CHECK(host.failure() == Status::DeviceError && owned_pcm_unit_count() == 0);
    } else if (name == "start-failure") {
        OwnedSession owned; std::array<s16, 2> a{1, 2}; owned.append(a); owned_pcm_fail(0, 1, 0);
        CHECK(fails([&] { owned.stream->Start(); }, Status::DeviceError));
        CHECK(owned.host.failure() == Status::DeviceError && owned.stream->IsPaused() && !owned_pcm_started());
    } else if (name == "write-failure") {
        OwnedSession owned; std::array<s16, 2> a{1, 2}; char error[128]{};
        CHECK(!whole_audio_platform_set_active(0, error, sizeof error)); owned.append(a);
        CHECK(fails([&] { owned.stream->Start(); }, Status::Backpressure));
        CHECK(owned.host.failure() == Status::Backpressure && !owned_pcm_started());
    } else if (name == "stop-failure") {
        OwnedSession owned; std::array<s16, 2> a{1, 2}; owned.append(a); owned.stream->Start();
        owned_pcm_fail(0, 0, 1);
        CHECK(fails([&] { owned.stream->Stop(); }, Status::DeviceError));
        CHECK(owned.host.failure() == Status::DeviceError && owned.stream->IsPaused());
    } else if (name == "capacity") {
        OwnedSession owned; std::vector<s16> samples(32768 * 2, 1); owned.append(samples);
        std::array<s16, 2> extra{1, 2}; CHECK(fails([&] { owned.append(extra); }, Status::Backpressure));
        CHECK(owned.host.failure() == Status::Backpressure && !owned_pcm_started());
    } else return 64;
    return 0;
}
#endif
