"""Owned CPU integration of the current one kernel and V6 memory; no Guest."""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
C=ROOT/'experiments/nxnative_base_v0'
OLD=ROOT/'experiments/botw_upstream_host/adapter'
BASELINE=os.environ.get('NXNATIVE_AUDREN_KERNEL_BASELINE')=='1'
PATCHES=('normal-entry.patch','base-v0-texture-view.patch','normal-hle-crash.patch',
 'horizon-address-signal.patch','normal-runtime-diagnostics.patch','base-v0-logical-memory.patch',
 'base-v0-conditional-shader-provider.patch','base-v0-normal-api-contracts.patch',
 'base-v0-normal-loader-guard.patch')
PROBE=r'''
#include "dm.h"
#include <assert.h>
#include <errno.h>
#include <sys/mman.h>
#include <stdatomic.h>
module_t g_mod[MAX_MOD]; int g_nmod=6;
/* No Guest thread/TLR is initialized by this kernel-only fixture. */
int botw_whole_current_thread_memory(uint64_t a,size_t n){(void)a;(void)n;return 0;}
/* Only signal dispatch registration is a host stand-in. Dirty tracking and all
   kernel/VM/provider code below are the actual complete production sources. */
static void (*owned_prepare)(void *,size_t);
void dm_guest_write_prep_register(void (*fn)(void *,size_t)){assert(!owned_prepare);owned_prepare=fn;}
void dm_guest_write_prepare(void *p,size_t n){if(owned_prepare)owned_prepare(p,n);}
void dm_fault_hook_register(int (*fn)(void *,int,void *)){(void)fn;}

size_t owned_live=(size_t)512<<20;
int owned_live_query(size_t *out,char *e,size_t n){(void)e;(void)n;*out=owned_live;return 0;}
static unsigned failed_unmaps;
static int observed_munmap(void *a,size_t n){if(failed_unmaps){failed_unmaps--;return munmap((void*)(uintptr_t)1,n);}return munmap(a,n);}
#define munmap observed_munmap
#include "actual_svc.c"
#undef munmap
void hlecount_svc(int n){(void)n;}uint64_t dm_entropy64(void){return 17;}
uint64_t svc_host_call(int n,uint64_t*r){(void)n;(void)r;return 0;}
static uint64_t info(unsigned id){uint64_t r[32]={0,id,0xffff8001};svc_dispatch(0x29,r,0);assert(!r[0]);return r[1];}
#ifdef OWNED_AUDREN_BRIDGE
#include "whole_kernel.h"
#include "whole_transfer.h"
#include "whole_heap_lease.h"
#include "whole_ipc.h"
#include "services.h"
#include "nvn_dirty.h"
static unsigned char tlr[0x200];
static int memory(void *p,uint64_t a,size_t n,int w){(void)p;return dm_guest_memory_access(a,n,w);}
static int read_memory(void*p,uint64_t a,void*out,size_t n,char*e,size_t c){(void)p;(void)e;(void)c;return memory(0,a,n,0)&&botw_whole_transfer_vm_read(out,(void*)(uintptr_t)a,n)?0:-1;}
static int write_memory(void*p,uint64_t a,const void*in,size_t n,char*e,size_t c){(void)p;(void)e;(void)c;return memory(0,a,n,1)&&dm_guest_write_prepare_checked((void*)(uintptr_t)a,n)&&botw_whole_transfer_vm_write((void*)(uintptr_t)a,in,n)?0:-1;}
static int current_tlr(void*p,void**o,size_t*n,char*e,size_t c){(void)p;(void)e;(void)c;*o=tlr;*n=sizeof tlr;return 0;}
#endif
static uint64_t call(int op,uint64_t a,uint64_t b,uint64_t c,uint64_t d){uint64_t r[32]={a,b,c,d};svc_dispatch(op,r,0);return r[0];}
uint32_t svc_thread_main_init(void);
int main(int argc,char **argv){assert(argc==2);assert(svc_thread_main_init());for(int i=0;i<6;i++)g_mod[i].size=0x200000;
 assert(!call(1,0,0xc2600000,0,0));
 if(!strcmp(argv[1],"transfer")){
  uint64_t src=info(4),r[32]={0,src,0x4000,0};svc_dispatch(0x15,r,0);
  assert(!r[0]&&r[1]);assert(!dm_guest_heap_readable(src,16));
  assert(!call(0x16,r[1],0,0,0));assert(dm_guest_heap_readable(src,16));
 }else if(!strcmp(argv[1],"ipc-unbound")){
  /* No configured service is a real fixed failure, never fake successful sm. */
  uint64_t r[32]={0,(uintptr_t)"sm:"};svc_dispatch(0x1f,r,0);assert(!"unbound IPC accepted");
 }
#ifdef OWNED_AUDREN_BRIDGE
 else if(!strcmp(argv[1],"event-owner")){
  char e[256];uint32_t wr=0,rd=0,wr2=0,rd2=0;botw_whole_event_producer *producer=NULL;
  assert(!botw_whole_kernel_create_event(NULL,0,&wr,&rd,e,sizeof e));
  assert(!botw_whole_kernel_retain_event_producer(wr,&producer,e,sizeof e));
  assert(!call(0x16,wr,0,0,0)&&!call(0x16,rd,0,0,0));
  assert(!botw_whole_kernel_create_event(NULL,0,&wr2,&rd2,e,sizeof e));
  assert(!botw_whole_kernel_producer_signal(producer,e,sizeof e));
  uint64_t r[32]={0,(uintptr_t)&rd2,1,0};svc_dispatch(0x18,r,0);assert(r[0]==0xea01);
  assert(!botw_whole_kernel_producer_release(&producer,e,sizeof e)&&!producer);
  assert(!call(0x16,wr2,0,0,0)&&!call(0x16,rd2,0,0,0));
 }else if(!strcmp(argv[1],"transfer-subpage")){
  uint64_t heap=info(4),src=heap+0x1000,r[32]={0,src,0x1000,0};
  *(uint64_t*)(uintptr_t)heap=0x1111;*(uint64_t*)(uintptr_t)src=0x2222;
  svc_dispatch(0x15,r,0);assert(!r[0]);uint32_t handle=(uint32_t)r[1];
  assert(!dm_guest_heap_readable(src,8)&&dm_guest_heap_readable(heap,8));
  char e[256];botw_whole_transfer_lease *lease=NULL;botw_whole_transfer_view view;
  assert(!botw_whole_transfer_acquire(handle,0x1000,3,&lease,&view,e,sizeof e));
  assert(view.source==src&&view.alias_size==0x1000&&*(uint64_t*)view.rw_alias==0x2222);
  *(uint64_t*)view.rw_alias=0x3333;assert(!call(0x16,handle,0,0,0));
  assert(!botw_whole_transfer_release(&lease,e,sizeof e)&&!lease&&!g_nmap);
  assert(*(uint64_t*)(uintptr_t)src==0x3333&&*(uint64_t*)(uintptr_t)heap==0x1111);
 }else if(!strcmp(argv[1],"transfer-cleanup-retry")){
  uint64_t src=info(4)+0x1000,r[32]={0,src,0x1000,0};svc_dispatch(0x15,r,0);assert(!r[0]);
  char e[256];botw_whole_transfer_lease *lease=NULL;botw_whole_transfer_view view;
  assert(!botw_whole_transfer_acquire((uint32_t)r[1],0x1000,3,&lease,&view,e,sizeof e));
  assert(!call(0x16,r[1],0,0,0));failed_unmaps=1;
  assert(botw_whole_transfer_release(&lease,e,sizeof e)&&lease&&g_nmap==1&&!dm_guest_heap_readable(src,8));
  assert(!botw_whole_transfer_release(&lease,e,sizeof e)&&!lease&&!g_nmap&&dm_guest_heap_readable(src,8));
 }else if(!strcmp(argv[1],"transfer-owner")){
  char e[256];uint64_t src=info(4)+0x4000,r[32]={0,src,0x4000,0};
  *(uint64_t*)(uintptr_t)src=0x1357;svc_dispatch(0x15,r,0);assert(!r[0]);
  uint32_t handle=(uint32_t)r[1];botw_whole_transfer_lease *lease=NULL;botw_whole_transfer_view view;
  assert(!botw_whole_transfer_acquire(handle,0x4000,3,&lease,&view,e,sizeof e));
  assert(view.backing_id&&view.source==src&&view.backing_offset==0x4000&&view.owner_permission==0);
  assert(*(uint64_t*)view.rw_alias==0x1357);*(uint64_t*)view.rw_alias=0x2468;
  assert(!call(0x16,handle,0,0,0)&&!dm_guest_heap_readable(src,8));
  uint64_t mi[5];assert(!call(6,(uintptr_t)mi,0,src,0));assert(((uint32_t*)mi)[5]==1&&((uint32_t*)mi)[6]==0);
  assert(call(4,info(14),src,0x4000,0));
  assert(!botw_whole_transfer_release(&lease,e,sizeof e)&&!lease&&dm_guest_heap_readable(src,8));
  assert(*(uint64_t*)(uintptr_t)src==0x2468&&!g_nmap);
 }else if(!strcmp(argv[1],"normal-owner")){
  char e[256];uint64_t src=info(4)+0x4000;botw_whole_heap_lease *lease=NULL;botw_whole_heap_view view;
  assert(!botw_whole_heap_acquire(src,0x1000,3,&lease,&view,e,sizeof e));
  assert(dm_guest_heap_readable(src,8));*(uint64_t*)view.data=0x9876;assert(*(uint64_t*)(uintptr_t)src==0x9876);
  assert(call(4,info(14),src,0x1000,0));
  assert(!botw_whole_heap_release(&lease,e,sizeof e)&&!lease&&!g_nmap);
 }else if(!strcmp(argv[1],"dirty-before")){
  uint64_t src=info(4)+0x4000;nvn_dirty_add_range((void*)(uintptr_t)src,0x4000);
  uint64_t r[32]={0,src,0x4000,0};svc_dispatch(0x15,r,0);assert(r[0]==0xd401&&!g_nmap);
  botw_whole_heap_lease *lease=NULL;botw_whole_heap_view view;char e[256];
  assert(botw_whole_heap_acquire(src,0x4000,3,&lease,&view,e,sizeof e)&&!lease);
  nvn_dirty_remove_range((void*)(uintptr_t)src);
 }else if(!strcmp(argv[1],"dirty-after")){
  uint64_t src=info(4)+0x4000;botw_whole_heap_lease *lease=NULL;botw_whole_heap_view view;char e[256];
  assert(!botw_whole_heap_acquire(src,0x4000,3,&lease,&view,e,sizeof e));
  nvn_dirty_add_range((void*)(uintptr_t)src,0x4000);assert(!"dirty tracker accepted an active writable alias");
 }else if(!strcmp(argv[1],"alias-output")){
  assert(!call(1,0,0xc2400000,0,0));uint64_t alias=info(2);
  assert(!call(0x2c,alias,0x4000,0,0));assert(!call(6,alias,0,alias,0));
  assert(((uint64_t*)(uintptr_t)alias)[0]==alias&&((uint32_t*)(uintptr_t)alias)[6]==3);
  assert(!call(0x2d,alias,0x4000,0,0));assert(!dm_guest_memory_access(alias,40,1));
 }else if(!strcmp(argv[1],"ipc")){
  /* The port and command live in the current real heap, not a fabricated
     readable-range registry. This case does not claim SDK TLR initialization. */
  char e[256];uint64_t heap=info(4);char *port=(void*)(uintptr_t)heap;memcpy(port,"sm:",4);
  unsigned char *packet=(void*)(uintptr_t)(heap+0x1000);
  botw_services *services=botw_services_create(NULL,memory,NULL);assert(services);
  botw_services_set_memory_copiers(services,read_memory,write_memory,NULL);
  botw_whole_ipc_configuration c={services,NULL,read_memory,current_tlr};assert(!botw_whole_ipc_bind(&c,e,sizeof e));
  uint64_t r[32]={0,heap};svc_dispatch(0x1f,r,0);assert(!r[0]);uint32_t handle=(uint32_t)r[1];
  memset(packet,0,256);uint32_t *w=(void*)packet;w[0]=4;w[1]=0x8000000a;w[2]=1;w[8]=0x49434653;
  r[0]=(uintptr_t)packet;r[1]=256;r[2]=handle;svc_dispatch(0x22,r,0);assert(!r[0]&&w[4]==0x4f434653&&!w[6]);
  assert(!call(0x16,handle,0,0,0));assert(!botw_whole_ipc_unbind(services,e,sizeof e));
  assert(!botw_services_destroy_checked(services,e,sizeof e));
 }
#endif
 else assert(!"unknown mode");return 0;
}
'''
@unittest.skipUnless(sys.platform=='darwin','Darwin complete native kernel CPU fixture')
class NxnativeAudrenKernel(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory(prefix='nxnative-audren-kernel-');cls.addClassCleanup(cls.temp.cleanup)
  cls.w=Path(cls.temp.name);cls.source=cls.w/'source';shutil.copytree(C/'upstream',cls.source)
  names=list(PATCHES)+([] if BASELINE else ['base-v0-audren-kernel.patch'])
  for name in names:
   p=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(C/'patches'/name)],cwd=cls.source,capture_output=True,text=True)
   if p.returncode:raise AssertionError(p.stdout+p.stderr)
  (cls.w/'actual_svc.c').write_bytes((cls.source/'dm/host/hle_svc.c').read_bytes())
  (cls.w/'game_profile.h').write_text('#define DM_GAME_CORE_PREEMPT 0\n#define DM_GAME_MEM_RESERVE (5ull<<30)\n#define DM_GAME_MEM_ALIAS (512ull<<20)\n#define DM_GAME_MEM_STACK (256ull<<20)\n#define DM_GAME_APP_POOL 0x100000000ull\n#define DM_GAME_PROGRAM_ID 0x01007ef00011e000ull\n')
  (cls.w/'driver.c').write_text(PROBE)
  (cls.w/'provider.c').write_text('#define botw_base_v0_heap_live_available owned_unavailable_native\n#include "'+str(OLD/'base_v0_heap.c')+'"\n#undef botw_base_v0_heap_live_available\nint owned_live_query(size_t*,char*,size_t);\nint botw_base_v0_heap_live_available(size_t*o,char*e,size_t n){return owned_live_query(o,e,n);}\n')
  cls.binary=cls.w/'probe'
  sources=[cls.w/'driver.c',cls.source/'dm/host/hle_thread.c',C/'upstream/dm/host/tests/t_condvar_stub.c',cls.w/'provider.c',OLD/'whole_address_space.c',OLD/'whole_policy.c',C/'adapter/base_v0_memory.c',C/'adapter/normal_failures.c',C/'adapter/normal_policy.c']
  if not BASELINE:sources += [OLD/'whole_transfer_vm.c',OLD/'whole_heap_lease.c',OLD/'whole_ipc.c',C/'adapter/base_v0_audren_memory.c',cls.source/'dm/gfx/nvn/nvn_dirty.c',*[ROOT/p for p in ('native/botw_host/services.c','native/botw_resources/romfs.c','native/botw_resources/savefs.c','native/botw_resources/local_user.c','native/botw_input/input.c')]]
  command=['clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-unused-parameter','-Wno-unused-but-set-variable','-Wno-format','-pthread','-fsanitize=address,undefined','-fno-sanitize-recover=all','-DNXNATIVE_NORMAL_POLICY=1','-DNXNATIVE_BASE_V0_MEMORY=1','-DNXNATIVE_BASE_V0_AUDREN=1','-I',str(cls.w),'-I',str(cls.source/'dm/host'),'-I',str(C/'adapter'),'-I',str(OLD),'-I',str(ROOT/'native/botw_host'),*[str(p) for p in sources],'-framework','Security','-o',str(cls.binary)]+([] if BASELINE else ['-DOWNED_AUDREN_BRIDGE=1','-I',str(cls.source/'dm/gfx/nvn')])
  p=subprocess.run(command,capture_output=True,text=True)
  if p.returncode:raise AssertionError(p.stdout+p.stderr)
 def run_case(self,mode,code):
  p=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=15,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1'})
  self.assertEqual(p.returncode,code,p.stdout+p.stderr)
 def test_transfer_uses_the_current_heap_and_original_handle_close(self):self.run_case('transfer',0)
 def test_unbound_ipc_cannot_claim_success(self):self.run_case('ipc-unbound',70)
 def test_actual_event_producer_survives_both_endpoint_closes_and_handle_reuse(self):self.run_case('event-owner',0)
 def test_horizon_subpage_transfer_keeps_adjacent_host_page_bytes_accessible(self):self.run_case('transfer-subpage',0)
 def test_failed_real_alias_unmap_retains_the_same_owner_for_retry(self):self.run_case('transfer-cleanup-retry',0)
 def test_transfer_lease_survives_handle_close_and_queries_exact_noaccess(self):self.run_case('transfer-owner',0)
 def test_normal_heap_alias_keeps_source_rw_but_rejects_a_stack_move(self):self.run_case('normal-owner',0)
 def test_existing_dirty_range_rejects_transfer_and_normal_alias(self):self.run_case('dirty-before',0)
 def test_existing_normal_alias_rejects_later_dirty_registration(self):self.run_case('dirty-after',70)
 def test_existing_v6_alias_output_and_unmap_permissions_remain_real(self):self.run_case('alias-output',0)
 def test_actual_sm_message_roundtrip_and_transport_close_share_the_kernel(self):self.run_case('ipc',0)
if __name__=='__main__' :unittest.main()
