// SPDX-License-Identifier: GPL-3.0-or-later
#include "audren_host.h"
#include "renderer_event.h"
#include "worker.h"
#include "wire.h"
#include "native_sink_stream.h"
#include "audio_core/adsp/apps/audio_renderer/audio_renderer.h"
#include "audio_core/audio_render_manager.h"
#include "audio_core/renderer/audio_renderer.h"
#include "audio_core/sink/sink.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <utility>
#include <vector>

using namespace botw::audio;
static_assert(BOTW_AUDREN_BUSY == int(Status::Busy));
static_assert(BOTW_AUDREN_BACKPRESSURE == int(Status::Backpressure));
namespace AR = AudioCore::Renderer;
using Parameters = AudioCore::AudioRendererParameterInternal;
struct RegionKey { Owner owner; Address address; };

struct botw_audren_host {
    // C service/control calls serialize here. Render workers never take this lock.
    std::mutex control;
    void* failure_context;
    void (*failed)(void*, int32_t);
    MemorySpace memory;
    std::unique_ptr<AudioCore::Sink::Sink> sink;
    RendererHost ports;
    AudioCore::ADSP::AudioRenderer::AudioRenderer adsp;
    std::unique_ptr<AR::Manager> manager;
    std::vector<botw_audren_session*> sessions;
    std::vector<RegionKey> regions;
    explicit botw_audren_host(const botw_audren_configuration& config)
        : failure_context(config.failure_context), failed(config.failed),
          sink(make_pcm_sink()), ports(*sink, config.initially_active != 0,
              {config.dump_audio_commands != 0, config.inspect_guest_stack != 0}), adsp(ports, *sink) {
        require(ports.bind_failure_producer({this, [](void* context, Status status) {
            auto& host = *static_cast<botw_audren_host*>(context);
            host.failed(host.failure_context, static_cast<int32_t>(status));
        }}));
        require(memory.configure_provider(config.memory_provider, &ports, [](void* context, Status status) {
            static_cast<RendererHost*>(context)->fail(status);
        }));
        require(ports.bind_renderer(adsp));
        manager = std::make_unique<AR::Manager>(ports);
    }
    ~botw_audren_host() {
        worker_boundary(ports, [&] { if (manager) manager->Stop(); });
        worker_boundary(ports, [&] { adsp.Stop(); });
        worker_boundary(ports, [&] { sink->CloseStreams(); });
    }
};

namespace {
std::shared_ptr<void> retain_resource(void* address, void* context,
                                     int (*retain)(void*), void (*release)(void*)) {
    if (!retain || !release) throw PlatformError(Status::InvalidArgument);
    if (retain(context)) throw PlatformError(Status::SystemError);
    // shared_ptr invokes the deleter itself if its control-block allocation fails.
    return {address, [context, release](void*) { release(context); }};
}
}
struct botw_audren_session {
    botw_audren_host& host;
    Owner owner;
    Address transfer_address;
    std::size_t transfer_size;
    RendererMemory memory;
    TransferPool transfer;
    std::shared_ptr<void> event_resource;
    RendererEvent event;
    AR::Renderer renderer;
    bool initialized = false, attached = false, event_closed = false;
    explicit botw_audren_session(botw_audren_host& host_, Owner owner_, Address address,
                                 std::size_t size, botw_audren_event port)
        : host(host_), owner(owner_), transfer_address(address), transfer_size(size),
          memory(host.memory, owner),
          event_resource(retain_resource(port.context, port.context, port.retain, port.release)),
          event({port.context, port.signal, port.clear}), renderer(host.ports, *host.manager, &event) {}
};

namespace {
botw_audren_result platform_result(Status status) {
    return {static_cast<std::int32_t>(status), 0, 0};
}
botw_audren_result service_result(Result result) { return {0, 1, result.raw}; }
void error_text(char* out, std::size_t capacity, const char* text) noexcept {
    if (!out || !capacity) return;
    const auto size = std::min(capacity - 1, std::strlen(text));
    std::memcpy(out, text, size); out[size] = '\0';
}
template <typename Function> botw_audren_result boundary(char* error, std::size_t capacity,
                                                         Function&& function) noexcept {
    if (capacity && !error) return platform_result(Status::InvalidArgument);
    error_text(error, capacity, "");
    try {
        auto result = function();
        if (result.host_status) error_text(error, capacity, status_name(static_cast<Status>(result.host_status)));
        else if (result.service_result_valid && result.service_result) {
            char message[80];
            std::snprintf(message, sizeof message, "original AudioRenderer Result 0x%08x", result.service_result);
            error_text(error, capacity, message);
        }
        return result;
    } catch (...) {
        const auto status = exception_status(std::current_exception());
        error_text(error, capacity, status_name(status));
        return platform_result(status);
    }
}
void checked_host(botw_audren_host* host) {
    if (!host) throw PlatformError(Status::InvalidArgument);
    require(host->ports.failure());
    if (host->ports.is_shutting_down()) throw PlatformError(Status::Closed);
}
void checked_session(botw_audren_session* session) {
    if (!session) throw PlatformError(Status::InvalidArgument);
    checked_host(&session->host);
    if (!session->initialized || std::find(session->host.sessions.begin(), session->host.sessions.end(),
                                          session) == session->host.sessions.end())
        throw PlatformError(Status::Closed);
}
Parameters parameters(const void* wire, std::size_t size) {
    if (!wire || size != BOTW_AUDREN_WIRE_PARAMETER_SIZE) throw PlatformError(Status::InvalidArgument);
    Parameters result{};
    require(parse_rev1_parameters({static_cast<const std::byte*>(wire), size}, result));
    return result;
}
bool overlaps(Address a, std::size_t as, Address b, std::size_t bs) {
    return a <= b ? b - a < as : a - b < bs;
}
void aligned_descriptor(const void* data, std::size_t size, bool optional = false) {
    if (!size && optional) return;
    if (!data || reinterpret_cast<std::uintptr_t>(data) % alignof(std::uint64_t))
        throw PlatformError(Status::InvalidArgument);
}
}

extern "C" botw_audren_result botw_audren_host_create(const botw_audren_configuration* config,
    botw_audren_host** out, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!config || !out || *out || config->abi_version != BOTW_AUDREN_HOST_ABI ||
            config->struct_size != sizeof(*config) || config->initially_active > 1 || !config->failed)
            throw PlatformError(Status::InvalidArgument);
        require(validate_platform_options({config->dump_audio_commands != 0, config->inspect_guest_stack != 0}));
        auto host = std::make_unique<botw_audren_host>(*config);
        *out = host.release();
        return platform_result(Status::Ok);
    });
}
extern "C" botw_audren_result botw_audren_host_destroy(botw_audren_host** owner,
    char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!owner || !*owner) throw PlatformError(Status::InvalidArgument);
        auto* host = *owner;
        {
            std::lock_guard lock(host->control);
            if (!host->sessions.empty()) return platform_result(Status::Busy);
            require(host->memory.retry_provider_cleanup());
            require(host->memory.provider_idle());
            if (!host->regions.empty()) return platform_result(Status::Busy);
            (void)host->ports.set_paused(true);
            host->manager->Stop(); host->adsp.Stop(); host->sink->CloseStreams();
        }
        delete host; *owner = nullptr;
        return platform_result(Status::Ok);
    });
}
extern "C" botw_audren_result botw_audren_host_set_active(botw_audren_host* host, int active,
    char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!host || (active != 0 && active != 1)) throw PlatformError(Status::InvalidArgument);
        std::lock_guard lock(host->control);
        require(host->ports.set_paused(active == 0));
        return platform_result(Status::Ok);
    });
}
extern "C" botw_audren_result botw_audren_register_region(botw_audren_host* host, uint64_t owner,
    uint64_t address, botw_audren_backing backing, uint32_t access, char* error, size_t capacity) {
    return botw_audren_register_region_provenance(host,owner,address,backing,access,0,0,error,capacity);
}
extern "C" botw_audren_result botw_audren_register_region_provenance(botw_audren_host* host,
    uint64_t owner, uint64_t address, botw_audren_backing backing, uint32_t access,
    uint64_t backing_id, uint64_t backing_offset, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!host || !backing.data || !backing.size || !backing.retain || !backing.release ||
            access < 1 || access > 3) throw PlatformError(Status::InvalidArgument);
        std::lock_guard lock(host->control); checked_host(host);
        host->regions.reserve(host->regions.size() + 1);
        auto resource = retain_resource(backing.data, backing.context, backing.retain, backing.release);
        require(host->memory.register_region(owner, address,
            {static_cast<std::byte*>(backing.data), backing.size}, static_cast<Access>(access), resource,
            {backing_id,backing_offset}));
        host->regions.push_back({owner, address});
        return platform_result(Status::Ok);
    });
}
extern "C" botw_audren_result botw_audren_unregister_region(botw_audren_host* host, uint64_t owner,
    uint64_t address, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!host) throw PlatformError(Status::InvalidArgument);
        std::lock_guard lock(host->control);
        require(host->memory.unregister_region(owner, address));
        std::erase_if(host->regions, [&](auto region) { return region.owner == owner && region.address == address; });
        return platform_result(Status::Ok);
    });
}
extern "C" botw_audren_result botw_audren_get_work_buffer_size(const void* wire, size_t size,
    uint64_t* out, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!out) throw PlatformError(Status::InvalidArgument);
        const auto params = parameters(wire, size);
        Status fatal = Status::Ok;
        const botw_audren_configuration config{BOTW_AUDREN_HOST_ABI, sizeof config, 0, 0, 0,
            &fatal, [](void* context, int32_t status) { *static_cast<Status*>(context) = static_cast<Status>(status); },{}};
        botw_audren_host host(config);
        std::uint64_t required = 0;
        const auto result = host.manager->GetWorkBufferSize(params, required);
        if (result.IsSuccess()) *out = required;
        return service_result(result);
    });
}
extern "C" botw_audren_result botw_audren_open(botw_audren_host* host, uint64_t owner,
    const void* wire, size_t wire_size, uint64_t transfer_address, uint64_t transfer_size,
    uint64_t applet_resource_user_id, botw_audren_event event, botw_audren_session** out,
    char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!host || !out || *out || !owner || !event.signal || !event.clear || !event.retain || !event.release ||
            !transfer_size || transfer_size > SIZE_MAX || transfer_address % TransferPool::Alignment ||
            transfer_size % TransferPool::Alignment || transfer_address > UINT64_MAX - transfer_size)
            throw PlatformError(Status::InvalidArgument);
        const auto params = parameters(wire, wire_size);
        std::lock_guard lock(host->control); checked_host(host);
        for (auto* session : host->sessions)
            if (session->owner == owner && overlaps(transfer_address, transfer_size,
                                                    session->transfer_address, session->transfer_size))
                throw PlatformError(Status::Busy);
        host->sessions.reserve(host->sessions.size() + 1);
        auto session = std::make_unique<botw_audren_session>(*host, owner, transfer_address, transfer_size, event);
        require(session->transfer.attach(host->memory, owner, transfer_address, transfer_size));
        session->attached = true;
        const auto id = host->manager->GetSessionId();
        if (id < 0) return service_result(Service::Audio::ResultOutOfSessions);
        Result result;
        try {
            result = session->renderer.Initialize(params, &session->transfer, transfer_size,
                &session->memory, applet_resource_user_id, id);
        } catch (...) {
            // Initialization publishes only transactionally; no worker keeps a
            // failing renderer. Release any real partial mappings before owner.
            worker_boundary(host->ports, [&] { session->renderer.GetSystem().Finalize(); });
            host->manager->ReleaseSessionId(id);
            throw;
        }
        if (result.IsError()) { host->manager->ReleaseSessionId(id); return service_result(result); }
        session->initialized = true;
        host->sessions.push_back(session.get());
        *out = session.release();
        return service_result(result);
    });
}
extern "C" botw_audren_result botw_audren_start(botw_audren_session* session, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!session) throw PlatformError(Status::InvalidArgument);
        std::lock_guard lock(session->host.control); checked_session(session);
        session->renderer.Start();
        return platform_result(Status::Ok);
    });
}
extern "C" botw_audren_result botw_audren_stop(botw_audren_session* session, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!session) throw PlatformError(Status::InvalidArgument);
        std::lock_guard lock(session->host.control); checked_session(session);
        session->renderer.Stop();
        return platform_result(Status::Ok);
    });
}
extern "C" botw_audren_result botw_audren_update(botw_audren_session* session,
    const void* input, size_t input_size, void* performance, size_t performance_size,
    void* output, size_t output_size, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!session) throw PlatformError(Status::InvalidArgument);
        aligned_descriptor(input, input_size); aligned_descriptor(output, output_size);
        aligned_descriptor(performance, performance_size, true);
        std::lock_guard lock(session->host.control); checked_session(session);
        const auto result = session->renderer.RequestUpdate(
            {static_cast<const u8*>(input), input_size}, {static_cast<u8*>(performance), performance_size},
            {static_cast<u8*>(output), output_size});
        // Ordinary pool attach rejection is reported by the unchanged original
        // pool status. A terminal backing failure/producer contract violation
        // during Update must not escape as a successful service completion.
        require(session->host.ports.failure());
        return service_result(result);
    });
}
extern "C" botw_audren_result botw_audren_get_info(botw_audren_session* session,
    botw_audren_session_info* out, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!session || !out) throw PlatformError(Status::InvalidArgument);
        std::lock_guard lock(session->host.control); checked_session(session);
        auto& system = session->renderer.GetSystem();
        *out = {system.GetSampleRate(), system.GetSampleCount(), system.GetMixBufferCount(),
                system.IsActive() ? 1u : 0u, system.GetRenderingTimeLimit(), system.GetVoiceDropParameter()};
        return platform_result(Status::Ok);
    });
}
extern "C" botw_audren_result botw_audren_set_rendering_time_limit(botw_audren_session* session,
    uint32_t percent, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!session) throw PlatformError(Status::InvalidArgument);
        std::lock_guard lock(session->host.control); checked_session(session);
        session->renderer.GetSystem().SetRenderingTimeLimit(percent);
        return platform_result(Status::Ok);
    });
}
extern "C" botw_audren_result botw_audren_close(botw_audren_session** owner, char* error, size_t capacity) {
    return boundary(error, capacity, [&] {
        if (!owner || !*owner) throw PlatformError(Status::InvalidArgument);
        auto* session = *owner; auto& host = session->host;
        {
            std::lock_guard lock(host.control);
            if (std::find(host.sessions.begin(), host.sessions.end(), session) == host.sessions.end())
                throw PlatformError(Status::NotFound);
            const auto previous_failure = host.ports.failure();
            if (session->initialized) { session->renderer.Finalize(); session->initialized = false; }
            if (session->attached) { require(session->transfer.detach()); session->attached = false; }
            require(host.memory.retry_provider_cleanup());
            if (!session->event_closed) { session->event.close(); session->event_closed = true; }
            // Stop/flush errors remain observable. The owner survives one error
            // for cleanup/retry, although workers have already been joined.
            if (host.ports.failure() != previous_failure && host.ports.failure() != Status::Ok)
                return platform_result(host.ports.failure());
            std::erase(host.sessions, session);
        }
        delete session; *owner = nullptr;
        return platform_result(Status::Ok);
    });
}
