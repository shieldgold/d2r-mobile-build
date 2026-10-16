// SPDX-License-Identifier: GPL-3.0-or-later
#include "platform.h"

#include <algorithm>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <mutex>
#include <new>
#include <time.h>
#include <vector>

namespace botw::audio {
namespace detail {
struct Region {
    Owner owner;
    Address address;
    std::span<std::byte> bytes;
    Access access;
    std::shared_ptr<void> lifetime;
};
struct MemoryState {
    std::mutex mutex;
    std::vector<std::shared_ptr<Region>> regions;
};
struct PoolState { MemoryView pin; };
struct EventState {
    explicit EventState(bool automatic) : auto_reset(automatic) {}
    std::mutex mutex;
    std::condition_variable condition;
    bool auto_reset, signaled = false, closed = false;
};
} // namespace detail

static bool valid_access(Access access) noexcept {
    const auto bits = static_cast<unsigned>(access);
    return bits > 0 && bits <= static_cast<unsigned>(Access::ReadWrite);
}
static bool permits(Access available, Access requested) noexcept {
    return valid_access(requested) &&
        (static_cast<unsigned>(available) & static_cast<unsigned>(requested)) ==
            static_cast<unsigned>(requested);
}
static bool valid_range(Address address, std::size_t size) noexcept {
    return address != 0 && size != 0 && size <= std::numeric_limits<Address>::max() - address;
}
static bool overlaps(Address a, std::size_t a_size, Address b, std::size_t b_size) noexcept {
    return a < b + b_size && b < a + a_size;
}

const char* status_name(Status status) noexcept {
    switch (status) {
#define STATUS_NAME(name) case Status::name: return #name
    STATUS_NAME(Ok); STATUS_NAME(InvalidArgument); STATUS_NAME(NotFound);
    STATUS_NAME(OwnerMismatch); STATUS_NAME(PermissionDenied); STATUS_NAME(Overlap);
    STATUS_NAME(OutOfBounds); STATUS_NAME(Busy); STATUS_NAME(AlreadyAttached);
    STATUS_NAME(NotAttached); STATUS_NAME(Closed); STATUS_NAME(TimedOut);
    STATUS_NAME(OutOfMemory); STATUS_NAME(SystemError); STATUS_NAME(Unsupported);
    STATUS_NAME(DeviceError); STATUS_NAME(Backpressure);
#undef STATUS_NAME
    }
    return "UnknownStatus";
}

std::span<const std::byte> MemoryView::readable() const noexcept {
    if (!region_ || !permits(access_, Access::Read)) return {};
    return region_->bytes.subspan(offset_, length_);
}
std::span<std::byte> MemoryView::writable() const noexcept {
    if (!region_ || !permits(access_, Access::Write)) return {};
    return region_->bytes.subspan(offset_, length_);
}
void MemoryView::reset() noexcept {
    region_.reset(); offset_ = length_ = 0; access_ = Access::Read;
}
Status MemoryView::slice(std::size_t offset, std::size_t length, Access access,
                         MemoryView& out) const {
    if (!region_) return Status::NotAttached;
    if (!valid_access(access) || !length) return Status::InvalidArgument;
    if (!permits(access_, access)) return Status::PermissionDenied;
    if (offset > length_ || length > length_ - offset) return Status::OutOfBounds;
    MemoryView result;
    result.region_ = region_; result.offset_ = offset_ + offset;
    result.length_ = length; result.access_ = access;
    out = std::move(result);
    return Status::Ok;
}

MemorySpace::MemorySpace() : state_(std::make_unique<detail::MemoryState>()) {}
MemorySpace::~MemorySpace() = default;
Status MemorySpace::register_region(Owner owner, Address address, std::span<std::byte> bytes,
                                    Access access, std::shared_ptr<void> lifetime) {
    const auto physical = reinterpret_cast<std::uintptr_t>(bytes.data());
    if (!owner || !valid_range(address, bytes.size()) || !valid_access(access) ||
        !bytes.data() || !lifetime || lifetime.get() != bytes.data() ||
        !valid_range(physical, bytes.size())) return Status::InvalidArgument;
    // A backing deleter is external owner code. Construct/release the prospective
    // region outside the registry lock, including on insertion failure.
    std::shared_ptr<detail::Region> prospective;
    try {
        prospective = std::make_shared<detail::Region>(owner, address, bytes, access,
                                                       std::move(lifetime));
    } catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    std::lock_guard lock(state_->mutex);
    for (const auto& region : state_->regions) {
        if (overlaps(address, bytes.size(), region->address, region->bytes.size()) ||
            overlaps(physical, bytes.size(), reinterpret_cast<std::uintptr_t>(region->bytes.data()),
                     region->bytes.size())) return Status::Overlap;
    }
    try { state_->regions.push_back(prospective); }
    catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    return Status::Ok;
}
Status MemorySpace::allocate(Owner owner, Address address, std::size_t size, Access access) {
    if (!owner || !valid_range(address, size) || !valid_access(access)) return Status::InvalidArgument;
    void* allocation = nullptr;
    if (posix_memalign(&allocation, TransferPool::Alignment, size)) return Status::OutOfMemory;
    std::memset(allocation, 0, size);
    try {
        std::shared_ptr<void> lifetime(allocation, std::free);
        return register_region(owner, address, {static_cast<std::byte*>(allocation), size},
                               access, std::move(lifetime));
    } catch (const std::bad_alloc&) {
        // shared_ptr calls its supplied deleter if control-block allocation fails.
        return Status::OutOfMemory;
    }
}
Status MemorySpace::pin(Owner owner, Address address, std::size_t length, Access access,
                        MemoryView& out) const {
    if (!owner || !valid_range(address, length) || !valid_access(access)) return Status::InvalidArgument;
    MemoryView result;
    {
        std::lock_guard lock(state_->mutex);
        for (const auto& region : state_->regions) {
            if (address < region->address || address - region->address >= region->bytes.size()) continue;
            if (owner != region->owner) return Status::OwnerMismatch;
            if (!permits(region->access, access)) return Status::PermissionDenied;
            const auto offset = static_cast<std::size_t>(address - region->address);
            if (length > region->bytes.size() - offset) return Status::OutOfBounds;
            result.region_ = region; result.offset_ = offset;
            result.length_ = length; result.access_ = access;
            break;
        }
    }
    if (!result) return Status::NotFound;
    out = std::move(result);
    return Status::Ok;
}
Status MemorySpace::read(Owner owner, Address address, std::span<std::byte> out) const {
    if (!out.data()) return Status::InvalidArgument;
    MemoryView view;
    auto status = pin(owner, address, out.size(), Access::Read, view);
    if (status == Status::Ok) std::memmove(out.data(), view.readable().data(), out.size());
    return status;
}
Status MemorySpace::write(Owner owner, Address address, std::span<const std::byte> bytes) const {
    if (!bytes.data()) return Status::InvalidArgument;
    MemoryView view;
    auto status = pin(owner, address, bytes.size(), Access::Write, view);
    if (status == Status::Ok) std::memmove(view.writable().data(), bytes.data(), bytes.size());
    return status;
}
Status MemorySpace::unregister_region(Owner owner, Address address) {
    std::shared_ptr<detail::Region> retired;
    {
        std::lock_guard lock(state_->mutex);
        auto entry = std::find_if(state_->regions.begin(), state_->regions.end(),
                                 [address](const auto& region) { return region->address == address; });
        if (entry == state_->regions.end()) return Status::NotFound;
        if ((*entry)->owner != owner) return Status::OwnerMismatch;
        if (entry->use_count() != 1) return Status::Busy;
        retired = std::move(*entry);
        state_->regions.erase(entry);
    }
    return Status::Ok;
}

struct TransferPool::Impl {
    mutable std::mutex mutex;
    std::shared_ptr<detail::PoolState> pool;
};
TransferPool::TransferPool() : impl_(std::make_unique<Impl>()) {}
TransferPool::~TransferPool() = default;
Status TransferPool::attach(MemorySpace& memory, Owner owner, Address address, std::size_t size,
                            Access access) {
    if (!valid_range(address, size) || address % Alignment || size % Alignment)
        return Status::InvalidArgument;
    std::lock_guard lock(impl_->mutex);
    if (impl_->pool) return Status::AlreadyAttached;
    MemoryView pin;
    auto status = memory.pin(owner, address, size, access, pin);
    if (status != Status::Ok) return status;
    const auto* backing = pin.readable().empty() ? pin.writable().data() : pin.readable().data();
    if (reinterpret_cast<std::uintptr_t>(backing) % Alignment) return Status::InvalidArgument;
    try { impl_->pool = std::make_shared<detail::PoolState>(std::move(pin)); }
    catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    return Status::Ok;
}
Status TransferPool::view(std::size_t offset, std::size_t length, Access access, TransferView& out) const {
    TransferView result;
    {
        std::lock_guard lock(impl_->mutex);
        if (!impl_->pool) return Status::NotAttached;
        auto status = impl_->pool->pin.slice(offset, length, access, result.view_);
        if (status != Status::Ok) return status;
        result.pool_ = impl_->pool;
    }
    out = std::move(result);
    return Status::Ok;
}
Status TransferPool::detach() {
    std::shared_ptr<detail::PoolState> retired;
    {
        std::lock_guard lock(impl_->mutex);
        if (!impl_->pool) return Status::NotAttached;
        if (impl_->pool.use_count() != 1) return Status::Busy;
        retired = std::move(impl_->pool);
    }
    return Status::Ok;
}
void TransferView::reset() noexcept { view_.reset(); pool_.reset(); }

HostEvent::HostEvent(bool auto_reset) : state_(std::make_shared<detail::EventState>(auto_reset)) {}
HostEvent::~HostEvent() { close(); }
Status HostEvent::signal() {
    const auto state = state_;
    std::lock_guard lock(state->mutex);
    if (state->closed) return Status::Closed;
    state->signaled = true;
    if (state->auto_reset) state->condition.notify_one(); else state->condition.notify_all();
    return Status::Ok;
}
Status HostEvent::clear() {
    const auto state = state_;
    std::lock_guard lock(state->mutex);
    if (state->closed) return Status::Closed;
    state->signaled = false;
    return Status::Ok;
}
Status HostEvent::wait_for(std::chrono::nanoseconds timeout) {
    if (timeout.count() < 0) return Status::InvalidArgument;
    const auto state = state_;
    std::unique_lock lock(state->mutex);
    const auto ready = [&] { return state->closed || state->signaled; };
    if (timeout == std::chrono::nanoseconds::max()) {
        state->condition.wait(lock, ready);
    } else if (!ready()) {
        const auto now = std::chrono::steady_clock::now();
        if (timeout > std::chrono::steady_clock::time_point::max() - now)
            return Status::InvalidArgument;
        if (!state->condition.wait_until(lock, now + timeout, ready)) return Status::TimedOut;
    }
    if (state->closed) return Status::Closed;
    if (state->auto_reset) state->signaled = false;
    return Status::Ok;
}
void HostEvent::close() {
    const auto state = state_;
    std::lock_guard lock(state->mutex);
    state->closed = true; state->signaled = false;
    state->condition.notify_all();
}
Status MonotonicClock::now_ns(std::uint64_t& out) noexcept {
    timespec time{};
    if (clock_gettime(CLOCK_MONOTONIC, &time) || time.tv_sec < 0 || time.tv_nsec < 0 ||
        time.tv_nsec >= 1000000000) return Status::SystemError;
    constexpr std::uint64_t billion = 1000000000;
    const auto seconds = static_cast<std::uint64_t>(time.tv_sec);
    if (seconds > (std::numeric_limits<std::uint64_t>::max() - time.tv_nsec) / billion)
        return Status::SystemError;
    out = seconds * billion + time.tv_nsec;
    return Status::Ok;
}
Status validate_platform_options(const PlatformOptions& options) noexcept {
    return options.dump_audio_commands || options.inspect_guest_stack ? Status::Unsupported : Status::Ok;
}
} // namespace botw::audio
