// SPDX-License-Identifier: GPL-3.0-or-later
// Owned native work round: no renderer, guest, PCM, or device is executed.
#include "renderer_host.h"
#include <cstdio>
#include <future>
#include <latch>
#include <thread>
using namespace botw::audio;
using namespace std::chrono_literals;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned pause ACK failed %d: %s\n", __LINE__, #x); return 1; } } while (0)
int main() {
    RendererHost host(true);
    std::latch entered(1), cancelled(1), release(1);
    auto worker = std::async(std::launch::async, [&] {
        require(host.begin_render({}));
        const auto token = host.render_token();
        entered.count_down();
        while (!token.stop_requested()) std::this_thread::sleep_for(1ms);
        cancelled.count_down();
        release.wait();
        host.end_render();
    });
    entered.wait();
    auto paused = std::async(std::launch::async, [&] { return host.set_paused(true); });
    // Do not hang an old implementation with no per-round cancellation.
    const bool real_cancel = [&] {
        for (unsigned i=0; i<100; ++i) {
            if (cancelled.try_wait()) return true;
            std::this_thread::sleep_for(1ms);
        }
        return false;
    }();
    const bool waited = paused.wait_for(5ms) == std::future_status::timeout;
    release.count_down();
    if (!real_cancel) host.fail(Status::DeviceError);
    worker.get();
    const auto result = paused.get();
    CHECK(real_cancel && waited && result == Status::Ok && host.is_paused());
    auto next = std::async(std::launch::async, [&] {
        const auto status = host.begin_render({});
        if (status == Status::Ok) host.end_render();
        return status;
    });
    CHECK(next.wait_for(5ms) == std::future_status::timeout);
    CHECK(host.set_paused(false) == Status::Ok && next.get() == Status::Ok);
    CHECK(!host.render_token().stop_requested());
    CHECK(host.begin_render({}) == Status::Ok);
    host.fail(Status::DeviceError);
    CHECK(host.render_token().stop_requested());
    host.end_render();
    CHECK(host.set_paused(true) == Status::DeviceError);
    CHECK(host.begin_render({}) == Status::DeviceError);
    return 0;
}
