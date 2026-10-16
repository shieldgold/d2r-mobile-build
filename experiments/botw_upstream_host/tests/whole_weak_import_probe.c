/* Normal owned symbols/relocation storage. Never executes a guest stub or code. */
#define _DARWIN_C_SOURCE
#include "dm.h"
#include "hle.h"
#include "relr.h"
#include <assert.h>
#include <dlfcn.h>
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>
#include <sys/wait.h>
#include <unistd.h>
module_t g_mod[MAX_MOD];int g_nmod;
struct dm_flags g_flag;
static unsigned host_calls,force_references;
static uint64_t owned_hle_first(void){return ++host_calls;}
static uint64_t owned_hle_second(void){host_calls+=2;return host_calls;}
static uint64_t owned_forced(void){host_calls+=3;return host_calls;}
/* Keep this real Mach-O section byte-exact; ASan global redzones would become
 * counterfeit registry entries. All production resolver code stays sanitized. */
__attribute__((used,section("__DATA,swhle"),no_sanitize("address"))) static const hle_ent owned_hles[]={
    {"owned_hle",(void*)owned_hle_first},{"owned_hle",(void*)owned_hle_second}
};
uint64_t unimpl_stubs[4096]; /* Addresses only; no executable guest trampolines. */
static const hle_force_ent owned_forces[]={{"owned_hle",(void*)owned_forced,"owned CPU force precedence",DM_TM_BOTW}};
static const hle_force_ent *g_force=owned_forces;
static unsigned g_nforce;
static unsigned force_hits[1];static uint8_t force_on[1]={1};
static unsigned *g_force_hits=force_hits;static uint8_t *g_force_on=force_on;
static unsigned long g_symchk_n,g_symchk_bad;
void *hlecount_wrap(const char *kind,const char *name,void *function){
    assert(kind&&name&&function);return function;
}
int base_v0_force_reference(const char *module,const char *symbol){
    assert(!strcmp(module,"owned-consumer")&&!strcmp(symbol,"owned_hle"));++force_references;return 0;
}
int dm_stop_on_unimpl(void){return 1;}
_Noreturn void botw_whole_host_exit(int code){_exit(code);}
#include "original_stop.inc"
#include "original_import.inc"
#include "original_resolution.inc"
#include "original_relocation.inc"
static uint8_t consumer_memory[16384],provider_memory[16384];
static Elf64_Sym symbols[8],provider_symbols[4];
static char strings[256],provider_strings[256];
static Elf64_Dyn dynamics[5];
static module_t consumer;
static unsigned add_string(char *table,const char *name){
    size_t at=1;while(table[at])at+=strlen(table+at)+1;
    assert(at+strlen(name)+1<256);strcpy(table+at,name);return (unsigned)at;
}
static void setup(void){
    strcpy(consumer.name,"owned-consumer");consumer.base=consumer_memory;consumer.size=sizeof consumer_memory;
    consumer.symtab=symbols;consumer.strtab=strings;consumer.nsym=8;consumer.dyn=dynamics;
    dynamics[0]=(Elf64_Dyn){DT_RELA,{.d_ptr=1024}};
    dynamics[1]=(Elf64_Dyn){DT_RELASZ,{.d_val=sizeof(Elf64_Rela)}};
    dynamics[2]=(Elf64_Dyn){DT_JMPREL,{.d_ptr=2048}};
    dynamics[3]=(Elf64_Dyn){DT_PLTRELSZ,{.d_val=0}};
}
static void symbol(unsigned index,const char *name,unsigned binding,unsigned type,unsigned section,uint64_t value){
    assert(index<8);symbols[index]=(Elf64_Sym){add_string(strings,name),(uint8_t)((binding<<4)|type),0,(uint16_t)section,value,0};
}
static uint64_t relocate_one(unsigned index,unsigned kind,int64_t addend){
    assert(index<8||kind==R_AARCH64_RELATIVE);
    Elf64_Rela *relocation=(void*)(consumer_memory+1024);
    *relocation=(Elf64_Rela){4096,((uint64_t)index<<32)|kind,addend};
    memset(consumer_memory+4096,0x5a,8);relocate(&consumer);
    uint64_t result;memcpy(&result,consumer_memory+4096,8);return result;
}
static void module_definition(const char *name,int duplicate){
    module_t *provider=&g_mod[0];strcpy(provider->name,"owned-provider");provider->base=provider_memory;provider->size=sizeof provider_memory;
    provider->symtab=provider_symbols;provider->strtab=provider_strings;provider->nsym=duplicate?3:2;
    provider_symbols[1]=(Elf64_Sym){add_string(provider_strings,name),0x20,0,1,256,0};
    if(duplicate)provider_symbols[2]=(Elf64_Sym){provider_symbols[1].st_name,0x10,0,1,512,0};
    g_nmod=1;build_symhash(provider);
}
static void require_zero(uint64_t value){
    if(value||g_nstub){fputs("owned weak unresolved incorrectly acquired a named stub\n",stderr);exit(91);}
}
int main(int argc,char **argv){
    assert(argc==2);setup();const char *mode=argv[1];
    if(!strcmp(mode,"weak-zero")){
        symbol(1,"nnosGetDefaultUserExceptionStackForApplication",2,0,0,0);
        require_zero(relocate_one(1,R_AARCH64_GLOB_DAT,0));
        require_zero(relocate_one(1,R_AARCH64_JUMP_SLOT,0));
    }else if(!strcmp(mode,"strong")){
        symbol(1,"owned_strong",1,0,0,0);uint64_t first=relocate_one(1,R_AARCH64_GLOB_DAT,0);
        assert(first==(uintptr_t)&unimpl_stubs[0]&&g_nstub==1);
        assert(relocate_one(1,R_AARCH64_JUMP_SLOT,0)==first&&g_nstub==1);
        pid_t child=fork();assert(child>=0);if(!child){unimpl_c(0,NULL);_exit(92);}
        int status;assert(waitpid(child,&status,0)==child&&WIFEXITED(status)&&WEXITSTATUS(status)==3);
    }else if(!strcmp(mode,"weak-after-strong")){
        symbol(1,"owned_same_name",1,0,0,0);assert(relocate_one(1,R_AARCH64_GLOB_DAT,0));assert(g_nstub==1);
        symbol(2,"owned_same_name",2,0,0,0);assert(!relocate_one(2,R_AARCH64_GLOB_DAT,0));assert(g_nstub==1);
        assert(!relocate_one(2,R_AARCH64_JUMP_SLOT,0)&&g_nstub==1);
    }else if(!strcmp(mode,"hle")){
        symbol(1,"owned_hle",2,0,0,0);uint64_t result=relocate_one(1,R_AARCH64_GLOB_DAT,0);
        assert(result==(uintptr_t)owned_hle_first&&!g_nstub);assert(((uint64_t(*)(void))(uintptr_t)result)()==1);
    }else if(!strcmp(mode,"module")){
        symbol(1,"owned_hle",2,0,0,0);module_definition("owned_hle",1);
        assert(relocate_one(1,R_AARCH64_GLOB_DAT,0)==(uintptr_t)provider_memory+256&&!g_nstub&&!host_calls);
    }else if(!strcmp(mode,"defined-weak")){
        symbol(1,"owned_hle",2,0,1,768);module_definition("owned_hle",0);
        assert(relocate_one(1,R_AARCH64_GLOB_DAT,0)==(uintptr_t)consumer_memory+768&&!g_nstub);
    }else if(!strcmp(mode,"force")){
        g_nforce=1;symbol(1,"owned_hle",2,0,1,768);module_definition("owned_hle",0);
        assert(relocate_one(1,R_AARCH64_GLOB_DAT,0)==(uintptr_t)owned_forced);
        assert(force_hits[0]==1&&force_references==1&&!g_nstub);
    }else if(!strcmp(mode,"addends")){
        symbol(1,"owned_optional",2,0,0,0);
        assert(relocate_one(1,R_AARCH64_GLOB_DAT,37)==37);
        assert(relocate_one(1,R_AARCH64_ABS64,-5)==(uint64_t)-5);
        assert(!relocate_one(1,R_AARCH64_JUMP_SLOT,37));
        symbol(2,"owned_hle",2,0,0,0);
        assert(relocate_one(2,R_AARCH64_GLOB_DAT,7)==(uintptr_t)owned_hle_first+7);
        assert(relocate_one(2,R_AARCH64_JUMP_SLOT,7)==(uintptr_t)owned_hle_first);
    }else if(!strcmp(mode,"boundaries")){
        symbol(7,"owned_last",2,0,0,0);require_zero(relocate_one(7,R_AARCH64_GLOB_DAT,0));
        assert(relocate_one(UINT32_MAX,R_AARCH64_RELATIVE,128)==(uintptr_t)consumer_memory+128&&!g_nstub);
    }else assert(!"unknown owned fixture mode");
    for(int i=0;i<g_nmod;i++)free(g_mod[i].symh);
    puts("WHOLE_WEAK_IMPORT_CPU_OK");return 0;
}
