// SPDX-License-Identifier: GPL-3.0-or-later
#include "platform.h"

#include <algorithm>
#include <atomic>
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
    MemoryProvenance provenance;
};
struct ProviderBinding {
    botw_audren_memory_provider port{};
    void* failure_context = nullptr;
    void (*failed)(void*, Status) = nullptr;
    std::atomic<bool> failed_once{false};
    ~ProviderBinding() { if (port.release) port.release(port.context); }
};
struct ProviderReceipt { std::atomic<Status> result{Status::Ok}; };
struct ProviderRecord {
    enum class Stage { Prospective, Live, Releasing, Quarantined };
    Owner owner;
    Address address;
    std::size_t size;
    Access access;
    botw_audren_memory_lease lease{};
    Stage stage = Stage::Prospective;
    bool listed = false, poisoned = false, region_done = false, attempted = false;
    Status failure = Status::Ok;
    std::weak_ptr<Region> region;
    std::shared_ptr<ProviderBinding> provider;
    std::shared_ptr<ProviderReceipt> receipt;
    ProviderRecord* next = nullptr;
};
struct MemoryState {
    std::mutex mutex;
    std::vector<std::shared_ptr<Region>> regions;
    std::shared_ptr<ProviderBinding> provider;
    ProviderRecord* borrowed = nullptr;
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
static bool source_overlaps(MemoryProvenance a, std::size_t a_size,
                             MemoryProvenance b, std::size_t b_size) noexcept {
    return a.backing_id && a.backing_id == b.backing_id &&
        (a.backing_offset <= b.backing_offset ? b.backing_offset-a.backing_offset < a_size :
                                               a.backing_offset-b.backing_offset < b_size);
}
static bool valid_provenance(MemoryProvenance source, std::size_t size) noexcept {
    return source.backing_id ? size <= UINT64_MAX - source.backing_offset : source.backing_offset == 0;
}
static Status callback_status(int32_t status) noexcept {
    return status >= 0 && status <= int(Status::Backpressure) ? Status(status) : Status::SystemError;
}
static void provider_failed(const std::shared_ptr<detail::ProviderBinding>& provider, Status status) noexcept {
    if (!provider->failed_once.exchange(true)) {
        try { provider->failed(provider->failure_context, status); } catch (...) {}
    }
}
static void link_record(detail::MemoryState& state, detail::ProviderRecord* record) {
    if (!record->listed) { record->next = state.borrowed; state.borrowed = record; record->listed = true; }
}
static void unlink_record(detail::MemoryState& state, detail::ProviderRecord* record) {
    auto** entry = &state.borrowed;
    while (*entry && *entry != record) entry = &(*entry)->next;
    if (*entry) *entry = record->next;
    record->listed = false;
}
// The record itself is allocated before acquire. Quarantine and last-view
// cleanup therefore cannot lose a real backing owner because malloc failed.
static Status release_record(const std::shared_ptr<detail::MemoryState>& state,
                              detail::ProviderRecord* record) noexcept {
    const auto provider = record->provider;
    const auto receipt = record->receipt;
    bool initial_attempt;
    {
        std::lock_guard lock(state->mutex);
        if (record->poisoned) {
            record->stage = detail::ProviderRecord::Stage::Quarantined;
            return record->failure;
        }
        record->stage = detail::ProviderRecord::Stage::Releasing;
        initial_attempt = !record->attempted; record->attempted = true;
        link_record(*state, record);
    }
    const auto before = record->lease.owner;
    Status status;
    char error[160]{};
    try { status = callback_status(provider->port.release_lease(provider->port.context,
                                                              &record->lease.owner, error, sizeof error)); }
    catch (...) { status = Status::SystemError; }
    // A broken release contract leaves uncertain ownership. Keep the metadata
    // quarantined permanently rather than retrying a possibly freed pointer.
    const bool violated = (status == Status::Ok && record->lease.owner) ||
        (status != Status::Ok && record->lease.owner != before);
    if (violated) status = Status::SystemError;
    {
        std::lock_guard lock(state->mutex);
        if (status == Status::Ok) unlink_record(*state, record);
        else {
            record->stage = detail::ProviderRecord::Stage::Quarantined;
            record->failure = status; record->poisoned = violated;
        }
    }
    // A concurrent successful retry cannot erase the failure observed by the
    // original caller, nor race its receipt read after the deleter returns.
    if (initial_attempt) receipt->result.store(status);
    if (status == Status::Ok) delete record;
    else provider_failed(provider, status);
    return status;
}

struct PinCandidate { std::shared_ptr<detail::Region> region; std::size_t offset = 0; };
static Status registered_candidate(detail::MemoryState& state, Owner owner, Address address,
                                    std::size_t size, Access access, PinCandidate& out) {
    for (const auto& region : state.regions) {
        if (!overlaps(address, size, region->address, region->bytes.size())) continue;
        if (address < region->address) return Status::OutOfBounds;
        if (owner != region->owner) return Status::OwnerMismatch;
        if (!permits(region->access, access)) return Status::PermissionDenied;
        const auto offset = std::size_t(address - region->address);
        if (size > region->bytes.size() - offset) return Status::OutOfBounds;
        out.region = region; out.offset = offset;
        return Status::Ok;
    }
    return Status::NotFound;
}
static Status pool_candidate(detail::MemoryState& state, Owner owner, Address address,
                              std::size_t size, Access access, PinCandidate& out) {
    for (auto* record = state.borrowed; record; record = record->next)
        if (record->stage != detail::ProviderRecord::Stage::Live &&
            overlaps(address,size,record->address,record->size)) return Status::Busy;
    auto status = registered_candidate(state, owner, address, size, access, out);
    if (status != Status::NotFound) return status;
    for (auto* record = state.borrowed; record; record = record->next) {
        if (!overlaps(address, size, record->address, record->size)) continue;
        if (record->stage != detail::ProviderRecord::Stage::Live) return Status::Busy;
        if (address < record->address) return Status::OutOfBounds;
        if (owner != record->owner) return Status::OwnerMismatch;
        if (!permits(record->access, access)) return Status::PermissionDenied;
        const auto offset = std::size_t(address - record->address);
        if (size > record->size - offset) return Status::OutOfBounds;
        out.region = record->region.lock(); out.offset = offset;
        return out.region ? Status::Ok : Status::Busy;
    }
    return Status::NotFound;
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

MemorySpace::MemorySpace() : state_(std::make_shared<detail::MemoryState>()) {}
MemorySpace::~MemorySpace() = default;
Status MemorySpace::register_region(Owner owner, Address address, std::span<std::byte> bytes,
                                    Access access, std::shared_ptr<void> lifetime, MemoryProvenance provenance) {
    const auto physical = reinterpret_cast<std::uintptr_t>(bytes.data());
    if (!owner || !valid_range(address, bytes.size()) || !valid_access(access) ||
        !bytes.data() || !lifetime || lifetime.get() != bytes.data() ||
        !valid_range(physical, bytes.size()) || !valid_provenance(provenance, bytes.size())) return Status::InvalidArgument;
    // A backing deleter is external owner code. Construct/release the prospective
    // region outside the registry lock, including on insertion failure.
    std::shared_ptr<detail::Region> prospective;
    try {
        prospective = std::make_shared<detail::Region>(owner, address, bytes, access,
                                                       std::move(lifetime), provenance);
    } catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    std::lock_guard lock(state_->mutex);
    if (state_->provider && !provenance.backing_id) return Status::InvalidArgument;
    for (const auto& region : state_->regions) {
        if (overlaps(address, bytes.size(), region->address, region->bytes.size()) ||
            overlaps(physical, bytes.size(), reinterpret_cast<std::uintptr_t>(region->bytes.data()),
                     region->bytes.size()) || source_overlaps(provenance, bytes.size(),
                     region->provenance, region->bytes.size())) return Status::Overlap;
    }
    for (auto* record = state_->borrowed; record; record = record->next)
        if (overlaps(address, bytes.size(), record->address, record->size) ||
            (record->lease.data && valid_range(reinterpret_cast<Address>(record->lease.data), record->size) &&
             overlaps(physical, bytes.size(), reinterpret_cast<Address>(record->lease.data), record->size)) ||
            source_overlaps(provenance, bytes.size(), {record->lease.backing_id,record->lease.backing_offset},record->size))
            return Status::Overlap;
    try { state_->regions.push_back(prospective); }
    catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    return Status::Ok;
}
Status MemorySpace::configure_provider(const botw_audren_memory_provider& port, void* context,
                                       void (*failed)(void*, Status)) {
    if (!port.abi_version && !port.struct_size && !port.context && !port.retain && !port.release &&
        !port.acquire && !port.release_lease) return Status::Ok;
    if (port.abi_version != BOTW_AUDREN_MEMORY_PROVIDER_ABI || port.struct_size != sizeof(port) ||
        !port.retain || !port.release || !port.acquire || !port.release_lease || !failed)
        return Status::InvalidArgument;
    std::shared_ptr<detail::ProviderBinding> binding;
    try { binding = std::make_shared<detail::ProviderBinding>(); }
    catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    try { if (port.retain(port.context)) return Status::SystemError; }
    catch (...) { return Status::SystemError; }
    binding->port = port; binding->failure_context = context; binding->failed = failed;
    {
        std::lock_guard lock(state_->mutex);
        if (state_->provider || !state_->regions.empty() || state_->borrowed) return Status::Busy;
        state_->provider = binding;
    }
    return Status::Ok;
}
Status MemorySpace::pin_pool(Owner owner, Address address, std::size_t length, Access access,
                             MemoryView& out) const {
    if (!owner || !valid_range(address,length) || !valid_access(access)) return Status::InvalidArgument;
    PinCandidate winner;
    std::shared_ptr<detail::ProviderBinding> provider;
    Status status;
    {
        std::lock_guard lock(state_->mutex);
        status = pool_candidate(*state_,owner,address,length,access,winner);
        if (status == Status::NotFound) provider = state_->provider;
    }
    auto publish_view = [&] {
        MemoryView result; result.region_ = std::move(winner.region); result.offset_ = winner.offset;
        result.length_ = length; result.access_ = access; out = std::move(result);
    };
    if (status != Status::NotFound) { if (status == Status::Ok) publish_view(); return status; }
    if (!provider) return Status::NotFound;
    std::shared_ptr<detail::Region> prospective;
    detail::ProviderRecord* record = nullptr;
    std::shared_ptr<detail::ProviderReceipt> receipt;
    try {
        auto pending = std::make_unique<detail::ProviderRecord>();
        pending->owner=owner; pending->address=address; pending->size=length; pending->access=access;
        pending->provider=provider; pending->receipt=std::make_shared<detail::ProviderReceipt>();
        auto region = std::make_unique<detail::Region>();
        receipt=pending->receipt; record=pending.release();
        prospective = {region.release(), [state=state_,record](detail::Region* pointer) {
            delete pointer;
            bool empty;
            {
                std::lock_guard lock(state->mutex);
                record->region_done = true;
                if (record->stage == detail::ProviderRecord::Stage::Quarantined) return;
                empty = !record->lease.owner && !record->listed;
                if (!empty) {record->stage=detail::ProviderRecord::Stage::Releasing;link_record(*state,record);}
            }
            if (empty) {delete record;return;}
            (void)release_record(state,record);
        }};
    } catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    char error[160]{};
    try { status=callback_status(provider->port.acquire(provider->port.context,owner,address,length,
                                          unsigned(access),&record->lease,error,sizeof error)); }
    catch (...) { status=Status::SystemError; }
    if (status != Status::Ok && record->lease.owner) {
        {
            std::lock_guard lock(state_->mutex);
            record->stage=detail::ProviderRecord::Stage::Quarantined;
            record->failure=Status::SystemError;link_record(*state_,record);
        }
        provider_failed(provider,Status::SystemError);
        return Status::SystemError;
    }
    auto discard = [&](Status primary) {
        prospective.reset(); const auto cleanup=receipt->result.load();
        return cleanup == Status::Ok ? primary : cleanup;
    };
    if (status != Status::Ok) return discard(status);
    const auto& lease=record->lease;
    if (!lease.owner || !lease.data || lease.size != length || !lease.backing_id ||
        !valid_range(reinterpret_cast<Address>(lease.data),length) ||
        !valid_provenance({lease.backing_id,lease.backing_offset},length) ||
        !valid_access(Access(lease.access)) || !permits(Access(lease.access),access) ||
        lease.access != unsigned(access)) return discard(Status::InvalidArgument);
    prospective->owner=owner;prospective->address=address;
    prospective->bytes={static_cast<std::byte*>(lease.data),length};prospective->access=access;
    prospective->provenance={lease.backing_id,lease.backing_offset};record->region=prospective;
    {
        std::lock_guard lock(state_->mutex);
        status=pool_candidate(*state_,owner,address,length,access,winner);
        if (status == Status::NotFound) {
            for (const auto& region : state_->regions)
                if (overlaps(reinterpret_cast<Address>(lease.data),length,reinterpret_cast<Address>(region->bytes.data()),region->bytes.size()) ||
                    source_overlaps(prospective->provenance,length,region->provenance,region->bytes.size())) { status=Status::Overlap;break; }
            if (status == Status::NotFound) for (auto* old=state_->borrowed;old;old=old->next)
                if ((old->lease.data && valid_range(reinterpret_cast<Address>(old->lease.data),old->size) &&
                     overlaps(reinterpret_cast<Address>(lease.data),length,reinterpret_cast<Address>(old->lease.data),old->size)) ||
                     source_overlaps(prospective->provenance,length,{old->lease.backing_id,old->lease.backing_offset},old->size)) {status=Status::Overlap;break;}
            if (status == Status::NotFound) {
                record->stage=detail::ProviderRecord::Stage::Live;link_record(*state_,record);
                winner.region=prospective;winner.offset=0;status=Status::Ok;
            }
        }
    }
    const bool own_winner=winner.region==prospective;
    if (!own_winner) status=discard(status);
    if (status == Status::Ok) publish_view();
    return status;
}
Status MemorySpace::retry_provider_cleanup() {
    std::vector<detail::ProviderRecord*> pending;
    {
        std::lock_guard lock(state_->mutex);
        try {
            for (auto* record=state_->borrowed;record;record=record->next)
                if (record->stage==detail::ProviderRecord::Stage::Quarantined && record->region_done) pending.push_back(record);
        } catch(const std::bad_alloc&) {return Status::OutOfMemory;}
        for(auto* record:pending)record->stage=detail::ProviderRecord::Stage::Releasing;
    }
    Status first=Status::Ok;
    for(auto* record:pending){
        const auto status=release_record(state_,record);
        if(status!=Status::Ok&&first==Status::Ok)first=status;
    }
    // Another thread may already be performing the genuine last-owner release,
    // or a quarantined view may still be exiting its destructor. Neither is an
    // ACK that the alias/pin is gone. Live, still-mapped pools are independent.
    {
        std::lock_guard lock(state_->mutex);
        for(auto* record=state_->borrowed;record;record=record->next)
            if(record->stage!=detail::ProviderRecord::Stage::Live && first==Status::Ok)
                first=Status::Busy;
    }
    return first;
}
Status MemorySpace::provider_idle() const {
    std::lock_guard lock(state_->mutex);return state_->borrowed?Status::Busy:Status::Ok;
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
