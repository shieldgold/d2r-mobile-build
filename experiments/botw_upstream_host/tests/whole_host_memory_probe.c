/* Owned CPU metadata and VM only. Reuse the actual staged single G/g_h/g_map
 * and original thread initializer; never enter a Guest gate or complete host. */
#include <pthread.h>
static unsigned owned_tsd_reads;
static void *owned_getspecific(pthread_key_t key){owned_tsd_reads++;return (pthread_getspecific)(key);}
#define pthread_getspecific(key) owned_getspecific(key)
#define main owned_transfer_source_main
#include "whole_transfer_probe.c"
#undef main
#undef pthread_getspecific
#undef calloc
#ifndef WHOLE_HOST_MEMORY_BASELINE
#include "whole_host_memory.h"
#include "whole_guest_memory.h"
#endif
static unsigned char *module_bytes;
static const size_t module_size=0x20000;
static unsigned preparations;
static int prepare_failure, kernel_denial;
#ifndef WHOLE_HOST_MEMORY_BASELINE
static int classify_kernel(void *context,uint64_t address,size_t length,int writable){
    (void)context;if(kernel_denial)return -1;return botw_whole_memory_classify(address,length,writable);
}
static int prepare_operand(void *context,uint64_t address,size_t length,char *message,size_t capacity){
    (void)context;(void)address;(void)length;preparations++;
    if(prepare_failure){if(message&&capacity)snprintf(message,capacity,"owned preparation refused");return -1;}
    return 0;
}
static botw_whole_guest_memory normal_memory={BOTW_WHOLE_GUEST_MEMORY_ABI,sizeof normal_memory,NULL,
    classify_kernel,botw_whole_host_module_classify,botw_whole_host_thread_classify,prepare_operand};
#endif
static int module_initialize(void){
    module_bytes=mmap(NULL,module_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    if(module_bytes==MAP_FAILED)return -1;
    g_nmod=1;g_mod[0]=(module_t){.base=module_bytes,.size=module_size,.text_end=0x1fff,
        .ro_off=0x2000,.data_off=0x9000,.patch_off=0x18000,.key_page_off=0x10000,.key_page_size=0x4000};
    return 0;
}
static int finish_metadata(void){
    g_nmod=0;if(munmap(module_bytes,module_size))return -1;
    if((munmap)(g_as,RESERVE))return -1;
    void *ownership=pthread_getspecific(g_keyown);
    if(pthread_setspecific(g_keyown,NULL)||pthread_setspecific(g_keytls,NULL)||pthread_setspecific(g_keytlr,NULL))return -1;
    own_free(ownership);
    return pthread_key_delete(g_keytlr)||pthread_key_delete(g_keytls)||pthread_key_delete(g_keyres[0])||
        pthread_key_delete(g_keyres[1])||pthread_key_delete(g_keythr)||pthread_key_delete(g_keygate)||
        pthread_key_delete(g_key18)||pthread_key_delete(g_keyown)?-1:0;
}
#ifndef WHOLE_HOST_MEMORY_BASELINE
static int worker_result;
static uint64_t parent_tls,parent_stack;
static void *thread_worker(void *unused){
    (void)unused;void *base=(void*)1;size_t length=17;char message[128];
    if(botw_whole_current_tlr(&base,&length)!=-1||base!=(void*)1||length!=17)return (void*)1;
    if(botw_whole_host_thread_classify(NULL,parent_tls,1,0)!=0||
        botw_whole_host_thread_classify(NULL,parent_stack,1,0)!=0)return (void*)2;
    guest_thread_init();
    if(botw_whole_host_current_tlr(&normal_memory,&base,&length,message,sizeof message)||length!=0x200)return (void*)3;
    if(!botw_whole_current_thread_memory((uintptr_t)base,0x600))return (void*)4;
    worker_result=1;return NULL;
}
#endif
int main(int argc,char **argv){
    CHECK(argc==2);
#ifndef WHOLE_HOST_MEMORY_BASELINE
    if(!strcmp(argv[1],"unready")){
        pthread_key_t owned_key;CHECK(!pthread_key_create(&owned_key,NULL));
        int owned_marker=7;CHECK(!pthread_setspecific(owned_key,&owned_marker));g_keytlr=owned_key;
        void *out=(void*)1;size_t length=19;owned_tsd_reads=0;
        CHECK(botw_whole_current_tlr(&out,&length)==-1&&out==(void*)1&&length==19&&!owned_tsd_reads);
        char message[128];CHECK(botw_whole_host_current_tlr(&normal_memory,&out,&length,message,sizeof message)==-1);
        CHECK(out==(void*)1&&length==19&&!owned_tsd_reads);
        CHECK(!pthread_key_delete(owned_key));return 0;
    }
#endif
    CHECK(!initialize()&&!module_initialize());uint64_t base=(uintptr_t)module_bytes;
#ifdef WHOLE_HOST_MEMORY_BASELINE
    CHECK(!strcmp(argv[1],"readonly"));
    /* The actual pre-provider admission only excluded rounded text, patch and
     * key pages; these owned logical RO bytes are physically writable. */
    CHECK(dm_guest_memory_access(base+0x5000,8,1)==0);
#else
    if(!strcmp(argv[1],"readonly")){
        for(uint64_t offset=0;offset<0x9000;offset+=0x1000)CHECK(botw_whole_host_module_classify(NULL,base+offset,8,1)==-1);
        CHECK(botw_whole_host_module_classify(NULL,base+0x18000,8,1)==-1);
        CHECK(botw_whole_host_module_classify(NULL,base+0x10000,8,1)==-1);
        CHECK(!botw_whole_guest_memory_access(&normal_memory,base+0x5000,8,1)&&!preparations);
    }else if(!strcmp(argv[1],"module-read")){
        CHECK(botw_whole_host_module_classify(NULL,base,module_size,0)==1);
        CHECK(botw_whole_guest_memory_access(&normal_memory,base,module_size,0));
    }else if(!strcmp(argv[1],"module-data")){
        CHECK(botw_whole_host_module_classify(NULL,base+0x9000,0x7000,1)==1);
        uint32_t value=0x12345678;CHECK(!botw_whole_guest_memory_write(&normal_memory,base+0x9000,&value,4,E));
        CHECK(!memcmp(module_bytes+0x9000,&value,4)&&preparations==1);
    }else if(!strcmp(argv[1],"module-bounds")){
        CHECK(botw_whole_host_module_classify(NULL,base-1,2,0)==-1);
        CHECK(botw_whole_host_module_classify(NULL,base+module_size-1,2,0)==-1);
        CHECK(botw_whole_host_module_classify(NULL,base+0xffff,2,1)==-1);
        CHECK(botw_whole_host_module_classify(NULL,base+0x17fff,2,1)==-1);
        CHECK(botw_whole_host_module_classify(NULL,UINT64_MAX-1,8,0)==-1);
        CHECK(botw_whole_host_module_classify(NULL,base,0,0)==-1);
    }else if(!strcmp(argv[1],"module-layout")){
        module_t good=g_mod[0];
        g_mod[0].ro_off=0x1000;CHECK(botw_whole_host_module_classify(NULL,base+0x9000,8,1)==-1);g_mod[0]=good;
        g_mod[0].data_off=0x19000;CHECK(botw_whole_host_module_classify(NULL,base,8,0)==-1);g_mod[0]=good;
        g_mod[0].key_page_off=UINT64_MAX-3;CHECK(botw_whole_host_module_classify(NULL,base,8,0)==-1);g_mod[0]=good;
        g_mod[0].base=(void*)(uintptr_t)(UINT64_MAX-3);CHECK(botw_whole_host_module_classify(NULL,base,8,0)==-1);g_mod[0]=good;
        g_nmod=MAX_MOD+1;CHECK(botw_whole_host_module_classify(NULL,base,8,0)==-1);g_nmod=1;
    }else if(!strcmp(argv[1],"module-overlap")){
        g_mod[1]=g_mod[0];g_nmod=2;CHECK(botw_whole_host_module_classify(NULL,base,8,0)==-1);g_nmod=1;
    }else if(!strcmp(argv[1],"module-vm")){
        size_t page=(size_t)getpagesize();CHECK(!mprotect(module_bytes+page,page,PROT_NONE));
        CHECK(botw_whole_host_module_classify(NULL,base,module_size,0)==1);
        CHECK(!botw_whole_guest_memory_access(&normal_memory,base,module_size,0));
        CHECK(!mprotect(module_bytes+page,page,PROT_READ|PROT_WRITE));
    }else if(!strcmp(argv[1],"unknown")){
        CHECK(botw_whole_host_module_classify(NULL,(uintptr_t)g_heap,8,0)==0);
        void *p=malloc(4096);CHECK(p&&!botw_whole_guest_memory_access(&normal_memory,(uintptr_t)p,16,0));free(p);
    }else if(!strcmp(argv[1],"stack")){
        char local[32];CHECK(botw_whole_host_thread_classify(NULL,(uintptr_t)local,sizeof local,1)==1);
        CHECK(botw_whole_guest_memory_access(&normal_memory,(uintptr_t)local,sizeof local,1));
        uint64_t top=(uintptr_t)pthread_get_stackaddr_np(pthread_self());size_t size=pthread_get_stacksize_np(pthread_self());
        CHECK(botw_whole_host_thread_classify(NULL,top-1,2,0)==-1);
        CHECK(botw_whole_host_thread_classify(NULL,top-size-1,2,0)==-1);
    }else if(!strcmp(argv[1],"tls")){
        void **own=pthread_getspecific(g_keyown);CHECK(own);
        CHECK(botw_whole_host_thread_classify(NULL,(uintptr_t)own[0],TLS_SIZE,1)==1);
        CHECK(botw_whole_host_thread_classify(NULL,(uintptr_t)own[1],TLR_SIZE+THREAD_OBJ_SIZE,1)==1);
        CHECK(botw_whole_host_thread_classify(NULL,(uintptr_t)own[0]+TLS_SIZE-1,2,1)!=1);
    }else if(!strcmp(argv[1],"thread")){
        void **own=pthread_getspecific(g_keyown);parent_tls=(uintptr_t)own[0];parent_stack=(uintptr_t)&own;
        pthread_t worker;void *result=NULL;CHECK(!pthread_create(&worker,NULL,thread_worker,NULL));
        CHECK(!pthread_join(worker,&result)&&!result&&worker_result==1);
    }else if(!strcmp(argv[1],"tlr")){
        void *tlr=NULL;size_t length=0;CHECK(!botw_whole_current_tlr(&tlr,&length)&&length==0x200);
        CHECK(tlr==pthread_getspecific(g_keytlr)&&tlr!=pthread_getspecific(g_keytls));
        void *checked=NULL;size_t checked_size=0;CHECK(!botw_whole_host_current_tlr(&normal_memory,&checked,&checked_size,E));
        CHECK(checked==tlr&&checked_size==0x200);
    }else if(!strcmp(argv[1],"tlr-rewrite")){
        void *original=pthread_getspecific(g_keytlr),*foreign=malloc(TLS_SIZE);CHECK(foreign);
        CHECK(!pthread_setspecific(g_keytlr,foreign));void *out=(void*)1;size_t length=19;
        CHECK(botw_whole_host_current_tlr(&normal_memory,&out,&length,E)==-1&&out==(void*)1&&length==19);
        CHECK(!pthread_setspecific(g_keytlr,original));free(foreign);
    }else if(!strcmp(argv[1],"tlr-bounds")){
        void *original=pthread_getspecific(g_keytlr);void **own=pthread_getspecific(g_keyown);
        CHECK(!pthread_setspecific(g_keytlr,(char*)own[0]+TLS_SIZE-0x100));void *out=(void*)1;size_t length=19;
        CHECK(botw_whole_host_current_tlr(&normal_memory,&out,&length,E)==-1&&out==(void*)1&&length==19);
        CHECK(!pthread_setspecific(g_keytlr,original));
    }else if(!strcmp(argv[1],"tlr-vm")){
        void *original=pthread_getspecific(g_keytlr);size_t page=(size_t)getpagesize();
        CHECK(!pthread_setspecific(g_keytlr,module_bytes+0x9000));CHECK(!mprotect(module_bytes+0x8000,page,PROT_NONE));
        void *out=(void*)1;size_t length=19;CHECK(botw_whole_host_current_tlr(&normal_memory,&out,&length,E)==-1&&out==(void*)1&&length==19);
        CHECK(!mprotect(module_bytes+0x8000,page,PROT_READ|PROT_WRITE));CHECK(!pthread_setspecific(g_keytlr,original));
    }else if(!strcmp(argv[1],"tlr-prepare")){
        prepare_failure=1;void *out=(void*)1;size_t length=19;
        CHECK(botw_whole_host_current_tlr(&normal_memory,&out,&length,E)==-1&&out==(void*)1&&length==19&&preparations==1);
    }else if(!strcmp(argv[1],"kernel-denial")){
        kernel_denial=1;void *out=(void*)1;size_t length=19;
        CHECK(botw_whole_host_current_tlr(&normal_memory,&out,&length,E)==-1&&out==(void*)1&&length==19&&!preparations);
    }else CHECK(0);
#endif
    CHECK(!finish_metadata());return 0;
}
