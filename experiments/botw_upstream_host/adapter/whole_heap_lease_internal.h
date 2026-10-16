/* Private single-kernel normal borrow/exclusion interface. */
#pragma once
#include "whole_heap_lease.h"
#include "whole_transfer_internal.h"
/* Original dirty slot exclusion. Success of begin keeps that existing lock;
 * end must run on the same thread. No callbacks run while the lock is held. */
int botw_whole_dirty_untracked_begin(uint64_t,size_t);
void botw_whole_dirty_untracked_end(void);
/* Called by original dirtyAdd under slot, so this takes only heap -> map. */
int botw_whole_heap_pin_overlap(uint64_t,size_t);
int botw_whole_heap_geometry(uint64_t,size_t,uint64_t *,size_t *,size_t *,char *,size_t);
int botw_whole_heap_prepare_write(uint64_t,size_t,char *,size_t);
int botw_whole_heap_memory_begin(uint64_t,size_t,botw_whole_transfer_object **,char *,size_t);
int botw_whole_heap_memory_finish(botw_whole_transfer_object *,char *,size_t);
int botw_whole_heap_memory_abort(botw_whole_transfer_object *,char *,size_t);
int botw_whole_heap_memory_reap(char *,size_t);
/* Immutable producer token stays in the actual raw lease, not a new catalog. */
int botw_whole_heap_acquire_owned(uint64_t,size_t,uint32_t,const void *,
    botw_whole_heap_lease **,botw_whole_heap_view *,char *,size_t);
int botw_whole_heap_release_owned(botw_whole_heap_lease **,const void *,char *,size_t);
