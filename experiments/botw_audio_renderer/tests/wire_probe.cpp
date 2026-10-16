// SPDX-License-Identifier: GPL-3.0-or-later
#include "wire.h"
#include <array>
#include <cstdio>
#include <limits>
#include <vector>
using namespace botw::audio;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned REV1 wire behavior failed %d: %s\n", __LINE__, #x); return 1; } } while (0)
static void put(std::span<std::byte> out, std::size_t offset, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) out[offset + i] = std::byte((value >> (i * 8)) & 255);
}
int main() {
    std::array<std::byte, 0x34> wire{};
    put(wire, 0, 48000); put(wire, 4, 240); put(wire, 8, 1);
    put(wire, 0x10, 1); put(wire, 0x14, 1); put(wire, 0x18, 1); put(wire, 0x30, 0x31564552);
    AudioCore::AudioRendererParameterInternal parameters{};
    CHECK(parse_rev1_parameters(wire, parameters) == Status::Ok);
    CHECK(parameters.sample_rate == 48000 && parameters.sample_count == 240 && parameters.effects == 1);
    std::array<std::byte, 64> sdk_client{};
    CHECK(parse_rev1_parameters(sdk_client, parameters) == Status::InvalidArgument);
    wire[0x23] = std::byte{1}; CHECK(parse_rev1_parameters(wire, parameters) == Status::Unsupported);
    wire[0x23] = std::byte{0}; put(wire, 0x30, 0x3f564552);
    CHECK(parse_rev1_parameters(wire, parameters) == Status::Unsupported);
    UpdateDimensions dimensions{5, 1, 1, 1, 1}; Rev1UpdateLayout layout{};
    CHECK(rev1_update_layout(dimensions, layout) == Status::Ok);
    CHECK(layout.input_size == 0xe10 && layout.output_size == 0x190);
    std::vector<std::byte> input(layout.input_size), output(layout.output_size, std::byte{0x55});
    put(input, 0, 0x31564552);
    for (std::size_t i = 0; i < layout.sections.size(); ++i) put(input, 4 + i * 4, layout.sections[i]);
    put(input, 0x3c, static_cast<std::uint32_t>(input.size())); put(input, 0x40, 0x31564552);
    CHECK(validate_rev1_update_layout(dimensions, input, output) == Status::Ok);
    CHECK(validate_rev1_update_layout(dimensions, {input.data(), 63}, output) == Status::OutOfBounds);
    CHECK(validate_rev1_update_layout(dimensions, input, {output.data(), output.size() - 1}) == Status::OutOfBounds);
    put(input, 4, 0); CHECK(validate_rev1_update_layout(dimensions, input, output) == Status::InvalidArgument);
    CHECK(output[0] == std::byte{0x55});
    CHECK(rev1_update_layout({0, std::numeric_limits<std::uint32_t>::max(), 0, 0, 0}, layout) == Status::OutOfBounds);
    return 0;
}
