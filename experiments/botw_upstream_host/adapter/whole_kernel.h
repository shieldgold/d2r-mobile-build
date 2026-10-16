/* SPDX-License-Identifier: MIT
 * Thin exports from the complete upstream's one G/g_h kernel table. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

enum botw_whole_handle_kind {
    BOTW_WHOLE_HANDLE_NONE, BOTW_WHOLE_HANDLE_THREAD,
    BOTW_WHOLE_HANDLE_EVENT_READ, BOTW_WHOLE_HANDLE_EVENT_WRITE,
    BOTW_WHOLE_HANDLE_TRANSPORT
};
/* A snapshot only. Never hold G while entering services: the sole lock order
 * is services -> G. A later operation must validate its own handle/owner. */
enum botw_whole_handle_kind botw_whole_kernel_handle_kind(uint32_t handle);
/* Direct botw_services_transport_api callbacks. A successful allocation borrows
 * exactly this stable private service token until matching release succeeds.
 * Duplicate owners, exhaustion and mismatched release fail without mutation. */
int botw_whole_kernel_transport_allocate(void *, const void *owner, uint32_t *out,
    char *error, size_t capacity);
int botw_whole_kernel_transport_release(void *, const void *owner, uint32_t handle,
    char *error, size_t capacity);
/* Direct botw_services_kernel_api event callbacks. Endpoints retain independent
 * references. No second handle namespace, signal flag or wait queue is created. */
int botw_whole_kernel_create_event(void *, int signaled, uint32_t *write_handle,
    uint32_t *read_handle, char *error, size_t capacity);
int botw_whole_kernel_duplicate_event(void *, uint32_t source, uint32_t *out,
    char *error, size_t capacity);
int botw_whole_kernel_signal_event(void *, uint32_t handle, char *error, size_t capacity);
int botw_whole_kernel_clear_event(void *, uint32_t handle, char *error, size_t capacity);
/* Event endpoints only; transport retirement requires its exact owner token.
 * Thread handles remain owned by the original thread SVC implementation. */
int botw_whole_kernel_close_event(void *, uint32_t handle, char *error, size_t capacity);

typedef struct botw_whole_event_producer botw_whole_event_producer;
/* Retain the actual event object, rather than a recyclable numeric handle.
 * Host-owned pointers never enter the guest. The owner must quiesce callbacks
 * before releasing this producer; signal/clear may run on native audio threads. */
int botw_whole_kernel_retain_event_producer(uint32_t write_handle,
    botw_whole_event_producer **out, char *error, size_t capacity);
int botw_whole_kernel_producer_signal(botw_whole_event_producer *, char *error, size_t capacity);
int botw_whole_kernel_producer_clear(botw_whole_event_producer *, char *error, size_t capacity);
int botw_whole_kernel_producer_release(botw_whole_event_producer **, char *error, size_t capacity);
#ifdef __cplusplus
}
#endif
