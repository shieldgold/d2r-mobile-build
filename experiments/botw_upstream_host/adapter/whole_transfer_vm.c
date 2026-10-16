/* SPDX-License-Identifier: MIT
 * Complete vm_remap(copy=FALSE) leaf from native/botw_host/kernel.c
 * transfer_alias, with its failed-cleanup ownership fixed. Only normal owned
 * operand spans are checked; this performs no address-space diagnostic scan. */
#include "whole_transfer_internal.h"
#include "../../../native/botw_host/protected_copy.h"
#include <sys/mman.h>
#include <stdio.h>
#include <stdarg.h>
#include <errno.h>
#include <string.h>
static int diagnostic(char *error,size_t capacity,const char *format,...) {
    if(error&&capacity){va_list args;va_start(args,format);vsnprintf(error,capacity,format,args);va_end(args);}return -1;
}
int botw_whole_transfer_vm_alias(uint64_t source,size_t size,uint64_t destination,
                                void **backing,char *error,size_t capacity) {
    vm_address_t target = (vm_address_t)destination;
    vm_prot_t current = 0, maximum = 0;
    int flags = destination ? VM_FLAGS_FIXED | VM_FLAGS_OVERWRITE : VM_FLAGS_ANYWHERE;
    kern_return_t result = vm_remap(mach_task_self(), &target, (vm_size_t)size, 0,
        flags, mach_task_self(), (vm_address_t)source, FALSE, &current, &maximum, VM_INHERIT_COPY);
    if (result != KERN_SUCCESS)
        return diagnostic(error, capacity, "remap shared transfer backing: kernel=%d", result);
    /* Retain every successful VM mapping until the actual unmap ACK. */
    *backing = (void *)(uintptr_t)target;
    if ((destination && target != destination) ||
        (current & (VM_PROT_READ | VM_PROT_WRITE)) != (VM_PROT_READ | VM_PROT_WRITE)) {
        if (!destination) {
            if (munmap((void *)(uintptr_t)target, size))
                return diagnostic(error, capacity, "transfer alias cleanup failed: %s",strerror(errno));
            *backing = NULL;
        }
        return diagnostic(error, capacity, "transfer alias has unexpected address/protection=0x%x", current);
    }
    return 0;
}
int botw_whole_transfer_vm_rw(const void *memory,size_t size) {
    return botw_protected_vm_span(memory,size,VM_PROT_READ|VM_PROT_WRITE)==KERN_SUCCESS;
}
int botw_whole_transfer_vm_read(void *out,const void *memory,size_t size) {
    return botw_protected_vm_read(out,memory,size)==KERN_SUCCESS;
}
int botw_whole_transfer_vm_write(void *memory,const void *source,size_t size) {
    return botw_protected_vm_write(memory,source,size)==KERN_SUCCESS;
}
int botw_whole_transfer_vm_move(void *destination,const void *source,size_t size) {
    /* MapMemory's existing move still copies bytes. Keep an independent owned
     * operand for each protected copy, without a whole-range snapshot. Its
     * original caller proves source/destination reservations are disjoint. */
    unsigned char bytes[4095];size_t offset=0;
    while(offset<size){
        size_t step=size-offset<sizeof bytes?size-offset:sizeof bytes;
        if(!botw_whole_transfer_vm_read(bytes,(const unsigned char*)source+offset,step)||
            !botw_whole_transfer_vm_write((unsigned char*)destination+offset,bytes,step))return 0;
        offset+=step;
    }
    return 1;
}
