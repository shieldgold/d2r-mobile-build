// SPDX-License-Identifier: GPL-3.0-or-later
// Actual original DeviceSink + ADSP + complete PCM, no guest/device execution.
#include "renderer_host.h"
#include "native_sink_stream.h"
#include "audio_core/adsp/apps/audio_renderer/audio_renderer.h"
#include "audio_core/renderer/command/command_list_header.h"
#include "audio_core/renderer/command/sink/device.h"
#include "audio_core/sink/sink.h"
#include <array>
#include <cstdio>
#include <future>
#include <new>
using namespace botw::audio;
using namespace AudioCore;
using namespace std::chrono_literals;
extern "C" int owned_pcm_render_at(unsigned, int16_t*, unsigned);
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"owned actual ADSP pause failed %d: %s\n",__LINE__,#x); return 1; } } while(0)
int main() {
    try {
        MemorySpace space; require(space.allocate(71,0x100000,4096,Access::ReadWrite));
        RendererMemory memory(space,71); int identity;
        DspMapping mapping{}; require(memory.map_pool(&identity,0x100000,4096,mapping));
        auto* data=reinterpret_cast<std::byte*>(mapping.address);
        auto* samples=reinterpret_cast<s32*>(data+2048);
        for(unsigned i=0;i<240;++i) { samples[i]=1000; samples[240+i]=-1000; }
        auto* header=new(data) Renderer::CommandListHeader{};
        auto* command=new(data+sizeof(*header)) Renderer::DeviceSinkCommand{};
        const auto size=sizeof(*header)+sizeof(*command);
        header->buffer_size=size; header->command_count=1;
        header->samples_buffer={samples,480}; header->buffer_count=2;
        header->sample_count=240; header->sample_rate=48000;
        command->magic=Renderer::CommandMagic; command->enabled=true;
        command->size=sizeof(*command); command->type=Renderer::CommandId::DeviceSink;
        command->input_count=2; command->inputs={0,1}; command->sample_buffer={samples,480};
        auto sink=make_pcm_sink(); RendererHost host(*sink,true);
        ::AudioCore::ADSP::AudioRenderer::AudioRenderer adsp(host,*sink);
        require(host.bind_renderer(adsp)); adsp.Start();
        auto round=[&] {
            require(host.begin_render({}));
            try {
                adsp.SetCommandBuffer(0,mapping.address,size,2'304'000,123,&memory,false);
                adsp.Signal(); adsp.Wait(); host.end_render();
            } catch(...) { host.end_render(); throw; }
        };
        for(unsigned i=0;i<4;++i) round();
        auto blocked=std::async(std::launch::async,round);
        CHECK(blocked.wait_for(20ms)==std::future_status::timeout); // Actual full FIFO, no callback.
        auto pause=std::async(std::launch::async,[&]{ return host.set_paused(true); });
        CHECK(pause.wait_for(1s)==std::future_status::ready && pause.get()==Status::Ok);
        CHECK(blocked.wait_for(1s)==std::future_status::ready); blocked.get();
        CHECK(host.failure()==Status::Ok && adsp.GetRemainCommandCount(0)==1);
        CHECK(memory.unmap(&identity)==Status::Busy); // Pending commands still pin real backing.
        adsp.StopSession(0); // Real Stop/flush cancels old PCM and drops the pending command pins.
        CHECK(adsp.GetRemainCommandCount(0)==0);
        require(host.set_paused(false)); round(); require(host.set_paused(true));
        std::array<int16_t,480> output{};
        CHECK(owned_pcm_render_at(0,output.data(),240)==0 && output[0]==1000 && output[1]==-1000);
        adsp.Stop(); sink->CloseStreams();
        command->~DeviceSinkCommand(); header->~CommandListHeader();
        CHECK(memory.unmap(&identity)==Status::Ok && space.unregister_region(71,0x100000)==Status::Ok);
        return 0;
    } catch(const PlatformError& error) { std::fprintf(stderr,"owned ADSP failure: %s\n",error.what()); return 1; }
}
