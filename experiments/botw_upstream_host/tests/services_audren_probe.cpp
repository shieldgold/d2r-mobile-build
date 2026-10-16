// Owned HIPC packets through the complete service module and real renderer C ABI.
#include "audren_host.h"
#include "services.h"
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunused-parameter"
#include "wire.h"
#pragma clang diagnostic pop
#include "platform.h"
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <thread>
#include <unordered_map>
#include <vector>
using namespace botw::audio;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"owned services audren failed %d: %s (%s)\n",__LINE__,#x,error); return 1; } } while(0)
static void put32(void* p, uint32_t v) { std::memcpy(p,&v,4); }
static void put64(void* p, uint64_t v) { std::memcpy(p,&v,8); }
static uint32_t get32(const void* p) { uint32_t v; std::memcpy(&v,p,4); return v; }
static uint64_t get64(const void* p) { uint64_t v; std::memcpy(&v,p,8); return v; }
using Packet=std::array<uint8_t,256>;
struct Span { uint64_t address; size_t size; };
static Span span(void* p, size_t n) { return {uint64_t(uintptr_t(p)),n}; }
static Packet request(uint32_t command, const void* data=nullptr, size_t length=0,
        bool pid=false, std::vector<uint32_t> copies={}, std::vector<Span> inputs={},
        std::vector<Span> outputs={}, uint32_t domain=0, uint32_t type=4) {
    Packet b{}; put32(b.data(),type|uint32_t(inputs.size())<<20|uint32_t(outputs.size())<<24);
    size_t offset=8;
    if(pid || !copies.empty()) {
        put32(b.data()+offset,uint32_t(pid)|uint32_t(copies.size())<<1); offset+=4;
        if(pid) offset+=8;
        for(auto handle:copies) { put32(b.data()+offset,handle); offset+=4; }
    }
    auto descriptor=[&](Span s) {
        put32(b.data()+offset,uint32_t(s.size)); put32(b.data()+offset+4,uint32_t(s.address));
        put32(b.data()+offset+8,uint32_t((s.address>>36)&0x3fffff)<<2|
            uint32_t((s.size>>32)&15)<<24|uint32_t((s.address>>32)&15)<<28); offset+=12;
    };
    for(auto s:inputs) descriptor(s);
    for(auto s:outputs) descriptor(s);
    size_t raw=offset; offset=(offset+15)&~size_t(15);
    if(domain) {
        b[offset]=1; b[offset+2]=uint8_t(16+length); b[offset+3]=uint8_t((16+length)>>8);
        put32(b.data()+offset+4,domain); offset+=16;
    }
    put32(b.data()+offset,0x49434653); put32(b.data()+offset+8,command);
    if(length) std::memcpy(b.data()+offset+16,data,length);
    put32(b.data()+4,uint32_t((offset+16+length-raw+3)/4)|((pid||!copies.empty())?0x80000000u:0));
    return b;
}
static Packet domain_close(uint32_t object) {
    Packet p{};put32(p.data(),4);put32(p.data()+4,6);p[16]=2;put32(p.data()+20,object);return p;
}
struct Resource { std::atomic<unsigned> retained{0}, released{0}, signals{0}, clears{0}; HostEvent event; };
static int retain(void* p) { ++static_cast<Resource*>(p)->retained; return 0; }
static void release(void* p) { ++static_cast<Resource*>(p)->released; }
static int signal(void* p) { auto& r=*static_cast<Resource*>(p);++r.signals;return r.event.signal()!=Status::Ok; }
static int clear(void* p) { auto& r=*static_cast<Resource*>(p);++r.clears;return r.event.clear()!=Status::Ok; }
struct SessionOwner { botw_audren_session* session=nullptr; };
struct Fixture {
    botw_audren_host* host=nullptr;
    botw_services* services=nullptr;
    Resource backing,event;
    std::array<uint8_t,0x34> wire{};
    void* bytes=nullptr;uint64_t size=0;
    unsigned owners=0,opens=0,closes=0,duplicates=0,fatal=0,updates=0;
    bool close_error=false,open_partial_error=false,invalid_result=false,update_error=false,release_error=false,allocate_error=false;
    std::unordered_map<uint32_t,const void*> transport_tokens;
    uint32_t next_transport=0x2001;
    uint64_t forbidden_address=0;
    std::atomic<unsigned> forbidden_calls{0};
    std::atomic<bool> block{false},entered{false},proceed{false};
    bool block_open=false;
    char error[512]{};
    Fixture() {
        put32(wire.data(),48000);put32(wire.data()+4,240);put32(wire.data()+8,1);
        put32(wire.data()+0x10,1);put32(wire.data()+0x14,1);put32(wire.data()+0x30,0x31564552);
    }
    int initialize() {
        auto result=botw_audren_get_work_buffer_size(wire.data(),wire.size(),&size,error,sizeof error);
        if(result.host_status || result.service_result_valid!=1 || result.service_result) return -1;
        botw_audren_configuration config{};
        config.abi_version=BOTW_AUDREN_HOST_ABI;config.struct_size=sizeof config;
        config.initially_active=0;config.dump_audio_commands=0;config.inspect_guest_stack=0;
        config.failure_context=this;config.failed=[](void* p,int32_t){++static_cast<Fixture*>(p)->fatal;};
        if(botw_audren_host_create(&config,&host,error,sizeof error).host_status) return -1;
        if(posix_memalign(&bytes,4096,size)) return -1;
        std::memset(bytes,0,size);
        if(botw_audren_register_region(host,71,0x100000,{bytes,size,&backing,retain,release},3,error,sizeof error).host_status) return -1;
        services=botw_services_create(nullptr,[](void* context,uint64_t p,size_t n,int) {
            auto& f=*static_cast<Fixture*>(context);
            if(p==f.forbidden_address) { ++f.forbidden_calls;return 0; }
            return int(p&&n&&p<=UINT64_MAX-n);
        },this);
        if(!services) return -1;
        botw_services_transport_api transport{this,
            [](void* p,const void* token,uint32_t* out,char*,size_t) {
                auto& f=*static_cast<Fixture*>(p);if(f.allocate_error) return -1;
                *out=f.next_transport++;f.transport_tokens.emplace(*out,token);return 0;
            },
            [](void* p,const void* token,uint32_t id,char*,size_t) {
                auto& f=*static_cast<Fixture*>(p);
                if(f.release_error || !f.transport_tokens.contains(id) || f.transport_tokens[id]!=token) return -1;
                f.transport_tokens.erase(id);return 0;
            }};
        if(botw_services_set_transport_api(services,&transport,error,sizeof error)) return -1;
        botw_services_kernel_api kernel{};kernel.opaque=this;
        kernel.duplicate_handle=[](void* p,uint32_t input,uint32_t* out,char*,size_t) {
            auto& f=*static_cast<Fixture*>(p); if(input!=61) return -1;
            *out=0x3000+(++f.duplicates); return 0;
        };
        botw_services_set_kernel_api(services,&kernel);
        botw_services_audren_api api{this,work_size,open,info,start,stop,update,set_time,close};
        return botw_services_set_audren_api(services,&api,error,sizeof error);
    }
    int finish() {
        if(services && botw_services_destroy_checked(services,error,sizeof error)) return -1;
        services=nullptr;
        if(owners || !transport_tokens.empty()) return -1;
        if(botw_audren_unregister_region(host,71,0x100000,error,sizeof error).host_status) return -1;
        if(botw_audren_host_destroy(&host,error,sizeof error).host_status) return -1;
        std::free(bytes);bytes=nullptr;
        return backing.retained==backing.released && event.retained==event.released && !fatal ? 0 : -1;
    }
    static botw_audren_result failure() { return {BOTW_AUDREN_SYSTEM_ERROR,0,0}; }
    static botw_audren_result work_size(void*,const void* w,size_t n,uint64_t* size,char* e,size_t c) {
        return botw_audren_get_work_buffer_size(w,n,size,e,c);
    }
    static botw_audren_result open(void* p,const void* w,size_t n,uint32_t transfer,uint32_t process,
            uint64_t size,uint64_t aruid,void** out,uint32_t* event,char* e,size_t c) {
        auto& f=*static_cast<Fixture*>(p); ++f.opens;
        if(f.block_open) { f.entered=true;while(!f.proceed) std::this_thread::yield(); }
        if(transfer!=41 || process!=0xffff8001u || size!=f.size || aruid!=123) return failure();
        auto* o=new SessionOwner;
        auto result=botw_audren_open(f.host,71,w,n,0x100000,size,aruid,
            {&f.event,signal,clear,retain,release},&o->session,e,c);
        if(o->session) { *out=o;*event=61;++f.owners; }
        else delete o;
        return f.open_partial_error ? failure() : result;
    }
    static botw_audren_result info(void* p,void* o,botw_audren_session_info* out,char* e,size_t c) {
        auto& f=*static_cast<Fixture*>(p);
        if(f.block) { f.entered=true;while(!f.proceed) std::this_thread::yield(); }
        auto result=botw_audren_get_info(static_cast<SessionOwner*>(o)->session,out,e,c);
        if(f.invalid_result) return {0,1,0}; // Explicit wrong-contract injection, never a success backend.
        return result;
    }
    static botw_audren_result start(void*,void* o,char* e,size_t c) { return botw_audren_start(static_cast<SessionOwner*>(o)->session,e,c); }
    static botw_audren_result stop(void*,void* o,char* e,size_t c) { return botw_audren_stop(static_cast<SessionOwner*>(o)->session,e,c); }
    static botw_audren_result update(void* p,void* o,const void* in,size_t n,void* perf,size_t pn,void* out,size_t on,char* e,size_t c) {
        auto& f=*static_cast<Fixture*>(p);++f.updates;
        if(f.update_error) return failure();
        return botw_audren_update(static_cast<SessionOwner*>(o)->session,in,n,perf,pn,out,on,e,c);
    }
    static botw_audren_result set_time(void*,void* o,uint32_t value,char* e,size_t c) { return botw_audren_set_rendering_time_limit(static_cast<SessionOwner*>(o)->session,value,e,c); }
    static botw_audren_result close(void* p,void** owner,char* e,size_t c) {
        auto& f=*static_cast<Fixture*>(p);++f.closes;if(f.close_error) return failure();
        auto* o=static_cast<SessionOwner*>(*owner);auto result=botw_audren_close(&o->session,e,c);
        if(!result.host_status && !o->session) { delete o;*owner=nullptr;--f.owners; }
        return result;
    }
    int send(uint32_t h,Packet& p) { error[0]=0;return botw_services_send(services,h,p.data(),p.size(),error,sizeof error); }
    uint32_t manager() {
        uint32_t sm=0;if(botw_services_connect(services,"sm:",3,&sm,error,sizeof error)) return 0;
        uint64_t zero=0;auto p=request(0,&zero,8,true);if(send(sm,p)) return 0;
        const char name[8]={'a','u','d','r','e','n',':','u'};p=request(1,name,8);if(send(sm,p)) return 0;
        return get32(p.data()+12);
    }
    Packet open_packet(uint32_t domain=0) {
        std::array<uint8_t,0x48> payload{};std::memcpy(payload.data(),wire.data(),wire.size());
        put32(payload.data()+0x34,0xdeadbeef); // Alignment padding is not a copied 64-byte client object.
        put64(payload.data()+0x38,size);put64(payload.data()+0x40,123);
        return request(0,payload.data(),payload.size(),true,{41,0xffff8001u},{},{},domain);
    }
    uint32_t renderer(uint32_t manager,uint32_t domain=0) {
        auto p=open_packet(domain);if(send(manager,p)) return 0;
        return get32(p.data()+(domain?48:12));
    }
};
static int run(const char* mode) {
    Fixture f;char* error=f.error;
    CHECK(!f.initialize());uint32_t manager=f.manager();CHECK(manager);
    if(!std::strcmp(mode,"unbound")) {
        CHECK(f.finish()==0);
        auto* s=botw_services_create(nullptr,[](void*,uint64_t p,size_t n,int){return int(p&&n);},nullptr);CHECK(s);
        uint32_t sm=0;CHECK(!botw_services_connect(s,"sm:",3,&sm,error,512));uint64_t zero=0;
        auto p=request(0,&zero,8,true);CHECK(!botw_services_send(s,sm,p.data(),256,error,512));
        const char name[8]={'a','u','d','r','e','n',':','u'};p=request(1,name,8);auto before=p;
        CHECK(botw_services_send(s,sm,p.data(),256,error,512)==-1 && p==before);
        CHECK(!botw_services_destroy_checked(s,error,512));return 0;
    }
    auto size_packet=request(1,f.wire.data(),f.wire.size());CHECK(!f.send(manager,size_packet));CHECK(get64(size_packet.data()+32)==f.size);
    bool domain=std::strstr(mode,"domain")!=nullptr;
    if(domain) { auto p=request(0,nullptr,0,false,{},{},{},0,5);CHECK(!f.send(manager,p));CHECK(get32(p.data()+32)==1); }
    if(!std::strcmp(mode,"partial-open")) {
        f.open_partial_error=true;auto p=f.open_packet();auto before=p;
        CHECK(f.send(manager,p)==-1 && p==before && f.owners==1);
        f.close_error=true;CHECK(botw_services_destroy_checked(f.services,error,512)==-1 && f.owners==1);
        f.close_error=false;CHECK(!f.finish());return 0;
    }
    if(!std::strcmp(mode,"open-borrow")) {
        auto p=request(0,nullptr,0,false,{},{},{},0,5);CHECK(!f.send(manager,p));
        uint32_t root=1;p=request(1,&root,4,false,{},{},{},0,5);CHECK(!f.send(manager,p));
        uint32_t copy=get32(p.data()+12);CHECK(copy);
        f.block_open=true;p=f.open_packet(1);int worker_result=-2;
        std::thread worker([&]{worker_result=f.send(manager,p);});while(!f.entered) std::this_thread::yield();
        char separate_error[512]{};auto other=request(1,f.wire.data(),f.wire.size());auto before=other;
        int send_result=botw_services_send(f.services,copy,other.data(),256,separate_error,sizeof separate_error);
        int close_result=botw_services_close(f.services,copy,separate_error,sizeof separate_error);
        f.proceed=true;worker.join();
        CHECK(send_result==-1 && other==before && close_result==-1 && worker_result==0 && f.owners==1);
        CHECK(!f.finish());return 0;
    }
    if(!std::strcmp(mode,"reserve-failure")) {
        f.allocate_error=true;auto p=f.open_packet();auto before=p;
        CHECK(f.send(manager,p)==-1 && p==before && !f.opens && !f.owners);
        f.allocate_error=false;CHECK(f.renderer(manager));CHECK(f.opens==1 && f.owners==1);
        CHECK(!f.finish());return 0;
    }
    uint32_t renderer=f.renderer(manager,domain?1:0);CHECK(renderer);
    uint32_t handle=domain?manager:renderer;uint32_t object=domain?renderer:0;
    if(!std::strcmp(mode,"protocol") || !std::strcmp(mode,"empty-perf") || !std::strcmp(mode,"renderer-result")) {
        for(uint32_t command=0;command<4;++command) {
            auto p=request(command);CHECK(!f.send(handle,p));
            const uint32_t expected[]={48000,240,1,1};CHECK(get32(p.data()+32)==expected[command]);
        }
        uint32_t limit=45;auto p=request(8,&limit,4);CHECK(!f.send(handle,p));p=request(9);CHECK(!f.send(handle,p));CHECK(get32(p.data()+32)==45);
        p=request(5);CHECK(!f.send(handle,p));p=request(3);CHECK(!f.send(handle,p));CHECK(get32(p.data()+32)==0);
        p=request(6);CHECK(!f.send(handle,p));p=request(3);CHECK(!f.send(handle,p));CHECK(get32(p.data()+32)==1);
        p=request(7);CHECK(!f.send(handle,p));CHECK(get32(p.data()+8)==2 && get32(p.data()+12)==0x3001);
        Rev1UpdateLayout layout{};CHECK(rev1_update_layout({4,1,0,1,1},layout)==Status::Ok);
        std::vector<uint8_t> input(layout.input_size),output(layout.output_size,0xa5),perf(0x100,0xa5);
        put32(input.data(),0x31564552);for(size_t i=0;i<layout.sections.size();++i) put32(input.data()+4+i*4,layout.sections[i]);
        put32(input.data()+60,uint32_t(input.size()));put32(input.data()+64,0x31564552);
        if(!std::strcmp(mode,"renderer-result")) {
            put32(input.data()+64,0); // Actual InfoUpdater rejects this Behavior revision.
            p=request(4,nullptr,0,false,{}, {span(input.data(),input.size())}, {span(output.data(),output.size()),{0,0}});
            CHECK(!f.send(handle,p));CHECK(get32(p.data()+24)==Service::Audio::ResultInvalidUpdateInfo.raw);
            CHECK(output.front()==0xa5 && f.updates==1 && !f.event.clears);
            CHECK(!f.finish());return 0;
        }
        const bool empty_perf=!std::strcmp(mode,"empty-perf");
        f.forbidden_address=0x12345678abcd0ull;
        Span performance=empty_perf?Span{f.forbidden_address,0}:span(perf.data(),perf.size());
        p=request(4,nullptr,0,false,{}, {span(input.data(),input.size())}, {span(output.data(),output.size()),performance});
        CHECK(!f.send(handle,p));CHECK(get32(p.data()+24)==0 && f.updates==1 && output[0]!=0xa5 && f.event.clears==1);
        if(!empty_perf) CHECK(perf.front()==0xa5 && perf.back()==0xa5);
        CHECK(!f.forbidden_calls);
        if(empty_perf) {
            p=request(4,nullptr,0,false,{}, {{f.forbidden_address,0}}, {span(output.data(),output.size()),{0,0}});auto before=p;
            CHECK(f.send(handle,p)==-1 && p==before && f.updates==1);
            p=request(4,nullptr,0,false,{}, {span(input.data(),input.size())}, {{f.forbidden_address,0},{0,0}});before=p;
            CHECK(f.send(handle,p)==-1 && p==before && f.updates==1 && !f.forbidden_calls);
        }
        f.update_error=true;std::fill(output.begin(),output.end(),0xa5);p=request(4,nullptr,0,false,{}, {span(input.data(),input.size())}, {span(output.data(),output.size()),{0,0}});auto before=p;
        CHECK(f.send(handle,p)==-1 && p==before && output[0]==0xa5 && f.updates==2);
    } else if(!std::strcmp(mode,"transport-retry")) {
        f.release_error=true;CHECK(botw_services_close(f.services,handle,error,512)==-1);
        CHECK(!f.owners && f.closes==1 && f.transport_tokens.contains(handle));
        auto p=request(3,nullptr,0,false,{},{},{},0,5);CHECK(!f.send(handle,p));CHECK(get32(p.data()+32)==0x500);
        p=request(0);auto before=p;CHECK(f.send(handle,p)==-1 && p==before);
        f.release_error=false;CHECK(!botw_services_close(f.services,handle,error,512));CHECK(f.closes==1 && !f.transport_tokens.contains(handle));
    } else if(!std::strcmp(mode,"close-retry") || !std::strcmp(mode,"domain-close-retry") || !std::strcmp(mode,"cmif-close-retry")) {
        f.close_error=true;
        if(domain) { auto p=domain_close(object);auto before=p;CHECK(f.send(handle,p)==-1 && p==before); }
        else if(!std::strcmp(mode,"cmif-close-retry")) { Packet p{};put32(p.data(),2);auto before=p;CHECK(f.send(handle,p)==-1 && p==before); }
        else CHECK(botw_services_close(f.services,handle,error,512)==-1);
        CHECK(f.owners==1 && f.closes==1);
        auto p=request(0,nullptr,0,false,{},{},{},object);CHECK(!f.send(handle,p));
        f.close_error=false;
        if(domain) { p=domain_close(object);CHECK(!f.send(handle,p)); }
        else if(!std::strcmp(mode,"cmif-close-retry")) { p={};put32(p.data(),2);CHECK(!f.send(handle,p)); }
        else CHECK(!botw_services_close(f.services,handle,error,512));
        CHECK(!f.owners && f.closes==2);
    } else if(!std::strcmp(mode,"clone") || !std::strcmp(mode,"domain-copy")) {
        Packet p;
        if(domain) p=request(1,&object,4,false,{},{},{},0,5);else p=request(2,nullptr,0,false,{},{},{},0,5);
        CHECK(!f.send(handle,p));uint32_t clone=get32(p.data()+12);CHECK(clone);
        if(domain) { p=domain_close(object);CHECK(!f.send(handle,p)); }else CHECK(!botw_services_close(f.services,handle,error,512));
        CHECK(f.owners==1 && f.closes==0);p=request(0);CHECK(!f.send(clone,p));
        CHECK(!botw_services_close(f.services,clone,error,512));CHECK(!f.owners && f.closes==1);
    } else if(!std::strcmp(mode,"borrow") || !std::strcmp(mode,"domain-borrow")) {
        uint32_t second=handle;
        if(domain) { auto p=request(1,&object,4,false,{},{},{},0,5);CHECK(!f.send(handle,p));second=get32(p.data()+12); }
        f.block=true;Packet p=request(0,nullptr,0,false,{},{},{},object);int result=-2;
        std::thread worker([&]{result=f.send(handle,p);});
        while(!f.entered) std::this_thread::yield();
        char separate_error[512]{};CHECK(botw_services_close(f.services,second,separate_error,sizeof separate_error)==-1);
        CHECK(botw_services_destroy_checked(f.services,separate_error,sizeof separate_error)==-1);
        auto concurrent=request(3,nullptr,0,false,{},{},{},object);auto before=concurrent;
        CHECK(botw_services_send(f.services,handle,concurrent.data(),256,separate_error,sizeof separate_error)==-1 && concurrent==before);
        f.proceed=true;worker.join();CHECK(result==0 && f.owners==1);
    } else if(!std::strcmp(mode,"schemas")) {
        unsigned opened=f.opens;
        auto p=f.open_packet();put32(p.data()+8,3);auto before=p;CHECK(f.send(manager,p)==-1 && p==before && f.opens==opened);
        p=request(0,nullptr,0,false,{41,42});before=p;CHECK(f.send(handle,p)==-1 && p==before);
        std::array<uint8_t,32> output{};p=request(0,nullptr,0,false,{}, {},{span(output.data(),16),span(output.data()+16,16)});before=p;
        CHECK(f.send(handle,p)==-1 && p==before);
        p=request(4,nullptr,0,false,{}, {span(output.data(),16)},{span(output.data(),16)});before=p;
        CHECK(f.send(handle,p)==-1 && p==before && !f.updates);
        uint32_t sm=0;CHECK(!botw_services_connect(f.services,"sm:",3,&sm,error,512));
        f.forbidden_address=0x12345678abcd0ull;
        p=request(1,nullptr,0,false,{}, {},{{f.forbidden_address,16},{f.forbidden_address+16,16}});before=p;
        CHECK(f.send(sm,p)==-1 && p==before && !f.forbidden_calls);
        p=request(1,nullptr,0,false,{41,42},{{f.forbidden_address,16}});before=p;
        CHECK(f.send(sm,p)==-1 && p==before && !f.forbidden_calls);
        f.invalid_result=true;p=request(0);before=p;CHECK(f.send(handle,p)==-1 && p==before);f.invalid_result=false;
    } else { CHECK(false); }
    CHECK(!f.finish());return 0;
}
int main(int argc,char** argv) { return run(argc==2?argv[1]:"protocol"); }
