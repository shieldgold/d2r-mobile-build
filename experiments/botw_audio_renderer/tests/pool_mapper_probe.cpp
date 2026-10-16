// SPDX-License-Identifier: GPL-3.0-or-later
#include "renderer_memory.h"
#include "audio_core/renderer/memory/address_info.h"
#include "audio_core/renderer/memory/pool_mapper.h"
#include <cstdio>
#include <limits>
using namespace botw::audio;
using namespace AudioCore::Renderer;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "owned Eden pool behavior failed %d: %s\n", __LINE__, #x); return 1; } } while (0)
int main() {
    MemorySpace memory; RendererMemory renderer(memory, 61);
    CHECK(memory.allocate(61, 0x90000, 8192, Access::ReadWrite) == Status::Ok);
    MemoryPoolInfo pool{MemoryPoolInfo::Location::CPU};
    PoolMapper mapper(&renderer, {&pool, 1}, 1, false);
    MemoryPoolInfo::InParameter input{}; MemoryPoolInfo::OutStatus output{};
    input.address = 0x90000; input.size = 4096; input.state = MemoryPoolInfo::State::RequestAttach;
    CHECK(mapper.Update(pool, input, output) == MemoryPoolInfo::ResultState::Success);
    CHECK(output.state == MemoryPoolInfo::State::Attached && pool.IsMapped());
    CHECK(pool.GetDspAddress() != pool.GetCpuAddress());
    const auto dsp = pool.GetDspAddress();
    CHECK(mapper.Update(pool, input, output) == MemoryPoolInfo::ResultState::BadParam);
    CHECK(pool.GetCpuAddress() == 0x90000 && pool.GetDspAddress() == dsp);
    renderer.Write32(pool.Translate(0x90004, 4), 0x11223344);
    std::uint32_t value = 0;
    CHECK(memory.read(61, 0x90004, {reinterpret_cast<std::byte*>(&value), 4}) == Status::Ok && value == 0x11223344);
    CHECK(!pool.Contains(std::numeric_limits<Address>::max() - 1, 8192));
    AddressInfo address{}; BehaviorInfo::ErrorInfo error{};
    CHECK(mapper.TryAttachBuffer(error, address, 0x90004, 4));
    CHECK(address.GetReference(true) == dsp + 4 && pool.IsUsed());
    input.state = MemoryPoolInfo::State::RequestDetach;
    CHECK(mapper.Update(pool, input, output) == MemoryPoolInfo::ResultState::InUse);
    pool.SetUsed(false);
    std::vector<MappedView> commands;
    CHECK(renderer.lease_all(commands) == Status::Ok);
    CHECK(mapper.Update(pool, input, output) == MemoryPoolInfo::ResultState::InUse);
    CHECK(pool.IsMapped() && pool.GetCpuAddress() == input.address);
    commands.clear();
    CHECK(mapper.Update(pool, input, output) == MemoryPoolInfo::ResultState::Success);
    CHECK(output.state == MemoryPoolInfo::State::Detached && !pool.IsMapped());
    PoolMapper force(&renderer, true);
    AddressInfo forced{};
    CHECK(force.TryAttachBuffer(error, forced, 0x90001, 3));
    CHECK(force.TryAttachBuffer(error, forced, 0x90001, 3));
    CHECK(forced.GetReference(false) && forced.GetReference(false) != 0x90001);
    force.ForceUnmapPointer(forced);
    MappedView gone;
    CHECK(renderer.view(forced.GetReference(false), 3, Access::Read, gone) == Status::NotFound);
    TransferPool transfer; TransferView loan;
    CHECK(transfer.attach(memory, 61, 0x90000, 8192) == Status::Ok);
    CHECK(transfer.view(0, 8192, Access::ReadWrite, loan) == Status::Ok);
    MemoryPoolInfo system_pool{MemoryPoolInfo::Location::DSP};
    CHECK(mapper.InitializeSystemPool(system_pool, loan, 8192));
    loan.reset(); CHECK(transfer.detach() == Status::Busy);
    CHECK(mapper.Unmap(system_pool));
    CHECK(transfer.detach() == Status::Ok);
    CHECK(memory.unregister_region(61, 0x90000) == Status::Ok);
    return 0;
}
