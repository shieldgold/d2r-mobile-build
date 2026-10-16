// SPDX-License-Identifier: GPL-3.0-or-later
#include "renderer_event.h"
#include <cstdio>
using namespace botw::audio;
using namespace std::chrono_literals;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned renderer event behavior failed %d: %s\n", __LINE__, #x); return 1; } } while (0)
struct OwnedKernelEvent { HostEvent event; bool fail = false; };
static int signal(void* context) {
    auto& owned = *static_cast<OwnedKernelEvent*>(context);
    return owned.fail || owned.event.signal() != Status::Ok;
}
static int clear(void* context) {
    auto& owned = *static_cast<OwnedKernelEvent*>(context);
    return owned.fail || owned.event.clear() != Status::Ok;
}
static int owned_case() {
    OwnedKernelEvent kernel;
    RendererEvent event({&kernel, signal, clear});
    CHECK(event.wait_for(0ns) == Status::TimedOut && kernel.event.wait_for(0ns) == Status::TimedOut);
    CHECK(event.signal() == Status::Ok);
    CHECK(kernel.event.wait_for(0ns) == Status::Ok && event.wait_for(0ns) == Status::Ok);
    CHECK(event.clear() == Status::Ok && event.wait_for(0ns) == Status::TimedOut);
    CHECK(kernel.event.wait_for(0ns) == Status::TimedOut);
    kernel.fail = true;
    CHECK(event.signal() == Status::SystemError && event.wait_for(0ns) == Status::TimedOut);
    CHECK(event.clear() == Status::SystemError);
    kernel.fail = false;
    event.close();
    CHECK(event.signal() == Status::Closed);
    CHECK(kernel.event.wait_for(0ns) == Status::TimedOut);
    bool rejected = false;
    try { RendererEvent missing({nullptr, nullptr, nullptr}); }
    catch (const PlatformError& error) { rejected = error.status == Status::InvalidArgument; }
    CHECK(rejected);
    return 0;
}
int main() {
    try { return owned_case(); }
    catch (const PlatformError& error) {
        std::fprintf(stderr, "owned renderer event behavior failed: unexpected %s\n", error.what()); return 1;
    }
}
