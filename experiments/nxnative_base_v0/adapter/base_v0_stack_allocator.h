/* SPDX-License-Identifier: MIT
 * Fixed base-v0 alias/stack constructor forwarding; no private allocator. */
#pragma once
#include <stdint.h>

#define BOTW_BASE_V0_STACK_ALLOCATOR_SYMBOL "_ZN2nn2os6detail21AddressSpaceAllocatorC1Emmm"
#define BOTW_BASE_V0_STACK_ALLOCATOR_WHY "base-v0 SDK alias/stack allocators use retained host regions"

/* The fixed SDK constructor returns its x0 unchanged. Preserve its actual
 * return value, including when an owned fixture supplies another token. */
typedef void *(*botw_base_v0_stack_constructor)(void *self, uint64_t start,
    uint64_t end, uint64_t guard);
