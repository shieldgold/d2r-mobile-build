"""Owned VM copies preserve allocation identity without running a guest or GPU."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

import test_botw_runtime_ipc_copy as ipc_fixture

ROOT = Path(__file__).resolve().parents[1]
INTERPOSE = r'''
#include <mach/mach.h>
static kern_return_t observed_read(vm_map_t,vm_address_t,vm_size_t,vm_address_t,vm_size_t*);
static kern_return_t observed_write(vm_map_t,vm_address_t,vm_offset_t,mach_msg_type_number_t);
#define vm_read_overwrite observed_read
#define vm_write observed_write
#include "RUNTIME_SOURCE"
#undef vm_read_overwrite
#undef vm_write
'''
EXTRA = r'''
static unsigned copy_calls, bad_chunks, fail_copy, short_copy;
static void observe_chunk(uintptr_t d,uintptr_t s,size_t n){
 copy_calls++;size_t p=(size_t)getpagesize();
 if(n>4095||n>p-(d&(p-1))||n>p-(s&(p-1)))bad_chunks++;
}
static kern_return_t observed_read(vm_map_t t,vm_address_t s,vm_size_t n,vm_address_t d,vm_size_t *got){
 observe_chunk(d,s,n);if(copy_calls==fail_copy)return KERN_RESOURCE_SHORTAGE;
 kern_return_t k=vm_read_overwrite(t,s,n,d,got);if(k==KERN_SUCCESS&&copy_calls==short_copy&&*got)*got-=1;return k;
}
static kern_return_t observed_write(vm_map_t t,vm_address_t d,vm_offset_t s,mach_msg_type_number_t n){
 observe_chunk(d,s,n);if(copy_calls==fail_copy)return KERN_RESOURCE_SHORTAGE;return vm_write(t,d,s,n);
}
#define REQUIRE(x) do{if(!(x)){fprintf(stderr,"CHECK:%d:%s calls=%u bad_chunks=%u\n",__LINE__,#x,copy_calls,bad_chunks);return 91;}}while(0)
static unsigned char *owned_source;
static size_t owned_bytes;
static void own_setup(void){
 page=(size_t)getpagesize();owned_bytes=4u*1024*1024+2*page;
 owned_source=mmap(NULL,owned_bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
 memory=mmap(NULL,owned_bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
 assert(owned_source!=MAP_FAILED&&memory!=MAP_FAILED);memset(owned_source,0x31,owned_bytes);memset(memory,0xa5,owned_bytes);
 state.modules[0]=(Module){.name="own-dest",.base=memory,.size=owned_bytes,.patch_off=owned_bytes};
 state.modules[1]=(Module){.name="own-source",.base=owned_source,.size=owned_bytes,.patch_off=owned_bytes};
}
typedef struct {unsigned leaves;vm_object_id_t object;} Identity;
static Identity identity(void *base,size_t n){
 uintptr_t at=(uintptr_t)base,end=at+n;Identity id={0};
 while(at<end){vm_address_t r=at;vm_size_t bytes=0;natural_t depth=0;vm_region_submap_info_data_64_t info={0};
  for(;;){mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;assert(vm_region_recurse_64(mach_task_self(),&r,&bytes,&depth,(vm_region_recurse_info_t)&info,&count)==KERN_SUCCESS&&r<=at&&bytes&&at-r<bytes&&count>=VM_REGION_SUBMAP_INFO_V2_COUNT_64);if(info.is_submap){depth++;r=at;continue;}if(!id.leaves)id.object=info.object_id_full;id.leaves++;at+=MIN((size_t)(bytes-(at-r)),end-at);break;}}
 return id;
}
static void *owned_alias(void *p){vm_address_t d=0;vm_prot_t c=0,m=0;assert(vm_remap(mach_task_self(),&d,owned_bytes,0,VM_FLAGS_ANYWHERE,mach_task_self(),(vm_address_t)p,FALSE,&c,&m,VM_INHERIT_COPY)==KERN_SUCCESS);return(void*)d;}
static int owned_copy(int read,void *d,const void *s,size_t n){char e[512];return read?protected_read_guest("OwnVM",(uintptr_t)s,d,n,e,sizeof e):protected_write_guest("OwnVM",(uintptr_t)d,s,n,0,e,sizeof e);}
static int layout(int read,int alias){
 void *view=alias?owned_alias(memory):NULL;Identity before=identity(memory,owned_bytes),source=identity(owned_source,owned_bytes);REQUIRE(before.leaves==1&&source.leaves==1);
 REQUIRE(!owned_copy(read,memory+1,owned_source+1,4u*1024*1024));REQUIRE(!memcmp(memory+1,owned_source+1,4u*1024*1024));
 Identity after=identity(memory,owned_bytes),source_after=identity(owned_source,owned_bytes);
 REQUIRE(after.leaves==1&&after.object==before.object);REQUIRE(source_after.leaves==1&&source_after.object==source.object);
 if(view){REQUIRE(!memcmp(view,memory,owned_bytes));REQUIRE(!munmap(view,owned_bytes));}return 0;
}
static int permissions(void){
 for(int read=0;read<2;read++)for(int source=0;source<2;source++){
  memset(memory,0xa5,owned_bytes);unsigned char *bad=source?owned_source:memory;REQUIRE(!mprotect(bad+page,page,PROT_NONE));copy_calls=bad_chunks=0;
  REQUIRE(owned_copy(read,memory,owned_source,2*page)==-1);REQUIRE(!copy_calls);for(size_t i=0;i<page;i++)REQUIRE(memory[i]==0xa5);
  REQUIRE(!mprotect(bad+page,page,PROT_READ|PROT_WRITE));
 }
 REQUIRE(!mprotect(owned_source,owned_bytes,PROT_READ));REQUIRE(!owned_copy(0,memory,owned_source,2*page));REQUIRE(!mprotect(owned_source,owned_bytes,PROT_READ|PROT_WRITE));return 0;
}
static int overlap(void){
 for(int read=0;read<2;read++)for(int reverse=0;reverse<2;reverse++){
  for(size_t i=0;i<4*page;i++)memory[i]=(unsigned char)((i/251)%251);unsigned char *expected=malloc(4*page);REQUIRE(expected);memcpy(expected,memory,4*page);
  size_t so=reverse?128:0,d=reverse?0:128;memmove(expected+d,expected+so,2*page);REQUIRE(!owned_copy(read,memory+d,memory+so,2*page));REQUIRE(!memcmp(expected,memory,4*page));free(expected);
 }
 REQUIRE(!mprotect(memory,page,PROT_READ));copy_calls=0;REQUIRE(owned_copy(0,memory,memory,page)==-1&&!copy_calls);REQUIRE(!mprotect(memory,page,PROT_READ|PROT_WRITE));return 0;
}
static int chunks(void){
 for(int read=0;read<2;read++){copy_calls=bad_chunks=0;REQUIRE(!owned_copy(read,memory+4094,owned_source+17,2*page+31));REQUIRE(copy_calls>1&&!bad_chunks);REQUIRE(!memcmp(memory+4094,owned_source+17,2*page+31));}return 0;
}
static int failures(void){
 for(int read=0;read<2;read++){memset(memory,0xa5,owned_bytes);copy_calls=bad_chunks=0;fail_copy=2;REQUIRE(owned_copy(read,memory,owned_source,2*page)==-1);REQUIRE(copy_calls==2);for(size_t i=4095;i<2*page;i++)REQUIRE(memory[i]==0xa5);fail_copy=0;}
 copy_calls=0;short_copy=2;REQUIRE(owned_copy(1,memory,owned_source,2*page)==-1&&copy_calls==2);short_copy=0;return 0;
}
static int admission(void){
 copy_calls=0;REQUIRE(!owned_copy(0,NULL,NULL,0)&&!owned_copy(1,NULL,NULL,0)&&!copy_calls);
 REQUIRE(owned_copy(0,memory,owned_source,(size_t)UINT32_MAX+1)==-1&&!copy_calls);
 state.modules[0].text_end=page;REQUIRE(owned_copy(0,memory,owned_source,64)==-1&&!copy_calls);state.modules[0].text_end=0;
 REQUIRE(owned_copy(0,memory,(const void*)(UINTPTR_MAX-7),32)==-1&&!copy_calls);
 REQUIRE(owned_copy(1,(void*)(UINTPTR_MAX-7),owned_source,32)==-1&&!copy_calls);return 0;
}
int main(int argc,char **argv){assert(argc==2);own_setup();int r;
 if(!strcmp(argv[1],"layout-write"))r=layout(0,0);else if(!strcmp(argv[1],"layout-read"))r=layout(1,0);else if(!strcmp(argv[1],"layout-alias"))r=layout(0,1);
 else if(!strcmp(argv[1],"permissions"))r=permissions();else if(!strcmp(argv[1],"overlap"))r=overlap();else if(!strcmp(argv[1],"chunks"))r=chunks();else if(!strcmp(argv[1],"failures"))r=failures();else{assert(!strcmp(argv[1],"admission"));r=admission();}
 if(!r){assert(!munmap(memory,owned_bytes)&&!munmap(owned_source,owned_bytes));puts("OWNED_VM_COPY_OK");}return r;}
'''
DRIVER = ipc_fixture.DRIVER.replace('#include "RUNTIME_SOURCE"', INTERPOSE).replace('int main(', 'int old_fixture_main(') + EXTRA

@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Darwin ARM64 owned VM required; no GPU')
class RuntimeVmCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-vm-copy-')
        cls.addClassCleanup(cls.build.cleanup)
        folder = Path(cls.build.name)
        driver = folder / 'copy.c'
        driver.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = folder / 'copy'
        sources = [driver, *(ROOT / 'native/botw_host' / n for n in ('entry.S','services.c','kernel.c','hipc_address.c','address_space.c')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / n for n in ('romfs.c','savefs.c','local_user.c'))]
        result = subprocess.run(['xcrun','clang','-O2','-g','-Wall','-Wextra','-Werror','-framework','Security', *(str(s) for s in sources),'-o',str(cls.exe)],capture_output=True,text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
    def mode(self, name):
        result = subprocess.run([str(self.exe),name],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('OWNED_VM_COPY_OK',result.stdout)
    def test_large_protected_write_retains_owned_vm_identity(self): self.mode('layout-write')
    def test_large_protected_read_retains_source_and_destination_vm_identity(self): self.mode('layout-read')
    def test_existing_shared_alias_stays_coherent_without_new_leaf_boundaries(self): self.mode('layout-alias')
    def test_whole_source_and_destination_permissions_precede_any_copy(self): self.mode('permissions')
    def test_numeric_overlap_matches_snapshot_and_same_address_still_checks_permissions(self): self.mode('overlap')
    def test_each_mach_call_is_small_and_inside_both_operand_pages(self): self.mode('chunks')
    def test_later_mach_failure_and_short_read_are_returned_without_fallback(self): self.mode('failures')
    def test_zero_length_logical_ownership_uint32_and_overflow_admission(self): self.mode('admission')
