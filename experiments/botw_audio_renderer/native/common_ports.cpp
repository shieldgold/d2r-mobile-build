// SPDX-License-Identifier: GPL-3.0-or-later
#include "renderer_memory.h"
#include "common/assert.h"
#include "common/thread.h"
#include <cstdio>
#include <mutex>
#include <pthread.h>
#if defined(__APPLE__)
#include <pthread/qos.h>
#endif

namespace Common::Log {
void FmtLogMessageImpl(Class, Level level, const char*, unsigned, const char*,
                       fmt::string_view format, const fmt::format_args& args) {
    const auto message = fmt::vformat(format, args);
    static std::mutex output;
    std::lock_guard lock(output);
    std::fprintf(stderr, "[native audio:%u] ", static_cast<unsigned>(level));
    std::fwrite(message.data(), 1, message.size(), stderr);
    std::fputc('\n', stderr);
}
} // namespace Common::Log

// A failed original assertion is a typed fatal operation error, not a debugger,
// VM scan, stack sampler, or a successful unimplemented operation.
void AssertFailSoftImpl() { throw botw::audio::PlatformError(botw::audio::Status::SystemError); }
[[noreturn]] void AssertFatalImpl() { throw botw::audio::PlatformError(botw::audio::Status::SystemError); }

namespace Common {
bool Event::WaitFor(std::chrono::nanoseconds time) {
    std::unique_lock lock(mutex);
    if (!condvar.wait_for(lock, time, [&] { return is_set.load(); })) return false;
    is_set = false;
    return true;
}
void SetCurrentThreadName(const char* name) {
    if (!name) throw botw::audio::PlatformError(botw::audio::Status::InvalidArgument);
#if defined(__APPLE__)
    if (pthread_setname_np(name)) throw botw::audio::PlatformError(botw::audio::Status::SystemError);
#else
    if (pthread_setname_np(pthread_self(), name)) throw botw::audio::PlatformError(botw::audio::Status::SystemError);
#endif
}
void SetCurrentThreadPriority(ThreadPriority priority) {
#if defined(__APPLE__)
    qos_class_t qos;
    switch (priority) {
    case ThreadPriority::Low: qos = QOS_CLASS_UTILITY; break;
    case ThreadPriority::Normal: qos = QOS_CLASS_DEFAULT; break;
    case ThreadPriority::High: qos = QOS_CLASS_USER_INITIATED; break;
    case ThreadPriority::VeryHigh:
    case ThreadPriority::Critical: qos = QOS_CLASS_USER_INTERACTIVE; break;
    default: throw botw::audio::PlatformError(botw::audio::Status::InvalidArgument);
    }
    if (pthread_set_qos_class_self_np(qos, 0))
        throw botw::audio::PlatformError(botw::audio::Status::SystemError);
#else
    if (priority != ThreadPriority::Normal)
        throw botw::audio::PlatformError(botw::audio::Status::Unsupported);
    sched_param parameters{};
    if (pthread_setschedparam(pthread_self(), SCHED_OTHER, &parameters))
        throw botw::audio::PlatformError(botw::audio::Status::SystemError);
#endif
}
} // namespace Common
