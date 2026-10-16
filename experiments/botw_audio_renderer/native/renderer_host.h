// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "renderer_memory.h"
#include <atomic>
#include <condition_variable>
#include <stop_token>

namespace AudioCore {
namespace Sink { class Sink; }
namespace ADSP::AudioRenderer { class AudioRenderer; }
}

namespace botw::audio {
// Real host ports used by the native module. No emulator Core/System facade.
// A renderer must be explicitly bound before renderer(); missing binding fails.
struct FailureProducer {
    void* context;
    void (*failed)(void* context, Status status);
};
class RendererHost {
public:
    explicit RendererHost(bool initially_active = false, const PlatformOptions& options = {});
    RendererHost(AudioCore::Sink::Sink& output, bool initially_active,
                 const PlatformOptions& options = {});
    ~RendererHost();
    Status bind_failure_producer(FailureProducer producer);
    Status bind_renderer(AudioCore::ADSP::AudioRenderer::AudioRenderer& renderer);
    Status bind_output(AudioCore::Sink::Sink& output);
    AudioCore::ADSP::AudioRenderer::AudioRenderer& renderer() const;
    AudioCore::Sink::Sink& output_sink() const;
    std::uint64_t clock_ns() const;
    std::uint64_t clock_us() const;
    bool is_paused() const noexcept;
    bool is_shutting_down() const noexcept;
    Status wait_active(std::stop_token token);
    // ACK means no render round can still call the PCM sink. Cancelling the
    // current round also interrupts bounded WaitFreeSpace when OS audio stops.
    Status set_paused(bool paused);
    Status begin_render(std::stop_token token);
    void end_render() noexcept;
    std::stop_token render_token() const;
    Status set_master_volume(float volume);
    float master_volume() const noexcept;
    Status failure() const noexcept;
    void fail(Status failure) noexcept;
    std::stop_token failure_token() const noexcept;
    void shutdown() noexcept;
private:
    FailureProducer failure_producer_{};
    AudioCore::Sink::Sink* output_ = nullptr;
    AudioCore::ADSP::AudioRenderer::AudioRenderer* renderer_ = nullptr;
    std::atomic<bool> paused_, shutting_down_{false};
    std::atomic<Status> failure_{Status::Ok};
    std::atomic<float> volume_{1.0f};
    std::stop_source failure_stop_, render_stop_;
    std::mutex pause_control_;
    unsigned in_flight_ = 0;
    mutable std::mutex mutex_;
    std::condition_variable_any active_;
};
} // namespace botw::audio
