// SPDX-License-Identifier: GPL-3.0-or-later
// Actual SDK-wire/services/Eden/PCM integration, entirely owned CPU operands.
#include "base_v0_audren.h"
#include "wire.h"
#include "audio_core/renderer/memory/memory_pool_info.h"
#include "audio_core/renderer/voice/voice_info.h"
#include "audio_core/renderer/voice/voice_channel_resource.h"
#include "audio_core/renderer/mix/mix_info.h"
#include "audio_core/renderer/sink/sink_info_base.h"
#include <array>
#include <thread>
#include <cstring>
#include <vector>
#include <cstdio>
using namespace botw::audio;using namespace AudioCore;namespace AR=AudioCore::Renderer;
extern "C" {
int owned_audio_initialize(void);void *owned_audio_bytes(size_t,size_t);
uint64_t owned_audio_svc(int,uint64_t*);unsigned owned_audio_maps(void);
void *owned_audio_tlr(void);int owned_audio_transfer(size_t,uint32_t*,uint64_t*);
int owned_pcm_started_at(unsigned);int owned_pcm_render_at(unsigned,int16_t*,unsigned);
}
#define CHECK(x) do {if(!(x)){std::fprintf(stderr,"owned audio integration line %d\n",__LINE__);return 91;}}while(0)
static void put32(void*p,uint32_t v){std::memcpy(p,&v,4);}static void put64(void*p,uint64_t v){std::memcpy(p,&v,8);}
static uint32_t get32(const void*p){uint32_t v;std::memcpy(&v,p,4);return v;}static uint64_t get64(const void*p){uint64_t v;std::memcpy(&v,p,8);return v;}
struct Span{void*data;size_t size;};using Packet=std::array<unsigned char,256>;
// Original complete owned whole_audren request builder, unchanged wire format.
static Packet request(uint32_t command,const void*data=nullptr,size_t size=0,bool pid=false,
 std::vector<uint32_t>copies={},std::vector<Span>inputs={},std::vector<Span>outputs={}){
 Packet p{};put32(p.data(),4|uint32_t(inputs.size())<<20|uint32_t(outputs.size())<<24);size_t offset=8;
 if(pid||!copies.empty()){put32(p.data()+offset,uint32_t(pid)|uint32_t(copies.size())<<1);offset+=4;if(pid)offset+=8;for(auto h:copies){put32(p.data()+offset,h);offset+=4;}}
 auto descriptor=[&](Span span){uint64_t a=uintptr_t(span.data);put32(p.data()+offset,uint32_t(span.size));put32(p.data()+offset+4,uint32_t(a));put32(p.data()+offset+8,uint32_t((a>>36)&0x3fffff)<<2|uint32_t((a>>32)&15)<<28|uint32_t((span.size>>32)&15)<<24);offset+=12;};
 for(auto s:inputs)descriptor(s);for(auto s:outputs)descriptor(s);size_t raw=offset;offset=(offset+15)&~size_t(15);put32(p.data()+offset,0x49434653);put32(p.data()+offset+8,command);if(size)std::memcpy(p.data()+offset+16,data,size);put32(p.data()+4,uint32_t((offset+16+size-raw+3)/4)|((pid||!copies.empty())?0x80000000u:0));return p;
}
static int send(uint32_t handle,Packet&p,bool tls=false){
 void *ipc=tls?owned_audio_tlr():owned_audio_bytes(1<<20,256);if(!ipc)return -1;std::memcpy(ipc,p.data(),256);
 uint64_t r[32]{};if(tls)r[0]=handle;else{r[0]=uintptr_t(ipc);r[1]=256;r[2]=handle;}
 owned_audio_svc(tls?0x21:0x22,r);if(r[0])return -1;std::memcpy(p.data(),ipc,256);return 0;
}
static int close_handle(uint32_t h){uint64_t r[32]={h};owned_audio_svc(0x16,r);return r[0]? -1:0;}
int main(int argc,char**argv){CHECK(argc==2);CHECK(!owned_audio_initialize());
 CHECK(!nxnative_base_v0_audren_focus(1,1)&&!nxnative_base_v0_audren_audio_available(1));
 CHECK(!nxnative_base_v0_audren_prepare()&&nxnative_base_v0_audren_ready());
 CHECK(nxnative_base_v0_audren_prepare()!=0); // Original provider is process one-shot.
 auto*port=static_cast<char*>(owned_audio_bytes(0x1000,16));std::memcpy(port,"sm:",4);uint64_t r[32]={0,uintptr_t(port)};owned_audio_svc(0x1f,r);CHECK(!r[0]);uint32_t sm=uint32_t(r[1]);
 uint64_t zero=0;auto p=request(0,&zero,8,true);CHECK(!send(sm,p,true)&&!get32(p.data()+24)); // Actual current TLR provider.
 p=request(1,"audren:u",8);CHECK(!send(sm,p));uint32_t manager=get32(p.data()+12);CHECK(manager);
 std::array<unsigned char,0x34>wire{};put32(wire.data(),32000);put32(wire.data()+4,160);put32(wire.data()+8,2);put32(wire.data()+0x10,1);put32(wire.data()+0x14,1);put32(wire.data()+0x30,0x31564552);
 p=request(1,wire.data(),wire.size());CHECK(!send(manager,p)&&!get32(p.data()+24));uint64_t required=get64(p.data()+32);CHECK(required);
 if(!std::strcmp(argv[1],"invalid-wire")){
  put32(wire.data()+0x30,1);p=request(1,wire.data(),wire.size());
  // Unsupported host parsing must stop the normal IPC path, never service success.
  (void)send(manager,p);return 92;
 }
 uint32_t transfer=0;uint64_t source=0;CHECK(!owned_audio_transfer(required,&transfer,&source));CHECK(owned_audio_maps()==1);
 std::array<unsigned char,0x48>open{};std::memcpy(open.data(),wire.data(),wire.size());put64(open.data()+0x38,required);put64(open.data()+0x40,0x80);
 if(!std::strcmp(argv[1],"wrong-aruid"))put64(open.data()+0x40,0x81);
 p=request(0,open.data(),open.size(),true,{transfer,0xffff8001});CHECK(!send(manager,p)&&!get32(p.data()+24));uint32_t renderer=get32(p.data()+12);CHECK(renderer);
 CHECK(!close_handle(transfer)); // Engine's strong owner survives the client close.
 p=request(7);CHECK(!send(renderer,p));uint32_t event=get32(p.data()+12);CHECK(event);
 auto*wave=static_cast<int16_t*>(owned_audio_bytes(2<<20,4096));for(unsigned i=0;i<160;i++)wave[i]=1000;
 Rev1UpdateLayout layout{};CHECK(rev1_update_layout({4,1,0,1,1},layout)==Status::Ok);
 auto*input=static_cast<unsigned char*>(owned_audio_bytes(4<<20,layout.input_size));auto*output=static_cast<unsigned char*>(owned_audio_bytes(8<<20,layout.output_size));CHECK(input&&output);std::memset(input,0,layout.input_size);std::memset(output,0xa5,layout.output_size);
 put32(input,0x31564552);for(size_t i=0;i<layout.sections.size();i++)put32(input+4+i*4,layout.sections[i]);put32(input+60,uint32_t(layout.input_size));put32(input+64,0x31564552);
 size_t at=0x40+layout.sections[0];AR::MemoryPoolInfo::InParameter pool{};pool.address=uintptr_t(wave);pool.size=4096;pool.state=AR::MemoryPoolInfo::State::RequestAttach;std::memcpy(input+at,&pool,sizeof pool);at+=layout.sections[1];
 AR::VoiceChannelResource::InParameter resource{};resource.id=0;resource.in_use=true;resource.mix_volumes[0]=1;resource.mix_volumes[1]=1;std::memcpy(input+at,&resource,sizeof resource);
 AR::VoiceInfo::InParameter voice{};voice.id=0;voice.is_new=true;voice.in_use=true;voice.play_state=PlayState::Started;voice.sample_format=SampleFormat::PcmInt16;voice.sample_rate=32000;voice.channel_count=1;voice.pitch=1;voice.volume=1;voice.mix_id=0;voice.splitter_id=UnusedSplitterId;voice.channel_resource_ids[0]=0;voice.wave_buffer_count=1;for(auto&w:voice.wave_buffer_internal)w.sent_to_DSP=true;voice.wave_buffer_internal[0].sent_to_DSP=false;voice.wave_buffer_internal[0].address=uintptr_t(wave);voice.wave_buffer_internal[0].size=320;voice.src_data_address=uintptr_t(wave);voice.src_data_size=320;voice.wave_buffer_internal[0].end_offset=160;voice.wave_buffer_internal[0].loop=true;std::memcpy(input+at+layout.sections[3],&voice,sizeof voice);at+=layout.sections[2]+layout.sections[3]+layout.sections[4];
 AR::MixInfo::InParameter mix{};mix.volume=1;mix.sample_rate=32000;mix.buffer_count=2;mix.in_use=true;mix.dest_mix_id=UnusedMixId;mix.dest_splitter_id=UnusedSplitterId;std::memcpy(input+at,&mix,sizeof mix);at+=layout.sections[5];
 AR::SinkInfoBase::InParameter sink{};sink.type=AR::SinkInfoBase::Type::DeviceSink;sink.in_use=true;sink.device.input_count=2;sink.device.inputs={0,1};std::memcpy(sink.device.name,"DeviceOut",10);std::memcpy(input+at,&sink,sizeof sink);
 p=request(4,nullptr,0,false,{},{{input,layout.input_size}},{{output,layout.output_size},{nullptr,0}});CHECK(!send(renderer,p)&&!get32(p.data()+24));CHECK(owned_audio_maps()==2);
 CHECK(get32(output+0x40)==uint32_t(AR::MemoryPoolInfo::State::Attached));
 p=request(5);CHECK(!send(renderer,p)&&!get32(p.data()+24));
 for(unsigned i=0;i<2000&&!owned_pcm_started_at(0);i++)std::this_thread::sleep_for(std::chrono::milliseconds(1));CHECK(owned_pcm_started_at(0));
 uint32_t waited_handles[1]={event};uint64_t waited[32]={0,uintptr_t(waited_handles),1,1000000000};owned_audio_svc(0x18,waited);CHECK(!waited[0]&&!waited[1]);
 std::array<int16_t,480>pcm{};unsigned nonzero=0;
 for(unsigned n=0;n<20&&nonzero<=200;n++){std::this_thread::sleep_for(std::chrono::milliseconds(5));CHECK(!owned_pcm_render_at(0,pcm.data(),240));nonzero=0;for(unsigned i=0;i<240;i++){CHECK(pcm[2*i]==pcm[2*i+1]);if(pcm[2*i])nonzero++;}}
 CHECK(nonzero>200);for(unsigned i=120;i<240;i++)CHECK(pcm[2*i]>=998&&pcm[2*i]<=1001);CHECK(!nxnative_base_v0_audren_focus(0,0)&&!owned_pcm_started_at(0));
 CHECK(!nxnative_base_v0_audren_focus(1,1));for(unsigned i=0;i<1000&&!owned_pcm_started_at(0);i++)std::this_thread::sleep_for(std::chrono::milliseconds(1));CHECK(owned_pcm_started_at(0));
 CHECK(!nxnative_base_v0_audren_audio_available(0)&&!owned_pcm_started_at(0));
 p=request(6);CHECK(!send(renderer,p)&&!get32(p.data()+24));CHECK(!close_handle(event)&&!close_handle(renderer));CHECK(!owned_audio_maps());
 CHECK(!close_handle(manager)&&!close_handle(sm));CHECK(!nxnative_base_v0_audren_shutdown()&&!nxnative_base_v0_audren_ready());
 std::printf("{\"nonzero\":%u,\"source_rate\":32000,\"pcm_rate\":48000,\"aruid\":128,\"same_kernel\":true,\"guest_executed\":false}\n",nonzero);return 0;
}
