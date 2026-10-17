/* SPDX-License-Identifier: MIT */
#include "base_v0_audren_memory.h"
#include "whole_transfer_internal.h"
/* The unchanged original hook prepares only this explicit normal operand.
 * The old void hook cannot ACK mprotect failure; actual complete RW permission
 * is checked afterwards. No read, enumeration or diagnostic sampling occurs. */
void dm_guest_write_prepare(void *,size_t);
int dm_guest_write_prepare_checked(void *address,size_t size) {
    if(!address||!size||size>UINTPTR_MAX-(uintptr_t)address)return 0;
    dm_guest_write_prepare(address,size);
    return botw_whole_transfer_vm_rw(address,size);
}
