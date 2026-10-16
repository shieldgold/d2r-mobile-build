// SPDX-License-Identifier: GPL-3.0-or-later
#include "renderer_memory.h"
#include <algorithm>
#include <cstring>
#include <limits>
#include <variant>

namespace botw::audio {
namespace detail {
struct RendererMapping {
    const void* identity;
    std::variant<MemoryView, TransferView> backing;
    std::span<const std::byte> readable() const {
        return std::visit([](const auto& view) { return view.readable(); }, backing);
    }
    std::span<std::byte> writable() const {
        return std::visit([](const auto& view) { return view.writable(); }, backing);
    }
};
} // namespace detail

static bool has_access(Access access, Access desired) {
    return (static_cast<unsigned>(access) & static_cast<unsigned>(desired)) ==
        static_cast<unsigned>(desired);
}
std::span<const std::byte> MappedView::readable() const noexcept {
    if (!mapping_ || !has_access(access_, Access::Read)) return {};
    return mapping_->readable().subspan(offset_, length_);
}
std::span<std::byte> MappedView::writable() const noexcept {
    if (!mapping_ || !has_access(access_, Access::Write)) return {};
    return mapping_->writable().subspan(offset_, length_);
}
void MappedView::reset() noexcept { mapping_.reset(); offset_ = length_ = 0; }

RendererMemory::RendererMemory(MemorySpace& memory, Owner owner) : memory_(memory), owner_(owner) {
    if (!owner) throw PlatformError(Status::InvalidArgument);
}
RendererMemory::~RendererMemory() = default;
Status RendererMemory::map_pool(const void* identity, Address address, std::size_t size, DspMapping& out) {
    return map_impl(identity, address, size, out, true);
}
Status RendererMemory::map_span(const void* identity, Address address, std::size_t size, DspMapping& out) {
    return map_impl(identity, address, size, out, false);
}
Status RendererMemory::map_impl(const void* identity, Address address, std::size_t size,
                                DspMapping& out, bool page_aligned) {
    if (!identity || !address || !size || (page_aligned && (address % TransferPool::Alignment ||
        size % TransferPool::Alignment))) return Status::InvalidArgument;
    MemoryView pin;
    auto status = memory_.pin_pool(owner_, address, size, Access::ReadWrite, pin);
    if (status != Status::Ok) return status;
    const auto dsp = reinterpret_cast<Address>(pin.writable().data());
    if (page_aligned && dsp % TransferPool::Alignment) return Status::InvalidArgument;
    std::shared_ptr<detail::RendererMapping> mapping;
    try { mapping = std::make_shared<detail::RendererMapping>(identity, std::move(pin)); }
    catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    {
        std::lock_guard lock(mutex_);
        const auto existing = std::find_if(mappings_.begin(), mappings_.end(),
                        [identity](const auto& entry) { return entry->identity == identity; });
        if (existing != mappings_.end()) {
            // Repeated force-buffer updates keep the same real pin. Replacing an
            // identity with different backing still requires explicit unmap.
            if (!page_aligned && (*existing)->readable().data() == mapping->readable().data() &&
                (*existing)->readable().size() == size) {
                out = {dsp, size};
                return Status::Ok;
            }
            return Status::Busy;
        }
        try { mappings_.push_back(mapping); }
        catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    }
    out = {dsp, size};
    return Status::Ok;
}
Status RendererMemory::map_transfer(const void* identity, const TransferView& transfer, DspMapping& out) {
    const auto dsp = reinterpret_cast<Address>(transfer.writable().data());
    if (!identity || !dsp || !transfer.size() || dsp % TransferPool::Alignment ||
        transfer.size() % TransferPool::Alignment || transfer.readable().size() != transfer.size() ||
        transfer.writable().size() != transfer.size()) return Status::InvalidArgument;
    std::shared_ptr<detail::RendererMapping> mapping;
    try { mapping = std::make_shared<detail::RendererMapping>(identity, transfer); }
    catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    {
        std::lock_guard lock(mutex_);
        if (std::any_of(mappings_.begin(), mappings_.end(),
                        [identity](const auto& entry) { return entry->identity == identity; })) return Status::Busy;
        try { mappings_.push_back(mapping); }
        catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    }
    out = {dsp, transfer.size()};
    return Status::Ok;
}
Status RendererMemory::unmap(const void* identity) {
    std::shared_ptr<detail::RendererMapping> retired;
    {
        std::lock_guard lock(mutex_);
        auto entry = std::find_if(mappings_.begin(), mappings_.end(),
                                 [identity](const auto& mapping) { return mapping->identity == identity; });
        if (entry == mappings_.end()) return Status::NotFound;
        if (entry->use_count() != 1) return Status::Busy;
        retired = std::move(*entry);
        mappings_.erase(entry);
    }
    return Status::Ok;
}
Status RendererMemory::view(Address dsp_address, std::size_t size, Access access, MappedView& out) const {
    if (!dsp_address || !size || size > std::numeric_limits<Address>::max() - dsp_address ||
        static_cast<unsigned>(access) < 1 || static_cast<unsigned>(access) > 3)
        return Status::InvalidArgument;
    MappedView result; bool starts_inside = false;
    {
        std::lock_guard lock(mutex_);
        for (const auto& mapping : mappings_) {
            const auto base = reinterpret_cast<Address>(mapping->readable().data());
            const auto length = mapping->readable().size();
            if (dsp_address < base || dsp_address - base >= length) continue;
            starts_inside = true;
            const auto offset = static_cast<std::size_t>(dsp_address - base);
            if (size > length - offset) continue;
            result.mapping_ = mapping; result.offset_ = offset; result.length_ = size; result.access_ = access;
            break;
        }
    }
    if (!result.mapping_) return starts_inside ? Status::OutOfBounds : Status::NotFound;
    out = std::move(result);
    return Status::Ok;
}
Status RendererMemory::lease_all(std::vector<MappedView>& out) const {
    std::vector<MappedView> result;
    {
        std::lock_guard lock(mutex_);
        try {
            result.reserve(mappings_.size());
            for (const auto& mapping : mappings_) {
                MappedView view;
                view.mapping_ = mapping; view.length_ = mapping->readable().size(); view.access_ = Access::ReadWrite;
                result.push_back(std::move(view));
            }
        } catch (const std::bad_alloc&) { return Status::OutOfMemory; }
    }
    out = std::move(result);
    return Status::Ok;
}
void RendererMemory::ReadBlockUnsafe(Address address, void* out, std::size_t size) const {
    if (!size) return;
    if (!out) throw PlatformError(Status::InvalidArgument);
    MappedView pin;
    require(view(address, size, Access::Read, pin));
    std::memmove(out, pin.readable().data(), size);
}
void RendererMemory::WriteBlockUnsafe(Address address, const void* data, std::size_t size) const {
    if (!size) return;
    if (!data) throw PlatformError(Status::InvalidArgument);
    MappedView pin;
    require(view(address, size, Access::Write, pin));
    std::memmove(pin.writable().data(), data, size);
}
void RendererMemory::Write32(Address address, std::uint32_t value) const {
    WriteBlockUnsafe(address, &value, sizeof value);
}
} // namespace botw::audio
