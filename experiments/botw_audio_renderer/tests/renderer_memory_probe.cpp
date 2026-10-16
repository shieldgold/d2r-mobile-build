// SPDX-License-Identifier: GPL-3.0-or-later
#include "renderer_memory.h"
#include <array>
#include <cstdio>
#include <limits>
using namespace botw::audio;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned renderer map behavior failed %d: %s\n", __LINE__, #x); return 1; } } while (0)
int main() {
    MemorySpace memory; RendererMemory renderer(memory, 51), foreign(memory, 52);
    CHECK(memory.allocate(51, 0x40000, 8192, Access::ReadWrite) == Status::Ok);
    int token = 1, other = 2; DspMapping mapping{};
    CHECK(foreign.map_pool(&token, 0x40000, 8192, mapping) == Status::OwnerMismatch);
    CHECK(renderer.map_pool(&token, 0x40001, 4096, mapping) == Status::InvalidArgument);
    CHECK(renderer.map_pool(&token, 0x40000, 8192, mapping) == Status::Ok);
    CHECK(mapping.address && mapping.address != 0x40000 && mapping.size == 8192);
    CHECK(renderer.map_pool(&token, 0x40000, 8192, mapping) == Status::Busy);
    renderer.Write32(mapping.address + 8192 - 4, 0x12345678);
    std::array<std::byte, 4> actual{};
    CHECK(memory.read(51, 0x40000 + 8192 - 4, actual) == Status::Ok);
    CHECK((actual == std::array<std::byte, 4>{std::byte{0x78}, std::byte{0x56}, std::byte{0x34}, std::byte{0x12}}));
    ReadSamples<std::uint32_t> sample(renderer, mapping.address + 8192 - 4, 1);
    CHECK(sample[0] == 0x12345678);
    bool failed = false;
    try { renderer.ReadBlockUnsafe(mapping.address + 8192 - 3, actual.data(), 4); }
    catch (const PlatformError& error) { failed = error.status == Status::OutOfBounds; }
    CHECK(failed);
    MappedView view;
    CHECK(renderer.view(std::numeric_limits<Address>::max() - 1, 4, Access::Read, view) == Status::InvalidArgument);
    std::vector<MappedView> command_pins;
    CHECK(renderer.lease_all(command_pins) == Status::Ok && command_pins.size() == 1);
    CHECK(renderer.unmap(&token) == Status::Busy);
    CHECK(memory.unregister_region(51, 0x40000) == Status::Busy);
    // TransferPool backing is shared by actual loans, never a success-only map.
    TransferPool transfer;
    CHECK(transfer.attach(memory, 51, 0x40000, 8192) == Status::Ok);
    TransferView loan;
    CHECK(transfer.view(0, 8192, Access::ReadWrite, loan) == Status::Ok);
    DspMapping internal{};
    CHECK(renderer.map_transfer(&other, loan, internal) == Status::Ok);
    loan.reset(); CHECK(transfer.detach() == Status::Busy);
    // A snapshot pins every map until the command-list consumer releases it.
    command_pins.clear();
    CHECK(renderer.unmap(&other) == Status::Ok);
    CHECK(transfer.detach() == Status::Ok);
    // sample still pins token; it is released at scope exit. A separate mapping
    // proves normal unmap really revokes address lookup and releases its backing.
    CHECK(memory.allocate(51, 0x50000, 4096, Access::ReadWrite) == Status::Ok);
    CHECK(renderer.map_pool(&other, 0x50000, 4096, internal) == Status::Ok);
    CHECK(renderer.unmap(&other) == Status::Ok);
    CHECK(renderer.view(internal.address, 4, Access::Read, view) == Status::NotFound);
    CHECK(memory.unregister_region(51, 0x50000) == Status::Ok);
    CHECK(renderer.map_span(&other, 0x40001, 3, internal) == Status::Ok);
    CHECK(renderer.map_span(&other, 0x40001, 3, internal) == Status::Ok);
    renderer.WriteBlockUnsafe(internal.address, actual.data(), 3);
    CHECK(renderer.unmap(&other) == Status::Ok);
    CHECK(renderer.map_span(&other, 0x41fff, 2, internal) == Status::OutOfBounds);
    return 0;
}
