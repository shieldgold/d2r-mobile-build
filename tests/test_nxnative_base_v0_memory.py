"""Owned sparse VM and complete patched SVC CPU fixture; no Guest execution."""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile, unittest
ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
OLD = ROOT / 'experiments/botw_upstream_host/adapter'
BASELINE = os.environ.get('NXNATIVE_MEMORY_BASELINE') == '1'
SHIM = r'''#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
#include <sys/mman.h>
#include <unistd.h>
#include <assert.h>
#define DM_GAME_MEM_RESERVE (5ull<<30)
#define DM_GAME_MEM_ALIAS (512ull<<20)
#define DM_GAME_MEM_STACK (256ull<<20)
#define DM_GAME_APP_POOL 0x100000000ull
#define DM_GAME_PROGRAM_ID 0x01007ef00011e000ull
#define LOG(...) ((void)0)
#define die(...) _Exit(70)
typedef struct {uint8_t *base; size_t size,file_image_len,ro_off,data_off,text_end,key_page_off,key_page_size; int64_t patch_off; size_t patch_len;const char *name;} module_t;
extern module_t g_mod[6]; extern int g_nmod;
extern struct flags {int svc,dbg;} g_flag;
static inline module_t *module_of(const void *p){(void)p;return NULL;}
static inline uint64_t guest_ns(void){return 0;}
static inline void dm_backtrace(uint64_t value){(void)value;}
'''
DRIVER = r'''#include "dm.h"
#include <stdatomic.h>
#include <errno.h>
#include "base_v0_memory.h"
module_t g_mod[6]; int g_nmod=6; struct flags g_flag;
size_t owned_live=(size_t)512<<20;
int fail_protect,fail_reserve;_Atomic unsigned reserve_calls,protect_calls;
int owned_live_query(size_t *out,char *e,size_t n){(void)e;(void)n;*out=owned_live;return owned_live?0:-1;}
static int observed_mprotect(void *a,size_t n,int p){protect_calls++;if(fail_protect){fail_protect=0;return mprotect(NULL,(size_t)getpagesize(),p);}return mprotect(a,n,p);}
#define mprotect observed_mprotect
#include "actual_svc.c"
#undef mprotect
void hlecount_svc(int n){(void)n;}int svc_thread(int n,uint64_t*r){(void)n;(void)r;return 0;}
uint64_t dm_entropy64(void){return 17;}uint64_t svc_host_call(int n,uint64_t*r){(void)n;(void)r;return 0;}
uint64_t info(unsigned id){uint64_t r[32]={0,id};svc_dispatch(0x29,r,0);assert(!r[0]);return r[1];}
uint64_t call(int op,uint64_t a,uint64_t b,uint64_t c){uint64_t r[32]={a,b,c};svc_dispatch(op,r,0);return r[0];}
static void heap(void){assert(!call(1,0,0xc2600000,0));}
static void *parallel_init(void *p){*(uint64_t*)p=info(12);return NULL;}
int owned_unavailable_native(size_t*,char*,size_t);
int main(int argc,char **argv){assert(argc==2);for(int i=0;i<6;i++)g_mod[i].size=0x200000;
 const char *mode=argv[1];
 if(!strcmp(mode,"reserve-failure")){fail_reserve=1;nxnative_base_v0_memory out,before;memset(&out,0x55,sizeof out);before=out;assert(nxnative_base_v0_memory_reserve(12ull<<20,&out));assert(!memcmp(&out,&before,sizeof out));assert(reserve_calls==1);}
 else if(!strcmp(mode,"native-query")){size_t out=0x1234;char error[256];assert(owned_unavailable_native(&out,error,sizeof error));assert(out==0x1234);}
 else if(!strcmp(mode,"parallel")){pthread_t threads[8];uint64_t bases[8]={0};for(int i=0;i<8;i++)assert(!pthread_create(&threads[i],NULL,parallel_init,&bases[i]));for(int i=0;i<8;i++){assert(!pthread_join(threads[i],NULL));assert(bases[i]==bases[0]&&bases[i]);}assert(reserve_calls==1);}
 else if(!strcmp(mode,"layout")){nxnative_base_v0_memory out,before;memset(&out,0x55,sizeof out);before=out;uint64_t b=info(12);assert(nxnative_base_v0_memory_layout((void*)(uintptr_t)b,4ull<<30,12ull<<20,&out));assert(!memcmp(&out,&before,sizeof out));assert(nxnative_base_v0_memory_layout((void*)(uintptr_t)(UINTPTR_MAX-0x3fff),5ull<<30,12ull<<20,&out));assert(!memcmp(&out,&before,sizeof out));}
 else if(!strcmp(mode,"map-protect")){heap();uint64_t dst=info(14),src=info(4);fail_protect=1;assert(call(4,dst,src,0x1000));assert(dm_guest_heap_readable(src,0x1000));assert(!g_nmap);assert(!call(4,dst,src,0x1000));}
 else if(!strcmp(mode,"alias-grow")){heap();assert(!call(1,0,0xc2400000,0));assert(!call(0x2c,info(2),0x4000,0));size_t old=info(7);assert(call(1,0,0xc2600000,0));assert(info(7)==old);}
 else if(!strcmp(mode,"alias-guard")){heap();assert(!call(1,0,0xc2400000,0));owned_live=(256ull<<20)-1;assert(call(0x2c,info(2),0x4000,0));assert(info(7)==0xc2400000ull+(12ull<<20));}
 else if(!strcmp(mode,"alias-zero")){heap();assert(!call(1,0,0xc2400000,0));uint64_t a=info(2);assert(!call(0x2c,a,0x4000,0));*(uint64_t*)(uintptr_t)a=0x39;*(uint64_t*)(uintptr_t)(a+0x1000)=0x71;assert(!call(0x2c,a+0x1000,0x1000,0));assert(*(uint64_t*)(uintptr_t)(a+0x1000)==0x71);assert(!call(0x2d,a,0x1000,0));assert(!call(0x2c,a,0x1000,0));assert(!*(uint64_t*)(uintptr_t)a);assert(*(uint64_t*)(uintptr_t)(a+0x1000)==0x71);}
 else if(!strcmp(mode,"alias-budget")){heap();assert(!call(1,0,0xc2400000,0));owned_live=(256ull<<20)+0x1000;assert(call(0x2c,info(2),0x4000,0));assert(info(7)==0xc2400000ull+(12ull<<20));}
 else if(!strcmp(mode,"immutable")){nxnative_base_v0_memory out,before;memset(&out,0x55,sizeof out);before=out;info(12);assert(nxnative_base_v0_memory_reserve(14ull<<20,&out));assert(!memcmp(&out,&before,sizeof out));assert(reserve_calls==1);}
 else if(!strcmp(mode,"module-count")){g_nmod=3;info(12);assert(!"wrong module count accepted");}
 else if(!strcmp(mode,"image-overflow")){g_mod[1].size=SIZE_MAX;info(12);assert(!"overflowed image accepted");}
 else if(!strcmp(mode,"alias-output")){heap();assert(!call(1,0,0xc2400000,0));uint64_t a=info(2);assert(!call(0x2c,a,0x4000,0));uint64_t *out=(void*)(uintptr_t)(a+0x1000);assert(!call(6,(uintptr_t)out,0,a));assert(out[0]==a&&out[1]==0x4000);assert(!call(0x2d,a+0x2000,0x1000,0));assert(call(6,a+0x2000-16,0,a)==0xd401);}
 else if(!strcmp(mode,"unknown-info")){const uint64_t ids[]={9,10,19,24,0x100000000ull,UINT64_MAX};for(unsigned i=0;i<sizeof ids/sizeof *ids;i++){uint64_t r[32]={0x1234,ids[i],0x5678};svc_dispatch(0x29,r,0);assert(r[0]==(i<3?0x4201u:0xf001u)&&r[1]==ids[i]&&r[2]==0x5678);}}
 else if(!strcmp(mode,"query-padding")){heap();uint64_t out[5];memset(out,0xa5,sizeof out);assert(!call(6,(uintptr_t)out,0,info(4)));const uint8_t *bytes=(const void*)out;for(unsigned i=36;i<40;i++)assert(!bytes[i]);}
 else if(!strcmp(mode,"permission")){heap();assert(call(2,info(4),0x4000,1)==0xd401);assert(call(3,info(4),0x4000,1)==0xd401);}
 else if(!strcmp(mode,"protect")){heap();size_t old=info(7);fail_protect=1;assert(call(1,0,0xc2400000,0));assert(info(7)==old);}
 else if(!strcmp(mode,"five")){heap();uint64_t base=info(12),heapbase=info(4);assert(info(13)==(5ull<<30));assert(info(3)==(512ull<<20));assert(info(15)==(256ull<<20));assert(heapbase==base+(1ull<<30));assert(info(5)==(4ull<<30));assert(info(6)==0xc2600000ull+(12ull<<20));assert(info(7)==info(6));assert(!nxnative_base_v0_memory_verify(0xc2600000));unsigned char *p=(void*)(uintptr_t)heapbase;p[0]=0x39;p[0xc2600000ull-1]=0x71;assert(p[0]==0x39&&p[0xc2600000ull-1]==0x71);}
 else if(!strcmp(mode,"guard")){owned_live=(256ull<<20)-1;info(12);assert(!"low live guard accepted");}
 else if(!strcmp(mode,"alignment")){heap();size_t old=info(7);assert(call(1,0,0xc2600001,0)==0xca01);assert(info(7)==old);}
 else if(!strcmp(mode,"growth-guard")){heap();assert(!call(1,0,0xc2400000,0));size_t old=info(7);owned_live=(256ull<<20)-1;assert(call(1,0,0xc2600000,0));assert(info(7)==old);}
 else if(!strcmp(mode,"stack")){heap();uint64_t dst=info(14),src=info(4);*(uint64_t*)(uintptr_t)src=0x1234;assert(!call(4,dst,src,0x1000));assert(*(uint64_t*)(uintptr_t)dst==0x1234);assert(!dm_guest_heap_readable(src,8));*(uint64_t*)(uintptr_t)dst=0x5678;assert(!call(5,dst,src,0x1000));assert(*(uint64_t*)(uintptr_t)src==0x5678);assert(dm_guest_heap_readable(src,8));}
 else if(!strcmp(mode,"bad-source")){heap();assert(call(4,info(14),1,0x1000));}
 else if(!strcmp(mode,"overflow")){heap();assert(call(4,UINT64_MAX-0xfff,info(4),0x2000));assert(call(0x2c,UINT64_MAX-0xfff,0x2000,0));}
 else if(!strcmp(mode,"borrow-shrink")){heap();uint64_t dst=info(14),src=info(4)+0xc2400000;assert(!call(4,dst,src,0x1000));size_t old=info(7);assert(call(1,0,0xc2400000,0));assert(info(7)==old);assert(!call(5,dst,src,0x1000));assert(!call(1,0,0xc2400000,0));}
 else if(!strcmp(mode,"query")){heap();uint64_t mi[5]={0};assert(!call(6,(uintptr_t)mi,0,info(4)));assert(mi[0]==info(4)&&mi[1]==0xc2600000&&((uint32_t*)mi)[4]==5);assert(call(6,1,0,info(4)));}
 else if(!strcmp(mode,"alias")){heap();assert(!call(1,0,0xc2400000,0));uint64_t a=info(2);assert(!call(0x2c,a,0x4000,0));*(uint64_t*)(uintptr_t)a=0x19;uint64_t mi[5]={0};assert(!call(6,(uintptr_t)mi,0,a));assert(((uint32_t*)mi)[6]==3);assert(!call(0x2d,a,0x1000,0));*(uint64_t*)(uintptr_t)(a+0x1000)=0x27;assert(!call(6,(uintptr_t)mi,0,a));assert(((uint32_t*)mi)[6]==0);assert(!call(6,(uintptr_t)mi,0,a+0x1000));assert(((uint32_t*)mi)[6]==3);}
 else assert(!"unknown case");
 puts("NXNATIVE_BASE_V0_MEMORY_OWNED_CPU_OK");return 0;}
'''
@unittest.skipUnless(sys.platform=='darwin','owned VM fixture uses actual Darwin pthread stack metadata')
class BaseV0Memory(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory(prefix='nxnative-owned-memory-');cls.addClassCleanup(cls.temp.cleanup);cls.work=Path(cls.temp.name)
  source=cls.work/'source';cls.source=source;shutil.copytree(COMPONENT/'upstream',source)
  names=['normal-entry.patch','base-v0-texture-view.patch','normal-hle-crash.patch','horizon-address-signal.patch','normal-runtime-diagnostics.patch']+([] if BASELINE else ['base-v0-logical-memory.patch'])
  for name in names:
   result=subprocess.run(['/usr/bin/patch','--batch','--fuzz=0','-p1','-i',str(COMPONENT/'patches'/name)],cwd=source,capture_output=True,text=True)
   if result.returncode:raise AssertionError(result.stdout+result.stderr)
  (cls.work/'actual_svc.c').write_bytes((source/'dm/host/hle_svc.c').read_bytes())
  (cls.work/'dm.h').write_text(SHIM);(cls.work/'driver.c').write_text(DRIVER)
  (cls.work/'provider.c').write_text('#define botw_base_v0_heap_live_available owned_unavailable_native\n#include "'+str(OLD/'base_v0_heap.c')+'"\n#undef botw_base_v0_heap_live_available\nint owned_live_query(size_t*,char*,size_t);\nint botw_base_v0_heap_live_available(size_t *o,char *e,size_t n){return owned_live_query(o,e,n); }\n')
  (cls.work/'address_provider.c').write_text('#include <sys/mman.h>\n#include <stdint.h>\n#include <stdatomic.h>\n#include <assert.h>\nextern int fail_reserve;extern _Atomic unsigned reserve_calls;\nstatic void *owned_reserve_map(void *a,size_t n,int p,int f,int fd,off_t o){reserve_calls++;if(fail_reserve)return mmap(NULL,SIZE_MAX&~(size_t)0x3fff,p,f,fd,o);return mmap(a,n,p,f,fd,o); }\n#define mmap owned_reserve_map\n#include "'+str(OLD/'whole_address_space.c')+'"\n')
  cls.binary=cls.work/'probe'
  command=['clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-format','-Wno-unused-function','-Wno-unused-variable','-Wno-unused-parameter','-Wno-unused-but-set-variable','-pthread','-fsanitize=address,undefined','-fno-sanitize-recover=all','-DNXNATIVE_NORMAL_POLICY=1','-DNXNATIVE_BASE_V0_MEMORY=1','-I',str(cls.work),'-I',str(COMPONENT/'adapter'),'-I',str(OLD),str(cls.work/'driver.c'),str(cls.work/'provider.c'),str(cls.work/'address_provider.c'),str(OLD/'whole_policy.c'),str(COMPONENT/'adapter/base_v0_memory.c'),str(COMPONENT/'adapter/normal_failures.c'),'-o',str(cls.binary)]
  result=subprocess.run(command,capture_output=True,text=True)
  if result.returncode:raise AssertionError(result.stdout+result.stderr)
 def case(self,name,code=0):
  result=subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=10)
  self.assertEqual(result.returncode,code,result.stdout+result.stderr)
  if not code:self.assertIn('NXNATIVE_BASE_V0_MEMORY_OWNED_CPU_OK',result.stdout)
 def test_both_complete_patched_translation_units_compile_with_actual_upstream_headers(self):
  generated=self.work/'generated';generated.mkdir(exist_ok=True)
  (generated/'game_profile.h').write_text('#define DM_GAME_MODULES {"main","subsdk0","subsdk1","subsdk2","subsdk3","sdk"}\n#define DM_GAME_NMOD 6\n#define DM_GAME_PROGRAM_ID 0x01007ef00011e000ull\n')
  for name in ('main.c','hle_svc.c'):
   command=['clang','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-Wno-format','-Wno-unused-parameter','-Wno-unused-variable','-Wno-unused-function','-Wno-unused-but-set-variable','-DNXNATIVE_NORMAL_POLICY=1','-DNXNATIVE_BASE_V0_MEMORY=1','-I',str(generated),'-I',str(COMPONENT/'adapter'),'-I',str(OLD),'-c',str(self.source/'dm/host'/name),'-o',str(self.work/(name+'.o'))]
   result=subprocess.run(command,capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_memory_mode_requires_the_normal_failure_policy_at_compile_time(self):
  source=self.work/'invalid-policy.c';source.write_text('#include "base_v0_memory.h"\n')
  result=subprocess.run(['clang','-std=c11','-DNXNATIVE_BASE_V0_MEMORY=1','-I',str(COMPONENT/'adapter'),'-c',str(source),'-o',str(self.work/'invalid-policy.o')],capture_output=True,text=True)
  self.assertNotEqual(result.returncode,0)
  self.assertIn('requires NXNATIVE_NORMAL_POLICY=1',result.stderr)
 def test_actual_reservation_failure_does_not_publish_or_try_smaller_capacity(self):self.case('reserve-failure')
 def test_original_production_live_query_refuses_unsupported_mac_without_fake_value(self):self.case('native-query')
 def test_parallel_initialization_retains_exactly_one_real_reservation(self):self.case('parallel')
 def test_too_small_and_overflowed_layouts_leave_output_unchanged(self):self.case('layout')
 def test_stack_protection_failure_does_not_publish_borrow(self):self.case('map-protect')
 def test_alias_usage_prevents_growth_above_logical_pool(self):self.case('alias-grow')
 def test_alias_mapping_checks_fresh_headroom_before_real_growth(self):self.case('alias-guard')
 def test_remapped_alias_pages_are_zero_without_erasing_already_mapped_neighbors(self):self.case('alias-zero')
 def test_alias_zeroing_growth_must_fit_fresh_physical_budget(self):self.case('alias-budget')
 def test_retained_image_geometry_cannot_be_reselected(self):self.case('immutable')
 def test_wrong_module_count_fails_before_mapping(self):self.case('module-count',70)
 def test_image_accounting_overflow_fails_before_mapping(self):self.case('image-overflow',70)
 def test_alias_output_buffer_requires_every_spanned_logical_page_mapped(self):self.case('alias-output')
 def test_unknown_getinfo_is_unsupported_and_does_not_publish_output(self):self.case('unknown-info')
 def test_query_memory_initializes_all_forty_bytes_including_padding(self):self.case('query-padding')
 def test_unimplemented_permission_mutation_returns_explicit_failure(self):self.case('permission')
 def test_actual_permission_failure_does_not_publish_heap_size(self):self.case('protect')
 def test_full_retained_five_gib_original_H_R_and_sparse_first_last_pages(self):self.case('five')
 def test_insufficient_fresh_live_headroom_fails_before_guest_memory_publication(self):self.case('guard',70)
 def test_heap_alignment_failure_preserves_state(self):self.case('alignment')
 def test_fresh_headroom_is_requeried_before_growth(self):self.case('growth-guard')
 def test_actual_stack_copy_borrow_and_return_preserve_ownership(self):self.case('stack')
 def test_invalid_stack_source_is_rejected_without_dereference(self):self.case('bad-source')
 def test_overflowed_stack_and_alias_ranges_are_rejected(self):self.case('overflow')
 def test_live_stack_borrow_prevents_heap_shrink(self):self.case('borrow-shrink')
 def test_query_uses_real_owned_output_and_rejects_invalid_output(self):self.case('query')
 def test_alias_bitmap_query_and_partial_host_page_unmap_preserve_neighbor(self):self.case('alias')
if __name__=='__main__':unittest.main()
