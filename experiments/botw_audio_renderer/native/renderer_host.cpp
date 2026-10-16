// SPDX-License-Identifier: GPL-3.0-or-later
#include "renderer_host.h"
#include <cmath>

namespace botw::audio {
RendererHost::RendererHost(bool initially_active, const PlatformOptions& options)
    : paused_(!initially_active) {
    require(validate_platform_options(options));
}
RendererHost::RendererHost(AudioCore::Sink::Sink& output, bool initially_active,
                           const PlatformOptions& options) : RendererHost(initially_active, options) {
    require(bind_output(output));
}
RendererHost::~RendererHost() { shutdown(); }
Status RendererHost::bind_failure_producer(FailureProducer producer) {
    if (!producer.failed) return Status::InvalidArgument;
    std::lock_guard lock(mutex_);
    if (shutting_down_ || failure_ != Status::Ok) return Status::Closed;
    if (failure_producer_.failed) return Status::Busy;
    failure_producer_ = producer;
    return Status::Ok;
}
Status RendererHost::bind_renderer(AudioCore::ADSP::AudioRenderer::AudioRenderer& renderer) {
    std::lock_guard lock(mutex_);
    if (shutting_down_) return Status::Closed;
    if (renderer_) return Status::Busy;
    renderer_ = &renderer;
    return Status::Ok;
}
Status RendererHost::bind_output(AudioCore::Sink::Sink& output) {
    std::lock_guard lock(mutex_);
    if (shutting_down_) return Status::Closed;
    if (output_) return Status::Busy;
    output_ = &output;
    return Status::Ok;
}
AudioCore::ADSP::AudioRenderer::AudioRenderer& RendererHost::renderer() const {
    std::lock_guard lock(mutex_);
    require(failure_);
    if (!renderer_ || shutting_down_) throw PlatformError(Status::Closed);
    return *renderer_;
}
AudioCore::Sink::Sink& RendererHost::output_sink() const {
    std::lock_guard lock(mutex_);
    require(failure_);
    if (!output_ || shutting_down_) throw PlatformError(Status::Closed);
    return *output_;
}
std::uint64_t RendererHost::clock_ns() const {
    std::uint64_t time = 0;
    require(MonotonicClock::now_ns(time));
    return time;
}
std::uint64_t RendererHost::clock_us() const { return clock_ns() / 1000; }
bool RendererHost::is_paused() const noexcept { return paused_; }
bool RendererHost::is_shutting_down() const noexcept { return shutting_down_; }
Status RendererHost::wait_active(std::stop_token token) {
    std::unique_lock lock(mutex_);
    if (token.stop_requested()) return Status::Closed;
    const auto ready = [&] { return shutting_down_ || failure_ != Status::Ok || !paused_; };
    if (!active_.wait(lock, token, ready)) return Status::Closed;
    if (token.stop_requested()) return Status::Closed;
    const Status status = failure_;
    if (status != Status::Ok) return status;
    return shutting_down_ ? Status::Closed : Status::Ok;
}
Status RendererHost::set_paused(bool paused) {
    std::lock_guard control(pause_control_);
    if (paused) {
        std::stop_source cancellation(std::nostopstate);
        {
            std::lock_guard lock(mutex_);
            paused_ = true;
            cancellation = render_stop_;
        }
        // A stop callback may need a sink lock; never invoke it under host lock.
        cancellation.request_stop();
        active_.notify_all();
        std::unique_lock lock(mutex_);
        active_.wait(lock, [&] { return in_flight_ == 0; });
        const Status failure = failure_;
        return failure != Status::Ok ? failure : shutting_down_ ? Status::Closed : Status::Ok;
    }
    std::lock_guard lock(mutex_);
    if (const Status failure = failure_; failure != Status::Ok) return failure;
    if (shutting_down_) return Status::Closed;
    if (!paused_) return Status::Ok;
    if (in_flight_) return Status::Busy;
    try { render_stop_ = std::stop_source{}; }
    catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    paused_ = false;
    active_.notify_all();
    return Status::Ok;
}
Status RendererHost::begin_render(std::stop_token token) {
    std::unique_lock lock(mutex_);
    const auto ready = [&] {
        return shutting_down_ || failure_ != Status::Ok || (!paused_ && !in_flight_);
    };
    if (!active_.wait(lock, token, ready) || token.stop_requested()) return Status::Closed;
    if (const Status status = failure_; status != Status::Ok) return status;
    if (shutting_down_) return Status::Closed;
    ++in_flight_;
    return Status::Ok;
}
void RendererHost::end_render() noexcept {
    std::lock_guard lock(mutex_);
    if (in_flight_) --in_flight_;
    active_.notify_all();
}
std::stop_token RendererHost::render_token() const {
    std::lock_guard lock(mutex_);
    return render_stop_.get_token();
}
Status RendererHost::set_master_volume(float volume) {
    if (!std::isfinite(volume) || volume < 0.0f || volume > 128.0f) return Status::InvalidArgument;
    if (const Status status = failure_; status != Status::Ok) return status;
    if (shutting_down_) return Status::Closed;
    volume_ = volume;
    return Status::Ok;
}
float RendererHost::master_volume() const noexcept { return volume_; }
Status RendererHost::failure() const noexcept { return failure_; }
void RendererHost::fail(Status failure) noexcept {
    if (failure == Status::Ok) return;
    std::stop_source render_cancel(std::nostopstate);
    FailureProducer producer{};
    {
        std::lock_guard lock(mutex_);
        Status expected = Status::Ok;
        if (failure_.compare_exchange_strong(expected, failure)) producer = failure_producer_;
        shutting_down_ = true;
        paused_ = true;
        render_cancel = render_stop_;
    }
    failure_stop_.request_stop();
    render_cancel.request_stop();
    active_.notify_all();
    // The producer may latch/wake an independent host terminal-failure path.
    // It must not reenter this engine, join/destroy this worker, or throw.
    if (producer.failed) {
        try { producer.failed(producer.context, failure); }
        catch (...) { /* Keep the original fatal state; never unwind a worker. */ }
    }
}
std::stop_token RendererHost::failure_token() const noexcept { return failure_stop_.get_token(); }
void RendererHost::shutdown() noexcept {
    std::stop_source render_cancel(std::nostopstate);
    {
        std::lock_guard lock(mutex_);
        shutting_down_ = true;
        paused_ = true;
        render_cancel = render_stop_;
    }
    failure_stop_.request_stop();
    render_cancel.request_stop();
    active_.notify_all();
}
} // namespace botw::audio
