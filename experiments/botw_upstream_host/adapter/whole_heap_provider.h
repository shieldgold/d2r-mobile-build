/* SPDX-License-Identifier: MIT
 * Thin typed CAPI adapter. Kernel lease/header remain renderer-independent. */
#pragma once
#include "../../botw_audio_renderer/native/audren_host.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct botw_whole_heap_provider botw_whole_heap_provider;
/* The normal identity owner verifies this process identity before calling.
 * base owner starts with one ref; CAPI retains its own context ref. Outputs are
 * unchanged on failure. All callbacks/leases must be quiesced before destroy. */
int botw_whole_heap_provider_create(uint64_t verified_process_owner,
    botw_whole_heap_provider **out,botw_audren_memory_provider *api,char *,size_t);
/* Only refs==1, active leases==0 and actual pending cleanup ACK may free.
 * The void CAPI context release never frees this owner automatically. */
int botw_whole_heap_provider_destroy(botw_whole_heap_provider **,char *,size_t);
#ifdef __cplusplus
}
#endif
