// SPDX-License-Identifier: GPL-3.0-or-later
// Full original Update/PoolMapper/Finalize, owned backing and actual mock PCM.
#include "audren_host.h"
#include "wire.h"
#include "audio_core/renderer/memory/memory_pool_info.h"
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string_view>
#include <vector>
using namespace botw::audio;
namespace AR=AudioCore::Renderer;
#define CHECK(x) do{if(!(x)){std::fprintf(stderr,"owned audren provider line %d: %s (%s)\n",__LINE__,#x,error);return 1;}}while(0)
struct Fixture {
    alignas(4096) std::array<std::byte,4096> samples{};
    std::atomic<unsigned> context_retains{0},context_releases{0},backing_retains{0},backing_releases{0},acquires{0},lease_releases{0},live{0},fatals{0};
    HostEvent event;int release_error=0;bool failed_owner=false;
    static int retain_context(void*p){++static_cast<Fixture*>(p)->context_retains;return 0;}
    static void release_context(void*p){++static_cast<Fixture*>(p)->context_releases;}
    static int retain(void*p){++static_cast<Fixture*>(p)->backing_retains;return 0;}
    static void release(void*p){++static_cast<Fixture*>(p)->backing_releases;}
    static int signal(void*p){return static_cast<Fixture*>(p)->event.signal()!=Status::Ok;}
    static int clear(void*p){return static_cast<Fixture*>(p)->event.clear()!=Status::Ok;}
    static void failed(void*p,int32_t){++static_cast<Fixture*>(p)->fatals;}
    static int32_t acquire(void*p,uint64_t owner,uint64_t address,size_t size,uint32_t access,botw_audren_memory_lease*out,char*,size_t){
        auto& f=*static_cast<Fixture*>(p);++f.acquires;
        if(owner!=71||address!=0x400000||size!=4096||access!=3)return int(Status::PermissionDenied);
        out->owner=new unsigned(1);out->data=f.samples.data();out->size=size;out->access=access;out->backing_id=77;out->backing_offset=0;
        ++f.live;return f.failed_owner?int(Status::DeviceError):0;
    }
    static int32_t release_lease(void*p,void**owner,char*,size_t){
        auto& f=*static_cast<Fixture*>(p);++f.lease_releases;if(f.release_error)return f.release_error;
        delete static_cast<unsigned*>(*owner);*owner=nullptr;--f.live;return 0;
    }
};
static void put(std::span<std::byte>bytes,size_t offset,uint32_t value){for(unsigned i=0;i<4;++i)bytes[offset+i]=std::byte((value>>(i*8))&255);}
static bool ok(botw_audren_result r){return r.host_status==0;}
static bool service(botw_audren_result r){return ok(r)&&r.service_result_valid==1&&r.service_result==0;}
int main(int argc,char**argv){
    char error[160]{};const std::string_view mode=argc==2?argv[1]:"normal";
    Fixture fixture;fixture.failed_owner=mode=="failed-owner";
    botw_audren_configuration config{};config.abi_version=BOTW_AUDREN_HOST_ABI;config.struct_size=sizeof config;
    config.failure_context=&fixture;config.failed=Fixture::failed;
    config.memory_provider={BOTW_AUDREN_MEMORY_PROVIDER_ABI,sizeof(botw_audren_memory_provider),&fixture,
        Fixture::retain_context,Fixture::release_context,Fixture::acquire,Fixture::release_lease};
    std::array<std::byte,0x34>wire{};put(wire,0,48000);put(wire,4,240);put(wire,8,1);put(wire,0x10,1);put(wire,0x14,1);put(wire,0x30,0x31564552);
    uint64_t required=0;CHECK(service(botw_audren_get_work_buffer_size(wire.data(),wire.size(),&required,error,sizeof error)));
    void* bytes=nullptr;CHECK(posix_memalign(&bytes,4096,required)==0);std::memset(bytes,0,required);
    botw_audren_host*host=nullptr;CHECK(ok(botw_audren_host_create(&config,&host,error,sizeof error))&&fixture.context_retains==1);
    botw_audren_backing backing{bytes,required,&fixture,Fixture::retain,Fixture::release};
    CHECK(botw_audren_register_region(host,71,0x100000,backing,3,error,sizeof error).host_status==int(Status::InvalidArgument));
    CHECK(ok(botw_audren_register_region_provenance(host,71,0x100000,backing,3,77,0x100000,error,sizeof error)));
    botw_audren_event event{&fixture,Fixture::signal,Fixture::clear,Fixture::retain,Fixture::release};
    botw_audren_session*session=nullptr;
    CHECK(botw_audren_open(host,71,wire.data(),wire.size(),0x400000,required,123,event,&session,error,sizeof error).host_status==int(Status::NotFound)&&fixture.acquires==0);
    CHECK(service(botw_audren_open(host,71,wire.data(),wire.size(),0x100000,required,123,event,&session,error,sizeof error)));
    Rev1UpdateLayout layout{};CHECK(rev1_update_layout({4,1,0,1,1},layout)==Status::Ok);
    std::vector<std::byte>input(layout.input_size),output(layout.output_size);
    put(input,0,0x31564552);for(size_t i=0;i<layout.sections.size();++i)put(input,4+i*4,layout.sections[i]);put(input,60,uint32_t(input.size()));put(input,64,0x31564552);
    AR::MemoryPoolInfo::InParameter pool{};pool.address=0x400000;pool.size=4096;pool.state=AR::MemoryPoolInfo::State::RequestAttach;
    std::memcpy(input.data()+0x40+layout.sections[0],&pool,sizeof pool);
    auto result=botw_audren_update(session,input.data(),input.size(),nullptr,0,output.data(),output.size(),error,sizeof error);
    if(mode=="failed-owner")CHECK(result.host_status==int(Status::SystemError)&&!result.service_result_valid&&fixture.fatals==1);
    else {CHECK(service(result));CHECK(reinterpret_cast<AR::MemoryPoolInfo::OutStatus*>(output.data()+0x40)->state==AR::MemoryPoolInfo::State::Attached);}
    CHECK(fixture.acquires==1&&fixture.live==1&&fixture.context_releases==0);
    if(mode=="release-failure"){
        fixture.release_error=int(Status::DeviceError);
        result=botw_audren_close(&session,error,sizeof error);
        CHECK(result.host_status==int(Status::DeviceError)&&session&&fixture.live==1&&fixture.fatals==1);
        CHECK(botw_audren_host_destroy(&host,error,sizeof error).host_status==int(Status::Busy)&&host);
        fixture.release_error=0;
    }
    CHECK(ok(botw_audren_close(&session,error,sizeof error))&&!session&&fixture.live==0);
    CHECK(ok(botw_audren_unregister_region(host,71,0x100000,error,sizeof error)));
    CHECK(ok(botw_audren_host_destroy(&host,error,sizeof error))&&!host);
    CHECK(fixture.context_retains==fixture.context_releases&&fixture.backing_retains==fixture.backing_releases);
    CHECK(fixture.fatals==(mode=="normal"?0u:1u));std::free(bytes);return 0;
}
