/* Private shared object metadata. refs is protected by original G. Mapping
 * membership, permission/blocked and alias cleanup are protected by heap/map.
 * Every mutation that also needs G acquires G first. No callbacks run under G. */
#pragma once
#include "whole_transfer.h"
typedef struct botw_whole_transfer_object {
    uint64_t source;
    size_t size;
    void *backing;
    unsigned refs,permission;
    int owner_applied,published,blocked;
    uint64_t backing_id,backing_offset;
} botw_whole_transfer_object;
int botw_whole_transfer_vm_alias(uint64_t,size_t,uint64_t,void **,char *,size_t);
int botw_whole_transfer_vm_rw(const void *,size_t);
int botw_whole_transfer_vm_read(void *,const void *,size_t);
int botw_whole_transfer_vm_write(void *,const void *,size_t);
int botw_whole_transfer_vm_move(void *,const void *,size_t);
int botw_whole_transfer_prepare(uint64_t,size_t,unsigned,
    botw_whole_transfer_object **,uint32_t *,char *,size_t);
int botw_whole_transfer_dispose(botw_whole_transfer_object *,char *,size_t);
int botw_whole_transfer_pending_cleanup(char *,size_t);
