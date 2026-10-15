/* Host address layout adaptation for the audited BOTW SDK allocator ABI.
 * Pure policy checks: no guest memory access, VM mappings or SDK execution. */
#ifndef BOTW_HOST_ADDRESS_SPACE_H
#define BOTW_HOST_ADDRESS_SPACE_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { BOTW_ADDRESS_SPACE_ALIAS = 1, BOTW_ADDRESS_SPACE_STACK = 2 };
typedef struct { uint64_t base, size; } botw_address_space_region;
typedef struct {
    unsigned kind;
    uint64_t object;
    botw_address_space_region region;
    uint64_t begin, end, guard, address_limit;
    size_t host_page;
} botw_address_space_policy;
/* Return 0 on success, -1 with an optional diagnostic on failure. Output is
 * unchanged on failure. This helper requires an explicitly selected 39-bit
 * kernel address limit. Both regions must be actual kernel reservations.
 *
 * Matches only this=singleton+0x18 (alias) or +0x40 (stack), original begin
 * 0x08000000, end=1<<36 or 0x80000000 respectively, and guard=0x4000.
 * Produces begin=region.base+guard, end=region.base+size-guard, guard unchanged.
 * The original constructor's end argument is an address bound, not a size. */
int botw_address_space_adapt_constructor(uint64_t object, uint64_t sdk_singleton,
    uint64_t original_begin, uint64_t original_end, uint64_t original_guard,
    const botw_address_space_region *alias, const botw_address_space_region *stack,
    uint64_t address_limit, size_t host_page, botw_address_space_policy *output,
    char *error, size_t error_capacity);
/* Alignment must be a power of two in [4096,1<<39]. Host page must be a power
 * of two >=4096 and divide the audited 16KiB guard. Returns max(input,page).
 * The SDK aligns the leading guard start, then adds the guard to its result. */
int botw_address_space_normalize_alignment(uint64_t original_alignment,
    size_t host_page, uint64_t *output, char *error, size_t error_capacity);
/* Validate a nonzero original AllocateSpace result before returning to guest.
 * Both guards must fit the actual region; address and bytes cover full host
 * pages; (address-guard) must meet the normalized candidate alignment.
 * Original zero/failure results can be propagated without calling this. */
int botw_address_space_validate_allocation(const botw_address_space_policy *policy,
    uint64_t address, uint64_t bytes, uint64_t normalized_alignment,
    char *error, size_t error_capacity);
/* Inspect actual loaded main bytes, not metadata: 1=fixed base-v0 allocation
 * alignment instruction+surrounding context verified, 2=capacity/tail-page v2
 * branch, thunk and complete size-consumer/free contexts verified, 3=v2 plus
 * the one Volante child-heap capacity/parent/allocation contexts verified,
 * 0=no policy,
 * -1=modified/truncated/unknown policy bytes. No memory is modified. */
int botw_address_space_audit_thread_stack_image(const void *image, size_t image_size,
    size_t text_end, char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
#endif
