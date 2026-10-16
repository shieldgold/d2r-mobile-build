// SPDX-License-Identifier: GPL-3.0-or-later
#include "renderer_host.h"
#include <cstdio>
#include <future>
using namespace botw::audio;
using namespace std::chrono_literals;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned renderer host behavior failed %d: %s\n", __LINE__, #x); return 1; } } while (0)
static int owned_case() {
    RendererHost host;
    CHECK(host.is_paused() && !host.is_shutting_down() && host.failure() == Status::Ok);
    bool missing = false;
    try { (void)host.output_sink(); } catch (const PlatformError& error) { missing = error.status == Status::Closed; }
    CHECK(missing);
    missing = false;
    try { (void)host.renderer(); } catch (const PlatformError& error) { missing = error.status == Status::Closed; }
    CHECK(missing);
    CHECK(host.clock_ns() > 0 && host.clock_us() > 0);
    auto ready = std::async(std::launch::async, [&] { return host.wait_active({}); });
    CHECK(ready.wait_for(5ms) == std::future_status::timeout);
    host.set_paused(false); CHECK(ready.get() == Status::Ok && !host.is_paused());
    CHECK(host.set_master_volume(0.5f) == Status::Ok && host.master_volume() == 0.5f);
    CHECK(host.set_master_volume(-1.0f) == Status::InvalidArgument && host.master_volume() == 0.5f);
    host.set_paused(true);
    auto failure = std::async(std::launch::async, [&] { return host.wait_active({}); });
    CHECK(failure.wait_for(5ms) == std::future_status::timeout);
    host.fail(Status::DeviceError);
    CHECK(failure.get() == Status::DeviceError && host.failure_token().stop_requested());
    host.set_paused(false); CHECK(host.is_paused() && host.is_shutting_down());
    host.fail(Status::OutOfBounds); CHECK(host.failure() == Status::DeviceError);
    RendererHost shutdown;
    auto closing = std::async(std::launch::async, [&] { return shutdown.wait_active({}); });
    shutdown.shutdown(); CHECK(closing.get() == Status::Closed);
    bool rejected = false;
    try { RendererHost unsupported(false, {true, false}); }
    catch (const PlatformError& error) { rejected = error.status == Status::Unsupported; }
    CHECK(rejected);
    return 0;
}
int main() {
    try { return owned_case(); }
    catch (const PlatformError& error) {
        std::fprintf(stderr, "owned renderer host behavior failed: unexpected %s\n", error.what());
        return 1;
    }
}
