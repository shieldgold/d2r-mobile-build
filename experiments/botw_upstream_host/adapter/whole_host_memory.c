/* SPDX-License-Identifier: MIT */
#include "whole_host_memory.h"
#include "whole_thread_memory.h"
#include "dm.h"

static int valid_operand(uint64_t address,size_t length,int writable){
    return address&&length&&length<=UINT64_MAX-address&&(writable==0||writable==1);
}
static int contains(uint64_t base,size_t size,uint64_t address,size_t length){
    return address>=base&&length<=size&&address-base<=size-length;
}
static int overlap(uint64_t a,size_t n,uint64_t b,size_t m){
    return n&&m&&(a<=b?b-a<n:a-b<m);
}
static int valid_module(const module_t *module){
    uint64_t base=(uintptr_t)module->base;
    return base&&module->size&&module->size<=UINT64_MAX-base&&module->text_end&&
        module->text_end<=module->ro_off&&module->ro_off<=module->data_off&&
        module->data_off<=module->patch_off&&module->patch_off<module->size&&
        !(module->patch_off&UINT64_C(0x3fff))&&
        module->key_page_size==0x4000&&!(module->key_page_off&UINT64_C(0x3fff))&&
        module->key_page_off<=module->size&&
        module->key_page_size<=module->size-module->key_page_off;
}
int botw_whole_host_module_classify(void *context,uint64_t address,size_t length,int writable){
    (void)context;
    if(!valid_operand(address,length,writable)||g_nmod<0||g_nmod>MAX_MOD)return -1;
    /* No copied catalog: validate the actual immutable loader records before
     * any successful lookup, including cross-module ambiguity/overflow. */
    for(int i=0;i<g_nmod;i++){
        if(!valid_module(&g_mod[i]))return -1;
        for(int j=0;j<i;j++)if(overlap((uintptr_t)g_mod[i].base,g_mod[i].size,
                (uintptr_t)g_mod[j].base,g_mod[j].size))return -1;
    }
    for(int i=0;i<g_nmod;i++){
        const module_t *module=&g_mod[i];uint64_t base=(uintptr_t)module->base;
        if(!overlap(address,length,base,module->size))continue;
        if(!contains(base,module->size,address,length))return -1;
        if(!writable)return 1;
        uint64_t offset=address-base;
        return offset>=module->data_off&&offset<=module->patch_off&&
            length<=module->patch_off-offset&&
            !overlap(offset,length,module->key_page_off,module->key_page_size)?1:-1;
    }
    return 0;
}
int botw_whole_host_thread_classify(void *context,uint64_t address,size_t length,int writable){
    (void)context;
    if(!valid_operand(address,length,writable))return -1;
    pthread_t self=pthread_self();
    uint64_t top=(uintptr_t)pthread_get_stackaddr_np(self);
    size_t size=pthread_get_stacksize_np(self);
    if(!top||!size||size>top)return -1;
    uint64_t base=top-size;
    if(overlap(address,length,base,size))return contains(base,size,address,length)?1:-1;
    /* The private original allocations remain authoritative even if the
     * publicly rewritten current TLS/TLR keys now hold different pointers. */
    return botw_whole_current_thread_memory(address,length)?1:0;
}
static int tlr_problem(char *error,size_t capacity,const char *message){
    if(error&&capacity)snprintf(error,capacity,"normal IPC TLR: %s",message);return -1;
}
int botw_whole_host_current_tlr(void *context,void **out,size_t *out_size,char *error,size_t capacity){
    if(error&&capacity)error[0]=0;
    if(!out||!out_size||(capacity&&!error))return tlr_problem(error,capacity,"invalid output buffer");
    void *base=NULL;size_t size=0;
    if(botw_whole_current_tlr(&base,&size)||!base||size!=0x200)
        return tlr_problem(error,capacity,"current thread has no ready 512-byte TLR");
    botw_whole_guest_memory *memory=context;
    if(!botw_whole_guest_memory_access(memory,(uintptr_t)base,size,0)||
            !botw_whole_guest_memory_access(memory,(uintptr_t)base,size,1))
        return tlr_problem(error,capacity,"complete logical/VM read-write access was not acknowledged");
    *out=base;*out_size=size;return 0;
}
