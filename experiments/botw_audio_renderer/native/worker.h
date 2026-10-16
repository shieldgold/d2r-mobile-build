// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "renderer_host.h"
#include <exception>
namespace botw::audio {
inline Status exception_status(std::exception_ptr exception) noexcept {
    try { if (exception) std::rethrow_exception(exception); }
    catch (const PlatformError& error) { return error.status; }
    catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    catch (...) { return Status::SystemError; }
    return Status::SystemError;
}
template <typename Function> void worker_boundary(RendererHost& host, Function&& work) noexcept {
    try { work(); }
    catch (...) { host.fail(exception_status(std::current_exception())); }
}
} // namespace botw::audio
