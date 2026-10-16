/* SPDX-License-Identifier: MIT
 * Normal API copies only. The original checked-prep registry and original
 * protected_copy primitives retain ownership of preparation and Mach copies. */
#include "whole_pool_builder.h"
#include "../../../native/botw_host/protected_copy.h"
#include <stdio.h>
#include <string.h>
int dm_guest_write_prepare_checked(void *,size_t);
static int fail(char *error,size_t capacity,const char *message) {
    if(error&&capacity)snprintf(error,capacity,"base-v0 pool builder: %s",message);
    return 0;
}
static int address(const void *raw,size_t offset,size_t bytes,void **out,char *error,size_t capacity) {
    if(error&&capacity)error[0]=0;
    uintptr_t begin=(uintptr_t)raw;
    if(!raw||offset>UINTPTR_MAX-begin||bytes>UINTPTR_MAX-(begin+offset))
        return fail(error,capacity,"null or overflowing field range");
    *out=(void*)(begin+offset);return 1;
}
static int write_field(void *raw,size_t offset,const void *value,size_t bytes,char *error,size_t capacity) {
    void *destination;
    if(!address(raw,offset,bytes,&destination,error,capacity))return 0;
    if(!dm_guest_write_prepare_checked(destination,bytes))
        return fail(error,capacity,"checked host write preparation failed");
    if(botw_protected_vm_write(destination,value,bytes)!=KERN_SUCCESS)
        return fail(error,capacity,"destination field is not fully writable or protected copy failed");
    return 1;
}
static int read_field(const void *raw,size_t offset,void *value,size_t bytes,char *error,size_t capacity) {
    void *source;
    if(!address(raw,offset,bytes,&source,error,capacity))return 0;
    if(botw_protected_vm_read(value,source,bytes)!=KERN_SUCCESS)
        return fail(error,capacity,"source field is not fully readable or protected copy failed");
    return 1;
}
int botw_whole_pool_builder_defaults(void *raw,char *error,size_t capacity) {
    unsigned char values[20]={0};uint32_t flags=0x21;
    memcpy(values+16,&flags,sizeof flags);
    return write_field(raw,8,values,sizeof values,error,capacity);
}
int botw_whole_pool_builder_device(void *raw,void *device,char *error,size_t capacity) {
    return write_field(raw,0,&device,sizeof device,error,capacity);
}
int botw_whole_pool_builder_storage(void *raw,void *memory,size_t size,char *error,size_t capacity) {
    uint64_t values[2]={(uint64_t)(uintptr_t)memory,(uint64_t)size};
    return write_field(raw,8,values,sizeof values,error,capacity);
}
int botw_whole_pool_builder_flags(void *raw,uint32_t flags,char *error,size_t capacity) {
    return write_field(raw,24,&flags,sizeof flags,error,capacity);
}
int botw_whole_pool_builder_get_memory(const void *raw,void **out,char *error,size_t capacity) {
    void *value;
    if(!out)return fail(error,capacity,"missing memory output");
    if(!read_field(raw,8,&value,sizeof value,error,capacity))return 0;
    *out=value;return 1;
}
int botw_whole_pool_builder_get_size(const void *raw,size_t *out,char *error,size_t capacity) {
    size_t value;
    if(!out)return fail(error,capacity,"missing size output");
    if(!read_field(raw,16,&value,sizeof value,error,capacity))return 0;
    *out=value;return 1;
}
int botw_whole_pool_builder_get_flags(const void *raw,uint32_t *out,char *error,size_t capacity) {
    uint32_t value;
    if(!out)return fail(error,capacity,"missing flags output");
    if(!read_field(raw,24,&value,sizeof value,error,capacity))return 0;
    *out=value;return 1;
}
