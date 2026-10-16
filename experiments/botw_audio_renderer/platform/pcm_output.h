// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "platform.h"
#include <mutex>
struct botw_pcm_sink;

namespace botw::audio {
struct PcmCounters {
    std::uint64_t submitted_frames, consumed_frames, dropped_frames, underrun_frames;
};
struct PcmWriteResult { Status status; std::size_t accepted_frames; };

// Non-realtime producer adapter for the existing audible 48kHz stereo PCM sink.
// The platform lifecycle gate remains owned by whole_audio_platform. No callback
// calls this adapter; lock order is adapter -> platform -> original PCM control.
class PcmOutput {
public:
    PcmOutput() = default;
    ~PcmOutput();
    PcmOutput(const PcmOutput&) = delete;
    PcmOutput& operator=(const PcmOutput&) = delete;
    Status open();
    Status set_active(bool active);
    PcmWriteResult write(std::span<const std::int16_t> samples, unsigned channels);
    Status set_volume(float volume);
    Status counters(PcmCounters& out) const;
    // botw_pcm_close is void. This retires this producer, but makes no claim that
    // AudioComponentInstanceDispose succeeded. Original retained callback storage
    // and quiescence behavior are unchanged and are never freed here.
    void close();
private:
    mutable std::mutex mutex_;
    botw_pcm_sink* sink_ = nullptr;
};
} // namespace botw::audio
