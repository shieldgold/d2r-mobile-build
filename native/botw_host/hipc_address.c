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
