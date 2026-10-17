// SPDX-License-Identifier: GPL-3.0-or-later
#include "wire.h"
#include "audio_core/renderer/effect/effect_info_base.h"
#include "audio_core/renderer/mix/mix_info.h"
#include "audio_core/renderer/performance/performance_manager.h"
#include "audio_core/renderer/sink/sink_info_base.h"
#include "audio_core/renderer/voice/voice_channel_resource.h"
#include "audio_core/renderer/voice/voice_info.h"
#include <bit>
#include <cstddef>
#include <limits>

namespace botw::audio {
using namespace AudioCore;
using namespace AudioCore::Renderer;
static std::uint32_t load32(std::span<const std::byte> bytes, std::size_t offset) {
    std::uint32_t value = 0;
    for (unsigned i = 0; i < 4; ++i)
        value |= std::uint32_t(std::to_integer<unsigned char>(bytes[offset + i])) << (i * 8);
    return value;
}
Status rev1_update_layout(UpdateDimensions dimensions, Rev1UpdateLayout& out) {
    // Header sizes are u32 on the normal audren wire. Compute in u64 first;
    // reject truncation before any original InfoUpdater span or state mutation.
    const std::array<std::uint64_t, 10> sections{
        sizeof(BehaviorInfo::InParameter),
        std::uint64_t(dimensions.pools) * sizeof(MemoryPoolInfo::InParameter),
        std::uint64_t(dimensions.voices) * sizeof(VoiceInfo::InParameter),
        std::uint64_t(dimensions.voices) * sizeof(VoiceChannelResource::InParameter),
        std::uint64_t(dimensions.effects) * sizeof(EffectInfoBase::InParameterVersion1),
        std::uint64_t(dimensions.mixes) * sizeof(MixInfo::InParameter),
        std::uint64_t(dimensions.sinks) * sizeof(SinkInfoBase::InParameter),
        sizeof(PerformanceManager::InParameter), 0, 0,
    };
    Rev1UpdateLayout result{};
    std::uint64_t input_size = 0x40;
    for (std::size_t i = 0; i < sections.size(); ++i) {
        if (sections[i] > UINT32_MAX) return Status::OutOfBounds;
        result.sections[i] = static_cast<std::uint32_t>(sections[i]);
        input_size += sections[i];
    }
    const auto output_size = std::uint64_t{0x40} +
        std::uint64_t(dimensions.pools) * sizeof(MemoryPoolInfo::OutStatus) +
        std::uint64_t(dimensions.voices) * sizeof(VoiceInfo::OutStatus) +
        std::uint64_t(dimensions.effects) * sizeof(EffectInfoBase::OutStatusVersion1) +
        std::uint64_t(dimensions.sinks) * sizeof(SinkInfoBase::OutStatus) +
        sizeof(PerformanceManager::OutStatus) + sizeof(BehaviorInfo::OutStatus);
    if (input_size > UINT32_MAX || output_size > UINT32_MAX) return Status::OutOfBounds;
    result.input_size = static_cast<std::size_t>(input_size);
    result.output_size = static_cast<std::size_t>(output_size);
    out = result;
    return Status::Ok;
}
Status parse_rev1_parameters(std::span<const std::byte> wire,
                             AudioRendererParameterInternal& out) {
    if (!wire.data() || wire.size() != 0x34) return Status::InvalidArgument;
    AudioRendererParameterInternal result{};
    result.sample_rate = load32(wire, 0x00); result.sample_count = load32(wire, 0x04);
    result.mixes = load32(wire, 0x08); result.sub_mixes = load32(wire, 0x0c);
    result.voices = load32(wire, 0x10); result.sinks = load32(wire, 0x14);
    result.effects = load32(wire, 0x18); result.perf_frames = load32(wire, 0x1c);
    result.voice_drop_enabled = std::to_integer<u8>(wire[0x20]);
    result.unk_21 = std::to_integer<u8>(wire[0x21]);
    result.rendering_device = std::to_integer<u8>(wire[0x22]);
    result.execution_mode = static_cast<ExecutionMode>(std::to_integer<u8>(wire[0x23]));
    result.splitter_infos = load32(wire, 0x24);
    result.splitter_destinations = std::bit_cast<s32>(load32(wire, 0x28));
    result.external_context_size = load32(wire, 0x2c); result.revision = load32(wire, 0x30);
    if (result.revision != Common::MakeMagic('R', 'E', 'V', '1') ||
        result.execution_mode != ExecutionMode::Auto) return Status::Unsupported;
    // Exact native compatibility profile for the original fixed-output sink.
    // Other renderer modes/rates must gain a real implementation before admission.
    if (!((result.sample_rate == 48000 && result.sample_count == 240) ||
          (result.sample_rate == 32000 && result.sample_count == 160))) return Status::Unsupported;
    if (!result.voices || !result.sinks || !result.mixes ||
        result.effects > MaxEffects || result.sub_mixes == UINT32_MAX ||
        result.perf_frames == UINT32_MAX) return Status::InvalidArgument;
    // mixes is the aggregate sample-buffer allocation across all mix nodes.
    // System stores it in s16 and adds MaxChannels in its s16 command header;
    // MaxMixBuffers bounds each node's fixed routing matrix instead.
    if (std::uint64_t(result.mixes) + MaxChannels >
        std::uint64_t(std::numeric_limits<s16>::max())) return Status::OutOfBounds;
    if (result.splitter_infos || result.splitter_destinations || result.external_context_size)
        return Status::Unsupported; // These are not REV1 features; never silently emulate them.
    const auto pool_count = std::uint64_t(result.effects) + std::uint64_t(result.voices) * MaxWaveBuffers;
    if (pool_count > UINT32_MAX) return Status::OutOfBounds;
    Rev1UpdateLayout layout{};
    const auto valid = rev1_update_layout({static_cast<u32>(pool_count), result.voices,
        result.effects, result.sub_mixes + 1, result.sinks}, layout);
    if (valid != Status::Ok) return valid;
    out = result;
    return Status::Ok;
}
Status validate_rev1_update_layout(UpdateDimensions dimensions,
                                   std::span<const std::byte> input,
                                   std::span<std::byte> output) {
    if (!input.data() || !output.data()) return Status::InvalidArgument;
    Rev1UpdateLayout layout{};
    const auto valid = rev1_update_layout(dimensions, layout);
    if (valid != Status::Ok) return valid;
    if (input.size() != layout.input_size || output.size() != layout.output_size)
        return Status::OutOfBounds;
    for (std::size_t i = 0; i < layout.sections.size(); ++i)
        if (load32(input, 4 + i * 4) != layout.sections[i]) return Status::InvalidArgument;
    std::size_t mix_offset = 0x40;
    for (std::size_t i = 0; i < 5; ++i) mix_offset += layout.sections[i];
    for (std::uint32_t i = 0; i < dimensions.mixes; ++i) {
        const auto offset = mix_offset + std::size_t(i) * sizeof(MixInfo::InParameter);
        const auto active = std::to_integer<unsigned char>(
            input[offset + offsetof(MixInfo::InParameter, in_use)]);
        if (active > 1 || (active && load32(input,
                offset + offsetof(MixInfo::InParameter, buffer_count)) > MaxMixBuffers))
            return Status::InvalidArgument;
    }
    return Status::Ok;
}
} // namespace botw::audio
