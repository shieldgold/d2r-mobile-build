/* SPDX-License-Identifier: MIT
 * Strong normal RW heap backing. The one kernel g_map owns every live/pending
 * host-page envelope. This interface contains no renderer or GPL types. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct botw_whole_heap_lease botw_whole_heap_lease;
typedef struct {
    void *data;
    size_t size;
    uint32_t access;
    uint64_t backing_id,backing_offset;
} botw_whole_heap_view;
/* One current process heap only; process identity is a separate typed-provider
 * duty. Requires nonempty Horizon-4K aligned RW(3) source/size and an actually
 * committed, logically RW complete host-page envelope. No source permission is
 * changed. data/size expose exactly the requested span of a true shared alias.
 * *out starts NULL; both outputs stay unchanged on failure. errno identifies
 * admission/allocation/VM failure for a thin typed status bridge. */
int botw_whole_heap_acquire(uint64_t source,size_t size,uint32_t access,
    botw_whole_heap_lease **out,botw_whole_heap_view *view,char *,size_t);
/* Quiesce all data users first. Failure retains the same opaque owner and its
 * actual alias; retry. Raw owners cannot release a typed provider's lease. */
int botw_whole_heap_release(botw_whole_heap_lease **owner,char *,size_t);
int botw_whole_heap_reap_pending(char *,size_t);
#ifdef __cplusplus
}
#endif
