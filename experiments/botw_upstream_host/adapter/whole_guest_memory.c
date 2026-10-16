/* SPDX-License-Identifier: MIT */
#include "whole_guest_memory.h"
#include "../../../native/botw_host/protected_copy.h"
#include <stdio.h>

static int denied(char *error,size_t capacity,const char *message){
    if(error&&capacity)snprintf(error,capacity,"normal guest operand: %s",message);
    return 0;
}
int botw_whole_guest_memory_valid(const botw_whole_guest_memory *c){
    return c&&c->abi==BOTW_WHOLE_GUEST_MEMORY_ABI&&c->size==sizeof *c&&
        c->kernel_classify&&c->module_classify&&c->thread_classify&&c->prepare_write;
}
static int checked(botw_whole_guest_memory *c,uint64_t address,size_t length,int writable,
        char *error,size_t capacity){
    if((capacity&&!error)||!botw_whole_guest_memory_valid(c)||!address||!length||
            length>UINT64_MAX-address||(writable!=0&&writable!=1))
        return denied(error,capacity,"invalid configuration or operand span");
    int (*owners[3])(void*,uint64_t,size_t,int)={c->kernel_classify,c->module_classify,c->thread_classify};
    int owned=0;
    for(unsigned i=0;i<3;i++){
        int result=owners[i](c->context,address,length,writable);
        if(!result)continue;
        if(result!=1)return denied(error,capacity,"existing owner denied the requested access");
        owned=1;break;
    }
    if(!owned)return denied(error,capacity,"no existing owner covers the complete span");
    if(writable&&c->prepare_write(c->context,address,length,error,capacity))
        return denied(error,capacity,"checked write preparation was not acknowledged");
    vm_prot_t permission=writable?VM_PROT_WRITE:VM_PROT_READ;
    if(botw_protected_vm_span((const void*)(uintptr_t)address,length,permission)!=KERN_SUCCESS)
        return denied(error,capacity,"actual VM access does not cover the complete span");
    return 1;
}
int botw_whole_guest_memory_access(void *opaque,uint64_t address,size_t length,int writable){
    return checked(opaque,address,length,writable,NULL,0);
}
int botw_whole_guest_memory_prepare(void *opaque,uint64_t address,size_t length,char *error,size_t capacity){
    return checked(opaque,address,length,1,error,capacity)?0:-1;
}
int botw_whole_guest_memory_read(void *opaque,uint64_t address,void *destination,size_t length,
        char *error,size_t capacity){
    if(!checked(opaque,address,length,0,error,capacity))return -1;
    if(!destination||botw_protected_vm_read(destination,(const void*)(uintptr_t)address,length)!=KERN_SUCCESS){
        denied(error,capacity,"protected normal read failed");return -1;
    }
    return 0;
}
int botw_whole_guest_memory_write(void *opaque,uint64_t address,const void *source,size_t length,
        char *error,size_t capacity){
    if(!checked(opaque,address,length,1,error,capacity))return -1;
    if(!source||botw_protected_vm_write((void*)(uintptr_t)address,source,length)!=KERN_SUCCESS){
        denied(error,capacity,"protected normal write failed");return -1;
    }
    return 0;
}
