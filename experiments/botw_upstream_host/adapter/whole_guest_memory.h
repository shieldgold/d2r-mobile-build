/* SPDX-License-Identifier: MIT
 * Synchronous normal IPC operands: existing metadata ownership, checked dirty
 * preparation and the canonical protected copier. No range catalog is created.
 * The caller owns operand lifetimes and excludes concurrent remapping. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { BOTW_WHOLE_GUEST_MEMORY_ABI = 1 };
typedef struct {
    uint32_t abi, size;
    void *context;
    /* 1 owned/allowed, -1 known range but denied, 0 unrelated. Known denial
     * always wins before another owner source or dirty preparation is queried. */
    int (*kernel_classify)(void *, uint64_t, size_t, int writable);
    int (*module_classify)(void *, uint64_t, size_t, int writable);
    int (*thread_classify)(void *, uint64_t, size_t, int writable);
    /* The actual complete host's checked write preparation, 0 only on ACK. */
    int (*prepare_write)(void *, uint64_t, size_t, char *, size_t);
} botw_whole_guest_memory;
int botw_whole_guest_memory_valid(const botw_whole_guest_memory *);
/* Direct botw_services_memory_fn. Writable validation prepares intentional
 * dirty-page protection before checking actual VM write access. Logical
 * NoAccess/RO denial happens before any preparation. Actual writes revalidate. */
int botw_whole_guest_memory_access(void *, uint64_t, size_t, int writable);
/* Direct services protected copy/write-preparer callbacks. Normal IPC errors
 * return -1 without diagnostic VM enumeration or guest register/stack capture. */
int botw_whole_guest_memory_prepare(void *, uint64_t, size_t, char *, size_t);
int botw_whole_guest_memory_read(void *, uint64_t, void *, size_t, char *, size_t);
int botw_whole_guest_memory_write(void *, uint64_t, const void *, size_t, char *, size_t);
#ifdef __cplusplus
}
#endif
