// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define BOTW_AUDREN_MEMORY_PROVIDER_ABI 1u
// One successful acquire transfers one strong kernel backing owner. data/size
// expose exactly the requested source view, with no copied samples. Provenance
// identifies actual source backing, never the numerical shared-alias pointer.
typedef struct {
    void* owner;
    void* data;
    size_t size;
    uint32_t access;                 // 1 Read, 2 Write, 3 ReadWrite; may not widen.
    uint64_t backing_id;
    uint64_t backing_offset;
} botw_audren_memory_lease;

typedef struct {
    uint32_t abi_version, struct_size;
    void* context;
    int (*retain)(void* context);    // 0 retains the actual provider context.
    void (*release)(void* context);
    // Returns canonical botw_audren_status. out starts zero; failure must not
    // transfer an owner. A violating failure that does return an owner is kept
    // quarantined and reported, never discarded. Source permissions/owner and
    // every host-page envelope byte must be validated by the unique kernel.
    int32_t (*acquire)(void* context, uint64_t process_owner, uint64_t source,
        size_t size, uint32_t access, botw_audren_memory_lease* out,
        char* error, size_t error_capacity);
    // 0 only after genuine release, clearing *owner. On error keep *owner valid
    // for retry. Failure cannot free an alias or backing still used by a worker.
    int32_t (*release_lease)(void* context, void** owner,
        char* error, size_t error_capacity);
} botw_audren_memory_provider;
// All callbacks run outside MemorySpace/RendererMemory registry mutexes. They
// must not throw or reenter this audren Host. Context release balances its one
// successful retain after every active/quarantined backing lease has retired.
// An all-zero provider explicitly selects registered-only operation.
#ifdef __cplusplus
}
#endif
