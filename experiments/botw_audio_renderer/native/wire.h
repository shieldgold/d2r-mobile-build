// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include "renderer_memory.h"
#include "audio_core/common/audio_renderer_parameter.h"

namespace botw::audio {
struct UpdateDimensions { std::uint32_t pools, voices, effects, mixes, sinks; };
struct Rev1UpdateLayout {
    std::size_t input_size, output_size;
    std::array<std::uint32_t, 10> sections;
};
Status parse_rev1_parameters(std::span<const std::byte> wire,
                            AudioCore::AudioRendererParameterInternal& out);
Status rev1_update_layout(UpdateDimensions dimensions, Rev1UpdateLayout& out);
Status validate_rev1_update_layout(UpdateDimensions dimensions,
                                  std::span<const std::byte> input,
                                  std::span<std::byte> output);
} // namespace botw::audio
