// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "../platform/memory_provider.h"
#ifdef __cplusplus
extern "C" {
#endif

#define BOTW_AUDREN_HOST_ABI 2u
#define BOTW_AUDREN_WIRE_PARAMETER_SIZE 0x34u
typedef enum {
    BOTW_AUDREN_OK = 0, BOTW_AUDREN_INVALID_ARGUMENT = 1, BOTW_AUDREN_NOT_FOUND = 2,
    BOTW_AUDREN_OWNER_MISMATCH = 3, BOTW_AUDREN_PERMISSION_DENIED = 4, BOTW_AUDREN_OVERLAP = 5,
    BOTW_AUDREN_OUT_OF_BOUNDS = 6, BOTW_AUDREN_BUSY = 7, BOTW_AUDREN_ALREADY_ATTACHED = 8,
    BOTW_AUDREN_NOT_ATTACHED = 9, BOTW_AUDREN_CLOSED = 10, BOTW_AUDREN_TIMED_OUT = 11,
    BOTW_AUDREN_OUT_OF_MEMORY = 12, BOTW_AUDREN_SYSTEM_ERROR = 13, BOTW_AUDREN_UNSUPPORTED = 14,
    BOTW_AUDREN_DEVICE_ERROR = 15, BOTW_AUDREN_BACKPRESSURE = 16
} botw_audren_status;
typedef struct botw_audren_host botw_audren_host;
typedef struct botw_audren_session botw_audren_session;

// host_status == 0 is necessary, never sufficient, for a successful IPC service
// result. service_result_valid is 1 only when the actual renderer returned that
// Nintendo Result; a host exception has valid=0 and must be mapped to an IPC
// error by the caller. No unsupported host failure can masquerade as Result 0.
// Operations wrapping original void controls/value queries return valid=0 even
// on host success; callers construct service success only after host_status==0
// and the actual requested control/query has completed.
typedef struct {
    int32_t host_status;
    uint32_t service_result_valid;
    uint32_t service_result;
} botw_audren_result;

typedef struct {
    uint32_t abi_version, struct_size;
    uint32_t initially_active;       // Measured foreground/audio availability.
    uint32_t dump_audio_commands;    // Must be 0; unsupported diagnostics reject.
    uint32_t inspect_guest_stack;    // Must be 0; no guest introspection.
    void* failure_context;           // Borrowed until successful host_destroy.
    // Mandatory real host terminal-failure producer, called once for first fatal
    // failure, possibly from the worker. Latch and notify/dispatch only: never
    // reenter the engine or synchronously join/destroy the current worker. Must
    // not throw. This is not a successful guest completion event.
    void (*failed)(void* context, int32_t host_status);
    botw_audren_memory_provider memory_provider; // All-zero = registered-only.
} botw_audren_configuration;

typedef struct {
    void* data;
    size_t size;
    void* context;
    // Retain the real backing/resource; zero means success. Release balances one
    // successful retain only after all registered/pool/command pins are gone.
    int (*retain)(void* context);
    void (*release)(void* context);
} botw_audren_backing;

typedef struct {
    void* context;
    // Strong normal host kernel event producer. Both operations must actually
    // update the event seen by nn::os consumers. No guest calls or reentrancy.
    int (*signal)(void* context);
    int (*clear)(void* context);
    int (*retain)(void* context);
    void (*release)(void* context);
} botw_audren_event;

typedef struct {
    uint32_t sample_rate, sample_count, mix_count;
    uint32_t active;                 // Boolean, not a guessed SDK enum encoding.
    uint32_t rendering_time_limit;
    float voice_drop_parameter;
} botw_audren_session_info;

// C service calls on a host serialize internally. Owner pointers must remain
// valid for the duration of every call; callers serialize handle removal with
// their real kernel references. Resource callbacks must not throw or reenter
// this Host/Event. Call the bridge outside kernel/IPC registry locks.
// All error buffers are optional when capacity==0; otherwise must be writable.
// create does not load a guest, install anything, or claim that sound is playing.
botw_audren_result botw_audren_host_create(const botw_audren_configuration* configuration,
    botw_audren_host** out, char* error, size_t error_capacity);
// Fails Busy with live sessions or registered regions/borrows. It never frees an active worker's owner.
botw_audren_result botw_audren_host_destroy(botw_audren_host** host,
    char* error, size_t error_capacity);
botw_audren_result botw_audren_host_set_active(botw_audren_host* host, int active,
    char* error, size_t error_capacity);
// Legacy registered-only entry. With a configured provider, use the provenance
// entry below so distinct physical aliases cannot bypass overlap validation.
botw_audren_result botw_audren_register_region(botw_audren_host* host, uint64_t owner,
    uint64_t logical_address, botw_audren_backing backing, uint32_t access,
    char* error, size_t error_capacity);
// Mandatory when a provider is configured. Provenance is the unique kernel's
// source-backing identity and byte offset, including explicit transfer aliases.
botw_audren_result botw_audren_register_region_provenance(botw_audren_host* host,
    uint64_t owner, uint64_t logical_address, botw_audren_backing backing, uint32_t access,
    uint64_t backing_id, uint64_t backing_offset, char* error, size_t error_capacity);
botw_audren_result botw_audren_unregister_region(botw_audren_host* host, uint64_t owner,
    uint64_t logical_address, char* error, size_t error_capacity);

// Only the normal serialized 0x34 wire structure is accepted, loaded field by
// field. A 64-byte SDK client parameter object is not the wire structure. Native
// REV1/Auto is the initial exact compatibility profile; no REV15 HLE takeover.
botw_audren_result botw_audren_get_work_buffer_size(const void* wire_parameters,
    size_t wire_size, uint64_t* out, char* error, size_t error_capacity);
botw_audren_result botw_audren_open(botw_audren_host* host, uint64_t owner,
    const void* wire_parameters, size_t wire_size, uint64_t transfer_address,
    uint64_t transfer_size, uint64_t applet_resource_user_id, botw_audren_event event,
    botw_audren_session** out, char* error, size_t error_capacity);
botw_audren_result botw_audren_start(botw_audren_session* session,
    char* error, size_t error_capacity);
botw_audren_result botw_audren_stop(botw_audren_session* session,
    char* error, size_t error_capacity);
// Normal SDK-serialized input/output/performance descriptors, with full lengths.
// Original Behavior/Pool/Voice/Effect/Mix/Sink/Performance update order is kept.
botw_audren_result botw_audren_update(botw_audren_session* session,
    const void* input, size_t input_size, void* performance, size_t performance_size,
    void* output, size_t output_size, char* error, size_t error_capacity);
botw_audren_result botw_audren_get_info(botw_audren_session* session,
    botw_audren_session_info* out, char* error, size_t error_capacity);
botw_audren_result botw_audren_set_rendering_time_limit(botw_audren_session* session,
    uint32_t percent, char* error, size_t error_capacity);
// Quiesces real workers/maps/events before clearing *session. Failure retains the
// owner for cleanup/retry rather than unloading Guest or backing underneath it.
botw_audren_result botw_audren_close(botw_audren_session** session,
    char* error, size_t error_capacity);

#ifdef __cplusplus
}
#endif
