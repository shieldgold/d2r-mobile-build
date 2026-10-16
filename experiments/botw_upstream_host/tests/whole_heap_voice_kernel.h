/* Owned CPU fixture only. Every backing is the original kernel's real owner. */
#pragma once
#include "whole_transfer.h"
#include "whole_heap_provider.h"
#ifdef __cplusplus
extern "C" {
#endif
int owned_heap_voice_initialize(void);
uint64_t owned_heap_voice_source(void);
uint64_t owned_heap_voice_process(void);
int owned_heap_voice_transfer(size_t,botw_whole_transfer_lease **,botw_whole_transfer_view *);
unsigned owned_heap_voice_maps(void);
int owned_heap_voice_dirty_admission(void);
void owned_heap_voice_fail_unmaps(unsigned);
int owned_heap_voice_cleanup(void);
#ifdef __cplusplus
}
#endif
