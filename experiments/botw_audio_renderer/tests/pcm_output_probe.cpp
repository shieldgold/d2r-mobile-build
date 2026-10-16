// SPDX-License-Identifier: GPL-3.0-or-later
#include "pcm_output.h"
#include <array>
#include <cmath>
#include <cstdio>
#include <limits>
#include <string_view>
extern "C" {
#include "whole_audio_platform.h"
void owned_pcm_fail(int allocation, int start, int stop);
unsigned owned_pcm_unit_count(void);
int owned_pcm_started(void);
int owned_pcm_render(std::int16_t*, unsigned);
}
using namespace botw::audio;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned PCM adapter behavior failed %d: %s\n", __LINE__, #x); return 1; } } while (0)
int main(int argc, char** argv) {
    CHECK(argc == 2); const std::string_view name{argv[1]};
    PcmOutput pcm; PcmCounters counters{};
    const std::array<std::int16_t, 4> source{200, -200, 100, -100};
    std::array<std::int16_t, 4> output{};
    CHECK(pcm.counters(counters) == Status::Closed);
    CHECK(pcm.open() == Status::Ok && owned_pcm_unit_count() == 1);
    CHECK(pcm.open() == Status::Busy);
    if (name == "consumption") {
        CHECK(pcm.set_active(true) == Status::Ok && owned_pcm_started());
        auto result = pcm.write(source, 2);
        CHECK(result.status == Status::Ok && result.accepted_frames == 2);
        CHECK(pcm.counters(counters) == Status::Ok && counters.submitted_frames == 2 && counters.consumed_frames == 0);
        CHECK(pcm.set_volume(0.5f) == Status::Ok);
        CHECK(!owned_pcm_render(output.data(), 2));
        CHECK((output == std::array<std::int16_t, 4>{100, -100, 50, -50}));
        CHECK(pcm.counters(counters) == Status::Ok && counters.consumed_frames == 2);
        CHECK(pcm.set_volume(std::numeric_limits<float>::quiet_NaN()) == Status::InvalidArgument);
        CHECK(pcm.set_active(false) == Status::Ok && !owned_pcm_started());
        result = pcm.write(source, 2);
        CHECK(result.status == Status::Backpressure && result.accepted_frames == 0);
        CHECK(pcm.write(source, 3).status == Status::InvalidArgument);
        pcm.close(); CHECK(pcm.set_active(true) == Status::Closed);
        CHECK(pcm.write(source, 2).status == Status::Closed);
    } else if (name == "lifecycle") {
        char error[128]{};
        CHECK(pcm.set_active(true) == Status::Ok);
        CHECK(pcm.write(source, 2).accepted_frames == 2);
        CHECK(!whole_audio_platform_set_active(0, error, sizeof error));
        CHECK(pcm.write(source, 2).status == Status::Backpressure);
        PcmOutput fresh; CHECK(fresh.open() == Status::Ok && fresh.set_active(true) == Status::Ok);
        CHECK(!owned_pcm_started() && fresh.write(source, 2).accepted_frames == 0);
        CHECK(!whole_audio_platform_set_active(1, error, sizeof error) && owned_pcm_started());
        CHECK(fresh.write(source, 2).accepted_frames == 2);
        CHECK(!owned_pcm_render(output.data(), 2) && output == source);
    } else if (name == "failure") {
        owned_pcm_fail(0, 1, 0);
        CHECK(pcm.set_active(true) == Status::DeviceError);
        CHECK(pcm.write(source, 2).accepted_frames == 0);
        owned_pcm_fail(0, 0, 0); CHECK(pcm.set_active(true) == Status::Ok);
        owned_pcm_fail(0, 0, 1); CHECK(pcm.set_active(false) == Status::DeviceError);
        CHECK(pcm.write(source, 2).accepted_frames == 0);
        owned_pcm_fail(0, 0, 0); pcm.close();
        owned_pcm_fail(1, 0, 0); CHECK(pcm.open() == Status::DeviceError);
        CHECK(pcm.counters(counters) == Status::Closed);
    } else return 64;
    return 0;
}
