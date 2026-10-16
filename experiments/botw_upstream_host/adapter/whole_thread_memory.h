/* SPDX-License-Identifier: MIT
 * Normal ownership of the current thread's original native TLS/TLR allocations.
 * No guest content is read; this is not a VM permission or lifetime pin. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
int botw_whole_current_thread_memory(uint64_t address, size_t length);
#ifdef __cplusplus
}
#endif
