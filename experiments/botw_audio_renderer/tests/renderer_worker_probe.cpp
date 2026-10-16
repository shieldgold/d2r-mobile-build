// SPDX-License-Identifier: GPL-3.0-or-later
// Real manager and ADSP workers, owned memory/event, exact original PCM mock AU.
#include "renderer_host.h"
#include "renderer_event.h"
#include "native_sink_stream.h"
#include "audio_core/adsp/apps/audio_renderer/audio_renderer.h"
#include "audio_core/audio_render_manager.h"
#include "audio_core/renderer/audio_renderer.h"
#include "audio_core/common/audio_renderer_parameter.h"
#include "audio_core/sink/sink.h"
#include <atomic>
#include <cstdio>
#include <thread>
using namespace botw::audio;
using namespace AudioCore;
using namespace std::chrono_literals;
extern "C" unsigned owned_pcm_unit_count(void);
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"owned worker failure %d: %s\n",__LINE__,#x); return 1; } } while(0)
struct Event { std::atomic<bool> fail{false}; HostEvent kernel; };
static int signal(void* context) { auto& e=*static_cast<Event*>(context); return e.fail ? -1 : e.kernel.signal()!=Status::Ok; }
static int clear(void* context) { return static_cast<Event*>(context)->kernel.clear()!=Status::Ok; }
int main() {
    try {
        AudioRendererParameterInternal params{};
        params.sample_rate=48000; params.sample_count=240; params.mixes=1;
        params.voices=1; params.sinks=1; params.execution_mode=ExecutionMode::Auto;
        params.revision=Common::MakeMagic('R','E','V','1');
        const auto size=Renderer::System::GetWorkBufferSize(params);
        MemorySpace space; require(space.allocate(71,0x100000,size,Access::ReadWrite));
        TransferPool pool; require(pool.attach(space,71,0x100000,size));
        RendererMemory memory(space,71);
        auto sink=make_pcm_sink(); RendererHost host(*sink,true);
        ::AudioCore::ADSP::AudioRenderer::AudioRenderer adsp(host,*sink);
        require(host.bind_renderer(adsp));
        Renderer::Manager manager(host);
        Event event; RendererEvent rendered({&event,signal,clear});
        Renderer::Renderer renderer(host,manager,&rendered);
        CHECK(!renderer.Initialize(params,&pool,size,&memory,123,manager.GetSessionId()).IsError());
        CHECK(owned_pcm_unit_count()==2);
        event.fail=true; renderer.Start();
        for (unsigned i=0; i<500 && host.failure()==Status::Ok; ++i) std::this_thread::sleep_for(1ms);
        CHECK(host.failure()==Status::SystemError && host.failure_token().stop_requested());
        // Cleanup must join workers and release command pins, even after failure.
        try { renderer.Stop(); } catch(const PlatformError&) {}
        try { renderer.Finalize(); } catch(const PlatformError&) { renderer.Finalize(); }
        try { manager.Stop(); } catch(const PlatformError&) {}
        try { adsp.Stop(); } catch(const PlatformError&) {}
        sink->CloseStreams();
        CHECK(pool.detach()==Status::Ok && space.unregister_region(71,0x100000)==Status::Ok);
        return 0;
    } catch(const PlatformError& error) {
        std::fprintf(stderr,"owned worker escaped failure: %s\n",error.what()); return 1;
    }
}
