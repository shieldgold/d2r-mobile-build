#include "address_space.h"
#include <stdarg.h>
#include <stdio.h>

#define HOST39_LIMIT (UINT64_C(1) << 39)
#define SDK_GUARD UINT64_C(0x4000)
#define SDK_PAGE UINT64_C(0x1000)
static int diagnostic(char *error, size_t capacity, const char *format, ...) {
    if (error && capacity) {
        va_list args; va_start(args, format); vsnprintf(error, capacity, format, args); va_end(args);
    }
    return -1;
}
static int power_of_two(uint64_t value) { return value && !(value & (value - 1)); }
static int valid_host_page(size_t page) {
    return page >= SDK_PAGE && page <= SDK_GUARD && power_of_two(page) && !(SDK_GUARD % page);
}
static int valid_region(const botw_address_space_region *region, uint64_t limit, size_t page) {
    return region && region->base && region->base < limit && region->size <= limit - region->base &&
        !((region->base | region->size) & (page - 1)) && region->size > SDK_GUARD * 3 + page;
}
int botw_address_space_adapt_constructor(uint64_t object, uint64_t singleton,
    uint64_t begin, uint64_t end, uint64_t guard,
    const botw_address_space_region *alias, const botw_address_space_region *stack,
    uint64_t limit, size_t page, botw_address_space_policy *output, char *error, size_t capacity) {
    if (!output || limit != HOST39_LIMIT || !valid_host_page(page))
        return diagnostic(error, capacity, "host39 requires a 39-bit kernel and compatible host page");
    if (!singleton || singleton >= limit || UINT64_C(0x60) > limit - singleton)
        return diagnostic(error, capacity, "invalid SDK resource manager address");
    unsigned kind;
    if (object == singleton + 0x18) kind = BOTW_ADDRESS_SPACE_ALIAS;
    else if (object == singleton + 0x40) kind = BOTW_ADDRESS_SPACE_STACK;
    else return diagnostic(error, capacity, "unknown SDK address allocator object");
    uint64_t expected_end = kind == BOTW_ADDRESS_SPACE_ALIAS ? UINT64_C(1) << 36 : UINT64_C(0x80000000);
    if (begin != UINT64_C(0x08000000) || end != expected_end || guard != SDK_GUARD)
        return diagnostic(error, capacity, "SDK address allocator arguments do not match audited ABI");
    if (!valid_region(alias, limit, page) || !valid_region(stack, limit, page))
        return diagnostic(error, capacity, "invalid host39 alias/stack reservation bounds or page alignment");
    if (alias->base < stack->base + stack->size && stack->base < alias->base + alias->size)
        return diagnostic(error, capacity, "host39 alias and stack reservations overlap");
    botw_address_space_region region = kind == BOTW_ADDRESS_SPACE_ALIAS ? *alias : *stack;
    *output = (botw_address_space_policy){.kind = kind, .object = object, .region = region,
        .begin = region.base + guard, .end = region.base + region.size - guard,
        .guard = guard, .address_limit = limit, .host_page = page};
    return 0;
}
int botw_address_space_normalize_alignment(uint64_t original, size_t page, uint64_t *output,
    char *error, size_t capacity) {
    if (!output || !valid_host_page(page) || original < SDK_PAGE || original > HOST39_LIMIT || !power_of_two(original))
        return diagnostic(error, capacity, "invalid SDK allocator alignment or host page");
    *output = original < page ? page : original; return 0;
}
int botw_address_space_validate_allocation(const botw_address_space_policy *policy,
    uint64_t address, uint64_t bytes, uint64_t alignment, char *error, size_t capacity) {
    if (!policy || (policy->kind != BOTW_ADDRESS_SPACE_ALIAS && policy->kind != BOTW_ADDRESS_SPACE_STACK) ||
        !policy->object || policy->object >= HOST39_LIMIT || policy->address_limit != HOST39_LIMIT ||
        policy->guard != SDK_GUARD || !valid_host_page(policy->host_page) ||
        !valid_region(&policy->region, policy->address_limit, policy->host_page))
        return diagnostic(error, capacity, "invalid host39 allocator policy");
    uint64_t base = policy->region.base, end = base + policy->region.size, guard = policy->guard;
    if (policy->begin != base + guard || policy->end != end - guard)
        return diagnostic(error, capacity, "host39 allocator policy bounds changed");
    uint64_t normalized;
    if (botw_address_space_normalize_alignment(alignment, policy->host_page, &normalized, error, capacity)) return -1;
    if (normalized != alignment || !address || !bytes || ((address | bytes) & (policy->host_page - 1)))
        return diagnostic(error, capacity, "SDK allocation must cover whole host pages with normalized alignment");
    /* Subtractions are ordered so arbitrary SDK results cannot overflow a
     * range check, and neither guard can escape the kernel-owned region. */
    if (address < base + guard || address > end - guard || bytes > end - guard - address ||
        ((address - guard) & (alignment - 1)))
        return diagnostic(error, capacity, "SDK allocation or guard lies outside its host39 region/alignment");
    return 0;
}
