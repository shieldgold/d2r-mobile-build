/* Reuse the frozen actual single-kernel owned VM fixture. No Guest or GPU. */
#define main frozen_transfer_fixture_main
#define dm_guest_write_prepare_checked frozen_fixture_prepare_unused
#include "whole_transfer_probe.c"
#undef dm_guest_write_prepare_checked
#undef main
#undef calloc
#include "whole_heap_lease.h"
#include "whole_heap_provider.h"
#include "normal_registry.inc"
#include <mach/mach_time.h>
static _Atomic int allocation_hold,allocation_entered,allocation_release;
static void pause_normal(void){struct timespec delay={0,1000000};nanosleep(&delay,NULL);}
static void *normal_dirty_calloc(size_t count,size_t size){
    if(atomic_load(&allocation_hold)&&!atomic_exchange(&allocation_entered,1))
        while(!atomic_load(&allocation_release))pause_normal();
    return (calloc)(count,size);
}
#define calloc(...) normal_dirty_calloc(__VA_ARGS__)
#include "nvn_dirty.c"
#undef calloc
static _Atomic int prep_hold,prep_entered,prep_release,prep_fail;
static _Atomic unsigned prep_count;
static int observed_prepare(void *memory,size_t size){
    (void)memory;(void)size;atomic_fetch_add(&prep_count,1);
    if(atomic_load(&prep_hold)){atomic_store(&prep_entered,1);while(!atomic_load(&prep_release))pause_normal();}
    return !atomic_load(&prep_fail);
}
static unsigned dirty_live(void){int n,peak,cap;nvn_dirty_stat(&n,&peak,&cap);return (unsigned)n;}
static int wait_normal(_Atomic int *flag){for(int i=0;i<3000;i++){if(atomic_load(flag))return 1;pause_normal();}return 0;}
typedef struct {uint64_t source;size_t size;botw_whole_heap_lease *lease;botw_whole_heap_view view;int status;_Atomic int entered,done;} operation;
static _Atomic int race_start;
static void *acquire_thread(void *argument){
    operation *op=argument;atomic_store(&op->entered,1);while(!atomic_load(&race_start)){}
    char message[256];op->status=botw_whole_heap_acquire(op->source,op->size,3,&op->lease,&op->view,message,sizeof message);
    atomic_store(&op->done,1);return NULL;
}
static void *dirty_thread(void *argument){
    operation *op=argument;atomic_store(&op->entered,1);while(!atomic_load(&race_start)){}
    op->status=nvn_dirty_add_range_checked((void*)(uintptr_t)op->source,op->size);
    atomic_store(&op->done,1);return NULL;
}
int main(int argc,char **argv){
    CHECK(argc==2&&!initialize());size_t page=(size_t)getpagesize();uint8_t *source=g_heap+2*MIB+4096;
    uint64_t envelope=(uintptr_t)source&~((uint64_t)page-1);source[0]=0x28;
    CHECK(dm_guest_write_prep_register_checked(observed_prepare));
    botw_whole_heap_lease *lease=NULL;botw_whole_heap_view view={0};
    if(!strcmp(argv[1],"shared")){
        source[-1]=0x44;source[4096]=0x55;
        CHECK(!botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E));
        CHECK(view.size==4096&&view.access==3&&view.backing_id==(uintptr_t)&g_heap&&view.backing_offset==(uintptr_t)source-(uintptr_t)g_heap);
        CHECK(((uint8_t*)view.data)[0]==0x28);((uint8_t*)view.data)[0]=0x61;CHECK(source[0]==0x61);
        source[1]=0x38;CHECK(((uint8_t*)view.data)[1]==0x38&&source[-1]==0x44&&source[4096]==0x55);
        CHECK(botw_whole_memory_access((uintptr_t)source,4096,1)&&atomic_load(&prep_count)==1);
        CHECK(!botw_whole_heap_release(&lease,E)&&!lease&&!g_nmap&&source[0]==0x61);
    }else if(!strcmp(argv[1],"normal-before-dirty")){
        CHECK(!botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E));
        int admitted=nvn_dirty_add_range_checked(source,4096);unsigned published=dirty_live();
        if(admitted)nvn_dirty_remove_range(source);CHECK(!botw_whole_heap_release(&lease,E));
        CHECK(!admitted&&!published);CHECK(nvn_dirty_add_range_checked(source,4096));nvn_dirty_remove_range(source);
    }else if(!strcmp(argv[1],"dirty-before-normal")){
        CHECK(nvn_dirty_add_range_checked(source,4096));uint64_t stamp=nvn_dirty_now();CHECK(nvn_dirty_protect_checked(source,4096));
        CHECK(botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E)&&errno==EBUSY&&!lease&&!view.data&&!g_nmap);
        CHECK(!atomic_load(&prep_count)&&!nvn_dirty_since(source,4096,stamp));nvn_dirty_remove_range(source);
    }else if(!strcmp(argv[1],"noaccess")){
        uint32_t handle;CHECK(!create((void*)(uintptr_t)envelope,page,0,&handle));
        CHECK(botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E)&&errno==EACCES&&!lease&&!atomic_load(&prep_count));
        CHECK(!botw_whole_transfer_close(handle,E));
    }else if(!strcmp(argv[1],"readonly")){
        CHECK(!(mprotect)((void*)(uintptr_t)envelope,page,PROT_READ));
        CHECK(botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E)&&errno==EACCES&&!lease&&!g_nmap&&atomic_load(&prep_count)==1);
        CHECK(!(mprotect)((void*)(uintptr_t)envelope,page,PROT_READ|PROT_WRITE));
    }else if(!strcmp(argv[1],"checked-failure")){
        atomic_store(&prep_fail,1);view.size=777;
        CHECK(botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E)&&errno==EIO&&!lease&&!g_nmap&&!last_alias&&view.size==777);
    }else if(!strcmp(argv[1],"envelope-hole")){
        uint64_t regs[31]={0};regs[0]=(uintptr_t)g_stack;regs[1]=envelope;regs[2]=4096;
        owned_memory_svc(4,regs);CHECK(!regs[0]&&g_nmap==1);
        CHECK(botw_whole_memory_access((uintptr_t)source,4096,1));
        int status=botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E);
        if(page>4096)CHECK(status&&errno==EACCES&&!lease&&!atomic_load(&prep_count));
        else CHECK(!status&&lease&&!botw_whole_heap_release(&lease,E));
        regs[0]=(uintptr_t)g_stack;regs[1]=envelope;regs[2]=4096;owned_memory_svc(5,regs);CHECK(!regs[0]&&!g_nmap);
    }else if(!strcmp(argv[1],"overlap")){
        CHECK(!botw_whole_heap_acquire((uintptr_t)source,8192,3,&lease,&view,E));
        botw_whole_heap_lease *second=NULL;botw_whole_heap_view other;
        CHECK(!botw_whole_heap_acquire((uintptr_t)source+4096,4096,3,&second,&other,E));
        CHECK(other.backing_id==view.backing_id&&other.backing_offset==view.backing_offset+4096&&g_nmap==2);
        ((uint8_t*)other.data)[0]=0x67;CHECK(((uint8_t*)view.data)[4096]==0x67);
        CHECK(!botw_whole_heap_release(&lease,E)&&g_nmap==1&&!botw_whole_heap_release(&second,E)&&!g_nmap);
    }else if(!strcmp(argv[1],"last-failure")){
        CHECK(!botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E));failed_unmaps=1;
        CHECK(botw_whole_heap_release(&lease,E)&&lease&&g_nmap==1&&((uint8_t*)view.data)[0]==0x28);
        CHECK(!nvn_dirty_add_range_checked(source,4096));CHECK(!botw_whole_heap_release(&lease,E)&&!lease&&!g_nmap);
    }else if(!strcmp(argv[1],"quarantine")){
        unexpected_alias_protection=1;failed_unmaps=2;
        CHECK(botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E)&&!lease&&!view.data&&g_nmap==1);
        CHECK(!g_map[0].transfer->published&&g_map[0].transfer->blocked&&!nvn_dirty_add_range_checked(source,4096));
        failed_unmaps=1;CHECK(botw_whole_heap_reap_pending(E)&&g_nmap==1);
        CHECK(!botw_whole_heap_reap_pending(E)&&!g_nmap&&source[0]==0x28);
    }else if(!strcmp(argv[1],"preparing")){
        atomic_store(&prep_hold,1);atomic_store(&race_start,1);operation op={.source=(uintptr_t)source,.size=4096};pthread_t thread;
        CHECK(!pthread_create(&thread,NULL,acquire_thread,&op)&&wait_normal(&prep_entered));
        CHECK(!nvn_dirty_add_range_checked(source,4096));CHECK(!botw_whole_heap_reap_pending(E)&&g_nmap==1);
        atomic_store(&prep_release,1);CHECK(!pthread_join(thread,NULL)&&!op.status&&op.lease);
        CHECK(!botw_whole_heap_release(&op.lease,E));
    }else if(!strcmp(argv[1],"dirty-publisher")){
        atomic_store(&allocation_hold,1);atomic_store(&race_start,1);operation dirty={.source=(uintptr_t)source,.size=4096};pthread_t a,b;
        CHECK(!pthread_create(&a,NULL,dirty_thread,&dirty)&&wait_normal(&allocation_entered));
        operation normal={.source=(uintptr_t)source,.size=4096};CHECK(!pthread_create(&b,NULL,acquire_thread,&normal)&&wait_normal(&normal.entered));
        for(int i=0;i<20;i++)pause_normal();CHECK(!atomic_load(&normal.done));
        atomic_store(&allocation_release,1);CHECK(!pthread_join(a,NULL)&&!pthread_join(b,NULL)&&dirty.status&&!normal.lease&&normal.status);
        nvn_dirty_remove_range(source);
    }else if(!strcmp(argv[1],"race")){
        for(unsigned i=0;i<100;i++){
            atomic_store(&race_start,0);operation normal={.source=(uintptr_t)source,.size=4096},dirty={.source=(uintptr_t)source,.size=4096};pthread_t a,b;
            CHECK(!pthread_create(&a,NULL,acquire_thread,&normal)&&!pthread_create(&b,NULL,dirty_thread,&dirty));
            CHECK(wait_normal(&normal.entered)&&wait_normal(&dirty.entered));atomic_store(&race_start,1);
            CHECK(!pthread_join(a,NULL)&&!pthread_join(b,NULL));
            CHECK((normal.status==0)!=!!dirty.status);
            if(dirty.status)nvn_dirty_remove_range(source);if(normal.lease)CHECK(!botw_whole_heap_release(&normal.lease,E));
        }
    }else if(!strcmp(argv[1],"mutations")){
        source=g_heap+600*MIB;CHECK(!botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E));
        uint64_t regs[31]={0};regs[1]=512*MIB;owned_memory_svc(1,regs);CHECK(regs[0]==0xd401&&g_heap_cur==640*MIB);
        regs[0]=(uintptr_t)g_stack;regs[1]=(uintptr_t)source;regs[2]=4096;owned_memory_svc(4,regs);CHECK(regs[0]==0xd401);
        uint32_t handle=0;CHECK(create((void*)((uintptr_t)source&~(page-1)),page,3,&handle)==0xd401&&!handle);
        CHECK(!botw_whole_heap_release(&lease,E));regs[1]=512*MIB;owned_memory_svc(1,regs);CHECK(!regs[0]&&g_heap_cur==512*MIB);
    }else if(!strcmp(argv[1],"invalid")){
        view.size=123;CHECK(botw_whole_heap_acquire((uintptr_t)source+1,4096,3,&lease,&view,E)&&view.size==123&&!lease);
        CHECK(botw_whole_heap_acquire((uintptr_t)source,4096,1,&lease,&view,E)&&view.size==123&&!lease);
        CHECK(botw_whole_heap_acquire((uintptr_t)g_heap+g_heap_cur,4096,3,&lease,&view,E)&&!lease);
        calloc_failure=1;CHECK(botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E)&&errno==ENOMEM&&!g_nmap);
        calloc_failure=2;CHECK(botw_whole_heap_acquire((uintptr_t)source,4096,3,&lease,&view,E)&&errno==ENOMEM&&!g_nmap);
        uint64_t regs[31]={0};regs[1]=512*MIB;owned_memory_svc(1,regs);CHECK(!regs[0]&&g_heap_cur==512*MIB);
        CHECK(botw_whole_heap_acquire((uintptr_t)g_heap+600*MIB,4096,3,&lease,&view,E)&&errno==EFAULT&&!lease&&!g_nmap);
    }else if(!strcmp(argv[1],"provider")){
        botw_whole_heap_provider *provider=NULL,*other=NULL;botw_audren_memory_provider api={0},other_api={0};uint64_t process=(uint64_t)getpid();
        CHECK(!botw_whole_heap_provider_create(process,&provider,&api,E)&&!api.retain(api.context));
        CHECK(botw_whole_heap_provider_destroy(&provider,E)&&provider);
        botw_audren_memory_lease value={0};CHECK(api.acquire(api.context,process+1,(uintptr_t)source,4096,3,&value,E)==BOTW_AUDREN_OWNER_MISMATCH&&!value.owner);
        CHECK(api.acquire(api.context,process,(uintptr_t)source+1,4096,3,&value,E)==BOTW_AUDREN_INVALID_ARGUMENT&&!value.owner);
        CHECK(api.acquire(api.context,process,(uintptr_t)source,4096,3,&value,E)==BOTW_AUDREN_OK&&value.owner&&value.size==4096);
        CHECK(!botw_whole_heap_provider_create(process,&other,&other_api,E));
        botw_audren_memory_lease second={0};CHECK(!other_api.acquire(other_api.context,process,(uintptr_t)source+4096,4096,3,&second,E));
        CHECK(other_api.release_lease(other_api.context,&value.owner,E)==BOTW_AUDREN_OWNER_MISMATCH&&value.owner);
        failed_unmaps=1;CHECK(api.release_lease(api.context,&value.owner,E)==BOTW_AUDREN_SYSTEM_ERROR&&value.owner);
        api.release(api.context);CHECK(botw_whole_heap_provider_destroy(&provider,E)&&provider);
        CHECK(!api.release_lease(api.context,&value.owner,E)&&!value.owner&&!other_api.release_lease(other_api.context,&second.owner,E));
        CHECK(!botw_whole_heap_provider_destroy(&provider,E)&&!provider&&!botw_whole_heap_provider_destroy(&other,E));
    }else if(!strcmp(argv[1],"provider-pending")){
        botw_whole_heap_provider *provider=NULL;botw_audren_memory_provider api={0};uint64_t process=(uint64_t)getpid();
        CHECK(!botw_whole_heap_provider_create(process,&provider,&api,E));unexpected_alias_protection=1;failed_unmaps=2;
        botw_audren_memory_lease value={0};CHECK(api.acquire(api.context,process,(uintptr_t)source,4096,3,&value,E)==BOTW_AUDREN_SYSTEM_ERROR&&!value.owner&&g_nmap==1);
        failed_unmaps=1;CHECK(botw_whole_heap_provider_destroy(&provider,E)&&provider&&g_nmap==1);
        CHECK(!botw_whole_heap_provider_destroy(&provider,E)&&!provider&&!g_nmap);
    }else return 2;
    CHECK(!g_nmap&&!live()&&!dirty_live());CHECK(!(munmap)(g_as,RESERVE));
    void *own=pthread_getspecific(g_keyown);CHECK(!pthread_setspecific(g_keyown,NULL));
    CHECK(!pthread_setspecific(g_keytls,NULL)&&!pthread_setspecific(g_keytlr,NULL));own_free(own);
    puts("WHOLE_HEAP_LEASE_CPU_OK");return 0;
}
