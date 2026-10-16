// SPDX-License-Identifier: GPL-3.0-or-later
// Complete REV1 voice/mix/upsample/device/PCM with the real normal heap provider.
#include "audren_host.h"
#include "whole_heap_voice_kernel.h"
#include "wire.h"
#include "audio_core/renderer/memory/memory_pool_info.h"
#include "audio_core/renderer/voice/voice_info.h"
#include "audio_core/renderer/voice/voice_channel_resource.h"
#include "audio_core/renderer/mix/mix_info.h"
#include "audio_core/renderer/sink/sink_info_base.h"
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>
using namespace botw::audio;
using namespace AudioCore;
namespace AR=AudioCore::Renderer;
extern "C" int owned_pcm_started_at(unsigned);
extern "C" int owned_pcm_render_at(unsigned,int16_t*,unsigned);
#define CHECK(x) do{if(!(x)){std::fprintf(stderr,"32k owned probe line %d: %s (%s)\n",__LINE__,#x,error);return 91;}}while(0)
static void put(std::span<std::byte>b,size_t o,uint32_t v){for(unsigned i=0;i<4;++i)b[o+i]=std::byte((v>>(8*i))&255);}
struct Resource{std::atomic<unsigned> retains{0},releases{0},signals{0},clears{0};HostEvent event;};
static int retain(void*p){++static_cast<Resource*>(p)->retains;return 0;}
static void release(void*p){++static_cast<Resource*>(p)->releases;}
static int signal(void*p){auto&r=*static_cast<Resource*>(p);++r.signals;return r.event.signal()!=Status::Ok;}
static int clear(void*p){auto&r=*static_cast<Resource*>(p);++r.clears;return r.event.clear()!=Status::Ok;}
static bool ok(botw_audren_result r){return r.host_status==0;}
static bool service(botw_audren_result r){return ok(r)&&r.service_result_valid&&r.service_result==0;}
int main(int argc,char**argv){
 const bool missing=argc==2&&std::strcmp(argv[1],"missing-provider")==0;
 const bool retry=argc==2&&std::strcmp(argv[1],"cleanup-retry")==0;
 char error[160]{};std::array<std::byte,0x34>wire{};
 put(wire,0,32000);put(wire,4,160);put(wire,8,2);put(wire,0x10,1);put(wire,0x14,1);put(wire,0x30,0x31564552);
 uint64_t required=0;CHECK(service(botw_audren_get_work_buffer_size(wire.data(),wire.size(),&required,error,sizeof error)));
 CHECK(owned_heap_voice_initialize()==0);const uint64_t process=owned_heap_voice_process(),source_address=owned_heap_voice_source();
 botw_whole_heap_provider*provider=nullptr;botw_audren_memory_provider api{};
 CHECK(botw_whole_heap_provider_create(process,&provider,&api,error,sizeof error)==0);
 std::atomic<int32_t>fatal{0};botw_audren_configuration config{BOTW_AUDREN_HOST_ABI,sizeof config,0,0,0,&fatal,[](void*p,int32_t s){*static_cast<std::atomic<int32_t>*>(p)=s;},{}};
 if(!missing)config.memory_provider=api;
 botw_audren_host*host=nullptr;CHECK(ok(botw_audren_host_create(&config,&host,error,sizeof error)));
 botw_whole_transfer_lease*transfer_owner=nullptr;botw_whole_transfer_view transfer{};
 CHECK(owned_heap_voice_transfer(required,&transfer_owner,&transfer)==0);
 CHECK(transfer.requested_size==required&&transfer.alias_size>=required&&transfer.backing_id&&transfer.owner_permission==0);
 void*source=reinterpret_cast<void*>(source_address);std::memset(source,0,4096);
 for(unsigned i=0;i<160;++i)static_cast<int16_t*>(source)[i]=1000;
 Resource tb,event;botw_audren_backing t{transfer.rw_alias,required,&tb,retain,release};
 CHECK(ok(botw_audren_register_region_provenance(host,process,transfer.source,t,3,transfer.backing_id,transfer.backing_offset,error,sizeof error)));
 CHECK(owned_heap_voice_maps()==1);
 botw_audren_session*session=nullptr;botw_audren_event ep{&event,signal,clear,retain,release};
 CHECK(service(botw_audren_open(host,process,wire.data(),wire.size(),transfer.source,required,123,ep,&session,error,sizeof error)));
 botw_audren_session_info info{};CHECK(ok(botw_audren_get_info(session,&info,error,sizeof error)));CHECK(info.sample_rate==32000&&info.sample_count==160);
 Rev1UpdateLayout layout{};CHECK(rev1_update_layout({4,1,0,1,1},layout)==Status::Ok);
 std::vector<std::byte>input(layout.input_size),output(layout.output_size);
 put(input,0,0x31564552);for(size_t i=0;i<layout.sections.size();++i)put(input,4+i*4,layout.sections[i]);put(input,60,uint32_t(input.size()));put(input,64,0x31564552);
 size_t at=0x40+layout.sections[0];AR::MemoryPoolInfo::InParameter pool{};pool.address=source_address;pool.size=4096;pool.state=AR::MemoryPoolInfo::State::RequestAttach;
 std::memcpy(input.data()+at,&pool,sizeof pool);at+=layout.sections[1];
 AR::VoiceChannelResource::InParameter resource{};resource.id=0;resource.in_use=true;resource.mix_volumes[0]=1.0f;resource.mix_volumes[1]=1.0f;
 std::memcpy(input.data()+at,&resource,sizeof resource);
 AR::VoiceInfo::InParameter voice{};voice.id=0;voice.is_new=true;voice.in_use=true;voice.play_state=PlayState::Started;voice.sample_format=SampleFormat::PcmInt16;voice.sample_rate=32000;
 voice.channel_count=1;voice.pitch=1.0f;voice.volume=1.0f;voice.mix_id=0;voice.splitter_id=UnusedSplitterId;voice.channel_resource_ids[0]=0;voice.wave_buffer_count=1;
 for(auto& wave:voice.wave_buffer_internal)wave.sent_to_DSP=true;voice.wave_buffer_internal[0].sent_to_DSP=false;voice.wave_buffer_internal[0].address=source_address;voice.wave_buffer_internal[0].size=320;voice.src_data_address=source_address;voice.src_data_size=320;voice.wave_buffer_internal[0].end_offset=160;voice.wave_buffer_internal[0].loop=true;
 std::memcpy(input.data()+at+layout.sections[3],&voice,sizeof voice);at+=layout.sections[2]+layout.sections[3]+layout.sections[4];
 AR::MixInfo::InParameter mix{};mix.volume=1.0f;mix.sample_rate=32000;mix.buffer_count=2;mix.in_use=true;mix.dest_mix_id=UnusedMixId;mix.dest_splitter_id=UnusedSplitterId;
 std::memcpy(input.data()+at,&mix,sizeof mix);at+=layout.sections[5];
 AR::SinkInfoBase::InParameter device{};device.type=AR::SinkInfoBase::Type::DeviceSink;device.in_use=true;device.device.input_count=2;device.device.inputs={0,1};std::memcpy(device.device.name,"DeviceOut",10);
 std::memcpy(input.data()+at,&device,sizeof device);
 CHECK(service(botw_audren_update(session,input.data(),input.size(),nullptr,0,output.data(),output.size(),error,sizeof error)));
 CHECK(owned_heap_voice_maps()==2);CHECK(!owned_heap_voice_dirty_admission());
 CHECK(botw_whole_heap_provider_destroy(&provider,error,sizeof error)!=0&&provider);
 CHECK(ok(botw_audren_start(session,error,sizeof error)));CHECK(ok(botw_audren_host_set_active(host,1,error,sizeof error)));
 for(unsigned i=0;i<1000&&!owned_pcm_started_at(0)&&!fatal;++i)std::this_thread::sleep_for(std::chrono::milliseconds(1));
 if(!owned_pcm_started_at(0)||fatal){std::fprintf(stderr,"32k startup fatal=%d\n",fatal.load());(void)botw_audren_close(&session,error,sizeof error);return 1;}CHECK(ok(botw_audren_host_set_active(host,0,error,sizeof error)));
 std::array<int16_t,480>pcm{};CHECK(owned_pcm_render_at(0,pcm.data(),240)==0);
 unsigned nonzero=0;int low=32767,high=-32768;for(unsigned i=0;i<240;++i){CHECK(pcm[i*2]==pcm[i*2+1]);if(pcm[i*2])++nonzero;low=std::min(low,int(pcm[i*2]));high=std::max(high,int(pcm[i*2]));}
 std::fprintf(stderr,"observed nonzero=%u low=%d high=%d tail=%d poolstate=%u\n",nonzero,low,high,pcm[478],unsigned(*reinterpret_cast<uint32_t*>(output.data()+64)));CHECK(nonzero>200);for(unsigned i=120;i<240;++i)CHECK(pcm[i*2]>=998&&pcm[i*2]<=1001);
 if(retry){
  // Finalize releases the pool, then Close makes one checked cleanup retry.
  // Both real munmap attempts must fail to keep a genuinely pending owner.
  owned_heap_voice_fail_unmaps(2);
  const auto closed=botw_audren_close(&session,error,sizeof error);
  CHECK(closed.host_status==BOTW_AUDREN_SYSTEM_ERROR&&session&&owned_heap_voice_maps()==2);
  CHECK(botw_whole_heap_provider_destroy(&provider,error,sizeof error)!=0&&provider);
 }
 CHECK(ok(botw_audren_close(&session,error,sizeof error))&&!session&&owned_heap_voice_maps()==1);
 CHECK(ok(botw_audren_unregister_region(host,process,transfer.source,error,sizeof error)));
 CHECK(ok(botw_audren_host_destroy(&host,error,sizeof error))&&!host);
 CHECK(botw_whole_heap_provider_destroy(&provider,error,sizeof error)==0&&!provider);
 CHECK(tb.retains==tb.releases&&event.retains==event.releases);
 CHECK(fatal==(retry?BOTW_AUDREN_SYSTEM_ERROR:BOTW_AUDREN_OK));
 CHECK(botw_whole_transfer_release(&transfer_owner,error,sizeof error)==0&&!transfer_owner);
 CHECK(owned_heap_voice_maps()==0&&owned_heap_voice_dirty_admission());CHECK(owned_heap_voice_cleanup()==0);
 std::printf("{\"source_rate\":32000,\"source_frames\":160,\"pcm_rate\":48000,\"callback_frames\":240,\"nonzero\":%u,\"min\":%d,\"max\":%d,\"tail\":%d,\"normal_pool_registered\":false,\"normal_kernel_pin_observed\":true,\"transfer_handle_closed_before_open\":true,\"cleanup_retry\":%s}\n",nonzero,low,high,pcm[478],retry?"true":"false");
 return 0;
}
