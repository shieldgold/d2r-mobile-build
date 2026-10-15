#include "address_space.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

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
int botw_address_space_audit_thread_stack_image(const void *image, size_t size,
    size_t text_end, char *error, size_t capacity) {
    const size_t site = 0xa1d758, capacity_site = 0xa1d6a0, original_text_end = 23280432;
    const uint32_t context[9] = {0xb900fa7b, 0xf9005273, 0x940059a1, 0x93407f17,
        0x321203e2, 0xaa1703e0, 0xaa1603e1, 0x97ff5740, 0xf9007660};
    if (!image) return diagnostic(error, capacity, "thread stack image is null");
    const unsigned char *bytes = image;
    uint32_t size_word = 0;
    if (size >= capacity_site + 4) memcpy(&size_word, bytes + capacity_site, 4);
    int native = (size_word & 0xfc000000) == 0x14000000;
    if (size <= site && !native) return 0;
    if (size <= site || size - site < sizeof(uint32_t))
        return diagnostic(error, capacity, "thread stack allocation instruction is truncated");
    uint32_t word; memcpy(&word, bytes + site, sizeof word);
    if (word == 0x321403e2 && !native) {
        if (text_end == original_text_end && size >= text_end && size_word != 0x2a0603f8)
            return diagnostic(error, capacity, "thread stack capacity changed without its complete policy");
        return 0;
    }
    if (word != context[4] || text_end != original_text_end || size < text_end || site + 20 > size)
        return diagnostic(error, capacity, "thread stack instruction/layout differs from fixed base-v0 profile");
    for (unsigned i = 0; i < 9; i++) {
        memcpy(&word, bytes + site - 16 + i * 4, sizeof word);
        if (word != context[i])
            return diagnostic(error, capacity, "thread stack actual allocation context differs at main+0x%zx", site - 16 + i * 4);
    }
    if (!native) {
        if (size_word != 0x2a0603f8)
            return diagnostic(error, capacity, "thread stack capacity instruction differs from legacy profile");
        return 1;
    }
    const uint32_t capacity_context[] = {0xa9057bfd, 0x910143fd, 0xd10043ff, 0x2a0703f7, 0x2a0603f8, 0xaa0503f9, 0x2a0403fa, 0x2a0303fb, 0xaa0203f6, 0xaa0103f5, 0xaa0003f3, 0x97ffadf6};
    for (size_t i = 0; i < sizeof capacity_context / sizeof *capacity_context; i++) {
        if (i == 4) continue; /* Verified executable branch below. */
        memcpy(&word, bytes + 0xa1d690 + i * 4, 4);
        if (word != capacity_context[i])
            return diagnostic(error, capacity, "thread stack capacity dataflow differs at main+0x%zx", (size_t)0xa1d690 + i * 4);
    }
    const uint32_t flow_0[] = {0x9102e260, 0xb9008a78, 0xf8090e9f};
    for (size_t i = 0; i < sizeof flow_0 / sizeof *flow_0; i++) {
        memcpy(&word, bytes + 0xa1d700 + i * 4, 4);
        if (word != flow_0[i])
            return diagnostic(error, capacity, "thread stack capacity dataflow differs at main+0x%zx", (size_t)0xa1d700 + i * 4);
    }
    const uint32_t flow_1[] = {0xf000c301, 0xf9407663, 0xb940fa65, 0xf9469821, 0xaa1303e2, 0xaa1703e4, 0x94305462};
    for (size_t i = 0; i < sizeof flow_1 / sizeof *flow_1; i++) {
        memcpy(&word, bytes + 0xa1d780 + i * 4, 4);
        if (word != flow_1[i])
            return diagnostic(error, capacity, "thread stack capacity dataflow differs at main+0x%zx", (size_t)0xa1d780 + i * 4);
    }
    const uint32_t flow_2[] = {0xf9407660, 0xb4000040, 0x97ff56fb};
    for (size_t i = 0; i < sizeof flow_2 / sizeof *flow_2; i++) {
        memcpy(&word, bytes + 0xa1db10 + i * 4, 4);
        if (word != flow_2[i])
            return diagnostic(error, capacity, "thread stack capacity dataflow differs at main+0x%zx", (size_t)0xa1db10 + i * 4);
    }
    int64_t delta = size_word & 0x03ffffff;
    if (delta & 0x02000000) delta -= 0x04000000;
    int64_t target = (int64_t)capacity_site + delta * 4;
    if (target < (int64_t)text_end || (uint64_t)target > size || size - (size_t)target < 68)
        return diagnostic(error, capacity, "thread stack capacity thunk is outside actual image");
    const uint32_t thunk[] = {0x2a0603f8, 0xa9bf27e8, 0xd53b4208, 0x72002f1f, 0x54000141, 0x7100031f, 0x5400010d, 0x32113fe9, 0x6b09031f, 0x540000a8, 0x91400f18, 0x913fff18, 0x9272c718, 0x91401318, 0xd51b4208, 0xa8c127e8};
    for (size_t i = 0; i < sizeof thunk / sizeof *thunk; i++) {
        memcpy(&word, bytes + (size_t)target + i * 4, 4);
        if (word != thunk[i]) return diagnostic(error, capacity, "thread stack capacity executable thunk differs");
    }
    delta = (int64_t)(capacity_site + 4) - (target + 64);
    if ((delta & 3) || delta < -(INT64_C(1) << 27) || delta >= (INT64_C(1) << 27))
        return diagnostic(error, capacity, "thread stack capacity return is out of range");
    uint32_t expected = 0x14000000 | ((uint32_t)(delta / 4) & 0x03ffffff);
    memcpy(&word, bytes + (size_t)target + 64, 4);
    if (word != expected) return diagnostic(error, capacity, "thread stack capacity return branch differs");
    return 2;
}
