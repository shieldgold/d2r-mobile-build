/* HIPC descriptor address bridge, independently implemented from the public
 * libnx protocol layout. No Nintendo code or data is included. */
#ifndef BOTW_HOST_HIPC_ADDRESS_H
#define BOTW_HOST_HIPC_ADDRESS_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct { uint64_t address, size; } botw_hipc_pointer;
/* AAPCS64 returns this 24-byte value through the caller's x8 output pointer. */
typedef struct { uint64_t address, size; uint32_t mode, reserved; } botw_hipc_buffer;

/* Exact original SDK member ABI: accessor=this in x0, index=w1. Full protocol
 * widths are static=42, map=58 and receive-list=48 address bits. Host39 policy
 * is imposed by the guest address allocator, without shrinking these fields.
 * Caller supplies live, aligned SDK accessor/descriptor memory as before. */
void botw_hipc_set_pointer(void *accessor, int index, uint64_t address,
                           uint64_t size, int receive_index);
botw_hipc_pointer botw_hipc_get_pointer(const void *accessor, int index);
botw_hipc_buffer botw_hipc_get_send(const void *accessor, int index);
botw_hipc_buffer botw_hipc_get_receive(const void *accessor, int index);
void botw_hipc_set_receive_list(void *accessor, int index, uint64_t address,
                                uint64_t size);
/* Resolve only the five SDK helper symbols with proven PLT call paths. Invoke
 * before guest symbol lookup, only for the explicitly selected host39 profile.
 * Unknown names (including NULL) return zero. No process-global state. */
uintptr_t botw_hipc_address_resolve(const char *symbol);
#ifdef __cplusplus
}
#endif
#endif
