// SPDX-License-Identifier: GPL-3.0-or-later
#include "pcm_output.h"
#include <cmath>
extern "C" {
#include "botw_pcm.h"
}

namespace botw::audio {
PcmOutput::~PcmOutput() { close(); }
Status PcmOutput::open() {
    std::lock_guard lock(mutex_);
    if (sink_) return Status::Busy;
    // Audible output is mandatory. No fallback to botw_pcm_open(0).
    sink_ = botw_pcm_open(1);
    return sink_ ? Status::Ok : Status::DeviceError;
}
Status PcmOutput::set_active(bool active) {
    std::lock_guard lock(mutex_);
    if (!sink_) return Status::Closed;
    return botw_pcm_set_active(sink_, active) == 0 ? Status::Ok : Status::DeviceError;
}
PcmWriteResult PcmOutput::write(std::span<const std::int16_t> samples, unsigned channels) {
    std::lock_guard lock(mutex_);
    if (!sink_) return {Status::Closed, 0};
    if ((channels != 1 && channels != 2 && channels != 6) || samples.size() % channels)
        return {Status::InvalidArgument, 0};
    const auto frames = samples.size() / channels;
    if (!frames) return {Status::Ok, 0};
    const auto accepted = botw_pcm_write(sink_, samples.data(), frames, channels);
    return {accepted == frames ? Status::Ok : Status::Backpressure, accepted};
}
Status PcmOutput::set_volume(float volume) {
    std::lock_guard lock(mutex_);
    if (!sink_) return Status::Closed;
    if (!std::isfinite(volume)) return Status::InvalidArgument;
    return botw_pcm_set_volume(sink_, volume) == 0 ? Status::Ok : Status::InvalidArgument;
}
Status PcmOutput::counters(PcmCounters& out) const {
    std::lock_guard lock(mutex_);
    if (!sink_) return Status::Closed;
    botw_pcm_counters value{};
    botw_pcm_get_counters(sink_, &value);
    out = {value.submitted_frames, value.consumed_frames, value.dropped_frames, value.underrun_frames};
    return Status::Ok;
}
void PcmOutput::close() {
    std::lock_guard lock(mutex_);
    if (!sink_) return;
    botw_pcm_close(sink_);
    sink_ = nullptr;
}
} // namespace botw::audio
