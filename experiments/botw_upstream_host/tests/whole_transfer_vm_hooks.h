/* Fault injection invokes real Darwin syscalls on deliberately misaligned owned
 * operands. No fake Mach SDK or success return is supplied. */
#pragma once
#include <mach/mach.h>
#include <sys/mman.h>
int owned_transfer_munmap(void *,size_t);
int owned_transfer_mprotect(void *,size_t,int);
kern_return_t owned_transfer_remap(vm_map_t,vm_address_t *,vm_size_t,vm_address_t,
    int,vm_map_t,vm_address_t,boolean_t,vm_prot_t *,vm_prot_t *,vm_inherit_t);
#define munmap(...) owned_transfer_munmap(__VA_ARGS__)
#define mprotect(...) owned_transfer_mprotect(__VA_ARGS__)
#define vm_remap(...) owned_transfer_remap(__VA_ARGS__)
