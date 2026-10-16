// SPDX-License-Identifier: GPL-3.0-or-later
#include "renderer_event.h"
namespace botw::audio {
RendererEvent::RendererEvent(EventCallbacks callbacks) : callbacks_(callbacks) {
    if (!callbacks.signal || !callbacks.clear) throw PlatformError(Status::InvalidArgument);
}
Status RendererEvent::signal() {
    std::lock_guard lock(control_);
    if (closed_) return Status::Closed;
    if (callbacks_.signal(callbacks_.context)) return Status::SystemError;
    return event_.signal();
}
Status RendererEvent::clear() {
    std::lock_guard lock(control_);
    if (closed_) return Status::Closed;
    if (callbacks_.clear(callbacks_.context)) return Status::SystemError;
    return event_.clear();
}
Status RendererEvent::wait_for(std::chrono::nanoseconds timeout) { return event_.wait_for(timeout); }
void RendererEvent::close() {
    std::lock_guard lock(control_);
    closed_ = true;
    event_.close();
}
} // namespace botw::audio
