// Owned CPU native audio/kernel binding contracts. Never executes a Guest.
#include "audren_host.h"
#include "platform.h"
#include <array>
#include <cstdio>
#include <cstring>
using namespace botw::audio;
static void put32(void* p,uint32_t value) { std::memcpy(p,&value,4); }
static std::array<unsigned char,0x34> parameters() {
    std::array<unsigned char,0x34> wire{};
    put32(wire.data(),48000);put32(wire.data()+4,240);put32(wire.data()+8,1);
    put32(wire.data()+0x10,1);put32(wire.data()+0x14,1);put32(wire.data()+0x30,0x31564552);return wire;
}
#ifdef BOTW_WHOLE_AUDREN_BASELINE
struct Event { HostEvent value; unsigned refs=0; };
int main() {
    auto wire=parameters();char error[256]{};unsigned failures=0;
    botw_audren_configuration config{};config.abi_version=BOTW_AUDREN_HOST_ABI;config.struct_size=sizeof config;
    config.failure_context=&failures;config.failed=[](void* p,int32_t){++*static_cast<unsigned*>(p);};
    botw_audren_host* host=nullptr;
    auto result=botw_audren_host_create(&config,&host,error,sizeof error);
    if(result.host_status || !host) return 70;
    uint64_t required=0;result=botw_audren_get_work_buffer_size(wire.data(),wire.size(),&required,error,sizeof error);
    if(result.host_status || result.service_result_valid!=1 || result.service_result) return 71;
    Event event;
    botw_audren_event port{&event,
        [](void* p){return int(static_cast<Event*>(p)->value.signal()!=Status::Ok);},
        [](void* p){return int(static_cast<Event*>(p)->value.clear()!=Status::Ok);},
        [](void* p){++static_cast<Event*>(p)->refs;return 0;},
        [](void* p){--static_cast<Event*>(p)->refs;}};
    botw_audren_session* session=nullptr;
    // Before the kernel/CAPI bridge, normal Open has no logical-to-backing
    // registration. Calling the real C API cannot fabricate a transfer pool.
    result=botw_audren_open(host,71,wire.data(),wire.size(),0x100000,required,123,port,&session,error,sizeof error);
    const bool opened=!result.host_status && result.service_result_valid==1 && !result.service_result && session;
    if(session) (void)botw_audren_close(&session,error,sizeof error);
    if(botw_audren_host_destroy(&host,error,sizeof error).host_status || event.refs || failures) return 72;
    if(!opened) { std::fprintf(stderr,"owned unbound native transfer Open: host=%d valid=%u result=%u\n",result.host_status,result.service_result_valid,result.service_result);return 92; }
    return 0;
}
#else
#include "whole_audren.h"
#include "whole_kernel.h"
#include "whole_transfer.h"
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunused-parameter"
#include "wire.h"
#include "audio_core/renderer/memory/memory_pool_info.h"
#pragma clang diagnostic pop
#include <atomic>
#include <thread>
#include <chrono>
#include <span>
#include <vector>
extern "C" {
int owned_audren_kernel_initialize(void);
int owned_audren_kernel_transfer(size_t,uint32_t*,uint64_t*);
int owned_audren_kernel_state(uint32_t,int*);
unsigned owned_audren_kernel_live(void);
unsigned owned_audren_kernel_maps(void);
void* owned_audren_kernel_bytes(size_t,size_t);
void owned_audren_kernel_fail_unmap(void);
int owned_audren_kernel_fill(unsigned);
int owned_audren_kernel_empty(void);
int owned_audren_kernel_finish(void);
}
#define CHECK(x) do{if(!(x)){std::fprintf(stderr,"WHOLE_AUDREN:%d %s (%s)\n",__LINE__,#x,error);return 1;}}while(0)
using Packet=std::array<unsigned char,256>;
static uint32_t get32(const void* p){uint32_t value;std::memcpy(&value,p,4);return value;}
static uint64_t get64(const void* p){uint64_t value;std::memcpy(&value,p,8);return value;}
static void put64(void* p,uint64_t value){std::memcpy(p,&value,8);}
struct Span{void* data;size_t size;};
static Packet request(uint32_t command,const void* data=nullptr,size_t size=0,bool pid=false,
        std::vector<uint32_t> copies={},std::vector<Span> inputs={},std::vector<Span> outputs={}){
    Packet p{};put32(p.data(),4|uint32_t(inputs.size())<<20|uint32_t(outputs.size())<<24);size_t offset=8;
    if(pid||!copies.empty()){
        put32(p.data()+offset,uint32_t(pid)|uint32_t(copies.size())<<1);offset+=4;
        if(pid)offset+=8;for(auto handle:copies){put32(p.data()+offset,handle);offset+=4;}
    }
    auto descriptor=[&](Span span){uint64_t address=uintptr_t(span.data);
        put32(p.data()+offset,uint32_t(span.size));put32(p.data()+offset+4,uint32_t(address));
        put32(p.data()+offset+8,uint32_t((address>>36)&0x3fffff)<<2|uint32_t((address>>32)&15)<<28|
            uint32_t((span.size>>32)&15)<<24);offset+=12;};
    for(auto span:inputs)descriptor(span);for(auto span:outputs)descriptor(span);
    size_t raw=offset;offset=(offset+15)&~size_t(15);put32(p.data()+offset,0x49434653);put32(p.data()+offset+8,command);
    if(size)std::memcpy(p.data()+offset+16,data,size);
    put32(p.data()+4,uint32_t((offset+16+size-raw+3)/4)|((pid||!copies.empty())?0x80000000u:0));return p;
}
struct Fatal{std::atomic<unsigned> calls{0};std::atomic<int32_t> status{0};HostEvent wake;};
static void failure(void* p,int32_t status){auto& fatal=*static_cast<Fatal*>(p);fatal.status=status;++fatal.calls;(void)fatal.wake.signal();}
struct Identity{std::atomic<bool> block{false},entered{false},proceed{false};};
static int identity(void* p,uint64_t owner,uint64_t aruid,char* error,size_t capacity){
    if(owner!=71||aruid!=123){if(error&&capacity)std::snprintf(error,capacity,"owned normal identities disagree");return -1;}
    auto& state=*static_cast<Identity*>(p);if(state.block){state.entered=true;while(!state.proceed)std::this_thread::yield();}return 0;
}
struct Context{
    botw_whole_audren* binding=nullptr;botw_services* services=nullptr;
    Fatal fatal;Identity identities;decltype(parameters()) wire=parameters();uint64_t size=0,source=0;uint32_t transfer=0,manager=0;
    char error[256]{};
    botw_whole_audren_configuration config(){
        botw_whole_audren_configuration value{};value.abi_version=BOTW_WHOLE_AUDREN_ABI;value.struct_size=sizeof value;
        value.process_owner=71;value.applet_resource_user_id=123;value.identity_context=&identities;value.validate_identity=identity;
        value.host.abi_version=BOTW_AUDREN_HOST_ABI;value.host.struct_size=sizeof value.host;value.host.initially_active=0;
        value.host.failure_context=&fatal;value.host.failed=failure;return value;
    }
    int send(uint32_t handle,Packet& p){error[0]=0;return botw_services_send(services,handle,p.data(),p.size(),error,sizeof error);}
    int initialize(){
        if(owned_audren_kernel_initialize())return -1;
        auto configuration=config();if(botw_whole_audren_create(&configuration,&binding,error,sizeof error))return -1;
        services=botw_services_create(nullptr,[](void*,uint64_t p,size_t n,int writable){return dm_guest_memory_access(p,n,writable);},nullptr);
        if(!services)return -1;
        botw_services_transport_api transport{nullptr,botw_whole_kernel_transport_allocate,botw_whole_kernel_transport_release};
        if(botw_services_set_transport_api(services,&transport,error,sizeof error))return -1;
        botw_services_kernel_api kernel{nullptr,botw_whole_kernel_create_event,botw_whole_kernel_duplicate_event,
            botw_whole_kernel_signal_event,botw_whole_kernel_clear_event,botw_whole_kernel_close_event,nullptr};
        botw_services_set_kernel_api(services,&kernel);
        if(botw_whole_audren_attach(binding,services,error,sizeof error))return -1;
        uint32_t sm=0;if(botw_services_connect(services,"sm:",3,&sm,error,sizeof error))return -1;
        uint64_t zero=0;auto p=request(0,&zero,8,true);if(send(sm,p))return -1;
        p=request(1,"audren:u",8);if(send(sm,p))return -1;manager=get32(p.data()+12);
        p=request(1,wire.data(),wire.size());if(send(manager,p))return -1;size=get64(p.data()+32);
        return owned_audren_kernel_transfer(size,&transfer,&source);
    }
    Packet open_packet(uint32_t process=0xffff8001u){
        std::array<unsigned char,0x48> bytes{};std::memcpy(bytes.data(),wire.data(),wire.size());put64(bytes.data()+0x38,size);put64(bytes.data()+0x40,123);
        return request(0,bytes.data(),bytes.size(),true,{transfer,process});
    }
    uint32_t open(){auto p=open_packet();return send(manager,p)?0:get32(p.data()+12);}
    int finish(){
        if(botw_whole_audren_destroy(&binding,&services,error,sizeof error)||binding||services)return -1;
        if(transfer&&botw_whole_kernel_handle_kind(transfer)==static_cast<botw_whole_handle_kind>(BOTW_WHOLE_HANDLE_TRANSFER)){if(botw_whole_transfer_close(transfer,error,sizeof error))return -1;}
        return owned_audren_kernel_finish();
    }
    int update(uint32_t renderer,bool ordinary_pool=false){
        Rev1UpdateLayout layout{};if(rev1_update_layout({4,1,0,1,1},layout)!=Status::Ok)return -1;
        auto* input=static_cast<unsigned char*>(owned_audren_kernel_bytes(32*1024*1024,layout.input_size));
        auto* output=static_cast<unsigned char*>(owned_audren_kernel_bytes(64*1024*1024,layout.output_size));
        if(!input||!output)return -1;std::memset(input,0,layout.input_size);std::memset(output,0xa5,layout.output_size);
        put32(input,0x31564552);for(size_t i=0;i<layout.sections.size();++i)put32(input+4+i*4,layout.sections[i]);
        put32(input+60,uint32_t(layout.input_size));put32(input+64,0x31564552);
        if(ordinary_pool){
            AudioCore::Renderer::MemoryPoolInfo::InParameter pool{};
            pool.address=uintptr_t(owned_audren_kernel_bytes(128*1024*1024,4096));pool.size=4096;
            pool.state=AudioCore::Renderer::MemoryPoolInfo::State::RequestAttach;
            std::memcpy(input+0x40+layout.sections[0],&pool,sizeof pool);
        }
        auto p=request(4,nullptr,0,false,{},{{input,layout.input_size}},{{output,layout.output_size},{nullptr,0}});
        if(send(renderer,p)||get32(p.data()+24))return -1;
        if(ordinary_pool&&get32(output+0x40)==uint32_t(AudioCore::Renderer::MemoryPoolInfo::State::Attached))return -1;
        return 0;
    }
};
static int run(const char* mode){
    Context context;char* error=context.error;
    if(!std::strcmp(mode,"configuration")){
        std::memset(error,0xa5,256);
        auto invalid=botw_whole_audren_set_active(nullptr,2,error,256);
        CHECK(invalid.host_status==BOTW_AUDREN_INVALID_ARGUMENT&&std::memchr(error,0,256));
        CHECK(botw_whole_audren_set_active(nullptr,2,nullptr,0).host_status==BOTW_AUDREN_INVALID_ARGUMENT);
        auto config=context.config();botw_whole_audren* binding=nullptr;
        config.validate_identity=nullptr;CHECK(botw_whole_audren_create(&config,&binding,error,256)==-1&&!binding);
        config=context.config();config.process_owner=72;CHECK(botw_whole_audren_create(&config,&binding,error,256)==-1&&!binding);
        config=context.config();config.host.failed=nullptr;CHECK(botw_whole_audren_create(&config,&binding,error,256)==-1&&!binding);
        config=context.config();config.host.inspect_guest_stack=1;CHECK(botw_whole_audren_create(&config,&binding,error,256)==-1&&!binding);
        CHECK(!context.fatal.calls);return 0;
    }
    CHECK(!context.initialize());
    if(!std::strcmp(mode,"process")){
        unsigned live=owned_audren_kernel_live();
        for(uint32_t process:{uint32_t(0),uint32_t(0xffff8000u),context.transfer}){
            auto p=context.open_packet(process);auto before=p;CHECK(context.send(context.manager,p)==-1&&p==before);
            CHECK(owned_audren_kernel_live()==live&&owned_audren_kernel_maps()==1);
        }
        CHECK(!context.fatal.calls&&!context.finish());return 0;
    }
    if(!std::strcmp(mode,"capacity")){
        CHECK(!owned_audren_kernel_fill(1));unsigned live=owned_audren_kernel_live();
        auto p=context.open_packet();auto before=p;CHECK(context.send(context.manager,p)==-1&&p==before);
        CHECK(owned_audren_kernel_live()==live&&owned_audren_kernel_maps()==1&&!context.fatal.calls);
        CHECK(!owned_audren_kernel_empty()&&!context.finish());return 0;
    }
    if(!std::strcmp(mode,"busy")){
        context.identities.block=true;auto p=context.open_packet();int result=-2;
        std::thread thread([&]{auto local=p;result=context.send(context.manager,local);});while(!context.identities.entered)std::this_thread::yield();
        char separate[256]{};int destroy=botw_whole_audren_destroy(&context.binding,&context.services,separate,sizeof separate);
        bool retained=context.binding&&context.services;context.identities.proceed=true;thread.join();
        CHECK(destroy==-1&&retained&&result==0&&!context.finish());return 0;
    }
    uint32_t renderer=context.open();CHECK(renderer&&owned_audren_kernel_maps()==1);
    auto p=request(7);CHECK(!context.send(renderer,p));uint32_t event=get32(p.data()+12);int bad=0;
    CHECK(botw_whole_kernel_handle_kind(event)==BOTW_WHOLE_HANDLE_EVENT_READ&&!owned_audren_kernel_state(event,&bad)&&!bad);
    CHECK(!context.update(renderer));
    if(!std::strcmp(mode,"protocol")){
        p=request(0);CHECK(!context.send(renderer,p)&&get32(p.data()+32)==48000);
        p=request(5);CHECK(!context.send(renderer,p));CHECK(!botw_whole_audren_set_active(context.binding,1,error,256).host_status);
        bool signaled=false;for(unsigned i=0;i<1000&&!signaled;++i){
            signaled=owned_audren_kernel_state(event,&bad)&&!bad;if(!signaled)std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        CHECK(signaled&&!context.fatal.calls);
        CHECK(!botw_whole_audren_set_active(context.binding,0,error,256).host_status);
        p=request(6);CHECK(!context.send(renderer,p));
    }else if(!std::strcmp(mode,"lease")||!std::strcmp(mode,"release-retry")){
        CHECK(!botw_whole_transfer_close(context.transfer,error,256));
        CHECK(!dm_guest_memory_access(context.source,1,0)&&owned_audren_kernel_maps()==1);
        CHECK(!context.update(renderer));
        if(!std::strcmp(mode,"release-retry")){
            owned_audren_kernel_fail_unmap();CHECK(botw_services_close(context.services,renderer,error,256)==-1);
            CHECK(owned_audren_kernel_maps()==1&&!dm_guest_memory_access(context.source,1,0));
            p=request(7);CHECK(!context.send(renderer,p));uint32_t retained_read=get32(p.data()+12);
            bad=0;(void)owned_audren_kernel_state(retained_read,&bad);CHECK(!bad);
            CHECK(!botw_whole_kernel_close_event(nullptr,retained_read,error,256));
            CHECK(context.fatal.calls==1&&context.fatal.status==BOTW_AUDREN_SYSTEM_ERROR&&context.fatal.wake.wait_for(std::chrono::nanoseconds(0))==Status::Ok);
        }
        CHECK(!botw_services_close(context.services,renderer,error,256)&&!owned_audren_kernel_maps());
        CHECK(dm_guest_memory_access(context.source,1,1));
    }else if(!std::strcmp(mode,"missing-provider")){
        CHECK(!context.update(renderer,true)); // Original OutStatus refuses Attached; no invented pool.
    }else if(!std::strcmp(mode,"ownership")){
        CHECK(botw_whole_audren_attach(context.binding,context.services,error,256)==-1);
        auto* other=botw_services_create(nullptr,[](void*,uint64_t p,size_t n,int w){return dm_guest_memory_access(p,n,w);},nullptr);CHECK(other);
        CHECK(botw_whole_audren_destroy(&context.binding,&other,error,256)==-1&&context.binding&&other);
        CHECK(!botw_services_destroy_checked(other,error,256));
    }else CHECK(false);
    CHECK(!botw_whole_kernel_close_event(nullptr,event,error,256));
    if(std::strcmp(mode,"release-retry"))CHECK(!context.fatal.calls);
    CHECK(!context.finish());return 0;
}
int main(int argc,char** argv){return run(argc==2?argv[1]:"protocol");}
#endif
