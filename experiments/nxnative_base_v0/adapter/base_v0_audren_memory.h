/* SPDX-License-Identifier: MIT
 * Post-V6 single-kernel audio ownership bridge. No second kernel/catalog. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#if !defined(NXNATIVE_BASE_V0_AUDREN) || NXNATIVE_BASE_V0_AUDREN != 1
#error "base-v0 audren requires explicit NXNATIVE_BASE_V0_AUDREN=1"
#endif
#if !defined(NXNATIVE_NORMAL_POLICY) || NXNATIVE_NORMAL_POLICY != 1 || !defined(NXNATIVE_BASE_V0_MEMORY) || NXNATIVE_BASE_V0_MEMORY != 1
#error "base-v0 audren requires the complete normal policy and V6 memory"
#endif
#ifdef __cplusplus
extern "C" {
#endif
int dm_guest_write_prepare_checked(void *, size_t);
int dm_guest_memory_access(uint64_t, size_t, int);
int nxnative_base_v0_audren_mapping_overlap(uint64_t, size_t);

#ifdef __cplusplus
}
#endif
