/* SPDX-License-Identifier: MIT
 * Strong backing borrows from the complete host's one G/g_h kernel table.
 * No guest execution, copy buffer or independent handle namespace is involved. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Extension to whole_kernel.h; its existing enum values remain unchanged. */
enum { BOTW_WHOLE_HANDLE_TRANSFER = 5 };
typedef struct botw_whole_transfer_lease botw_whole_transfer_lease;
typedef struct {
    void *rw_alias;
    uint64_t source;
    size_t alias_size, requested_size;
    uint32_t owner_permission;
    /* One actual process-lifetime heap incarnation, plus its source offset.
     * These are provenance tokens; alias virtual addresses are never IDs. */
    uint64_t backing_id,backing_offset;
} botw_whole_transfer_view;
/* required_alias_permission is 1 (R) or 3 (RW). The privileged alias is RW;
 * owner_permission is the distinct, exact SVC15 source permission 0/1/3.
 * requested_size must be nonzero and <= alias_size. Outputs are host-owned and
 * unchanged on failure. *out must initially be NULL. Retains the actual object,
 * so closing/reusing its numeric handle does not invalidate this borrow. */
int botw_whole_transfer_acquire(uint32_t handle,size_t requested_size,
    uint32_t required_alias_permission,botw_whole_transfer_lease **out,
    botw_whole_transfer_view *view,char *error,size_t capacity);
/* Quiesce every user of the view before release. On cleanup failure the owner
 * and actual backing remain live; retry the same owner. Do not copy lease
 * pointers or concurrently release/use the same owner. */
int botw_whole_transfer_release(botw_whole_transfer_lease **owner,char *,size_t);
int botw_whole_transfer_duplicate(uint32_t handle,uint32_t *out,char *,size_t);
int botw_whole_transfer_close(uint32_t handle,char *,size_t);
/* Normal cleanup of unpublished, failed-rollback objects retained by g_map.
 * Successful entries retire; a failed entry stays pinned for a later retry. */
int botw_whole_transfer_reap_pending(char *,size_t);
/* Normal SVC entry, called before IPC/thread dispatch. 0 unrelated, 1 handled,
 * -1 actual host failure (registers unchanged). 0x15 creates; 0x16 only claims
 * an actual transfer handle. 0x51/0x52 are not implemented by this slice. */
int botw_whole_transfer_dispatch(int number,uint64_t *registers,char *,size_t);
/* Logical ownership + source-permission overlay, followed by the caller's
 * normal protected copy. This does not pin the operand or inspect its bytes. */
int dm_guest_memory_access(uint64_t address,size_t size,int writable);
/* Kernel metadata only. classify: 1 allowed; -1 a managed heap/map operand is
 * denied; 0 outside this kernel's metadata. Do not fall back to TLS/module/stack
 * ownership after -1. access is exactly classify==1. Neither grants the whole
 * reserved AS, nor substitutes for protected physical VM copy validation. */
int botw_whole_memory_classify(uint64_t address,size_t size,int writable);
int botw_whole_memory_access(uint64_t address,size_t size,int writable);
#ifdef __cplusplus
}
#endif
