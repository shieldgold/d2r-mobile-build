// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "platform.h"
#include <mutex>
#include <stdexcept>
#include <vector>

namespace botw::audio {
class PlatformError : public std::runtime_error {
public:
    explicit PlatformError(Status status) : std::runtime_error(status_name(status)), status(status) {}
    Status status;
};
inline void require(Status status) { if (status != Status::Ok) throw PlatformError(status); }
namespace detail { struct RendererMapping; }

class MappedView {
public:
    std::span<const std::byte> readable() const noexcept;
    std::span<std::byte> writable() const noexcept;
    void reset() noexcept;
private:
    friend class RendererMemory;
    std::shared_ptr<detail::RendererMapping> mapping_;
    std::size_t offset_ = 0, length_ = 0;
    Access access_ = Access::Read;
};
struct DspMapping { Address address; std::size_t size; };

// Native renderer memory port, not an emulator Process/System facade. A DSP
// address is valid only while its explicitly mapped backing remains pinned.
class RendererMemory {
public:
    RendererMemory(MemorySpace& memory, Owner owner);
    ~RendererMemory();
    RendererMemory(const RendererMemory&) = delete;
    RendererMemory& operator=(const RendererMemory&) = delete;
    Status map_pool(const void* identity, Address address, std::size_t size, DspMapping& out);
    Status map_span(const void* identity, Address address, std::size_t size, DspMapping& out);
    Status map_transfer(const void* identity, const TransferView& transfer, DspMapping& out);
    Status unmap(const void* identity);
    Status view(Address dsp_address, std::size_t size, Access access, MappedView& out) const;
    Status lease_all(std::vector<MappedView>& out) const;

    // Original command algorithms use these calls. Bounds/missing maps throw a
    // typed failure, caught at the normal session/worker boundary; never silent.
    void ReadBlockUnsafe(Address address, void* out, std::size_t size) const;
    void WriteBlockUnsafe(Address address, const void* data, std::size_t size) const;
    void Write32(Address address, std::uint32_t value) const;
private:
    Status map_impl(const void* identity, Address address, std::size_t size,
                    DspMapping& out, bool page_aligned);
    MemorySpace& memory_;
    Owner owner_;
    mutable std::mutex mutex_;
    std::vector<std::shared_ptr<detail::RendererMapping>> mappings_;
};

template <typename T> class ReadSamples {
public:
    ReadSamples(RendererMemory& memory, Address address, std::size_t count) {
        if (!count || count > SIZE_MAX / sizeof(T) || address % alignof(T))
            throw PlatformError(Status::InvalidArgument);
        require(memory.view(address, count * sizeof(T), Access::Read, view_));
        samples_ = {reinterpret_cast<const T*>(view_.readable().data()), count};
    }
    const T* data() const noexcept { return samples_.data(); }
    const T& operator[](std::size_t index) const {
        if (index >= samples_.size()) throw PlatformError(Status::OutOfBounds);
        return samples_[index];
    }
private:
    MappedView view_;
    std::span<const T> samples_;
};
} // namespace botw::audio
