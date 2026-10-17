"""Actual concentrated Eden/PCM output boundaries; owned CPU only."""
from pathlib import Path
import os,shutil,subprocess,sys,tempfile,unittest
R=Path(__file__).resolve().parents[1];C=R/'experiments/nxnative_base_v0';A=R/'experiments/botw_audio_renderer';O=R/'experiments/botw_upstream_host'
BASELINE=os.environ.get('NXNATIVE_AUDREN_OUTPUT_BASELINE')=='1'
CPP=r'''
#include <csignal>
#include <sys/mman.h>
#include <unistd.h>
#include <cstring>
#include "common/logging.h"
static void fail(int){_Exit(97);}
int main(int argc,char**argv){if(argc!=2)return 98;signal(SIGSEGV,fail);signal(SIGBUS,fail);
 int effects=0;
 if(!strcmp(argv[1],"macros")){
 LOG_TRACE(Audio,"{}",++effects);LOG_DEBUG(Audio,"{}",++effects);LOG_INFO(Audio,"{}",++effects);
 LOG_WARNING(Audio,"{}",++effects);LOG_ERROR(Audio,"{}",++effects);LOG_CRITICAL(Audio,"{}",++effects);
 return effects?91:0;
 }
 if(!strcmp(argv[1],"direct")){
 void *p=mmap(nullptr,16384,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);if(p==MAP_FAILED)return 98;
 Common::Log::FmtLogMessageImpl(Common::Log::Class::Audio,Common::Log::Level::Error,nullptr,0,nullptr,fmt::string_view(static_cast<char*>(p),1),fmt::format_args{});
 return munmap(p,16384)?98:0;
 }
 return 98;
}
'''
SERVICE=r'''
#include <assert.h>
#include <sys/mman.h>
#include <string.h>
#include "native/botw_host/services.c"
static uint64_t forbidden;static unsigned reads;
static int memory(void *p,uint64_t a,size_t n,int w){(void)p;(void)w;return a&&n;}
static int read_owned(void*p,uint64_t a,void*out,size_t n,char*e,size_t c){(void)p;(void)e;(void)c;if(a==forbidden){reads++;return -1;}memcpy(out,(void*)(uintptr_t)a,n);return 0;}
static int write_owned(void*p,uint64_t a,const void*in,size_t n,char*e,size_t c){(void)p;(void)e;(void)c;memcpy((void*)(uintptr_t)a,in,n);return 0;}
int main(void){
 void *page=mmap(NULL,16384,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);assert(page!=MAP_FAILED);forbidden=(uintptr_t)page;
 botw_services*s=botw_services_create(NULL,memory,NULL);assert(s);botw_services_set_memory_copiers(s,read_owned,write_owned,NULL);
 Object*o=new_object(OBJECT_LOGGER);assert(o);Session*session=new_session(o);assert(session);uint32_t handle=0;char error[128];assert(!assign_handle(s,session,&handle,error,sizeof error));
 unsigned char ipc[256]={0};uint32_t header=4|(1u<<16),raw=4,x=(24u<<16)|((uint32_t)(forbidden>>32)&15u)<<12|((uint32_t)(forbidden>>36)&63u)<<6,low=(uint32_t)forbidden,magic=0x49434653;
 memcpy(ipc,&header,4);memcpy(ipc+4,&raw,4);memcpy(ipc+8,&x,4);memcpy(ipc+12,&low,4);memcpy(ipc+16,&magic,4);
 int result=botw_services_send(s,handle,ipc,sizeof ipc,error,sizeof error);
 if(result||reads)return 91;uint32_t response_magic=0,result_code=~0u;memcpy(&response_magic,ipc+16,4);memcpy(&result_code,ipc+24,4);assert(response_magic==0x4f434653&&!result_code);
 assert(!botw_services_close(s,handle,error,sizeof error));assert(!botw_services_destroy_checked(s,error,sizeof error));return munmap(page,16384)?98:0;
}
'''
class NxnativeAudrenOutput(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.t=tempfile.TemporaryDirectory(prefix='nxnative-owned-audio-output-');cls.addClassCleanup(cls.t.cleanup);cls.w=Path(cls.t.name)
  (cls.w/'eden/src').mkdir(parents=True);shutil.copytree(A/'vendor/eden/src/common',cls.w/'eden/src/common')
  (cls.w/'native').mkdir();shutil.copy2(A/'native/common_ports.cpp',cls.w/'native/common_ports.cpp')
  (cls.w/'pcm/dm/host').mkdir(parents=True)
  for n in ('botw_pcm.c','botw_pcm.h'):shutil.copy2(O/'upstream/dm/host'/n,cls.w/'pcm/dm/host'/n)
  for n in ('ios-audio-output.patch','audio-write-observed.patch'):
   p=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(O/'patches'/n)],cwd=cls.w/'pcm',capture_output=True,text=True)
   if p.returncode:raise AssertionError(p.stdout+p.stderr)
  (cls.w/'services/native/botw_host').mkdir(parents=True);shutil.copy2(R/'native/botw_host/services.c',cls.w/'services/native/botw_host/services.c')
  if not BASELINE:
   p=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(C/'patches/base-v0-audren-output-policy.patch')],cwd=cls.w,capture_output=True,text=True)
   if p.returncode:raise AssertionError(p.stdout+p.stderr)
  (cls.w/'probe.cpp').write_text(CPP);cls.cpp=cls.w/'cpp'
  flags=['-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-DNXNATIVE_NORMAL_POLICY=1']
  cmd=['clang++','-std=c++23',*flags,'-DFMT_HEADER_ONLY=1','-D_DEBUG=1','-I',str(cls.w/'eden/src'),'-I',str(A/'native'),'-I',str(A/'platform'),'-I',str(A/'vendor/fmt/include'),str(cls.w/'probe.cpp'),str(cls.w/'native/common_ports.cpp'),str(A/'platform/platform.cpp'),'-o',str(cls.cpp)]
  p=subprocess.run(cmd,capture_output=True,text=True)
  if p.returncode:raise AssertionError(p.stdout+p.stderr)
  prefix=(cls.w/'pcm/dm/host/botw_pcm.c').read_text().split('enum {',1)[0]
  (cls.w/'pcm.c').write_text(prefix+'\nint main(void){int effects=0;PCM_LOG("%d",++effects);return effects?91:0;}\n');cls.pcm=cls.w/'pcm-test'
  p=subprocess.run(['clang','-std=gnu11',*flags,'-I',str(cls.w/'pcm/dm/host'),'-I',str(O/'adapter'),str(cls.w/'pcm.c'),'-o',str(cls.pcm)],capture_output=True,text=True)
  if p.returncode:raise AssertionError(p.stdout+p.stderr)
  (cls.w/'services/probe.c').write_text(SERVICE);cls.service=cls.w/'services-test'
  command=['clang','-std=gnu11',*flags,'-I',str(R/'native/botw_host'),str(cls.w/'services/probe.c'),*[str(R/q)for q in ('native/botw_input/input.c','native/botw_resources/romfs.c','native/botw_resources/savefs.c','native/botw_resources/local_user.c')],'-framework','Security','-o',str(cls.service)]
  p=subprocess.run(command,capture_output=True,text=True)
  if p.returncode:raise AssertionError(p.stdout+p.stderr)
 def run_owned(self,binary,*args):
  p=subprocess.run([str(binary),*args],capture_output=True,text=True,timeout=10)
  self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertEqual(p.stdout,'');self.assertEqual(p.stderr,'')
 def test_all_six_original_eden_log_macros_do_not_evaluate_arguments(self):self.run_owned(self.cpp,'macros')
 def test_original_formatter_entry_never_reads_prot_none_format(self):self.run_owned(self.cpp,'direct')
 def test_original_pcm_log_macro_does_not_evaluate_arguments(self):self.run_owned(self.pcm)
 def test_real_logger_ipc_does_not_read_its_owned_prot_none_payload(self):self.run_owned(self.service)
if __name__=='__main__':unittest.main()
