/* SPDX-License-Identifier: MIT
 * Normal synchronous operands from the original loader/current thread only.
 * g_mod/g_nmod must be fully loaded and immutable throughout these calls.
 * The caller excludes remapping/destruction until the synchronous copy ends. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "whole_guest_memory.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Exact botw_whole_guest_memory classifier callbacks. context is unused.
 * 1 covers the complete span, -1 is invalid/known-denied, 0 is unrelated.
 * No VM scan or guest contents are read to establish this ownership. */
int botw_whole_host_module_classify(void *,uint64_t,size_t,int writable);
int botw_whole_host_thread_classify(void *,uint64_t,size_t,int writable);
/* Original thread.c export. Readiness precedes the TSD lookup. This returns
 * the current rewritten Horizon TLR slot plus exactly 0x200 bytes; it supplies
 * neither logical ownership nor VM permission. Failure leaves outputs intact. */
int botw_whole_current_tlr(void **,size_t *);
/* Exact whole_ipc current_tlr callback. context is the live, fully configured
 * botw_whole_guest_memory also used by read_guest. Check complete 512-byte
 * logical+VM read/write access, with actual checked write preparation, before
 * publication. Never reads the TLR contents or treats the key as ELF TLS. */
int botw_whole_host_current_tlr(void *,void **,size_t *,char *,size_t);
#ifdef __cplusplus
}
#endif
