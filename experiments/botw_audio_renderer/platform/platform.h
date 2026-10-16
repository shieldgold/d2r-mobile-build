// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include "memory_provider.h"

namespace botw::audio {

enum class Status {
    Ok, InvalidArgument, NotFound, OwnerMismatch, PermissionDenied, Overlap,
    OutOfBounds, Busy, AlreadyAttached, NotAttached, Closed, TimedOut,
    OutOfMemory, SystemError, Unsupported, DeviceError, Backpressure
};
const char* status_name(Status status) noexcept;
using Owner = std::uint64_t;
using Address = std::uint64_t;
enum class Access : unsigned { Read = 1, Write = 2, ReadWrite = 3 };

namespace detail { struct Region; struct MemoryState; struct PoolState; struct EventState; }
struct MemoryProvenance { std::uint64_t backing_id = 0, backing_offset = 0; };

// A pin holds both the registration and the host backing alive. Rights may only
// be narrowed by a slice. No pointer is obtained by casting a guest address. Owners
// synchronize sample/command contents; these locks protect registrations/lifetime.
class MemoryView {
public:
    MemoryView() = default;
    std::span<const std::byte> readable() const noexcept;
    std::span<std::byte> writable() const noexcept;
    std::size_t size() const noexcept { return length_; }
    explicit operator bool() const noexcept { return bool(region_); }
    void reset() noexcept;
    Status slice(std::size_t offset, std::size_t length, Access access, MemoryView& out) const;
private:
    friend class MemorySpace;
    std::shared_ptr<detail::Region> region_;
    std::size_t offset_ = 0, length_ = 0;
    Access access_ = Access::Read;
};

class MemorySpace {
public:
    MemorySpace();
    ~MemorySpace();
    MemorySpace(const MemorySpace&) = delete;
    MemorySpace& operator=(const MemorySpace&) = delete;
    // lifetime.get() must be bytes.data(), and its deleter must retain/release the
    // real backing. This records host ownership; it does not change VM protection.
    Status register_region(Owner owner, Address address, std::span<std::byte> bytes,
                           Access access, std::shared_ptr<void> lifetime,
                           MemoryProvenance provenance = {});
    Status allocate(Owner owner, Address address, std::size_t size, Access access);
    Status pin(Owner owner, Address address, std::size_t length, Access access,
               MemoryView& out) const;
    // Configure once before registrations. Only pool/span mapping may resolve
    // a complete registered miss; transfer attach keeps pin() registered-only.
    Status configure_provider(const botw_audren_memory_provider& provider,
        void* failure_context, void (*failed)(void*, Status));
    Status pin_pool(Owner owner, Address address, std::size_t length, Access access,
                    MemoryView& out) const;
    Status retry_provider_cleanup();
    Status provider_idle() const;
    Status read(Owner owner, Address address, std::span<std::byte> out) const;
    Status write(Owner owner, Address address, std::span<const std::byte> bytes) const;
    Status unregister_region(Owner owner, Address address);
private:
    std::shared_ptr<detail::MemoryState> state_;
};

class TransferView {
public:
    std::span<const std::byte> readable() const noexcept { return view_.readable(); }
    std::span<std::byte> writable() const noexcept { return view_.writable(); }
    std::size_t size() const noexcept { return view_.size(); }
    void reset() noexcept;
private:
    friend class TransferPool;
    std::shared_ptr<detail::PoolState> pool_;
    MemoryView view_;
};

class TransferPool {
public:
    static constexpr std::size_t Alignment = 4096;
    TransferPool();
    ~TransferPool();
    TransferPool(const TransferPool&) = delete;
    TransferPool& operator=(const TransferPool&) = delete;
    Status attach(MemorySpace& memory, Owner owner, Address address, std::size_t size,
                  Access access = Access::ReadWrite);
    Status view(std::size_t offset, std::size_t length, Access access, TransferView& out) const;
    Status detach();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Manual-reset or auto-reset host event. close wakes every waiter with Closed;
// it cannot be mistaken for a successful signal. No guest/kernel event stub.
class HostEvent {
public:
    explicit HostEvent(bool auto_reset = false);
    ~HostEvent();
    HostEvent(const HostEvent&) = delete;
    HostEvent& operator=(const HostEvent&) = delete;
    Status signal();
    Status clear();
    Status wait_for(std::chrono::nanoseconds timeout);
    void close();
private:
    std::shared_ptr<detail::EventState> state_;
};

class MonotonicClock {
public:
    // On error out is unchanged, and no synthetic timestamp is returned.
    static Status now_ns(std::uint64_t& out) noexcept;
};

struct PlatformOptions {
    bool dump_audio_commands = false;
    bool inspect_guest_stack = false;
};
// Must precede renderer construction. Diagnostic guest inspection is unsupported.
Status validate_platform_options(const PlatformOptions& options) noexcept;

} // namespace botw::audio
