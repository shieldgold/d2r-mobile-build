/* Only owned CPU helpers around the existing complete transfer/kernel fixture.
 * The included fixture compiles the actual patched G/g_h/g_map/SVC algorithms;
 * this file creates no additional handle table, VM policy or shadow allocator. */
#define main owned_transfer_fixture_main
#include "whole_transfer_probe.c"
#undef main
#undef calloc
int owned_audren_kernel_initialize(void){return initialize();}
int owned_audren_kernel_transfer(size_t requested,uint32_t *handle,uint64_t *source){
    size_t page=(size_t)getpagesize();
    if(!requested||requested>SIZE_MAX-page+1)return -1;
    size_t size=(requested+page-1)&~(page-1);
    unsigned char *bytes=g_heap+2*MIB;
    if(size>g_heap_cur-2*MIB)return -1;
    memset(bytes,0,size);int result=create(bytes,size,0,handle);
    if(!result)*source=(uintptr_t)bytes;return result;
}
int owned_audren_kernel_state(uint32_t handle,int *bad){
    pthread_mutex_lock(&G);int state=sig_obj(handle,bad);pthread_mutex_unlock(&G);return state;
}
void *owned_audren_kernel_bytes(size_t offset,size_t length){
    if(offset>g_heap_cur||length>g_heap_cur-offset||
        !dm_guest_memory_access((uintptr_t)g_heap+offset,length,1))return NULL;
    return g_heap+offset;
}
unsigned owned_audren_kernel_live(void){pthread_mutex_lock(&G);unsigned count=live();pthread_mutex_unlock(&G);return count;}
unsigned owned_audren_kernel_maps(void){pthread_mutex_lock(&g_map_lock);unsigned count=(unsigned)g_nmap;pthread_mutex_unlock(&g_map_lock);return count;}
void owned_audren_kernel_fail_unmap(void){failed_unmaps=1;}
static int capacity_tokens[MAX_H];
static uint32_t capacity_handles[MAX_H];
static unsigned capacity_count;
int owned_audren_kernel_fill(unsigned leave){
    if(leave>MAX_H-1)return -1;
    while(owned_audren_kernel_live()<MAX_H-1-leave){
        if(capacity_count>=MAX_H)return -1;
        if(botw_whole_kernel_transport_allocate(NULL,&capacity_tokens[capacity_count],
            &capacity_handles[capacity_count],E))return -1;
        capacity_count++;
    }
    return 0;
}
int owned_audren_kernel_empty(void){
    while(capacity_count){unsigned i=capacity_count-1;
        if(botw_whole_kernel_transport_release(NULL,&capacity_tokens[i],capacity_handles[i],E))return -1;
        capacity_count--;
    }return 0;
}
int owned_audren_kernel_finish(void){
    if(g_nmap||owned_audren_kernel_live())return -1;
    if((munmap)(g_as,RESERVE))return -1;
    void *ownership=pthread_getspecific(g_keyown);
    if(pthread_setspecific(g_keyown,NULL)||pthread_setspecific(g_keytls,NULL)||pthread_setspecific(g_keytlr,NULL))return -1;
    own_free(ownership);
    return pthread_key_delete(g_keytlr)||pthread_key_delete(g_keytls)||pthread_key_delete(g_keyres[0])||
        pthread_key_delete(g_keyres[1])||pthread_key_delete(g_keythr)||pthread_key_delete(g_keygate)||
        pthread_key_delete(g_key18)||pthread_key_delete(g_keyown)?-1:0;
}
