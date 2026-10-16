// SPDX-License-Identifier: GPL-3.0-or-later
// C ABI normal wire/resource contract; complete upstream algorithms, owned CPU.
#include "audren_host.h"
#include "wire.h"
#include "audio_core/renderer/mix/mix_info.h"
#include "audio_core/renderer/sink/sink_info_base.h"
#include <thread>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
using namespace botw::audio;
extern "C" unsigned owned_pcm_unit_count(void);
extern "C" void owned_pcm_fail(int, int, int);
extern "C" int owned_pcm_started_at(unsigned);
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"owned audren C ABI failed %d: %s\n",__LINE__,#x); return 1; } } while(0)
static void put(std::span<std::byte> bytes, size_t offset, uint32_t value) {
    for(unsigned i=0;i<4;++i) bytes[offset+i]=std::byte((value>>(i*8))&255);
}
struct Resource { std::atomic<unsigned> retains{0}, releases{0}, signals{0}, clears{0}; HostEvent event; };
static int retain(void* p) { ++static_cast<Resource*>(p)->retains; return 0; }
static void release(void* p) { ++static_cast<Resource*>(p)->releases; }
static int signal(void* p) { auto& r=*static_cast<Resource*>(p); ++r.signals; return r.event.signal()!=Status::Ok; }
static int clear(void* p) { auto& r=*static_cast<Resource*>(p); ++r.clears; return r.event.clear()!=Status::Ok; }
struct Fatal { std::atomic<unsigned> calls{0}; std::atomic<int32_t> status{0}; HostEvent wake; };
static void failed(void* context, int32_t status) {
    auto& fatal=*static_cast<Fatal*>(context); fatal.status=status; ++fatal.calls; (void)fatal.wake.signal();
}
static bool platform_ok(botw_audren_result r) { return r.host_status==0; }
static bool service_ok(botw_audren_result r) { return platform_ok(r)&&r.service_result_valid==1&&r.service_result==0; }
int main() {
    std::array<std::byte,0x34> wire{};
    put(wire,0,48000); put(wire,4,240); put(wire,8,1); put(wire,0x10,1); put(wire,0x14,1); put(wire,0x30,0x31564552);
    char error[160]{};
    uint64_t size=0;
    CHECK(service_ok(botw_audren_get_work_buffer_size(wire.data(),wire.size(),&size,error,sizeof error)));
    CHECK(size>=0x18000 && size%4096==0 && owned_pcm_unit_count()==0);
    std::array<std::byte,64> client{};
    auto bad=botw_audren_get_work_buffer_size(client.data(),client.size(),&size,error,sizeof error);
    CHECK(bad.host_status!=0 && !bad.service_result_valid && error[0]);
    Fatal fatal;
    botw_audren_configuration config{BOTW_AUDREN_HOST_ABI,sizeof config,0,0,0,&fatal,failed,{}};
    botw_audren_host* host=nullptr;
    CHECK(platform_ok(botw_audren_host_create(&config,&host,error,sizeof error)) && host);
    botw_audren_host* rejected=nullptr; config.dump_audio_commands=1;
    CHECK(botw_audren_host_create(&config,&rejected,error,sizeof error).host_status==int(Status::Unsupported)&&!rejected);
    config.dump_audio_commands=0;
    config.failed=nullptr;
    CHECK(botw_audren_host_create(&config,&rejected,error,sizeof error).host_status==int(Status::InvalidArgument)&&!rejected);
    config.failed=failed;
    void* bytes=nullptr; CHECK(posix_memalign(&bytes,4096,size*2)==0); std::memset(bytes,0,size*2);
    Resource backing, event;
    botw_audren_backing region{bytes,size*2,&backing,retain,release};
    CHECK(platform_ok(botw_audren_register_region(host,71,0x100000,region,3,error,sizeof error)));
    CHECK(backing.retains==1 && backing.releases==0);
    botw_audren_event event_port{&event,signal,clear,retain,release};
    botw_audren_session* session=nullptr;
    bad=botw_audren_open(host,72,wire.data(),wire.size(),0x100000,size,123,event_port,&session,error,sizeof error);
    CHECK(bad.host_status==int(Status::OwnerMismatch)&&!session&&event.retains==event.releases);
    CHECK(service_ok(botw_audren_open(host,71,wire.data(),wire.size(),0x100000,size,123,event_port,&session,error,sizeof error))&&session);
    CHECK(event.retains==event.releases+1 && owned_pcm_unit_count()==2);
    CHECK(botw_audren_host_destroy(&host,error,sizeof error).host_status==int(Status::Busy)&&host);
    CHECK(botw_audren_unregister_region(host,71,0x100000,error,sizeof error).host_status==int(Status::Busy));
    botw_audren_session* overlap=nullptr;
    CHECK(botw_audren_open(host,71,wire.data(),wire.size(),0x100000,size,123,event_port,&overlap,error,sizeof error).host_status==int(Status::Busy)&&!overlap);
    botw_audren_session* second=nullptr;
    CHECK(service_ok(botw_audren_open(host,71,wire.data(),wire.size(),0x100000+size,size,123,event_port,&second,error,sizeof error))&&second);
    botw_audren_session* third=nullptr;
    bad=botw_audren_open(host,71,wire.data(),wire.size(),0x100000,size,123,event_port,&third,error,sizeof error);
    CHECK(bad.host_status!=0 || (bad.service_result_valid && bad.service_result!=0));
    botw_audren_session_info info{};
    CHECK(platform_ok(botw_audren_get_info(session,&info,error,sizeof error)));
    CHECK(info.sample_rate==48000 && info.sample_count==240 && info.mix_count==1 && !info.active);
    Rev1UpdateLayout layout{}; CHECK(rev1_update_layout({4,1,0,1,1},layout)==Status::Ok);
    std::vector<std::byte> input(layout.input_size), output(layout.output_size,std::byte{0xa5});
    put(input,0,0x31564552);
    for(size_t i=0;i<layout.sections.size();++i) put(input,4+i*4,layout.sections[i]);
    put(input,60,uint32_t(input.size())); put(input,64,0x31564552);
    CHECK(service_ok(botw_audren_update(session,input.data(),input.size(),nullptr,0,output.data(),output.size(),error,sizeof error)));
    CHECK(event.clears==1);
    std::fill(output.begin(),output.end(),std::byte{0xa5});
    bad=botw_audren_update(session,input.data(),63,nullptr,0,output.data(),output.size(),error,sizeof error);
    CHECK(bad.host_status || (bad.service_result_valid && bad.service_result));
    CHECK(output.front()==std::byte{0xa5} && event.clears==1);
    CHECK(platform_ok(botw_audren_start(session,error,sizeof error)));
    CHECK(platform_ok(botw_audren_stop(session,error,sizeof error)));
    CHECK(platform_ok(botw_audren_host_set_active(host,1,error,sizeof error)));
    CHECK(platform_ok(botw_audren_host_set_active(host,0,error,sizeof error)));
    CHECK(platform_ok(botw_audren_close(&session,error,sizeof error))&&!session);
    CHECK(platform_ok(botw_audren_close(&second,error,sizeof error))&&!second);
    CHECK(event.retains==event.releases);
    CHECK(platform_ok(botw_audren_unregister_region(host,71,0x100000,error,sizeof error)));
    CHECK(backing.retains==backing.releases);
    CHECK(platform_ok(botw_audren_host_destroy(&host,error,sizeof error))&&!host);
    CHECK(fatal.calls==0);
    // Actual original PCM Stop failure must cross the C ABI, notify the real
    // independent fatal producer once, and preserve the session owner for cleanup.
    CHECK(platform_ok(botw_audren_host_create(&config,&host,error,sizeof error)));
    CHECK(platform_ok(botw_audren_register_region(host,71,0x100000,region,3,error,sizeof error)));
    put(wire,8,2);
    CHECK(service_ok(botw_audren_open(host,71,wire.data(),wire.size(),0x100000,region.size,123,event_port,&session,error,sizeof error)));
    AudioCore::Renderer::MixInfo::InParameter mix{};
    mix.volume=1.0f; mix.sample_rate=48000; mix.buffer_count=2; mix.in_use=true;
    mix.dest_mix_id=AudioCore::UnusedMixId; mix.dest_splitter_id=AudioCore::UnusedSplitterId;
    const auto mix_offset=0x40+layout.sections[0]+layout.sections[1]+layout.sections[2]+layout.sections[3];
    std::memcpy(input.data()+mix_offset,&mix,sizeof mix);
    AudioCore::Renderer::SinkInfoBase::InParameter device{};
    device.type=AudioCore::Renderer::SinkInfoBase::Type::DeviceSink; device.in_use=true;
    device.device.input_count=2; device.device.inputs={0,1};
    std::memcpy(device.device.name,"DeviceOut",10);
    std::memcpy(input.data()+mix_offset+sizeof mix,&device,sizeof device);
    CHECK(service_ok(botw_audren_update(session,input.data(),input.size(),nullptr,0,output.data(),output.size(),error,sizeof error)));
    CHECK(platform_ok(botw_audren_start(session,error,sizeof error)));
    CHECK(platform_ok(botw_audren_host_set_active(host,1,error,sizeof error)));
    for(unsigned i=0;i<500 && !owned_pcm_started_at(2);++i) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    CHECK(owned_pcm_started_at(2));
    CHECK(platform_ok(botw_audren_host_set_active(host,0,error,sizeof error)));
    owned_pcm_fail(0,0,1);
    bad=botw_audren_stop(session,error,sizeof error);
    CHECK(bad.host_status==int(Status::DeviceError)&&!bad.service_result_valid&&session);
    CHECK(fatal.calls==1 && fatal.status==int(Status::DeviceError) && fatal.wake.wait_for(std::chrono::nanoseconds(0))==Status::Ok);
    CHECK(botw_audren_start(session,error,sizeof error).host_status==int(Status::DeviceError));
    owned_pcm_fail(0,0,0);
    CHECK(platform_ok(botw_audren_close(&session,error,sizeof error))&&!session);
    CHECK(platform_ok(botw_audren_unregister_region(host,71,0x100000,error,sizeof error)));
    CHECK(platform_ok(botw_audren_host_destroy(&host,error,sizeof error))&&!host);
    CHECK(fatal.calls==1 && backing.retains==backing.releases && event.retains==event.releases);
    std::free(bytes);
    return 0;
}
