// SPDX-License-Identifier: GPL-3.0-or-later
#include "platform.h"
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <future>
#include <limits>
#include <string_view>
#include <thread>
using namespace botw::audio;
using namespace std::chrono_literals;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned platform behavior failed %d: %s\n", __LINE__, #x); return 1; } } while (0)

static int memory_case() {
    MemorySpace memory;
    CHECK(memory.allocate(11, 0x10000, 8192, Access::ReadWrite) == Status::Ok);
    std::array<std::byte, 4> values{std::byte{1}, std::byte{2}, std::byte{3}, std::byte{4}}, out{};
    CHECK(memory.write(11, 0x11ffc, values) == Status::Ok);
    CHECK(memory.read(11, 0x11ffc, out) == Status::Ok && out == values);
    CHECK(memory.read(12, 0x11ffc, out) == Status::OwnerMismatch);
    CHECK(memory.write(11, 0x11ffd, values) == Status::OutOfBounds);
    CHECK(memory.allocate(11, 0x11000, 4096, Access::ReadWrite) == Status::Overlap);
    CHECK(memory.allocate(0, 0x20000, 4096, Access::ReadWrite) == Status::InvalidArgument);
    CHECK(memory.allocate(11, std::numeric_limits<Address>::max() - 2, 4, Access::Read) == Status::InvalidArgument);
    MemoryView read;
    CHECK(memory.pin(11, 0x10000, 4096, Access::Read, read) == Status::Ok);
    CHECK(read.readable().size() == 4096 && read.writable().empty());
    MemoryView denied;
    CHECK(read.slice(0, 1, Access::Write, denied) == Status::PermissionDenied);
    CHECK(read.slice(4095, 2, Access::Read, denied) == Status::OutOfBounds);
    CHECK(memory.unregister_region(11, 0x10000) == Status::Busy);
    read.reset();
    CHECK(memory.unregister_region(12, 0x10000) == Status::OwnerMismatch);
    CHECK(memory.unregister_region(11, 0x10000) == Status::Ok);
    CHECK(memory.pin(11, 0x10000, 1, Access::Read, denied) == Status::NotFound);
    CHECK(memory.allocate(11, 0x30000, 4096, Access::Read) == Status::Ok);
    CHECK(memory.write(11, 0x30000, values) == Status::PermissionDenied);
    return 0;
}

static int lifetime_case() {
    std::atomic<int> deletes{0}; MemoryView surviving;
    {
        MemorySpace memory;
        auto bytes = new std::byte[64]{};
        std::shared_ptr<void> backing(bytes, [&](void* p) { ++deletes; delete[] static_cast<std::byte*>(p); });
        CHECK(memory.register_region(7, 0x4000, {bytes, 64}, Access::ReadWrite, backing) == Status::Ok);
        CHECK(memory.register_region(8, 0x8000, {bytes, 64}, Access::ReadWrite, backing) == Status::Overlap);
        backing.reset();
        CHECK(memory.pin(7, 0x403f, 1, Access::Write, surviving) == Status::Ok);
        CHECK(memory.unregister_region(7, 0x4000) == Status::Busy);
    }
    CHECK(deletes == 0 && surviving.writable().size() == 1);
    surviving.writable()[0] = std::byte{99};
    surviving.reset(); CHECK(deletes == 1);
    // Owner deleters may release other registrations. No registry mutex is held
    // when the last backing is released by unregister.
    MemorySpace memory;
    CHECK(memory.allocate(7, 0xa000, 64, Access::Read) == Status::Ok);
    auto bytes = new std::byte[64]{};
    Status released = Status::SystemError;
    std::shared_ptr<void> reentrant(bytes, [&](void* p) {
        released = memory.unregister_region(7, 0xa000);
        delete[] static_cast<std::byte*>(p);
    });
    CHECK(memory.register_region(7, 0xb000, {bytes, 64}, Access::Read, reentrant) == Status::Ok);
    reentrant.reset();
    CHECK(memory.unregister_region(7, 0xb000) == Status::Ok && released == Status::Ok);
    return 0;
}

static int pool_case() {
    MemorySpace memory; TransferPool pool; TransferView loan;
    CHECK(memory.allocate(21, 0x20000, 8192, Access::ReadWrite) == Status::Ok);
    CHECK(pool.attach(memory, 21, 0x20001, 4096) == Status::InvalidArgument);
    CHECK(pool.attach(memory, 22, 0x20000, 4096) == Status::OwnerMismatch);
    CHECK(pool.attach(memory, 21, 0x20000, 12288) == Status::OutOfBounds);
    CHECK(pool.attach(memory, 21, 0x20000, 8192) == Status::Ok);
    CHECK(pool.attach(memory, 21, 0x20000, 8192) == Status::AlreadyAttached);
    CHECK(memory.unregister_region(21, 0x20000) == Status::Busy);
    CHECK(pool.view(8188, 4, Access::Write, loan) == Status::Ok);
    loan.writable()[3] = std::byte{77};
    CHECK(pool.detach() == Status::Busy);
    TransferView invalid;
    CHECK(pool.view(8189, 4, Access::Read, invalid) == Status::OutOfBounds);
    loan.reset(); CHECK(pool.detach() == Status::Ok);
    CHECK(pool.view(0, 4, Access::Read, loan) == Status::NotAttached);
    CHECK(memory.unregister_region(21, 0x20000) == Status::Ok);
    CHECK(pool.detach() == Status::NotAttached);
    // A pool must validate host backing alignment as well as its virtual address.
    void* allocation = nullptr; CHECK(!posix_memalign(&allocation, 4096, 12288));
    auto bytes = static_cast<std::byte*>(allocation) + 1;
    std::shared_ptr<void> backing(bytes, [allocation](void*) { std::free(allocation); });
    CHECK(memory.register_region(21, 0x30000, {bytes, 8192}, Access::ReadWrite, backing) == Status::Ok);
    // The fixture owns an explicitly misaligned host view.
    Address start = 0x30000;
    CHECK(reinterpret_cast<std::uintptr_t>(bytes) % 4096 != 0);
    CHECK(pool.attach(memory, 21, start, 4096) == Status::InvalidArgument);
    return 0;
}

static int event_case() {
    HostEvent manual;
    CHECK(manual.wait_for(0ns) == Status::TimedOut);
    auto wait = std::async(std::launch::async, [&] { return manual.wait_for(2s); });
    CHECK(wait.wait_for(5ms) == std::future_status::timeout);
    CHECK(manual.signal() == Status::Ok && wait.get() == Status::Ok);
    CHECK(manual.wait_for(0ns) == Status::Ok && manual.wait_for(0ns) == Status::Ok);
    CHECK(manual.clear() == Status::Ok && manual.wait_for(1ms) == Status::TimedOut);
    HostEvent automatic(true);
    CHECK(automatic.signal() == Status::Ok && automatic.wait_for(0ns) == Status::Ok);
    CHECK(automatic.wait_for(1ms) == Status::TimedOut);
    auto closing = std::async(std::launch::async, [&] { return automatic.wait_for(2s); });
    CHECK(closing.wait_for(5ms) == std::future_status::timeout);
    automatic.close(); CHECK(closing.get() == Status::Closed);
    CHECK(automatic.signal() == Status::Closed && automatic.clear() == Status::Closed);
    CHECK(automatic.wait_for(0ns) == Status::Closed);
    return 0;
}

static int clock_case() {
    std::uint64_t before = 0, after = 0;
    CHECK(MonotonicClock::now_ns(before) == Status::Ok && before > 0);
    std::this_thread::sleep_for(2ms);
    CHECK(MonotonicClock::now_ns(after) == Status::Ok && after > before);
    CHECK(validate_platform_options({}) == Status::Ok);
    CHECK(validate_platform_options({true, false}) == Status::Unsupported);
    CHECK(validate_platform_options({false, true}) == Status::Unsupported);
    return 0;
}

int main(int argc, char** argv) {
    CHECK(argc == 2);
    std::string_view name{argv[1]};
    if (name == "memory") return memory_case();
    if (name == "lifetime") return lifetime_case();
    if (name == "pool") return pool_case();
    if (name == "event") return event_case();
    if (name == "clock") return clock_case();
    return 64;
}
