/* SPDX-License-Identifier: MIT */
#pragma once
#include <stddef.h>
#include <stdint.h>
#if defined(NXNATIVE_BASE_V0_MEMORY) && NXNATIVE_BASE_V0_MEMORY && \
    (!defined(NXNATIVE_NORMAL_POLICY) || !NXNATIVE_NORMAL_POLICY)
#error "base-v0 normal memory requires NXNATIVE_NORMAL_POLICY=1"
#endif
#define NXNATIVE_BASE_V0_MEMORY_RESERVE (UINT64_C(5) << 30)
#define NXNATIVE_BASE_V0_MEMORY_PREFIX (UINT64_C(256) << 20)
#define NXNATIVE_BASE_V0_MEMORY_ALIAS (UINT64_C(512) << 20)
#define NXNATIVE_BASE_V0_MEMORY_STACK (UINT64_C(256) << 20)
#define NXNATIVE_BASE_V0_MEMORY_HEAP UINT64_C(0xc2600000)
#define NXNATIVE_BASE_V0_MEMORY_ROOT UINT64_C(0xc0600000)
_Static_assert(NXNATIVE_BASE_V0_MEMORY_ROOT+(UINT64_C(32)<<20)==NXNATIVE_BASE_V0_MEMORY_HEAP,
               "original H/R startup contract changed");
typedef struct {
    uint8_t *base, *alias, *stack, *heap;
    size_t size, alias_size, stack_size, heap_size, image_size, pool;
} nxnative_base_v0_memory;
/* One actual retained 5-GiB reservation; no smaller or bounded-heap fallback.
 * The complete old provider retains its immutable original-logical plan in
 * private host TSD keys. No Guest parameter/key/literal is rewritten. */
int nxnative_base_v0_memory_reserve(size_t image, nxnative_base_v0_memory *out);
int nxnative_base_v0_memory_layout(void *base, size_t size, size_t image,
                                  nxnative_base_v0_memory *out);
int nxnative_base_v0_memory_admit(size_t requested, size_t previous);
int nxnative_base_v0_memory_record(size_t accepted);
int nxnative_base_v0_memory_verify(size_t accepted);
int nxnative_base_v0_memory_initial_pending(void);
/* Alias growth writes its zeroed pages, unlike sparse logical heap growth. */
int nxnative_base_v0_memory_guard(size_t physical_growth);
_Noreturn void nxnative_base_v0_memory_failure(void);
