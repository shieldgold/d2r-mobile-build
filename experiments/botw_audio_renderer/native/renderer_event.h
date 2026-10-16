// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "renderer_memory.h"
namespace botw::audio {
struct EventCallbacks {
    void* context;
    int (*signal)(void* context);
    int (*clear)(void* context);
};
// Both callbacks are mandatory normal host kernel producers. Zero means actual
// success. They must not reenter this RendererEvent. The root service keeps their
// context alive until session quiescence. Control calls serialize both producers.
class RendererEvent {
public:
    explicit RendererEvent(EventCallbacks callbacks);
    Status signal();
    Status clear();
    Status wait_for(std::chrono::nanoseconds timeout);
    void close();
private:
    EventCallbacks callbacks_;
    HostEvent event_;
    std::mutex control_;
    bool closed_ = false;
};
} // namespace botw::audio
