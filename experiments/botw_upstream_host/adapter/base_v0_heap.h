/* SPDX-License-Identifier: MIT
 * botw-switch-base-v0-bounded-heap-v1. KRES0/KRES1 hold byte capacities,
 * never addresses. The sole upstream SVC allocator continues to own backing. */
#pragma once
#include <stddef.h>
#include <pthread.h>

typedef struct {
    size_t kernel_available, live_available, image_bytes;
} botw_base_v0_heap_inputs;
typedef int (*botw_base_v0_heap_query)(botw_base_v0_heap_inputs *, char *, size_t);

int botw_base_v0_heap_plan(size_t kernel_available, size_t live_available,
    size_t *heap, size_t *root, char *error, size_t capacity);
/* Native iOS app-limit query. Unknown/unsupported sources fail; no physical
 * memory total is passed off as a current app limit. */
int botw_base_v0_heap_live_available(size_t *out, char *error, size_t capacity);
/* Freeze exactly one plan and publish the same values to each actual pthread.
 * query reads ordinary immutable allocator metadata and a real live limit; it
 * must not acquire the SVC resize lock or reenter this module. No guest reads.
 * The module lock serializes plan selection and TSD publication. */
int botw_base_v0_heap_thread_init(pthread_key_t heap_key, pthread_key_t root_key,
    botw_base_v0_heap_query query, char *error, size_t capacity);
/* The existing SVC resize lock may call these. Admission rechecks the live
 * growth budget; a permission failure must never be recorded as accepted. */
int botw_base_v0_heap_admit(size_t requested, size_t previous, char *error, size_t capacity);
int botw_base_v0_heap_record(size_t accepted, char *error, size_t capacity);
int botw_base_v0_heap_initial_pending(void);
int botw_base_v0_heap_verify(size_t actual_heap, char *error, size_t capacity);

/* Normal upstream integration points, defined in thread.c / hle_svc.c. */
void botw_base_v0_heap_enable(void);
int dm_guest_heap_plan_inputs(botw_base_v0_heap_inputs *, char *, size_t);
size_t dm_guest_heap_current(void);
