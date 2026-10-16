// SPDX-License-Identifier: GPL-3.0-or-later
#include "renderer_host.h"
#include "worker.h"
#include <atomic>
#include <cstdio>
#include <thread>
using namespace botw::audio;
using namespace std::chrono_literals;
struct Failure { std::atomic<unsigned> calls{0}; std::atomic<Status> status{Status::Ok}; HostEvent wake; };
static void failed(void* context, Status status) {
    auto& failure=*static_cast<Failure*>(context);
    failure.status=status; ++failure.calls; (void)failure.wake.signal();
}
#define CHECK(x) do { if(!(x)) { std::fprintf(stderr,"owned fatal producer failed %d: %s\n",__LINE__,#x); return 1; } } while(0)
int main() {
    Failure failure; RendererHost host;
    CHECK(host.bind_failure_producer({&failure,failed})==Status::Ok);
    std::jthread worker([&]{ worker_boundary(host,[]{ throw PlatformError(Status::DeviceError); }); });
    worker.join();
    CHECK(failure.calls==1 && failure.status==Status::DeviceError && failure.wake.wait_for(0ns)==Status::Ok);
    host.fail(Status::OutOfBounds); host.shutdown();
    CHECK(failure.calls==1 && host.failure()==Status::DeviceError);
    return 0;
}
