// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstdint>
#include <memory>

namespace AudioCore::Sink { class Sink; class SinkStream; }
namespace botw::audio {
// Factory construction is device-free. AcquireSinkStream opens mandatory audible
// output. The supplied RendererHost must outlive every acquired stream.
std::unique_ptr<AudioCore::Sink::Sink> make_pcm_sink();

// Host-owned FIFO metadata. A tag is opaque and may repeat; sequence identifies
// each Append. Completion means actual PCM callback consumption, never write or
// elapsed time. Cancellation includes explicit and platform lifecycle flushes.
// No unbounded history of tags is kept. A zero front_sequence means empty.
struct NativeSinkProgress {
    std::uint64_t submitted_frames{}, consumed_frames{}, cancelled_frames{};
    std::uint64_t completed_buffers{}, cancelled_buffers{};
    std::uint64_t last_completed_sequence{}, last_completed_tag{};
    std::uint64_t front_sequence{}, front_tag{}, front_frames_played{};
    std::uint32_t queued_buffers{};
};
NativeSinkProgress pcm_sink_progress(AudioCore::Sink::SinkStream& stream);
} // namespace botw::audio
