// SPDX-License-Identifier: GPL-3.0-or-later
// Entire real Eden System/InfoUpdater on explicitly owned REV1 wire bytes.
// No guest, manager/ADSP thread Start, AudioUnit Acquire, or GPU operation.
#include "renderer_host.h"
#include "renderer_event.h"
#include "native_sink_stream.h"
#include "audio_core/adsp/apps/audio_renderer/audio_renderer.h"
#include "audio_core/common/audio_renderer_parameter.h"
#include "audio_core/renderer/system.h"
#include "audio_core/renderer/voice/voice_info.h"
#include "audio_core/renderer/voice/voice_channel_resource.h"
#include "audio_core/renderer/effect/effect_info_base.h"
#include "audio_core/renderer/mix/mix_info.h"
#include "audio_core/renderer/sink/sink_info_base.h"
#include "audio_core/sink/sink.h"
#include <array>
#include <cstdio>
#include <cstring>
#include <vector>
extern "C" unsigned owned_pcm_unit_count(void);
using namespace botw::audio;
using namespace AudioCore;
using namespace AudioCore::Renderer;
using namespace std::chrono_literals;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned complete Eden Update behavior failed %d: %s\n", __LINE__, #x); return 1; } } while (0)
struct OwnedKernelEvent {
    HostEvent event;
    unsigned signals = 0, clears = 0;
    bool fail = false;
};
static int signal_kernel(void* context) {
    auto& event = *static_cast<OwnedKernelEvent*>(context);
    if (event.fail) return -1;
    ++event.signals; return event.event.signal() != Status::Ok;
}
static int clear_kernel(void* context) {
    auto& event = *static_cast<OwnedKernelEvent*>(context);
    if (event.fail) return -1;
    ++event.clears; return event.event.clear() != Status::Ok;
}
template <typename T> static void append(std::vector<u8>& bytes, const T& value) {
    const auto offset = bytes.size(); bytes.resize(offset + sizeof(T));
    std::memcpy(bytes.data() + offset, &value, sizeof(T));
}
static int owned_case() {
    constexpr u32 revision = Common::MakeMagic('R', 'E', 'V', '1');
    AudioRendererParameterInternal parameters{};
    parameters.sample_rate = 48000; parameters.sample_count = 240;
    parameters.mixes = 1; parameters.voices = 1; parameters.sinks = 1;
    parameters.execution_mode = ExecutionMode::Auto; parameters.revision = revision;
    const auto work_size = System::GetWorkBufferSize(parameters);
    CHECK(work_size >= 0x18000 && work_size % 4096 == 0);
    MemorySpace memory;
    CHECK(memory.allocate(71, 0x100000, work_size, Access::ReadWrite) == Status::Ok);
    TransferPool transfer;
    CHECK(transfer.attach(memory, 71, 0x100000, work_size) == Status::Ok);
    RendererMemory renderer_memory(memory, 71);
    auto sink = make_pcm_sink(); RendererHost host(*sink, true);
    ::AudioCore::ADSP::AudioRenderer::AudioRenderer adsp(host, *sink);
    CHECK(host.bind_renderer(adsp) == Status::Ok);
    OwnedKernelEvent kernel;
    RendererEvent rendered({&kernel, signal_kernel, clear_kernel});
    System system(host, &rendered);
    const auto initialized = system.Initialize(parameters, &transfer, work_size,
                                                &renderer_memory, 123, 0);
    CHECK(!initialized.IsError());
    CHECK(transfer.detach() == Status::Busy);
    CHECK(memory.unregister_region(71, 0x100000) == Status::Busy);

    // Use the original ABI types and original section order. This is owned REV1
    // serialized update data, never a 64-byte SDK client parameter memcpy.
    constexpr u32 pool_count = MaxWaveBuffers;
    std::array<u32, 16> header{};
    header[0] = revision;
    header[1] = sizeof(BehaviorInfo::InParameter);
    header[2] = pool_count * sizeof(MemoryPoolInfo::InParameter);
    header[3] = sizeof(VoiceInfo::InParameter);
    header[4] = sizeof(VoiceChannelResource::InParameter);
    header[5] = 0;
    header[6] = sizeof(MixInfo::InParameter);
    header[7] = sizeof(SinkInfoBase::InParameter);
    header[8] = sizeof(PerformanceManager::InParameter);
    std::vector<u8> input;
    append(input, header);
    BehaviorInfo::InParameter behavior{}; behavior.revision = revision;
    append(input, behavior);
    std::array<MemoryPoolInfo::InParameter, pool_count> pools{}; append(input, pools);
    VoiceChannelResource::InParameter resource{}; append(input, resource);
    VoiceInfo::InParameter voice{}; append(input, voice);
    MixInfo::InParameter mix{}; append(input, mix);
    SinkInfoBase::InParameter unused_sink{}; append(input, unused_sink);
    PerformanceManager::InParameter performance{}; append(input, performance);
    header[15] = static_cast<u32>(input.size());
    std::memcpy(input.data(), header.data(), sizeof header);
    const auto output_size = sizeof(header) + pool_count * sizeof(MemoryPoolInfo::OutStatus) +
        sizeof(VoiceInfo::OutStatus) + sizeof(SinkInfoBase::OutStatus) +
        sizeof(PerformanceManager::OutStatus) + sizeof(BehaviorInfo::OutStatus);
    std::vector<u8> output(output_size);
    CHECK(rendered.signal() == Status::Ok && kernel.event.wait_for(0ns) == Status::Ok);
    auto result = system.Update(input, {}, output);
    CHECK(!result.IsError() && kernel.clears == 1 && kernel.event.wait_for(0ns) == Status::TimedOut);
    std::array<u32, 16> out_header{}; std::memcpy(out_header.data(), output.data(), sizeof out_header);
    CHECK(out_header[1] == sizeof(BehaviorInfo::OutStatus));
    CHECK(out_header[2] == pool_count * sizeof(MemoryPoolInfo::OutStatus));
    CHECK(out_header[3] == sizeof(VoiceInfo::OutStatus));
    CHECK(out_header[7] == sizeof(SinkInfoBase::OutStatus) && out_header[15] == output_size);
    CHECK(!system.Update(input, {}, output).IsError() && kernel.clears == 2);
    auto invalid = input;
    const u32 wrong_revision = Common::MakeMagic('R', 'E', 'V', '2');
    std::memcpy(invalid.data() + sizeof(header), &wrong_revision, sizeof wrong_revision);
    CHECK(system.Update(invalid, {}, output).IsError() && kernel.clears == 2);
    // The backing remains fully owned so this RED cannot touch unowned memory.
    // A descriptor admitting only 63 bytes must fail before output/state writes.
    std::fill(output.begin(), output.end(), u8{0xa5});
    CHECK(system.Update({input.data(), 63}, {}, output).IsError());
    CHECK(output.front() == 0xa5 && kernel.clears == 2);
    kernel.fail = true;
    bool failed = false;
    try { (void)system.Update(input, {}, output); }
    catch (const PlatformError& error) { failed = error.status == Status::SystemError; }
    CHECK(failed && kernel.clears == 2);
    kernel.fail = false;
    CHECK(owned_pcm_unit_count() == 0);
    system.Finalize(); adsp.Stop(); sink->CloseStreams();
    CHECK(transfer.detach() == Status::Ok && memory.unregister_region(71, 0x100000) == Status::Ok);
    std::puts("complete owned Eden REV1 Update ok; no worker/device/guest execution");
    return 0;
}
int main() {
    try { return owned_case(); }
    catch (const PlatformError& error) {
        std::fprintf(stderr, "owned complete Eden Update behavior failed: %s\n", error.what()); return 1;
    }
}
