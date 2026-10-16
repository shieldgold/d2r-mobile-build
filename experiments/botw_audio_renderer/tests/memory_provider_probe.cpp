// SPDX-License-Identifier: GPL-3.0-or-later
// Real MemorySpace with owned backing/strong leases; no guest or VM inspection.
#include "platform.h"
#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <future>
#include <mutex>
#include <string_view>
using namespace botw::audio;
using namespace std::chrono_literals;
#define CHECK(x) do { if(!(x)){std::fprintf(stderr,"owned provider line %d: %s\n",__LINE__,#x);return 1;} } while(0)
constexpr Address Base=0x400000;
struct Provider {
    alignas(4096) std::array<std::byte,16384> bytes{};
    std::atomic<unsigned> retains{0},releases{0},acquires{0},lease_releases{0},fatals{0};
    std::atomic<int> release_error{0},acquire_error{0};
    bool owner_on_failure=false,wrong_size=false;
    std::uint64_t id=77,offset_bias=0;
    std::mutex mutex;std::condition_variable condition;
    bool hold=false,pause_fatal=false,hold_release=false;unsigned entered=0;
    MemorySpace* reenter=nullptr;
    struct Lease { Provider* provider; };
    static int retain(void* p){++static_cast<Provider*>(p)->retains;return 0;}
    static void release(void* p){++static_cast<Provider*>(p)->releases;}
    static void failed(void* p,Status){
        auto& self=*static_cast<Provider*>(p);++self.fatals;
        // Owned scheduling barrier only: emulate preemption before the failing
        // release returns, while another independent host thread retries it.
        std::unique_lock lock(self.mutex);self.condition.notify_all();
        self.condition.wait(lock,[&]{return !self.pause_fatal;});
    }
    static int32_t acquire(void* p,uint64_t owner,uint64_t address,size_t size,uint32_t access,
            botw_audren_memory_lease* out,char*,size_t){
        auto& self=*static_cast<Provider*>(p);++self.acquires;
        if(self.reenter && self.reenter->provider_idle()!=Status::Ok)return int(Status::SystemError);
        {std::unique_lock lock(self.mutex);++self.entered;self.condition.notify_all();self.condition.wait(lock,[&]{return !self.hold;});}
        if(owner!=71)return int(Status::OwnerMismatch);
        if(access!=3)return int(Status::PermissionDenied);
        if(address<Base||address-Base>self.bytes.size()||size>self.bytes.size()-(address-Base))return int(Status::OutOfBounds);
        const int failure=self.acquire_error;
        if(!failure||self.owner_on_failure){
            out->owner=new Lease{&self};out->data=self.bytes.data()+(address-Base);
            out->size=size+(self.wrong_size?1:0);out->access=access;
            out->backing_id=self.id;out->backing_offset=address-Base+self.offset_bias;
        }
        return failure;
    }
    static int32_t release_lease(void* p,void** owner,char*,size_t){
        auto& self=*static_cast<Provider*>(p);++self.lease_releases;
        if(self.reenter)(void)self.reenter->provider_idle();
        {std::unique_lock lock(self.mutex);self.condition.notify_all();self.condition.wait(lock,[&]{return !self.hold_release;});}
        if(self.release_error)return self.release_error;
        delete static_cast<Lease*>(*owner);*owner=nullptr;return 0;
    }
    botw_audren_memory_provider port(){return {BOTW_AUDREN_MEMORY_PROVIDER_ABI,sizeof(botw_audren_memory_provider),this,retain,release,acquire,release_lease};}
    void wait(unsigned count){std::unique_lock lock(mutex);condition.wait(lock,[&]{return entered>=count;});}
    void proceed(){std::lock_guard lock(mutex);hold=false;condition.notify_all();}
};
static int run(std::string_view mode){
    if(mode=="lifetime"){
        Provider provider;MemoryView pin;
        {MemorySpace memory;CHECK(memory.configure_provider(provider.port(),&provider,Provider::failed)==Status::Ok);
         CHECK(memory.pin_pool(71,Base,4096,Access::ReadWrite,pin)==Status::Ok);}
        CHECK(provider.retains==1&&provider.releases==0&&provider.lease_releases==0);
        pin.writable()[0]=std::byte{0x55};pin.reset();
        CHECK(provider.lease_releases==1&&provider.releases==1);return 0;
    }
    Provider provider;MemorySpace memory;
    CHECK(memory.configure_provider(provider.port(),&provider,Provider::failed)==Status::Ok);
    CHECK(provider.retains==1&&provider.releases==0);
    MemoryView view;
    if(mode=="resolve"){
        provider.reenter=&memory;
        CHECK(memory.pin(71,Base,4096,Access::ReadWrite,view)==Status::NotFound&&provider.acquires==0);
        CHECK(memory.pin_pool(71,Base,4096,Access::ReadWrite,view)==Status::Ok&&provider.acquires==1);
        view.writable()[19]=std::byte{0x5a};CHECK(provider.bytes[19]==std::byte{0x5a});
        MemoryView slice;CHECK(memory.pin_pool(71,Base+16,32,Access::Read,slice)==Status::Ok&&provider.acquires==1);
        view.reset();CHECK(provider.lease_releases==0&&memory.provider_idle()==Status::Busy);
        CHECK(slice.readable()[3]==std::byte{0x5a});slice.reset();CHECK(provider.lease_releases==1&&memory.provider_idle()==Status::Ok);
    }else if(mode=="priority"){
        auto bytes=std::shared_ptr<void>(provider.bytes.data(),[](void*){});
        CHECK(memory.register_region(71,Base+4096,{provider.bytes.data()+4096,4096},Access::Read,bytes)==Status::InvalidArgument);
        bytes={provider.bytes.data()+4096,[](void*){}};
        CHECK(memory.register_region(71,Base+4096,{provider.bytes.data()+4096,4096},Access::Read,bytes,{77,4096})==Status::Ok);
        CHECK(memory.pin_pool(72,Base+4096,4,Access::Read,view)==Status::OwnerMismatch);
        CHECK(memory.pin_pool(71,Base+4096,4,Access::Write,view)==Status::PermissionDenied);
        CHECK(memory.pin_pool(71,Base+8190,4,Access::Read,view)==Status::OutOfBounds);
        CHECK(memory.pin_pool(71,Base+4094,4,Access::Read,view)==Status::OutOfBounds);
        CHECK(provider.acquires==0);
        CHECK(memory.unregister_region(71,Base+4096)==Status::Ok);
    }else if(mode=="physical"){
        alignas(4096) std::array<std::byte,4096> other{};auto bytes=std::shared_ptr<void>(other.data(),[](void*){});
        CHECK(memory.register_region(71,0x800000,other,Access::ReadWrite,bytes,{77,4096})==Status::Ok);
        provider.offset_bias=4096;
        CHECK(memory.pin_pool(71,Base,4096,Access::ReadWrite,view)==Status::Overlap&&!view);
        CHECK(provider.acquires==1&&provider.lease_releases==1);
        CHECK(memory.unregister_region(71,0x800000)==Status::Ok);
    }else if(mode=="concurrent"){
        provider.hold=true;MemoryView second;
        auto a=std::async(std::launch::async,[&]{return memory.pin_pool(71,Base,4096,Access::ReadWrite,view);});
        auto b=std::async(std::launch::async,[&]{return memory.pin_pool(71,Base,4096,Access::ReadWrite,second);});
        provider.wait(2);provider.proceed();CHECK(a.get()==Status::Ok&&b.get()==Status::Ok);
        CHECK(provider.acquires==2&&provider.lease_releases==1&&view.readable().data()==second.readable().data());
        view.reset();second.reset();CHECK(provider.lease_releases==2&&memory.provider_idle()==Status::Ok);
    }else if(mode=="registration-race"||mode=="loser-failure"||mode=="loser-retry-race"){
        provider.hold=true;
        auto a=std::async(std::launch::async,[&]{return memory.pin_pool(71,Base,4096,Access::ReadWrite,view);});provider.wait(1);
        auto bytes=std::shared_ptr<void>(provider.bytes.data(),[](void*){});
        CHECK(memory.register_region(71,Base, {provider.bytes.data(),4096},Access::Read,bytes,{77,0})==Status::Ok);
        const bool failed_loser=mode!="registration-race";
        if(failed_loser)provider.release_error=int(Status::DeviceError);
        if(mode=="loser-retry-race")provider.pause_fatal=true;
        provider.proceed();
        if(mode=="loser-retry-race"){
            {std::unique_lock lock(provider.mutex);provider.condition.wait(lock,[&]{return provider.fatals.load()!=0;});}
            provider.release_error=0;CHECK(memory.retry_provider_cleanup()==Status::Ok);
            {std::lock_guard lock(provider.mutex);provider.pause_fatal=false;provider.condition.notify_all();}
        }
        CHECK(a.get()==(failed_loser?Status::DeviceError:Status::PermissionDenied)&&!view&&provider.lease_releases==(mode=="loser-retry-race"?2u:1u));
        if(mode=="loser-failure"){
            CHECK(memory.pin_pool(71,Base,4096,Access::Read,view)==Status::Busy&&provider.fatals==1);
            CHECK(memory.provider_idle()==Status::Busy);provider.release_error=0;
            CHECK(memory.retry_provider_cleanup()==Status::Ok);
        }
        CHECK(memory.unregister_region(71,Base)==Status::Ok);
    }else if(mode=="cleanup-inflight"){
        CHECK(memory.pin_pool(71,Base,4096,Access::ReadWrite,view)==Status::Ok);
        provider.hold_release=true;
        auto close=std::async(std::launch::async,[&]{view.reset();});
        {std::unique_lock lock(provider.mutex);provider.condition.wait(lock,[&]{return provider.lease_releases.load()!=0;});}
        const auto cleanup=memory.retry_provider_cleanup();
        {std::lock_guard lock(provider.mutex);provider.hold_release=false;provider.condition.notify_all();}
        close.get();CHECK(cleanup==Status::Busy&&memory.provider_idle()==Status::Ok);
    }else if(mode=="release-failure"){
        CHECK(memory.pin_pool(71,Base,4096,Access::ReadWrite,view)==Status::Ok);
        provider.release_error=int(Status::DeviceError);view.reset();
        CHECK(provider.fatals==1&&memory.provider_idle()==Status::Busy);
        CHECK(memory.pin_pool(71,Base,4096,Access::ReadWrite,view)==Status::Busy&&!view);
        CHECK(memory.retry_provider_cleanup()==Status::DeviceError&&provider.fatals==1);
        provider.release_error=0;CHECK(memory.retry_provider_cleanup()==Status::Ok&&memory.provider_idle()==Status::Ok);
        CHECK(provider.lease_releases==3);
    }else if(mode=="failed-owner"){
        provider.acquire_error=int(Status::OutOfBounds);provider.owner_on_failure=true;
        CHECK(memory.pin_pool(71,Base,4096,Access::ReadWrite,view)==Status::SystemError&&!view);
        CHECK(provider.fatals==1&&provider.lease_releases==0&&memory.provider_idle()==Status::Busy);
        CHECK(memory.retry_provider_cleanup()==Status::Ok&&provider.lease_releases==1);
    }else if(mode=="source-denials"){
        CHECK(memory.pin_pool(72,Base,4096,Access::ReadWrite,view)==Status::OwnerMismatch&&!view);
        CHECK(memory.pin_pool(71,Base,4096,Access::Read,view)==Status::PermissionDenied&&!view);
        CHECK(memory.pin_pool(71,Base+16383,2,Access::ReadWrite,view)==Status::OutOfBounds&&!view);
        CHECK(provider.acquires==3&&provider.lease_releases==0&&memory.provider_idle()==Status::Ok);
    }else if(mode=="bad-view"){
        provider.wrong_size=true;
        CHECK(memory.pin_pool(71,Base,4096,Access::ReadWrite,view)==Status::InvalidArgument&&!view);
        CHECK(provider.lease_releases==1&&memory.provider_idle()==Status::Ok);
    }else return 2;
    CHECK(provider.fatals==((mode=="release-failure"||mode=="failed-owner"||mode=="loser-failure"||mode=="loser-retry-race")?1u:0u));
    return 0;
}
int main(int argc,char**argv){return argc==2?run(argv[1]):2;}
