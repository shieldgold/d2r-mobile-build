// SPDX-License-Identifier: GPL-3.0-or-later
#include "native_sink_stream.h"
#include "audio_core/sink/sink.h"
#include "renderer_host.h"
#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <deque>
#include <limits>
#include <utility>
extern "C" {
#include "botw_pcm.h"
}

namespace botw::audio {
namespace {
using AudioCore::Sink::SinkBuffer;
using AudioCore::Sink::SinkStream;
using AudioCore::Sink::StreamType;
constexpr std::uint64_t CapacityFrames = 32768; // Real original PCM ring capacity.
bool valid_channels(u32 channels) {
    return channels == 1 || channels == 2 || channels == 4 || channels == 6;
}
bool valid_volume(float volume) {
    return std::isfinite(volume) && volume >= 0.0f && volume <= 128.0f;
}

// All methods below are normal producer/control calls. The original PCM callback
// touches only its lock-free ring/counters and never calls or locks this object.
class NativeSinkStream final : public SinkStream {
public:
    NativeSinkStream(RendererHost& host, u32 channels, std::string stream_name, StreamType stream_type)
        : SinkStream(host, stream_type), host_(host) {
        check_live();
        if (stream_type != StreamType::Render && stream_type != StreamType::Out) raise(Status::Unsupported);
        if (!valid_channels(channels)) raise(Status::InvalidArgument);
        system_channels = channels;
        device_channels = 2;
        name = std::move(stream_name);
        pcm_ = botw_pcm_open(1); // Mandatory real audible sink; no muted fallback.
        if (!pcm_) raise(Status::DeviceError);
    }
    ~NativeSinkStream() override {
        try { Finalize(); } catch (...) { /* Finalize already latches any real device failure. */ }
    }

    void AppendBuffer(SinkBuffer& buffer, std::span<s16> samples) override {
        std::lock_guard lock(mutex_);
        check_live(); refresh();
        if (!valid_channels(system_channels) || buffer.frames == 0 || buffer.frames > CapacityFrames ||
            buffer.frames_played || buffer.consumed || samples.size() != buffer.frames * system_channels)
            raise(Status::InvalidArgument);
        // The original converter casts float to s32 before saturation. Preserve
        // its arithmetic, but reject nonfinite/overflowing gain before that cast.
        const float master_snapshot = host_.master_volume();
        float master = master_snapshot;
        if (!valid_volume(GetSystemVolume()) || !valid_volume(GetDeviceVolume()) || !valid_volume(master))
            raise(Status::InvalidArgument);
        if (master > 1.0f) master = 0.6f + 20.0f * std::log10(master);
        const float gain = GetSystemVolume() * GetDeviceVolume() * master;
        if (!std::isfinite(gain) || gain > float(std::numeric_limits<s32>::max()) / (32768.0f * 3.0f))
            raise(Status::InvalidArgument);
        if (buffer.frames > CapacityFrames - buffered_frames_) raise(Status::Backpressure);
        try {
            append_master_volume_ = master_snapshot;
            // Complete original Eden volume, channel order, and clamp algorithm.
            // Only its final SubmitBuffer dispatch enters this PCM transport.
            SinkStream::AppendBuffer(buffer, samples);
        } catch (const std::bad_alloc&) { raise(Status::OutOfMemory); }
    }

    void Start(bool = false) override {
        std::lock_guard lock(mutex_);
        check_live(); refresh();
        const Status status = activate(true);
        if (status != Status::Ok) raise(status);
        paused = false;
        for (auto& entry : queue_) {
            if (!entry.submitted) write(entry);
        }
        refresh();
        wake_.notify_all();
    }

    void Stop() override {
        std::lock_guard lock(mutex_);
        if (finalized_) throw PlatformError(Status::Closed);
        const Status status = stop_and_cancel();
        if (status != Status::Ok) raise(status);
    }

    void ClearQueue() override {
        std::lock_guard lock(mutex_);
        check_live();
        const bool restart = !paused;
        const Status status = stop_and_cancel();
        if (status != Status::Ok) raise(status);
        if (restart) {
            const Status start = activate(true);
            if (start != Status::Ok) raise(start);
            paused = false;
        }
    }

    void Finalize() override {
        std::lock_guard lock(mutex_);
        if (finalized_) return;
        Status status = Status::Ok;
        try { status = stop_and_cancel(); }
        catch (const PlatformError& error) { status = error.status; }
        botw_pcm_close(pcm_);
        pcm_ = nullptr;
        finalized_ = true;
        wake_.notify_all();
        if (status != Status::Ok) raise(status);
    }

    void SetSystemChannels(u32 channels) override {
        std::lock_guard lock(mutex_); check_live();
        if (!valid_channels(channels)) raise(Status::InvalidArgument);
        system_channels = channels;
    }
    void SetSystemVolume(f32 volume) override {
        std::lock_guard lock(mutex_); check_live();
        if (!valid_volume(volume)) raise(Status::InvalidArgument);
        SinkStream::SetSystemVolume(volume);
    }
    void SetDeviceVolume(f32 volume) override {
        std::lock_guard lock(mutex_); check_live();
        if (!valid_volume(volume)) raise(Status::InvalidArgument);
        SinkStream::SetDeviceVolume(volume);
    }
    void SetRingSize(u32 size) override {
        std::lock_guard lock(mutex_); check_live();
        if (!size || size > CapacityFrames) raise(Status::InvalidArgument);
        ring_limit_ = size;
        wake_.notify_all();
    }
    u32 GetQueueSize() const override {
        return const_cast<NativeSinkStream*>(this)->progress().queued_buffers;
    }
    u64 GetExpectedPlayedSampleCount() override {
        return progress().consumed_frames;
    }
    void WaitFreeSpace(std::stop_token stop) override {
        // stop callbacks notify only; they never take the stream/PCM registry
        // locks. PCM counters have no producer notification, so poll while asleep.
        std::stop_callback cancelled(stop, [this] { wake_.notify_all(); });
        std::stop_callback failed(host_.failure_token(), [this] { wake_.notify_all(); });
        std::unique_lock lock(mutex_);
        for (;;) {
            if (stop.stop_requested()) return;
            check_live(); refresh();
            if (paused || queue_.size() < ring_limit_) return;
            wake_.wait_for(lock, std::chrono::milliseconds(1));
        }
    }
    NativeSinkProgress progress() {
        std::lock_guard lock(mutex_); check_live(); refresh();
        auto result = progress_;
        result.queued_buffers = static_cast<u32>(queue_.size());
        if (!queue_.empty()) {
            result.front_sequence = queue_.front().sequence;
            result.front_tag = queue_.front().buffer.tag;
            result.front_frames_played = queue_.front().buffer.frames_played;
        }
        return result;
    }

protected:
    f32 GetMasterVolume() const override {
        // Called only by the original converter under this Append's lock. A
        // concurrent Host setting applies to the next Append, never a re-read
        // between the checked gain and the original float-to-s32 conversion.
        return append_master_volume_;
    }
    void SubmitBuffer(const SinkBuffer& buffer, std::span<const s16> samples) override {
        // Append's lock is held. Copy the original converted stack span before
        // returning; DeviceSinkCommand calls Start only AFTER AppendBuffer.
        Entry entry{buffer, next_sequence_, false, 0, std::vector<s16>(samples.begin(), samples.end())};
        queue_.push_back(std::move(entry));
        ++next_sequence_;
        buffered_frames_ += buffer.frames;
        if (!paused) write(queue_.back());
    }

private:
    struct Entry {
        SinkBuffer buffer;
        std::uint64_t sequence;
        bool submitted;
        std::uint64_t cancelled;
        std::vector<s16> samples;
    };
    [[noreturn]] void raise(Status status) {
        host_.fail(status);
        wake_.notify_all();
        throw PlatformError(status);
    }
    void check_live() {
        if (finalized_) throw PlatformError(Status::Closed);
        require(host_.failure());
        if (host_.is_shutting_down()) throw PlatformError(Status::Closed);
    }
    void write(Entry& entry) {
        botw_pcm_counters before{};
        const auto accepted = botw_pcm_write_observed(pcm_, entry.samples.data(), entry.buffer.frames, 2, &before);
        // Before belongs strictly to the FIFO BEFORE this write. In particular,
        // any old lifecycle cancellation must precede this entry's consumption.
        refresh(before);
        if (accepted != entry.buffer.frames) {
            // A rejected/partial write is a real terminal failure, never an
            // accepted buffer or a muted fallback. Stop any accepted prefix.
            paused = true;
            (void)activate(false);
            raise(Status::Backpressure);
        }
        entry.submitted = true;
        entry.samples.clear();
    }
    void retire(std::uint64_t frames, bool cancelled) {
        while (frames) {
            if (queue_.empty() || !queue_.front().submitted) raise(Status::SystemError);
            auto& entry = queue_.front();
            const auto remaining = entry.buffer.frames - entry.buffer.frames_played - entry.cancelled;
            const auto count = std::min(frames, remaining);
            if (!count || count > buffered_frames_) raise(Status::SystemError);
            if (cancelled) {
                entry.cancelled += count;
                progress_.cancelled_frames += count;
            } else entry.buffer.frames_played += count;
            buffered_frames_ -= count;
            frames -= count;
            if (count == remaining) {
                if (entry.cancelled) ++progress_.cancelled_buffers;
                else {
                    ++progress_.completed_buffers;
                    progress_.last_completed_tag = entry.buffer.tag;
                    progress_.last_completed_sequence = entry.sequence;
                }
                queue_.pop_front();
            }
        }
    }
    void refresh() {
        if (!pcm_) raise(Status::Closed);
        botw_pcm_counters now{};
        botw_pcm_get_counters(pcm_, &now);
        refresh(now);
    }
    void refresh(const botw_pcm_counters& now) {
        // Refused writes are terminal. Successful observed writes reconcile the
        // previous FIFO BEFORE new publication, so deltas here cannot span an
        // unobserved flush followed by consumption of a later submitted entry.
        const auto consumed = now.consumed_frames - counters_.consumed_frames;
        const auto dropped = now.dropped_frames - counters_.dropped_frames;
        if (consumed > buffered_frames_ || dropped > buffered_frames_ - consumed) raise(Status::SystemError);
        retire(consumed, false);
        retire(dropped, true);
        counters_ = now;
        progress_.submitted_frames = now.submitted_frames;
        progress_.consumed_frames = now.consumed_frames;
        if (consumed || dropped) wake_.notify_all();
    }
    Status stop_and_cancel() {
        paused = true;
        const Status status = activate(false);
        refresh();
        // Successful or failed stop has original PCM's inactive/quiescent flush
        // semantics. Retire only remaining host-owned pending buffers as cancelled.
        while (!queue_.empty()) {
            const auto& entry = queue_.front();
            if (entry.submitted) raise(Status::SystemError);
            progress_.cancelled_frames += entry.buffer.frames;
            ++progress_.cancelled_buffers;
            buffered_frames_ -= entry.buffer.frames;
            queue_.pop_front();
        }
        wake_.notify_all();
        return status;
    }
    Status activate(bool active) {
        return botw_pcm_set_active(pcm_, active) == 0 ? Status::Ok : Status::DeviceError;
    }
    RendererHost& host_;
    botw_pcm_sink* pcm_ = nullptr;
    mutable std::mutex mutex_;
    std::condition_variable wake_;
    std::deque<Entry> queue_;
    botw_pcm_counters counters_{};
    NativeSinkProgress progress_{};
    std::uint64_t next_sequence_ = 1, buffered_frames_ = 0;
    u32 ring_limit_ = 4;
    f32 append_master_volume_ = 1.0f;
    bool finalized_ = false;
};

class NativeSink final : public AudioCore::Sink::Sink {
public:
    ~NativeSink() override { try { CloseStreams(); } catch (...) {} }
    SinkStream* AcquireSinkStream(RendererHost& host, u32 channels, const std::string& name,
                                  StreamType type) override {
        std::lock_guard lock(mutex_);
        try {
            auto stream = std::make_unique<NativeSinkStream>(host, channels, name, type);
            stream->SetDeviceVolume(device_volume);
            stream->SetSystemVolume(system_volume_);
            auto* pointer = stream.get();
            streams_.push_back(std::move(stream));
            return pointer;
        } catch (const std::bad_alloc&) { host.fail(Status::OutOfMemory); throw PlatformError(Status::OutOfMemory); }
    }
    void CloseStream(SinkStream* stream) override {
        std::unique_ptr<NativeSinkStream> retired;
        {
            std::lock_guard lock(mutex_);
            const auto found = std::find_if(streams_.begin(), streams_.end(),
                                           [stream](const auto& item) { return item.get() == stream; });
            if (found == streams_.end()) throw PlatformError(Status::InvalidArgument);
            retired = std::move(*found);
            streams_.erase(found);
        }
        retired->Finalize();
    }
    void CloseStreams() override {
        std::vector<std::unique_ptr<NativeSinkStream>> retired;
        { std::lock_guard lock(mutex_); retired.swap(streams_); }
        Status first = Status::Ok;
        for (auto& stream : retired) {
            try { stream->Finalize(); }
            catch (const PlatformError& error) { if (first == Status::Ok) first = error.status; }
        }
        require(first);
    }
    f32 GetDeviceVolume() const override { std::lock_guard lock(mutex_); return device_volume; }
    void SetDeviceVolume(f32 volume) override {
        std::lock_guard lock(mutex_);
        if (!valid_volume(volume)) throw PlatformError(Status::InvalidArgument);
        for (auto& stream : streams_) stream->SetDeviceVolume(volume);
        device_volume = volume;
    }
    void SetSystemVolume(f32 volume) override {
        std::lock_guard lock(mutex_);
        if (!valid_volume(volume)) throw PlatformError(Status::InvalidArgument);
        for (auto& stream : streams_) stream->SetSystemVolume(volume);
        system_volume_ = volume;
    }
private:
    mutable std::mutex mutex_;
    f32 system_volume_ = 1.0f;
    std::vector<std::unique_ptr<NativeSinkStream>> streams_;
};
} // namespace

std::unique_ptr<AudioCore::Sink::Sink> make_pcm_sink() { return std::make_unique<NativeSink>(); }
NativeSinkProgress pcm_sink_progress(AudioCore::Sink::SinkStream& stream) {
    auto* native = dynamic_cast<NativeSinkStream*>(&stream);
    if (!native) throw PlatformError(Status::InvalidArgument);
    return native->progress();
}
} // namespace botw::audio
