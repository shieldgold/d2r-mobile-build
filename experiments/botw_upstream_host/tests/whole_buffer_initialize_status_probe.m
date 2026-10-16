/* Actual buffer entry bodies over the complete existing owned pool provider.
 * No Metal device, Guest instructions, or GPU work is executed. */
#define BOTW_WHOLE_POLICY_IMPLEMENTATION
#include "whole_policy.h"
#include <errno.h>
#define main original_pool_fixture_main
#include "whole_pool_backing_probe.m"
#undef main
static unsigned status_impl_calls;
static int status_impl_result,status_impl_errno,status_impl_returned,status_violation;
static unsigned original_storage_logs,original_physical_logs;
static void owned_original_buffer_log(const char *message){
    if(!strcmp(message,"[ERROR] NVN buffer storage lies outside its memory pool"))original_storage_logs++;
    else if(!strcmp(message,"[ERROR] NVN buffers cannot use physical-only pools directly"))original_physical_logs++;
    else status_violation=1;
    fprintf(stderr,"[dm] %s\n",message);
    errno=ENOSPC;
}
#undef LOG
#define LOG(message) owned_original_buffer_log(message)
#include "buffer_entries.inc"
ssize_t owned_buffer_status_write(int fd,const void *bytes,size_t size){
    static const char pool_ok[]="[botw whole-host] api=NvnMemoryPoolInitialize result=ok\n";
    static const char ok[]="[botw whole-host] api=NvnBufferInitialize result=ok\n";
    static const char error[]="[botw whole-host] api=NvnBufferInitialize result=error\n";
    int pool=size==sizeof pool_ok-1&&!memcmp(bytes,pool_ok,size);
    int buffer_ok=size==sizeof ok-1&&!memcmp(bytes,ok,size);
    int buffer_error=size==sizeof error-1&&!memcmp(bytes,error,size);
    if(fd!=STDERR_FILENO||(!pool&&!buffer_ok&&!buffer_error))status_violation=1;
    if(!pool&&(!status_impl_returned||status_impl_result!=(buffer_ok?1:0)))status_violation=1;
    if(pthread_mutex_trylock(&g_pool_gate.mutex))status_violation=1;
    else{if(g_pool_gate.writer||g_pool_gate.readers)status_violation=1;pthread_mutex_unlock(&g_pool_gate.mutex);}
    if(pthread_mutex_trylock(&g_mu))status_violation=1;
    else pthread_mutex_unlock(&g_mu);
    ssize_t result=write(fd,bytes,size);errno=EIO;return result;
}
static NVNboolean checked_buffer_initialize(NVNbuffer *buffer,const NVNbufferBuilder *builder){
    unsigned before=status_impl_calls;status_impl_returned=0;errno=EDOM;
    NVNboolean result=impl_nvnBufferInitialize(buffer,builder);
#if WHOLE_BUFFER_HAS_STATUS
    if(!status_impl_returned||status_impl_calls!=before+1||result!=status_impl_result||errno!=status_impl_errno)status_violation=1;
#else
    (void)before;
#endif
    return result;
}
int main(int argc,char **argv){@autoreleasepool{
    if(argc!=2)return 2;
    int physical=!strcmp(argv[1],"physical");
    int outside=!strcmp(argv[1],"outside");
    int once=!strcmp(argv[1],"once");
    if(!physical&&!outside&&!once&&strcmp(argv[1],"success"))return 2;
    size_t page=(size_t)getpagesize();owned_size=page*2;
    owned=mmap(NULL,owned_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    if(owned==MAP_FAILED)return 3;memset(owned,0x39,owned_size);
    OwnedDevice *device=[OwnedDevice new];device.maxBufferLength=owned_size*4;g_dev=(id<MTLDevice>)device;
    g_keep=[NSMutableArray new];g_res=(id<MTLResidencySet>)[OwnedResidency new];g_res_objects=[NSHashTable weakObjectsHashTable];
    NVNdevice raw_device={0};((dev_t_*)&raw_device)->magic=MAGIC('D','E','V',0);
    NVNmemoryPool pool={0};
    if(physical){
        /* This is the exact typed invalid operand, not a claimed physical
         * Metal backing. The ordinary pool API correctly refuses that mode. */
        *(pool_t*)&pool=(pool_t){.magic=MAGIC('P','O','O','L'),.flags=NVN_MEMORY_POOL_FLAGS_PHYSICAL,.mem=owned,.size=owned_size};
    }else{
        pool_builder_t pool_builder={.dev=&raw_device,.mem=owned,.size=owned_size,.flags=0x22};
        if(h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&pool_builder)!=1)return 4;
        if(!mtl_pool_validate(&pool,owned,owned_size,0x22)||factories!=1)return 5;
    }
    NVNmemoryPool pool_before=pool;
    struct {unsigned char before[16];NVNbuffer object;unsigned char after[16];} output;
    memset(&output,0xa5,sizeof output);__typeof__(output) original=output;
    NVNbufferBuilder builder={0};
    impl_nvnBufferBuilderSetDevice(&builder,&raw_device);
    impl_nvnBufferBuilderSetDefaults(&builder);
    impl_nvnBufferBuilderSetStorage(&builder,&pool,outside?(ptrdiff_t)owned_size:16,outside?1:page);
    NVNbufferBuilder builder_before=builder;
    unsigned factories_before=factories,dirty_before=dirty_live,shader_before=shader_live;
    NVNboolean value=checked_buffer_initialize(&output.object,&builder);
    int result=0;
    result|=check(value==(physical||outside?0:1),"original boolean branch unchanged");
    result|=check(errno==(physical||outside?ENOSPC:EDOM),"original body errno unchanged");
    result|=check(!memcmp(&pool,&pool_before,sizeof pool)&&!memcmp(&builder,&builder_before,sizeof builder),"pool and builder operands unchanged");
    result|=check(!memcmp(output.before,original.before,sizeof output.before)&&!memcmp(output.after,original.after,sizeof output.after),"buffer adjacent guards unchanged");
    if(value){
        struct buffer *b=(struct buffer*)&output.object;
        result|=check(b->magic==BUFFER_MAGIC&&!b->reserved&&b->device==&raw_device&&b->pool==(pool_t*)&pool&&b->offset==16&&b->size==page&&b->serial!=0,"actual complete buffer payload");
    }else result|=check(!memcmp(&output,&original,sizeof output),"false preserves every output byte");
    if(once){
        uint64_t first_serial=((struct buffer*)&output.object)->serial;
        result|=check(checked_buffer_initialize(&output.object,&builder)==1&&errno==EDOM,"second real success preserves return and errno");
        result|=check(((struct buffer*)&output.object)->serial==first_serial+1,"second success really runs original serial producer");
        __typeof__(output) accepted=output;
        impl_nvnBufferBuilderSetStorage(&builder,&pool,(ptrdiff_t)owned_size,1);
        for(int i=0;i<2;i++){
            result|=check(checked_buffer_initialize(&output.object,&builder)==0&&errno==ENOSPC,"each actual bounds failure preserves errno");
            result|=check(!memcmp(&output,&accepted,sizeof output),"each false retains the last accepted complete object");
        }
#if WHOLE_BUFFER_HAS_STATUS
        result|=check(status_impl_calls==4,"once logging does not skip any original invocation");
#endif
    }
    result|=check(original_storage_logs==(once?2u:(unsigned)outside)&&original_physical_logs==(unsigned)physical,"actual fixed original false logs preserved");
    result|=check(factories==factories_before&&dirty_live==dirty_before&&shader_live==shader_before,"buffer API creates no extra pool state");
    if(!physical)h_nvnMemoryPoolFinalize(&pool);
    result|=check(!g_npool&&!dirty_live&&!shader_live,"real pool retirement completes");
    result|=check(!status_violation,"status ran after original return without holding state or changing errno");
    munmap(owned,owned_size);
    if(!result)puts("WHOLE_BUFFER_INITIALIZE_CPU_OK");return result;
}}
