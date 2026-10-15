/* HIPC descriptor address bridge. Independently implemented using the public
 * field layout: https://github.com/switchbrew/libnx/blob/master/nx/include/switch/sf/hipc.h
 * Architecture integration: jhaizhou-ops/d2r-mobile (MIT), commit
 * e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8; see THIRD_PARTY_LICENSE.txt. */
#include "hipc_address.h"

_Static_assert(sizeof(uintptr_t) == 8, "Horizon helper ABI requires a 64-bit host");
_Static_assert(sizeof(botw_hipc_pointer) == 16, "Horizon pointer result ABI");
_Static_assert(sizeof(botw_hipc_buffer) == 24, "Horizon buffer result ABI");
_Static_assert(offsetof(botw_hipc_buffer, mode) == 16, "Horizon buffer mode ABI");

static uint64_t read_le(const void *memory, unsigned length) {
    const unsigned char *bytes = memory;
    uint64_t value = 0;
    for (unsigned i = 0; i < length; i++) value |= (uint64_t)bytes[i] << (i * 8);
    return value;
}
static void write_le(void *memory, uint64_t value, unsigned length) {
    unsigned char *bytes = memory;
    for (unsigned i = 0; i < length; i++) bytes[i] = (unsigned char)(value >> (i * 8));
}
static unsigned char *descriptor(const void *accessor, size_t field, int index, unsigned stride) {
    uintptr_t base = (uintptr_t)read_le((const unsigned char *)accessor + field, 8);
    return (unsigned char *)(base + (uintptr_t)((intptr_t)index * (intptr_t)stride));
}
void botw_hipc_set_pointer(void *accessor, int index, uint64_t address, uint64_t size, int receive_index) {
    unsigned char *bytes = descriptor(accessor, 0x40, index, 8);
    uint32_t first = ((uint32_t)receive_index & 63u) |
        (uint32_t)((address >> 36) & 63u) << 6 |
        (uint32_t)((address >> 32) & 15u) << 12 |
        (uint32_t)(size & 0xffffu) << 16;
    write_le(bytes, first, 4); write_le(bytes + 4, address, 4);
}
botw_hipc_pointer botw_hipc_get_pointer(const void *accessor, int index) {
    const unsigned char *bytes = descriptor(accessor, 0x40, index, 8);
    uint32_t first = (uint32_t)read_le(bytes, 4);
    return (botw_hipc_pointer){read_le(bytes + 4, 4) |
        (uint64_t)((first >> 12) & 15u) << 32 |
        (uint64_t)((first >> 6) & 63u) << 36, first >> 16};
}
static botw_hipc_buffer get_buffer(const void *accessor, int index, size_t field) {
    const unsigned char *bytes = descriptor(accessor, field, index, 12);
    uint32_t third = (uint32_t)read_le(bytes + 8, 4);
    return (botw_hipc_buffer){read_le(bytes + 4, 4) |
        (uint64_t)(third >> 28) << 32 |
        (uint64_t)((third >> 2) & 0x3fffffu) << 36,
        read_le(bytes, 4) | (uint64_t)((third >> 24) & 15u) << 32,
        third & 3u, 0};
}
botw_hipc_buffer botw_hipc_get_send(const void *accessor, int index) { return get_buffer(accessor, index, 0x48); }
botw_hipc_buffer botw_hipc_get_receive(const void *accessor, int index) { return get_buffer(accessor, index, 0x50); }
void botw_hipc_set_receive_list(void *accessor, int index, uint64_t address, uint64_t size) {
    unsigned char *bytes = descriptor(accessor, 0x68, index, 8);
    write_le(bytes, address, 4);
    write_le(bytes + 4, ((address >> 32) & 0xffffu) | (size & 0xffffu) << 16, 4);
}
static int same(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}
uintptr_t botw_hipc_address_resolve(const char *name) {
    if (!name) return 0;
    if (same(name, "_ZN2nn2sf4hipc6detail25HipcMessageBufferAccessor10SetPointerEimmi")) return (uintptr_t)botw_hipc_set_pointer;
    if (same(name, "_ZNK2nn2sf4hipc6detail25HipcMessageBufferAccessor10GetPointerEi")) return (uintptr_t)botw_hipc_get_pointer;
    if (same(name, "_ZNK2nn2sf4hipc6detail25HipcMessageBufferAccessor7GetSendEi")) return (uintptr_t)botw_hipc_get_send;
    if (same(name, "_ZNK2nn2sf4hipc6detail25HipcMessageBufferAccessor10GetReceiveEi")) return (uintptr_t)botw_hipc_get_receive;
    if (same(name, "_ZN2nn2sf4hipc6detail25HipcMessageBufferAccessor14SetReceiveListEimm")) return (uintptr_t)botw_hipc_set_receive_list;
    return 0;
}

/* Independent runtime verifier of the fixed transport instructions. This
 * checks bytes in the actual mapped image, before keys/guest code execute. */
enum AuditKind { AUDIT_PACK_BUFFER, AUDIT_PACK_STATIC, AUDIT_DECODE_BUFFER,
    AUDIT_DECODE_STATIC, AUDIT_VIRTUAL_STATIC };
typedef struct {
    unsigned module; size_t offset; enum AuditKind kind;
    unsigned source, target; uint32_t original;
} AuditSite;
static const char *const audit_modules[] = {"main", "rtld", "sdk", "subsdk0", "subsdk1", "subsdk2", "subsdk3"};
static const AuditSite audit_sites[] = {
    {2, 0x43958, AUDIT_PACK_BUFFER, 0, 27, 0x12040f7bu},
    {2, 0x439e8, AUDIT_PACK_BUFFER, 0, 27, 0x12040f7bu},
    {2, 0x43a5c, AUDIT_PACK_STATIC, 0, 22, 0x2a1602f6u},
    {2, 0x43aec, AUDIT_PACK_BUFFER, 23, 0, 0x12040c00u},
    {2, 0x43b90, AUDIT_PACK_BUFFER, 23, 0, 0x12040c00u},
    {2, 0x43e3c, AUDIT_PACK_STATIC, 6, 7, 0x33103ca7u},
    {2, 0x43f10, AUDIT_PACK_BUFFER, 14, 4, 0x12040c84u},
    {2, 0x43f94, AUDIT_PACK_BUFFER, 14, 4, 0x12040c84u},
    {2, 0x936cc, AUDIT_DECODE_BUFFER, 4, 4, 0x531c7c84u},
    {2, 0x93718, AUDIT_DECODE_STATIC, 3, 5, 0xd3507c63u},
    {2, 0x93768, AUDIT_DECODE_BUFFER, 3, 7, 0x531c7c67u},
    {2, 0x93af4, AUDIT_VIRTUAL_STATIC, 16, 18, 0x33002d32u},
    {3, 0xe5f8, AUDIT_PACK_BUFFER, 9, 14, 0x12040dceu},
    {3, 0xe680, AUDIT_PACK_BUFFER, 9, 14, 0x12040dceu},
    {3, 0xe6e4, AUDIT_PACK_STATIC, 8, 9, 0x2a090169u},
    {3, 0xe758, AUDIT_PACK_BUFFER, 9, 14, 0x12040dceu},
    {3, 0xe7f0, AUDIT_PACK_BUFFER, 9, 15, 0x12040defu},
    {3, 0xeab8, AUDIT_PACK_STATIC, 12, 13, 0x33103d2du},
    {3, 0xeb88, AUDIT_PACK_BUFFER, 14, 1, 0x12040c21u},
    {3, 0xec04, AUDIT_PACK_BUFFER, 14, 1, 0x12040c21u},
    {3, 0x4d908, AUDIT_PACK_BUFFER, 9, 14, 0x12040dceu},
    {3, 0x4d990, AUDIT_PACK_BUFFER, 9, 14, 0x12040dceu},
    {3, 0x4d9f4, AUDIT_PACK_STATIC, 8, 9, 0x2a090169u},
    {3, 0x4da68, AUDIT_PACK_BUFFER, 9, 14, 0x12040dceu},
    {3, 0x4db00, AUDIT_PACK_BUFFER, 9, 15, 0x12040defu},
    {3, 0x4ddc8, AUDIT_PACK_STATIC, 12, 13, 0x33103d2du},
    {3, 0x4de98, AUDIT_PACK_BUFFER, 14, 1, 0x12040c21u},
    {3, 0x4df14, AUDIT_PACK_BUFFER, 14, 1, 0x12040c21u},
    {4, 0xb4d8, AUDIT_PACK_BUFFER, 0, 27, 0x12040f7bu},
    {4, 0xb568, AUDIT_PACK_BUFFER, 0, 27, 0x12040f7bu},
    {4, 0xb5dc, AUDIT_PACK_STATIC, 0, 22, 0x2a1602f6u},
    {4, 0xb66c, AUDIT_PACK_BUFFER, 23, 0, 0x12040c00u},
    {4, 0xb710, AUDIT_PACK_BUFFER, 23, 0, 0x12040c00u},
    {4, 0xb9bc, AUDIT_PACK_STATIC, 6, 7, 0x33103ca7u},
    {4, 0xba90, AUDIT_PACK_BUFFER, 14, 4, 0x12040c84u},
    {4, 0xbb14, AUDIT_PACK_BUFFER, 14, 4, 0x12040c84u},
};
static void audit_append(char *error, size_t capacity, size_t *used, const char *text) {
    while (*text) {
        if (error && *used + 1 < capacity) error[*used] = *text;
        (*used)++; text++;
    }
    if (error && capacity) error[*used < capacity ? *used : capacity - 1] = 0;
}
static int audit_problem(char *error, size_t capacity, const char *name, size_t offset, const char *reason) {
    size_t used = 0; char number[19]; unsigned length = 0;
    static const char hex[] = "0123456789abcdef";
    number[length++] = '0'; number[length++] = 'x';
    int started = 0;
    for (int shift = 60; shift >= 0; shift -= 4) {
        unsigned digit = (unsigned)((uint64_t)offset >> shift) & 15;
        if (digit || started || !shift) { number[length++] = hex[digit]; started = 1; }
    }
    number[length] = 0;
    audit_append(error, capacity, &used, "Host39 HIPC ");
    audit_append(error, capacity, &used, name ? name : "<null>");
    audit_append(error, capacity, &used, "+"); audit_append(error, capacity, &used, number);
    audit_append(error, capacity, &used, ": "); audit_append(error, capacity, &used, reason);
    return -1;
}
static int audit_span(size_t offset, size_t length, size_t begin, size_t end) {
    return begin <= end && offset >= begin && offset <= end && length <= end - offset;
}
static uint32_t audit_word(const unsigned char *image, size_t offset) { return (uint32_t)read_le(image + offset, 4); }
static int audit_branch_target(uint32_t word, size_t offset, size_t *target) {
    if ((word & 0xfc000000u) != 0x14000000u) return -1;
    int64_t delta = ((int64_t)((word & 0x3ffffffu) ^ 0x2000000u) - 0x2000000) * 4;
    if (delta < 0) {
        uint64_t distance = (uint64_t)-delta;
        if (distance > offset) return -1;
        *target = offset - (size_t)distance;
    } else {
        if ((uint64_t)delta > SIZE_MAX - offset) return -1;
        *target = offset + (size_t)delta;
    }
    return 0;
}
static int audit_branch_word(size_t source, size_t target, uint32_t *word) {
    int64_t delta;
    if (source > target) {
        if (source - target > (size_t)(UINT64_C(1) << 27)) return -1;
        delta = -(int64_t)(source - target);
    } else {
        if (target - source >= (size_t)(UINT64_C(1) << 27)) return -1;
        delta = (int64_t)(target - source);
    }
    if (delta % 4) return -1;
    *word = 0x14000000u | ((uint32_t)(delta / 4) & 0x3ffffffu); return 0;
}
static uint32_t audit_pair(unsigned first, unsigned second, int offset, int load) {
    return (load ? 0xa9400000u : 0xa9000000u) |
        ((uint32_t)(offset / 8) & 127u) << 15 | second << 10 | 31u << 5 | first;
}
static uint32_t audit_ubfx(unsigned target, unsigned source, unsigned lsb, unsigned width) {
    return 0xd3400000u | lsb << 16 | (lsb + width - 1) << 10 | source << 5 | target;
}
static uint32_t audit_bfi(unsigned target, unsigned source, unsigned lsb, unsigned width, int wide) {
    unsigned bits = wide ? 64 : 32;
    return (wide ? 0xb3400000u : 0x33000000u) | ((bits - lsb) % bits) << 16 |
        (width - 1) << 10 | source << 5 | target;
}
static int audit_thunk(const unsigned char *image, size_t size, size_t patch_off,
    const AuditSite *site, size_t instruction, unsigned target, uint32_t original,
    char *error, size_t capacity) {
    const char *name = audit_modules[site->module]; size_t thunk;
    if (audit_branch_target(audit_word(image, instruction), instruction, &thunk) ||
            !audit_span(thunk, 24, patch_off, size))
        return audit_problem(error, capacity, name, site->offset, "address thunk branch is missing or leaves executable patch range");
    uint32_t used = (1u << site->source) | (1u << target) | (1u << 18) | (1u << 31);
    static const unsigned fields[] = {0, 5, 10, 16};
    for (unsigned i = 0; i < sizeof fields / sizeof fields[0]; i++)
        used |= 1u << ((original >> fields[i]) & 31u);
    unsigned scratch = 0; while (scratch < 18 && (used & (1u << scratch))) scratch++;
    if (scratch == 18) return audit_problem(error, capacity, name, site->offset, "no permitted scratch register");
    uint32_t extract, insert;
    switch (site->kind) {
        case AUDIT_PACK_BUFFER:
            extract = audit_ubfx(scratch, site->source, 36, 22);
            insert = audit_bfi(target, scratch, 2, 22, 0); break;
        case AUDIT_PACK_STATIC: case AUDIT_VIRTUAL_STATIC:
            extract = audit_ubfx(scratch, site->source, 36, 6);
            insert = audit_bfi(target, scratch, 6, 6, 0); break;
        case AUDIT_DECODE_BUFFER:
            extract = audit_ubfx(scratch, site->source, 2, 22);
            insert = audit_bfi(target, scratch, 4, 22, 0); break;
        case AUDIT_DECODE_STATIC:
            extract = audit_ubfx(scratch, site->source, 6, 6);
            insert = audit_bfi(target, scratch, 36, 6, 1); break;
        default: return audit_problem(error, capacity, name, site->offset, "unknown fixed address transform");
    }
    int saved = site->kind == AUDIT_VIRTUAL_STATIC ? -32 : -16;
    uint32_t back;
    if (audit_branch_word(thunk + 20, instruction + 4, &back))
        return audit_problem(error, capacity, name, site->offset, "address thunk return branch exceeds ARM64 range");
    uint32_t expected[6] = {audit_pair(scratch, 31, saved, 0), extract, original, insert,
                           audit_pair(scratch, 31, saved, 1), back};
    for (unsigned i = 0; i < 6; i++)
        if (audit_word(image, thunk + i * 4) != expected[i])
            return audit_problem(error, capacity, name, thunk + i * 4, "fixed address thunk instruction differs");
    return 0;
}
static int audit_virtual_static(const unsigned char *image, size_t size, size_t text_end,
    size_t patch_off, const AuditSite *site, char *error, size_t capacity) {
    const char *name = audit_modules[site->module]; size_t block;
    if (audit_branch_target(audit_word(image, site->offset), site->offset, &block) ||
            !audit_span(block, 40, patch_off, size))
        return audit_problem(error, capacity, name, site->offset, "virtual x18 generic outer branch is missing or leaves patch range");
    uint32_t words[10];
    for (unsigned i = 0; i < 10; i++) words[i] = audit_word(image, block + i * 4);
    unsigned saved = words[0] & 31u, temporary = words[0] >> 10 & 31u;
    if (saved == 9 || saved == 16 || saved == 18 || saved == 31 ||
            temporary == 9 || temporary == 16 || temporary == 18 || temporary == 31 || saved == temporary)
        return audit_problem(error, capacity, name, block, "virtual x18 generic scratch registers differ");
    uint32_t back;
    if (audit_branch_word(block + 36, site->offset + 4, &back))
        return audit_problem(error, capacity, name, block, "virtual x18 generic return exceeds ARM64 range");
    uint32_t expected[10] = {audit_pair(saved, temporary, -16, 0), 0xd53bd060u | temporary,
        words[2], 0xf9400000u | saved << 5 | saved,
        0x8b000000u | saved << 16 | temporary << 5 | temporary,
        0xf9400000u | temporary << 5 | saved, words[6],
        0xf9000000u | temporary << 5 | saved, audit_pair(saved, temporary, -16, 1), back};
    for (unsigned i = 0; i < 10; i++)
        if (words[i] != expected[i])
            return audit_problem(error, capacity, name, block + i * 4, "virtual x18 generic save/key/store/restore instruction differs");
    if ((words[2] & 0x9f00001fu) != (0x90000000u | saved))
        return audit_problem(error, capacity, name, block + 8, "virtual x18 key-page ADRP differs");
    uint32_t immediate = (words[2] >> 29 & 3u) | (words[2] >> 5 & 0x7ffffu) << 2;
    int64_t delta = ((int64_t)(immediate ^ 0x100000u) - 0x100000) * 4096;
    size_t key_pc = (block + 8) & ~(size_t)0xfff, key;
    if (delta < 0) {
        if ((uint64_t)-delta > key_pc) return audit_problem(error, capacity, name, block + 8, "virtual x18 key page underflows image");
        key = key_pc - (size_t)-delta;
    } else {
        if ((uint64_t)delta > SIZE_MAX - key_pc) return audit_problem(error, capacity, name, block + 8, "virtual x18 key page overflows image");
        key = key_pc + (size_t)delta;
    }
    if ((key & 0x3fffu) || !audit_span(key, 8, text_end, patch_off))
        return audit_problem(error, capacity, name, block + 8, "virtual x18 key page is not within mapped nonexecutable key region");
    return audit_thunk(image, size, patch_off, site, block + 24, saved,
                       0x33002c00u | 9u << 5 | saved, error, capacity);
}
int botw_hipc_address_audit_image(const char *name, const void *memory, size_t size,
    size_t text_end, size_t patch_off, char *error, size_t capacity) {
    if (error && capacity) error[0] = 0;
    unsigned module = 0;
    while (module < sizeof audit_modules / sizeof audit_modules[0] && (!name || !same(name, audit_modules[module]))) module++;
    if (module == sizeof audit_modules / sizeof audit_modules[0])
        return audit_problem(error, capacity, name, 0, "unknown module for fixed profile");
    if (!memory || !text_end || (text_end & 3u) || (patch_off & 3u) || text_end > patch_off || patch_off >= size)
        return audit_problem(error, capacity, name, 0, "invalid mapped image/text/patch layout");
    const unsigned char *image = memory;
    for (size_t i = 0; i < sizeof audit_sites / sizeof audit_sites[0]; i++) {
        const AuditSite *site = &audit_sites[i];
        if (site->module != module) continue;
        if (!audit_span(site->offset, 4, 0, text_end))
            return audit_problem(error, capacity, name, site->offset, "fixed original site leaves guest text");
        int result = site->kind == AUDIT_VIRTUAL_STATIC ?
            audit_virtual_static(image, size, text_end, patch_off, site, error, capacity) :
            audit_thunk(image, size, patch_off, site, site->offset, site->target, site->original, error, capacity);
        if (result) return result;
    }
    return 0;
}
