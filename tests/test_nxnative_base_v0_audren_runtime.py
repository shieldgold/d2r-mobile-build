"""Complete actual REV1 IPC -> Eden -> real PCM ring with owned AU; no Guest."""
from pathlib import Path
import json,os,shutil,subprocess,sys,tempfile,unittest
import test_nxnative_base_v0_audren_kernel as kernel
R=kernel.ROOT;C=kernel.C;OLD=kernel.OLD;A=R/'experiments/botw_audio_renderer'
BRIDGE=r'''
pthread_key_t g_key18,g_keytls,g_keytlr,g_keygate,g_keyres[2],g_keyaspect,g_keyaspectui;
void dm_gate(void){} uint64_t front_game_res(int d){(void)d;return 720ull<<32|1280;}
uint64_t dm_game_res(int d){return front_game_res(d);}
void guest_thread_init(void);uint32_t svc_thread_main_init(void);
int owned_audio_initialize(void){
 for(int i=0;i<6;i++){
  g_mod[i].base=mmap(NULL,0x10000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(g_mod[i].base!=MAP_FAILED);
  g_mod[i].size=0x10000;g_mod[i].text_end=0x4000;g_mod[i].ro_off=0x4000;g_mod[i].data_off=0x8000;g_mod[i].patch_off=0xc000;g_mod[i].key_page_off=0x8000;g_mod[i].key_page_size=0x4000;
 }
 assert(!pthread_key_create(&g_key18,NULL)&&!pthread_key_create(&g_keytls,NULL)&&!pthread_key_create(&g_keytlr,NULL)&&!pthread_key_create(&g_keygate,NULL));
 guest_thread_init();assert(svc_thread_main_init());return (int)call(1,0,0xc2600000,0,0);
}
void *owned_audio_tlr(void){return pthread_getspecific(g_keytlr);}
void *owned_audio_bytes(size_t off,size_t n){assert(off<=g_heap_cur&&n<=g_heap_cur-off);return g_heap+off;}
uint64_t owned_audio_svc(int number,uint64_t *frame){return svc_dispatch(number,frame,0);}
unsigned owned_audio_maps(void){pthread_mutex_lock(&g_memory_lock);unsigned n=g_nmap;pthread_mutex_unlock(&g_memory_lock);return n;}
int owned_audio_transfer(size_t size,uint32_t *out,uint64_t *source){
 size_t span=(size+0xfff)&~(size_t)0xfff;if(!span||span>32*1024*1024)return -1;
 *source=(uintptr_t)(g_heap+(32<<20)+0x1000);memset((void*)(uintptr_t)*source,0,span);uint64_t r[32]={0,*source,span,0};svc_dispatch(0x15,r,0);if(r[0])return -1;*out=(uint32_t)r[1];return 0;
}
'''
@unittest.skipUnless(sys.platform=='darwin','actual Darwin VM, thread and PCM mock AU CPU fixture')
class NxnativeAudrenRuntime(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.t=tempfile.TemporaryDirectory(prefix='nxnative-audren-runtime-');cls.addClassCleanup(cls.t.cleanup);cls.w=Path(cls.t.name);S=cls.w/'source';shutil.copytree(C/'upstream',S)
  for name in (*kernel.PATCHES,'base-v0-audren-kernel.patch','base-v0-audren-runtime.patch'):
   p=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(C/'patches'/name)],cwd=S,capture_output=True,text=True)
   if p.returncode:raise AssertionError(p.stdout+p.stderr)
  (cls.w/'actual_svc.c').write_bytes((S/'dm/host/hle_svc.c').read_bytes())
  profile='#define DM_GAME_CORE_PREEMPT 0\n#define DM_GAME_MEM_RESERVE (5ull<<30)\n#define DM_GAME_MEM_ALIAS (512ull<<20)\n#define DM_GAME_MEM_STACK (256ull<<20)\n#define DM_GAME_APP_POOL 0x100000000ull\n#define DM_GAME_PROGRAM_ID 0x01007ef00011e000ull\n'
  (cls.w/'game_profile.h').write_text(profile)
  # Reuse the complete new kernel fixture's original SVC and hook registration,
  # remove its unexecuted test entry/private-thread stand-in, then link the
  # actual patched original guest_thread_init ownership producer instead.
  prefix=kernel.PROBE[:kernel.PROBE.index('uint32_t svc_thread_main_init(void);')]
  prefix=prefix.replace('int botw_whole_current_thread_memory(uint64_t a,size_t n){(void)a;(void)n;return 0;}','')
  (cls.w/'kernel.c').write_text(prefix+BRIDGE)
  stub=(C/'upstream/dm/host/tests/t_condvar_stub.c').read_text().replace('void guest_thread_init(void) {}','').replace('#include "../dm.h"','#include "dm.h"')
  (cls.w/'host-facilities.c').write_text(stub)
  (cls.w/'provider.c').write_text('#define botw_base_v0_heap_live_available owned_unavailable_native\n#include "'+str(OLD/'base_v0_heap.c')+'"\n#undef botw_base_v0_heap_live_available\nint owned_live_query(size_t*,char*,size_t);\nint botw_base_v0_heap_live_available(size_t*o,char*e,size_t n){return owned_live_query(o,e,n);}\n')
  sources=[C/'tests/audren_runtime_probe.cpp',cls.w/'kernel.c',cls.w/'host-facilities.c',cls.w/'provider.c',S/'dm/host/thread.c',S/'dm/host/hle_thread.c',S/'dm/gfx/nvn/nvn_dirty.c',C/'adapter/base_v0_memory.c',C/'adapter/base_v0_audren.c',C/'adapter/base_v0_audren_memory.c',C/'adapter/base_v0_audren_host_memory.c',C/'adapter/normal_failures.c',C/'adapter/normal_policy.c',*[OLD/n for n in ('whole_address_space.c','whole_policy.c','whole_transfer_vm.c','whole_heap_lease.c','whole_heap_provider.c','whole_ipc.c','whole_guest_memory.c','whole_audren.c')],*[R/p for p in ('native/botw_resources/romfs.c','native/botw_resources/savefs.c','native/botw_resources/local_user.c','native/botw_input/input.c')]]
  quote=lambda p:'"'+str(p)+'"'
  includes=[cls.w,S/'dm/host',S/'dm/gfx/nvn',C/'adapter',OLD,R/'native/botw_host']
  text='''cmake_minimum_required(VERSION 3.24)
project(owned_nxnative_audren LANGUAGES C CXX OBJC)
set(BOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE ON CACHE BOOL "" FORCE)
set(BOTW_AUDIO_RENDERER_NATIVE_SANITIZERS ON CACHE BOOL "" FORCE)
add_subdirectory("@NATIVE@" native)
# Apply the explicit normal output policy only to private copies of complete
# original files. No canonical external component or generated cache is edited.
get_target_property(incs botw_audio_renderer_engine INCLUDE_DIRECTORIES)
foreach(inc IN LISTS incs)
 if(EXISTS "${inc}/common/logging.h")
  set(eden_src "${inc}")
 endif()
endforeach()
if(NOT eden_src)
 message(FATAL_ERROR "verified native renderer common header missing")
endif()
set(policy "${CMAKE_CURRENT_BINARY_DIR}/normal-audio")
file(REMOVE_RECURSE "${policy}")
file(MAKE_DIRECTORY "${policy}/eden/src" "${policy}/native" "${policy}/pcm/dm/host" "${policy}/services/native/botw_host")
configure_file("@SERVICES@" "${policy}/services/native/botw_host/services.c" COPYONLY)
file(COPY "${eden_src}/common" DESTINATION "${policy}/eden/src")
configure_file("@COMMON@" "${policy}/native/common_ports.cpp" COPYONLY)
configure_file("${CMAKE_CURRENT_BINARY_DIR}/native/owned-pcm/dm/host/botw_pcm.c" "${policy}/pcm/dm/host/botw_pcm.c" COPYONLY)
configure_file("${CMAKE_CURRENT_BINARY_DIR}/native/owned-pcm/dm/host/botw_pcm.h" "${policy}/pcm/dm/host/botw_pcm.h" COPYONLY)
execute_process(COMMAND patch --batch --fuzz=0 -p1 -i "@OUTPUT_PATCH@" WORKING_DIRECTORY "${policy}" RESULT_VARIABLE result)
if(NOT result EQUAL 0)
 message(FATAL_ERROR "normal audio output patch failed")
endif()
get_target_property(engine_sources botw_audio_renderer_engine SOURCES)
list(FILTER engine_sources EXCLUDE REGEX "(^|/)common_ports[.]cpp$")
list(APPEND engine_sources "${policy}/native/common_ports.cpp")
set_property(TARGET botw_audio_renderer_engine PROPERTY SOURCES "${engine_sources}")
target_include_directories(botw_audio_renderer_engine BEFORE PUBLIC "${policy}/eden/src")
target_include_directories(botw_audio_renderer_engine PRIVATE "@NATIVE@")
target_compile_definitions(botw_audio_renderer_engine PRIVATE NXNATIVE_NORMAL_POLICY=1)
get_target_property(pcm_sources botw_renderer_owned_pcm SOURCES)
list(REMOVE_ITEM pcm_sources "${CMAKE_CURRENT_BINARY_DIR}/native/owned-pcm/dm/host/botw_pcm.c")
list(APPEND pcm_sources "${policy}/pcm/dm/host/botw_pcm.c")
set_property(TARGET botw_renderer_owned_pcm PROPERTY SOURCES "${pcm_sources}")
target_compile_definitions(botw_renderer_owned_pcm PRIVATE NXNATIVE_NORMAL_POLICY=1)
add_executable(owned_nxnative_audren @SOURCES@ "${policy}/services/native/botw_host/services.c")
target_include_directories(owned_nxnative_audren PRIVATE @INCLUDES@)
target_compile_features(owned_nxnative_audren PRIVATE cxx_std_23)
target_compile_definitions(owned_nxnative_audren PRIVATE NXNATIVE_NORMAL_POLICY=1 NXNATIVE_BASE_V0_MEMORY=1 NXNATIVE_BASE_V0_AUDREN=1)
target_compile_options(owned_nxnative_audren PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-function -Wno-unused-variable -Wno-unused-but-set-variable -Wno-format -fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
target_link_options(owned_nxnative_audren PRIVATE -fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
target_link_libraries(owned_nxnative_audren PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,botw_audio_renderer_engine>" "-framework Security")
'''
  text=text.replace('@SERVICES@',str(R/'native/botw_host/services.c')).replace('@NATIVE@',str(A/'native')).replace('@COMMON@',str(A/'native/common_ports.cpp')).replace('@OUTPUT_PATCH@',str(C/'patches/base-v0-audren-output-policy.patch')).replace('@SOURCES@',' '.join(map(quote,sources))).replace('@INCLUDES@',' '.join(map(quote,includes)))
  # Optional persistent owned build is only a test output, never an install product.
  cls.project=Path(os.environ.get('NXNATIVE_AUDREN_OWNED_BUILD',str(cls.w/'project'))).resolve();cls.project.mkdir(parents=True,exist_ok=True)
  (cls.project/'CMakeLists.txt').write_text(text);cls.build=cls.project/'build'
  for command in (['cmake','-S',str(cls.project),'-B',str(cls.build),'-DCMAKE_BUILD_TYPE=Debug'],['cmake','--build',str(cls.build),'--target','owned_nxnative_audren','botw_owned_renderer_wire','botw_owned_audren_host','--parallel','3']):
   p=subprocess.run(command,capture_output=True,text=True,timeout=300)
   if p.returncode:raise AssertionError(p.stdout+p.stderr)
 def run_owned(self,mode,code=0):
  p=subprocess.run([str(self.build/'owned_nxnative_audren'),mode],capture_output=True,text=True,timeout=30,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=0'})
  self.assertEqual(p.returncode,code,p.stdout+p.stderr)
  if not code:
   v=json.loads(p.stdout);self.assertGreater(v['nonzero'],200);self.assertEqual(v['aruid'],128);self.assertFalse(v['guest_executed']);self.assertEqual((v['source_rate'],v['pcm_rate']),(32000,48000))
 def test_actual_rev1_sm_tlr_transfer_event_update_voice_and_pcm_lifecycle(self):self.run_owned('voice')
 def test_bad_revision_is_a_real_service_failure_not_a_fake_open(self):self.run_owned('invalid-wire',70)
 def test_wrong_aruid_cannot_publish_a_renderer_owner(self):self.run_owned('wrong-aruid',70)
 def run_shared_audio_fixture(self,name):
  p=subprocess.run([str(self.build/'native'/name)],capture_output=True,text=True,timeout=30,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=0'})
  self.assertEqual(p.returncode,0,p.stdout+p.stderr)
 def test_aggregate_mix_buffers_preserve_signed_header_and_per_node_bounds(self):self.run_shared_audio_fixture('botw_owned_renderer_wire')
 def test_complete_thirty_buffer_two_mix_native_open_update_dsp_and_cleanup(self):self.run_shared_audio_fixture('botw_owned_audren_host')
if __name__=='__main__':unittest.main()
