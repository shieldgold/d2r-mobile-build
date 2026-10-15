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
/* V3 owns only this fixed child backing size. Its caller, parent array,
 * original parent allocation, queue/thread allocations and name must match. */
static int audit_volante_heap(const unsigned char *bytes, size_t size, char *error, size_t capacity) {
    static const unsigned char context_0[] = "\x88\xa2\x00\xd0\x08\x41\x23\x91\x60\x0a\x01\xf9\xf4\xa3\x14\xa9\x68\xae\x80\xb9\xc2\x7a\x68\xf8\x20\x00\xa0\x52\xe1\x23\x05\x91\xe3\x03\x1d\x32\xe4\x03\x00\x32\xe5\x03\x1f\x2a\x14\x8a\x0b\x94\x60\x12\x01\xf9";
    static const unsigned char context_1[] = "\xf5\xd9\x00\xd0\xb5\x46\x41\xf9\x88\xa2\x00\xd0\x08\x0d\x23\x91\x76\x22\x02\x91\x00\x40\x81\x52\xe1\x63\x05\x91\xe3\x03\x1d\x32\xe4\x03\x00\x32\xb4\x42\x00\x91\xf4\xa3\x15\xa9\x68\xae\x80\xb9\xc2\x7a\x68\xf8\xe5\x03\x1f\x2a\x20\x8a\x0b\x94";
    static const unsigned char context_2[] = "\x61\x12\x41\xf9\x60\x0e\x01\xf9\x00\x21\x80\x52\xe2\x03\x1d\x32\x54\x32\x0b\x94\x88\xa2\x00\xd0\x08\x0d\x24\x91\x62\x12\x41\xf9\x77\x0e\x41\xf9\xb5\xa3\x30\xa9\xa8\xda\x00\x90\x08\x4d\x42\xf9\x03\x01\x40\xb9\xa1\xe3\x03\xd1\xe4\x03\x00\x32\xe5\x7b\x00\x32\xe6\x03\x13\x32\xe7\x03\x1b\x32\xf4\x03\x00\xaa\xe8\xda\x0b\x94";
    static const unsigned char context_3[] = "\x68\x02\x40\xf9\x08\x19\x40\xf9\xa2\x7e\x14\x1b\xe0\x03\x13\xaa\xe1\x03\x18\xaa\x00\x01\x3f\xd6";
    static const unsigned char context_4[] = "\xe0\x03\x14\xaa\xe1\x03\x16\xaa\xe2\x03\x13\xaa\xe3\x03\x14\xaa\xe4\x03\x18\xaa\x8a\x0e\x00\x94";
    static const unsigned char context_5[] = "\x89\x22\x40\xf9\x29\xc1\x04\xd1\x09\x0d\x00\xf9";
    static const unsigned char context_6[] = "\xe0\x03\x1c\xaa\xe1\x03\x17\x2a\xff\x0f\x00\xb9\xe9\x0f\x40\xb9\x08\x21\xc9\x1a\xe2\x03\x16\xaa\x7f\xfe\x0e\xa9\x68\xda\x00\xb9\x7f\x72\x00\xf9\x7b\xfa\x00\xb9\x73\x52\x00\xf9\xa1\x59\x00\x94";
    static const unsigned char context_7[] = "\xea\x03\x1d\x32\x8a\x7e\xca\x9b\x29\x7c\x7d\x93\xff\x03\x0a\xeb\x0a\x00\x80\x92\x40\x11\x89\x9a\xe2\x03\x1d\x32\xe1\x03\x08\xaa\x94\xfd\xfe\x97";
    static const unsigned char context_8[] = "\xe0\x0b\x1a\x32\xe2\x03\x1d\x32\xe1\x03\x16\xaa\x1e\x57\xff\x97\x60\x72\x00\xf9";
    static const unsigned char context_9[] = "\x56\x6f\x6c\x61\x6e\x74\x65\x00";
    const struct { size_t offset; const unsigned char *bytes; size_t size; } contexts[] = {
        {0x7268c0, context_0, sizeof context_0 - 1},
        {0x726884, context_1, sizeof context_1 - 1},
        {0x726a90, context_2, sizeof context_2 - 1},
        {0xa091e4, context_3, sizeof context_3 - 1},
        {0xa09224, context_4, sizeof context_4 - 1},
        {0xa093c8, context_5, sizeof context_5 - 1},
        {0xa1d724, context_6, sizeof context_6 - 1},
        {0xa33df4, context_7, sizeof context_7 - 1},
        {0xa1d76c, context_8, sizeof context_8 - 1},
        {0x1b788d0, context_9, sizeof context_9 - 1},
    };
    for (size_t i = 0; i < sizeof contexts / sizeof *contexts; i++) {
        size_t offset = contexts[i].offset, count = contexts[i].size;
        if (offset > size || count > size - offset || memcmp(bytes + offset, contexts[i].bytes, count))
            return diagnostic(error, capacity, "Volante child-heap actual ownership/caller context differs at main+0x%zx", offset);
    }
    return 0;
}
/* V4 changes only the SZS decompressor child size addition. Preserve its
 * original parent, size helper, complete object/queue/stack/SDK ownership. */
static int audit_szs_heap(const unsigned char *bytes, size_t size, char *error, size_t capacity) {
    static const unsigned char context_0[] = "\xe0\x03\x1d\x32\x2f\xae\xee\x97\x08\xd9\x9f\x52\x00\x00\x08\x8b\x48\x6b\x00\xd0\x08\xa9\x2c\x91\xe1\x03\x00\x91\xe3\x03\x1d\x32\xe4\x03\x00\x32\xf7\x23\x00\xa9\xe5\x03\x00\x32\xe2\x03\x14\xaa\x90\xac\xee\x97\x60\x32\x00\xf9";
    static const unsigned char context_1[] = "\x28\x00\x40\xf9\xf4\x03\x02\xaa\xf3\x03\x00\xaa\x00\x34\x80\x52\xe2\x03\x1d\x32\xe1\x03\x14\xaa\x68\x56\x00\xf9\x8d\x55\xee\x97";
    static const unsigned char context_2[] = "\x08\x7c\x40\x93\x00\xc1\x04\x91\xc0\x03\x5f\xd6";
    static const unsigned char context_3[] = "\x22\xa0\x00\xd0\x61\x32\x40\xf9\x42\x88\x40\xf9\x00\x44\x80\x52\x28\x54\xee\x97\xf5\x03\x00\xaa\x75\x02\x00\xb4\x60\x02\x80\x52\x6a\x00\xef\x97\xe8\x03\x1f\x32\x63\x32\x40\xf9\xe8\x1f\x00\xb9\xe8\x1f\x40\xb9\xe9\x03\x00\x32\xe2\x03\x00\x2a\xe1\x03\x0f\x32\xe5\x03\x00\x91\xe0\x03\x15\xaa\xe4\x03\x14\xaa\x28\x21\xc8\x1a\xe8\x03\x00\xb9\x61\x23\x0e\x94";
    static const unsigned char context_4[] = "\xf4\x03\x00\xaa\x9f\x7e\x02\xa9\x19\x41\x00\x91\xf3\x03\x05\xaa\xf8\x03\x04\xaa\xf6\x03\x03\xaa\xf7\x03\x02\x2a\xf5\x03\x01\x2a";
    static const unsigned char context_5[] = "\xe8\x4f\x00\xf0\x4a\x41\x00\x91\xe1\x03\x00\x91\xe5\x7b\x00\x32\xe6\x03\x14\x32\xe7\x03\x1b\x32\xe2\x03\x16\xaa\xe3\x03\x17\x2a\x89\x02\x00\xf9\x08\xd1\x3e\x91\xe4\x03\x1f\x2a\xea\x23\x00\xa9\xc4\xd8\xe0\x97";
    static const unsigned char context_6[] = "\x60\x32\x40\xf9\x80\x00\x00\xb4\x08\x00\x40\xf9\x08\x11\x40\xf9\x00\x01\x3f\xd6";
    static const unsigned char context_7[] = "\xf3\x03\x00\xaa\x68\x36\x40\xf9\xe8\x03\x00\xb4\x4a\xd6\x08\x94\x61\x32\x40\xf9\x61\x02\x00\xb4\x68\x3a\x40\xf9\x28\x02\x00\xb4\x95\xa0\x00\xb0\xb5\x52\x40\xf9\xa0\x02\x40\xf9\x3e\xbe\xee\x97\xf4\x03\x00\xaa\x60\x3a\x40\xf9\x80\x00\x00\xb4\x08\x00\x40\xf9\x08\x05\x40\xf9\x00\x01\x3f\xd6\x7f\x3a\x00\xf9\x9f\x06\x00\xf1\x80\x00\x00\x54\xa0\x02\x40\xf9\xe1\x03\x14\xaa\x32\xbe\xee\x97";
    static const unsigned char context_8[] = "\x62\x02\x40\xf9\xa1\x03\x03\xd1\xe3\x03\x1d\x32\xe4\x03\x00\x32\xe0\x03\x1f\xaa\xe5\x03\x1f\x2a\x89\x3e\xf1\x97\xf6\x03\x00\xaa\x05\x91\x02\x94\x68\x26\x40\xf9\xe8\x2f\x00\xf9\x08\xa6\x00\xb0\x08\x39\x43\xf9\x00\x01\x40\xf9\xe1\x63\x01\x91\xe2\x03\x16\xaa\x92\x91\x02\x94";
    static const unsigned char context_9[] = "\x53\x5a\x53\x44\x65\x63\x6f\x6d\x70\x72\x65\x73\x73\x6f\x72\x49\x6e\x73\x74\x00";
    static const unsigned char context_10[] = "\x44\x65\x63\x6f\x6d\x70\x54\x68\x72\x65\x61\x64\x00";
    const struct { size_t offset; const unsigned char *bytes; size_t size; } contexts[] = {
        {0xe5decc, context_0, sizeof context_0 - 1},
        {0xe5dda0, context_1, sizeof context_1 - 1},
        {0xa0978c, context_2, sizeof context_2 - 1},
        {0xe5e428, context_3, sizeof context_3 - 1},
        {0x11e7228, context_4, sizeof context_4 - 1},
        {0x11e733c, context_5, sizeof context_5 - 1},
        {0xe5dc60, context_6, sizeof context_6 - 1},
        {0xe5ea18, context_7, sizeof context_7 - 1},
        {0xdb9700, context_8, sizeof context_8 - 1},
        {0x1bc7b2a, context_9, sizeof context_9 - 1},
        {0x1be6fb4, context_10, sizeof context_10 - 1},
    };
    for (size_t i = 0; i < sizeof contexts / sizeof *contexts; i++) {
        size_t offset = contexts[i].offset, count = contexts[i].size;
        if (offset > size || count > size - offset || memcmp(bytes + offset, contexts[i].bytes, count))
            return diagnostic(error, capacity, "SZS child-heap actual ownership/caller context differs at main+0x%zx", offset);
    }
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
    uint32_t heap_word = 0;
    if (size >= 0x7268dc) memcpy(&heap_word, bytes + 0x7268d8, 4);
    int volante = heap_word == 0x52a00020;
    uint32_t szs_word = 0;
    if (size >= 0xe5ded8) memcpy(&szs_word, bytes + 0xe5ded4, 4);
    int szs = szs_word == 0x529fd908;
    /* Check this later word before every legacy/no-policy early return.
     * Short synthetic images cannot carry it; complete base-v0 images must. */
    if (szs && (!native || !volante))
        return diagnostic(error, capacity, "SZS child-heap change requires the complete v4 stack policy");
    if (text_end == original_text_end && size > 0xe5ded4 && szs_word != 0x5283c408 && !szs)
        return diagnostic(error, capacity, "SZS child-heap instruction differs from fixed base-v0 profile");
    if (volante && !native)
        return diagnostic(error, capacity, "Volante child-heap change requires the complete v3 stack policy");
    if (text_end == original_text_end && size >= site + 4 && heap_word != 0x321203e0 && !volante)
        return diagnostic(error, capacity, "Volante child-heap instruction differs from fixed base-v0 profile");
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
    if (volante && audit_volante_heap(bytes, size, error, capacity)) return -1;
    if (szs && audit_szs_heap(bytes, size, error, capacity)) return -1;
    return szs ? 4 : (volante ? 3 : 2);
}
