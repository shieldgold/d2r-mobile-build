/* Owned mmap pages, real shared VM aliases, actual staged G/g_h/g_map and SVC
 * bodies. No Guest bytes, complete host, diagnostic path or GPU is executed. */
#include "dm.h"
#include "hle_thread_core.h"
#include "whole_kernel.h"
#include "whole_transfer_internal.h"
#include "base_v0_heap.h"
#include <setjmp.h>
#include <stdatomic.h>
#include <stdarg.h>
#include <limits.h>
#include <errno.h>
#include <time.h>
#include <unistd.h>
#include "whole_transfer_vm_hooks.h"
#undef LOG
#define LOG(...) ((void)0)
static int failed_unmaps,failed_protects,unexpected_alias_protection,calloc_failure;
static unsigned syscall_failures;
static void *last_alias,*protect_failure_address;
int owned_transfer_munmap(void *memory,size_t size){
    if(failed_unmaps){failed_unmaps--;syscall_failures++;return (munmap)((char*)memory+1,size);}
    return (munmap)(memory,size);
}
int owned_transfer_mprotect(void *memory,size_t size,int protection){
    if(failed_protects&&(!protect_failure_address||memory==protect_failure_address)){
        failed_protects--;syscall_failures++;return (mprotect)((char*)memory+1,size,protection);
    }
    return (mprotect)(memory,size,protection);
}
kern_return_t owned_transfer_remap(vm_map_t target,vm_address_t *address,vm_size_t size,vm_address_t mask,
        int flags,vm_map_t source,vm_address_t origin,boolean_t copy,vm_prot_t *current,vm_prot_t *maximum,vm_inherit_t inherit){
    kern_return_t result=(vm_remap)(target,address,size,mask,flags,source,origin,copy,current,maximum,inherit);
    if(!result){last_alias=(void*)(uintptr_t)*address;if(unexpected_alias_protection)*current=VM_PROT_READ;}
    return result;
}
static void *owned_calloc(size_t count,size_t size){if(calloc_failure&&!--calloc_failure)return NULL;return (calloc)(count,size);}
#define calloc(...) owned_calloc(__VA_ARGS__)
#define CHECK(x) do{if(!(x)){fprintf(stderr,"TRANSFER_CHECK:%d %s\n",__LINE__,#x);return 91;}}while(0)
static char error[256];
#define E error,sizeof error
#ifdef WHOLE_TRANSFER_BASELINE
static int diagnostic(char *message,size_t capacity,const char *format,...){
    if(message&&capacity){va_list args;va_start(args,format);vsnprintf(message,capacity,format,args);va_end(args);}return -1;
}
#include "original_alias.inc"
#else
pthread_key_t g_key18,g_keytls,g_keytlr,g_keygate,g_keyres[2],g_keyaspect,g_keyaspectui;
void dm_gate(void){abort();} /* Identity only; executing a guest gate is forbidden. */
#include "thread.c"
module_t g_mod[MAX_MOD];int g_nmod;
struct dm_flags g_flag;
#define MIB ((size_t)1024*1024)
#define RESERVE ((uint64_t)1024*MIB)
static uint8_t *g_as,*g_heap,*g_stack;
static size_t g_as_size=RESERVE;
static size_t g_heap_cap=640*MIB,g_stack_cap=64*MIB;
static _Atomic size_t g_heap_cur;
static int g_heap_rejects;
static pthread_mutex_t g_heap_resize_lock=PTHREAD_MUTEX_INITIALIZER;
int dm_guest_write_prepare_checked(void *memory,size_t size){(void)memory;(void)size;return 1;}
static size_t image_bytes(void){return 4*MIB;}
static uint64_t app_pool(void){return g_heap_cap+image_bytes();}
#include "original_memory_registry.inc"
#include "original_kernel_registry.inc"
#include "original_cur.inc"
static int64_t block(thr_t *thread,int kind,int64_t timeout){(void)thread;(void)kind;(void)timeout;abort();}
#include "original_atomics.inc"
uint64_t now_ns(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*UINT64_C(1000000000)+t.tv_nsec;}
uint64_t guest_ns(void){return now_ns();}
int dm_detclock_enabled(void){return 0;}
static int64_t now_mono_ns(void){return (int64_t)now_ns();}
static int owned_word_svc(int num,uint64_t *regs){switch(num){
#include "original_word_cases.inc"
default:abort();}}
static uint64_t owned_memory_svc(int number,uint64_t *regs){switch(number){
#include "original_memory_cases.inc"
default:abort();}}
static int owned_query(botw_base_v0_heap_inputs *out,char *message,size_t capacity){
    (void)message;(void)capacity;*out=(botw_base_v0_heap_inputs){640*MIB,896*MIB,4*MIB};return 0;
}
int dm_guest_heap_plan_inputs(botw_base_v0_heap_inputs *out,char *message,size_t capacity){return owned_query(out,message,capacity);}
static int create(void *source,size_t size,unsigned permission,uint32_t *handle){
    uint64_t regs[31]={0};regs[0]=0xfeed;regs[1]=(uintptr_t)source;regs[2]=size;regs[3]=permission;
    int result=botw_whole_transfer_dispatch(0x15,regs,E);
    if(result<0)return -1;if(regs[0])return (int)regs[0];*handle=(uint32_t)regs[1];return 0;
}
static unsigned live(void){unsigned n=0;for(unsigned i=1;i<MAX_H;i++)n+=g_h[i].type!=O_FREE;return n;}
static int query(void *address,uint64_t *base,uint64_t *size,unsigned *attribute,unsigned *permission){
    unsigned char info[36];memset(info,0xa5,sizeof info);uint64_t regs[31]={0};regs[0]=(uintptr_t)info;regs[2]=(uintptr_t)address;
    owned_memory_svc(0x06,regs);if(regs[0])return -1;
    memcpy(base,info,8);memcpy(size,info+8,8);memcpy(attribute,info+20,4);memcpy(permission,info+24,4);return 0;
}
static uint32_t shared_handle;
static _Atomic int start_parallel;
static void *pin_worker(void *unused){
    (void)unused;while(!atomic_load(&start_parallel)){}
    for(unsigned i=0;i<200;i++){
        botw_whole_transfer_lease *lease=NULL;botw_whole_transfer_view view;char message[256];
        if(!botw_whole_transfer_acquire(shared_handle,16,3,&lease,&view,message,sizeof message)){
            if(!lease||!view.rw_alias||botw_whole_transfer_release(&lease,message,sizeof message)||lease)return (void*)1;
        }
    }
    return NULL;
}
static int initialize(void){
    CHECK(!pthread_key_create(&g_keytlr,NULL)&&!pthread_key_create(&g_keytls,NULL)&&!pthread_key_create(&g_keyres[0],NULL)&&!pthread_key_create(&g_keyres[1],NULL)&&!pthread_key_create(&g_keythr,NULL)&&!pthread_key_create(&g_keygate,NULL)&&!pthread_key_create(&g_key18,NULL));
    guest_thread_init();
    g_as=mmap(NULL,RESERVE,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(g_as!=MAP_FAILED);g_heap=g_as;g_stack=g_as+768*MIB;
    CHECK(!botw_base_v0_heap_thread_init(g_keyres[0],g_keyres[1],owned_query,E));
    uint64_t regs[31]={0};regs[1]=g_heap_cap;owned_memory_svc(1,regs);CHECK(!regs[0]&&g_heap_cur==g_heap_cap);return 0;
}
#endif
int main(int argc,char **argv){
    CHECK(argc==2);size_t page=(size_t)getpagesize();
    if(!strcmp(argv[1],"alias-cleanup")){
        void *source=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(source!=MAP_FAILED);
        void *alias=NULL;unexpected_alias_protection=1;failed_unmaps=1;
#ifdef WHOLE_TRANSFER_BASELINE
        int status=transfer_alias((uintptr_t)source,page,0,&alias,E);
#else
        int status=botw_whole_transfer_vm_alias((uintptr_t)source,page,0,&alias,E);
#endif
        int retained=status&&alias==last_alias&&syscall_failures==1;
        CHECK(!(munmap)(last_alias,page));CHECK(!(munmap)(source,page));
        CHECK(retained);
    }else{
#ifdef WHOLE_TRANSFER_BASELINE
        fprintf(stderr,"baseline has no typed transfer integration\n");return 92;
#else
        CHECK(!initialize());uint8_t *source=g_heap+2*MIB;source[0]=0x4a;
        uint32_t handle=0;botw_whole_transfer_lease *lease=NULL;botw_whole_transfer_view view={0};
        if(!strcmp(argv[1],"shared")){
            CHECK(!create(source,page,0,&handle));CHECK(botw_whole_kernel_handle_kind(handle)==BOTW_WHOLE_HANDLE_TRANSFER);
            CHECK(!botw_whole_transfer_acquire(handle,page-1,3,&lease,&view,E));
            CHECK(view.alias_size==page&&view.requested_size==page-1&&view.source==(uintptr_t)source&&!view.owner_permission);
            CHECK(((uint8_t*)view.rw_alias)[0]==0x4a);((uint8_t*)view.rw_alias)[0]=0x76;
            CHECK(!dm_guest_heap_readable((uintptr_t)source,1)&&!dm_guest_memory_access((uintptr_t)source,1,0));
            CHECK(botw_whole_memory_classify((uintptr_t)source,1,0)==-1&&!botw_whole_memory_access((uintptr_t)source,1,0));
            CHECK(botw_whole_memory_classify((uintptr_t)&view,sizeof view,1)==0);
            CHECK(botw_whole_memory_access((uintptr_t)(source+page),1,1));
            CHECK(!botw_whole_transfer_close(handle,E)&&g_nmap==1&&!live());
            CHECK(!botw_whole_transfer_release(&lease,E)&&!lease&&!g_nmap&&source[0]==0x76);
            CHECK(botw_whole_transfer_close(handle,E));
        }else if(!strcmp(argv[1],"permissions")){
            for(unsigned permission=0;permission<=3;permission++){
                if(permission==2){CHECK(create(source,page,permission,&handle)==0xd801);continue;}
                CHECK(!create(source,page,permission,&handle));uint64_t base,size;unsigned attr,perm;
                CHECK(!query(source,&base,&size,&attr,&perm)&&base==(uintptr_t)source&&size==page&&attr==1&&perm==permission);
                CHECK(dm_guest_memory_access((uintptr_t)source,1,0)==(permission!=0));
                CHECK(dm_guest_memory_access((uintptr_t)source,1,1)==(permission==3));
                CHECK(!query(source-1,&base,&size,&attr,&perm)&&base+size==(uintptr_t)source&&!attr&&perm==3);
                CHECK(!query(source+page,&base,&size,&attr,&perm)&&base==(uintptr_t)source+page&&!attr);
                CHECK(!botw_whole_transfer_close(handle,E));
            }
        }else if(!strcmp(argv[1],"last-restore")){
            CHECK(!create(source,page,0,&handle)&&!botw_whole_transfer_acquire(handle,page,3,&lease,&view,E));
            CHECK(!botw_whole_transfer_close(handle,E));CHECK(!(munmap)(source,page));
            CHECK(botw_whole_transfer_release(&lease,E)&&lease&&g_nmap==1);
            /* Restore the owned test address as a true shared remap for retry. */
            vm_address_t address=(uintptr_t)source;vm_prot_t current,maximum;
            CHECK(!(vm_remap)(mach_task_self(),&address,page,0,VM_FLAGS_FIXED|VM_FLAGS_OVERWRITE,mach_task_self(),(uintptr_t)view.rw_alias,FALSE,&current,&maximum,VM_INHERIT_COPY));
            CHECK(!botw_whole_transfer_release(&lease,E)&&!lease&&!g_nmap&&source[0]==0x4a);
        }else if(!strcmp(argv[1],"last-unmap")){
            CHECK(!create(source,page,0,&handle)&&!botw_whole_transfer_acquire(handle,page,3,&lease,&view,E));
            CHECK(!botw_whole_transfer_close(handle,E));failed_unmaps=1;
            CHECK(botw_whole_transfer_release(&lease,E)&&lease&&g_nmap==1&&syscall_failures==1);
            CHECK(!dm_guest_memory_access((uintptr_t)source,1,0)&&((uint8_t*)view.rw_alias)[0]==0x4a);
            CHECK(!botw_whole_transfer_release(&lease,E)&&!lease&&!g_nmap);
        }else if(!strcmp(argv[1],"svc-close-failure")){
            CHECK(!create(source,page,0,&handle));uint64_t regs[31]={0};regs[0]=handle;regs[1]=0xfeed;
            failed_unmaps=1;CHECK(botw_whole_transfer_dispatch(0x16,regs,E)==-1&&regs[0]==handle&&regs[1]==0xfeed);
            CHECK(live()==1&&g_nmap==1&&botw_whole_kernel_handle_kind(handle)==BOTW_WHOLE_HANDLE_TRANSFER);
            CHECK(botw_whole_transfer_dispatch(0x16,regs,E)==1&&!regs[0]&&regs[1]==0xfeed&&!live()&&!g_nmap);
        }else if(!strcmp(argv[1],"capacity-rollback")||!strcmp(argv[1],"capacity-quarantine")){
            static int tokens[MAX_H];for(unsigned i=1;i<MAX_H;i++){g_h[i].type=O_TRANSPORT;g_h[i].obj=&tokens[i];}
            int quarantine=!strcmp(argv[1],"capacity-quarantine");failed_unmaps=quarantine;
            CHECK(create(source,page,0,&handle)==(quarantine?-1:(int)R_OUT_OF_RES));
            CHECK(!handle&&g_nmap==quarantine&&live()==MAX_H-1);
            if(quarantine){
                CHECK(!g_map[0].transfer->published&&g_map[0].transfer->backing&&syscall_failures==1);
                CHECK(create(source,page,3,&handle)==0xd401);
                CHECK(!botw_whole_transfer_reap_pending(E)&&!g_nmap);
            }
            CHECK(source[0]==0x4a);
            memset(g_h,0,sizeof g_h);
        }else if(!strcmp(argv[1],"create-protect")){
            protect_failure_address=source;failed_protects=1;CHECK(create(source,page,0,&handle)==-1);
            CHECK(!handle&&!g_nmap&&!live()&&source[0]==0x4a&&syscall_failures==1);
        }else if(!strcmp(argv[1],"create-quarantine")){
            protect_failure_address=source;failed_protects=1;failed_unmaps=1;CHECK(create(source,page,0,&handle)==-1);
            CHECK(!handle&&g_nmap==1&&!live()&&g_map[0].transfer->blocked&&syscall_failures==2);
            CHECK(!dm_guest_memory_access((uintptr_t)source,1,0)&&create(source,page,3,&handle)==0xd401);
            CHECK(!botw_whole_transfer_reap_pending(E)&&!g_nmap&&source[0]==0x4a);
        }else if(!strcmp(argv[1],"duplicate-aba")){
            CHECK(!create(source,page,0,&handle)&&!botw_whole_transfer_acquire(handle,page,3,&lease,&view,E));
            uint32_t duplicate;CHECK(!botw_whole_transfer_duplicate(handle,&duplicate,E)&&duplicate!=handle);
            CHECK(!botw_whole_transfer_close(handle,E)&&!botw_whole_transfer_close(duplicate,E)&&g_nmap==1);
            uint32_t reused;CHECK(!create(source+page,page,0,&reused)&&reused==handle&&g_nmap==2);
            ((uint8_t*)view.rw_alias)[0]=0x88;CHECK(!botw_whole_transfer_release(&lease,E)&&source[0]==0x88&&g_nmap==1);
            CHECK(!dm_guest_memory_access((uintptr_t)(source+page),1,0)&&!botw_whole_transfer_close(reused,E)&&!g_nmap);
        }else if(!strcmp(argv[1],"parallel")){
            CHECK(!create(source,page,0,&shared_handle));uint32_t duplicate;CHECK(!botw_whole_transfer_duplicate(shared_handle,&duplicate,E));
            pthread_t threads[8];for(unsigned i=0;i<8;i++)CHECK(!pthread_create(&threads[i],NULL,pin_worker,NULL));
            atomic_store(&start_parallel,1);CHECK(!botw_whole_transfer_close(shared_handle,E));
            for(unsigned i=0;i<8;i++){void *result;CHECK(!pthread_join(threads[i],&result)&&!result);}
            CHECK(g_nmap==1&&!botw_whole_transfer_close(duplicate,E)&&!g_nmap);
        }else if(!strcmp(argv[1],"invalid")){
            CHECK(create(source+1,page,0,&handle)==0xcc01&&create(source,page-1,0,&handle)==0xca01);
            CHECK(create(g_heap+g_heap_cur,page,0,&handle)==0xd401);
            if(page>4096)CHECK(create(source+4096,4096,0,&handle)==0xd401);
            CHECK(!create(source,page,0,&handle));botw_whole_transfer_view unchanged={.source=0xfeed};view=unchanged;
            CHECK(botw_whole_transfer_acquire(handle,page+1,3,&lease,&view,E)&&!lease&&view.source==0xfeed);
            CHECK(botw_whole_transfer_acquire(handle,page,2,&lease,&view,E)&&!lease&&view.source==0xfeed);
            calloc_failure=1;CHECK(botw_whole_transfer_acquire(handle,page,3,&lease,&view,E)&&!lease);
            CHECK(!botw_whole_transfer_close(handle,E));calloc_failure=1;CHECK(create(source,page,0,&handle)==0xd001&&!g_nmap);
        }else if(!strcmp(argv[1],"shrink-map")){
            source=g_heap+600*MIB;CHECK(!create(source,page,3,&handle));uint64_t regs[31]={0};regs[1]=512*MIB;
            owned_memory_svc(1,regs);CHECK(regs[0]==0xd401&&g_heap_cur==640*MIB);
            regs[0]=(uintptr_t)g_stack;regs[1]=(uintptr_t)source;regs[2]=page;owned_memory_svc(4,regs);CHECK(regs[0]==0xd401&&g_nmap==1);
            CHECK(!botw_whole_transfer_close(handle,E));source[0]=0x32;
            regs[0]=(uintptr_t)g_stack;regs[1]=(uintptr_t)source;regs[2]=page;owned_memory_svc(4,regs);CHECK(!regs[0]&&g_stack[0]==0x32&&g_nmap==1);
            CHECK(create(source,page,0,&handle)==0xd401&&!dm_guest_memory_access((uintptr_t)source,1,0));
            g_stack[0]=0x62;regs[0]=(uintptr_t)g_stack;regs[1]=(uintptr_t)source;regs[2]=page;owned_memory_svc(5,regs);
            CHECK(!regs[0]&&!g_nmap&&source[0]==0x62);
            regs[1]=512*MIB;owned_memory_svc(1,regs);CHECK(!regs[0]&&g_heap_cur==512*MIB);
        }else if(!strcmp(argv[1],"synchronization")){
            thr_t waiting={.wkind=1,.waddr=(uintptr_t)source};g_wait=&waiting;
            CHECK(create(source,page,0,&handle)==0xd401&&!g_nmap);g_wait=NULL;
            CHECK(!create(source,page,0,&handle));uint64_t regs[31]={0};regs[0]=handle;regs[1]=0xbeef;
            CHECK(botw_whole_transfer_dispatch(0x16,regs,E)==1&&!regs[0]&&regs[1]==0xbeef&&!g_nmap);
        }else if(!strcmp(argv[1],"sync-noaccess")){
            CHECK(!create(source,page,0,&handle));thr_t self={0};CHECK(!pthread_setspecific(g_keythr,&self));
            const int entries[]={0x1a,0x1b,0x1c,0x1d,0x34,0x35,0x18};
            for(unsigned i=0;i<sizeof entries/sizeof *entries;i++){
                uint64_t regs[31]={0};regs[0]=(uintptr_t)source;regs[1]=(uintptr_t)source;regs[2]=1;
                CHECK(owned_word_svc(entries[i],regs)==1&&regs[0]);
            }
            CHECK(!pthread_setspecific(g_keythr,NULL)&&!botw_whole_transfer_close(handle,E));
        }else return 2;
        CHECK(!g_nmap&&!live());CHECK(!(munmap)(g_as,RESERVE));
        void *own=pthread_getspecific(g_keyown);CHECK(!pthread_setspecific(g_keyown,NULL));
        CHECK(!pthread_setspecific(g_keytls,NULL)&&!pthread_setspecific(g_keytlr,NULL));own_free(own);
#endif
    }
    puts("WHOLE_TRANSFER_CPU_OK");return 0;
}
