/* SPDX-License-Identifier: MIT
 * Pure planning function retained verbatim from native/botw_host/runtime.c;
 * source binding and exact disk thunk profile are in base_v0_heap.json. */
#include "base_v0_heap.h"
#include "whole_policy.h"
#include <stdint.h>
#include <stdio.h>
#if defined(__APPLE__)
#include <TargetConditionals.h>
#if TARGET_OS_IPHONE
#include <os/proc.h>
#endif
#endif
#define HEAP_POLICY_UNIT ((size_t)0x200000)
#define HEAP_POLICY_RESERVE ((size_t)0x10000000)
#define HEAP_POLICY_STARTUP ((size_t)0x02000000)
#define HEAP_POLICY_MIN_ROOT ((size_t)0x20000000)
#define HEAP_POLICY_ORIGINAL ((size_t)0xc2600000)
int botw_base_v0_heap_plan(size_t kernel_available, size_t live_available,
        size_t *heap, size_t *root, char *error, size_t capacity) {
    if (!heap || !root || heap == root || live_available < HEAP_POLICY_RESERVE) {
        if (error && capacity) snprintf(error, capacity, "bounded heap: invalid output or live host memory below reserve");
        return 1;
    }
    size_t value = live_available - HEAP_POLICY_RESERVE;
    if (value > kernel_available) value = kernel_available;
    if (value > HEAP_POLICY_ORIGINAL) value = HEAP_POLICY_ORIGINAL;
    value &= ~(HEAP_POLICY_UNIT - 1);
    if (value < HEAP_POLICY_STARTUP + HEAP_POLICY_MIN_ROOT) {
        if (error && capacity) snprintf(error, capacity, "bounded heap: cannot fit 512 MiB experimental root plus 32 MiB startup block");
        return 1;
    }
    *heap = value; *root = value - HEAP_POLICY_STARTUP; return 0;
}
/* Provider state contains normal host metadata only, never a guest object. */
static pthread_mutex_t plan_lock = PTHREAD_MUTEX_INITIALIZER;
static int selected, initial_accepted;
static pthread_key_t selected_heap_key, selected_root_key;
static botw_base_v0_heap_inputs initial_inputs;
static size_t heap_capacity, root_capacity;
static botw_base_v0_heap_query input_query;
static int fail(char *error, size_t capacity, const char *message) {
    if (error && capacity) snprintf(error, capacity, "%s", message);
    return -1;
}
int botw_base_v0_heap_live_available(size_t *out, char *error, size_t capacity) {
    if (!out) return fail(error, capacity, "base-v0 heap: missing live-memory output");
#if defined(__APPLE__) && TARGET_OS_IPHONE
    size_t available = os_proc_available_memory();
    if (!available) return fail(error, capacity, "base-v0 heap: current iOS app memory limit is unavailable or exhausted");
    *out = available; return 0;
#else
    return fail(error, capacity, "base-v0 heap: no supported live app-memory query on this platform");
#endif
}
int botw_base_v0_heap_thread_init(pthread_key_t heap_key, pthread_key_t root_key,
        botw_base_v0_heap_query query, char *error, size_t capacity) {
    if (error && capacity) error[0] = 0;
    if (!query || heap_key == root_key) return fail(error, capacity, "base-v0 heap: invalid provider or TSD key pair");
    pthread_mutex_lock(&plan_lock);
    size_t heap = heap_capacity, root = root_capacity;
    botw_base_v0_heap_inputs inputs = initial_inputs;
    int result = 0, first_commit = 0;
    if (selected && (heap_key != selected_heap_key || root_key != selected_root_key || query != input_query))
        result = fail(error, capacity, "base-v0 heap: immutable provider or TSD key pair changed");
    if (!result && !selected) {
        result = query(&inputs, error, capacity);
        if (!result) result = botw_base_v0_heap_plan(inputs.kernel_available, inputs.live_available,
            &heap, &root, error, capacity);
        if (result && error && capacity && !error[0]) fail(error, capacity, "base-v0 heap: actual memory inputs unavailable");
    }
    if (!result) {
        void *old_heap = pthread_getspecific(heap_key);
        if (pthread_setspecific(heap_key, (void *)(uintptr_t)heap))
            result = fail(error, capacity, "base-v0 heap: failed to publish heap capacity TSD");
        else if (pthread_setspecific(root_key, (void *)(uintptr_t)root)) {
            int rollback = pthread_setspecific(heap_key, old_heap);
            result = fail(error, capacity, rollback ? "base-v0 heap: root capacity TSD and heap rollback failed" :
                "base-v0 heap: failed to publish root capacity TSD; heap slot restored");
        }
    }
    if (!result && !selected) {
        initial_inputs = inputs; heap_capacity = heap; root_capacity = root;
        selected_heap_key = heap_key; selected_root_key = root_key; input_query = query; selected = 1;
        first_commit = 1;
    }
    if (error && capacity) error[capacity - 1] = 0;
    pthread_mutex_unlock(&plan_lock);
    if (first_commit) botw_whole_host_heap_capacities(heap, root);
    return result ? -1 : 0;
}
int botw_base_v0_heap_initial_pending(void) {
    pthread_mutex_lock(&plan_lock); int pending = !initial_accepted;
    pthread_mutex_unlock(&plan_lock); return pending;
}
int botw_base_v0_heap_admit(size_t requested, size_t previous, char *error, size_t capacity) {
    if (error && capacity) error[0] = 0;
    pthread_mutex_lock(&plan_lock);
    int result = 0;
    if (!selected || requested > heap_capacity || (requested & (HEAP_POLICY_UNIT - 1)) ||
            (!initial_accepted && requested != heap_capacity))
        result = fail(error, capacity, "base-v0 heap: sizing differs from the immutable selected capacity");
    if (!result && requested > previous) {
        botw_base_v0_heap_inputs fresh = {0};
        if (input_query(&fresh, error, capacity))
            result = fail(error, capacity, "base-v0 heap: fresh live-memory query failed");
        else if (fresh.image_bytes != initial_inputs.image_bytes || requested > fresh.kernel_available ||
                fresh.live_available < HEAP_POLICY_RESERVE || requested - previous > fresh.live_available - HEAP_POLICY_RESERVE)
            result = fail(error, capacity, "base-v0 heap: fresh host memory cannot retain the reserve for this growth");
    }
    if (error && capacity) error[capacity - 1] = 0;
    pthread_mutex_unlock(&plan_lock); return result;
}
int botw_base_v0_heap_record(size_t accepted, char *error, size_t capacity) {
    pthread_mutex_lock(&plan_lock); int result = 0;
    if (!selected || (!initial_accepted && accepted != heap_capacity))
        result = fail(error, capacity, "base-v0 heap: unexpected accepted initial sizing");
    else initial_accepted = 1;
    pthread_mutex_unlock(&plan_lock); return result;
}
int botw_base_v0_heap_verify(size_t actual_heap, char *error, size_t capacity) {
    pthread_mutex_lock(&plan_lock); int result = 0;
    if (!selected || !initial_accepted || actual_heap != heap_capacity ||
            root_capacity + HEAP_POLICY_STARTUP != heap_capacity)
        result = fail(error, capacity, "base-v0 heap: actual SDK heap and root capacity are inconsistent before nnMain");
    pthread_mutex_unlock(&plan_lock); return result;
}
