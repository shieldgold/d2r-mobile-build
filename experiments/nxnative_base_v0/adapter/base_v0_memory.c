/* SPDX-License-Identifier: MIT
 * Thin normal-memory binding to the complete existing retained-AS and
 * original-logical heap providers. No VM enumeration or Guest reads. */
#include "base_v0_memory.h"
#include "base_v0_heap.h"
#include "whole_address_space.h"
#include <stdint.h>
#include <sys/mman.h>
#include <pthread.h>
#include <unistd.h>
#include <stdlib.h>

static nxnative_base_v0_memory retained;
static pthread_mutex_t initialize_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_key_t private_heap_key, private_root_key;
static int initialized, attempted;

int nxnative_base_v0_memory_layout(void *base, size_t size, size_t image,
                                  nxnative_base_v0_memory *out) {
    const size_t prefix = NXNATIVE_BASE_V0_MEMORY_PREFIX;
    const size_t alias = NXNATIVE_BASE_V0_MEMORY_ALIAS;
    const size_t stack = NXNATIVE_BASE_V0_MEMORY_STACK;
    const size_t offset = prefix + alias + stack;
    uintptr_t address = (uintptr_t)base;
    if (!out || !base || size != NXNATIVE_BASE_V0_MEMORY_RESERVE ||
            size > UINTPTR_MAX - address || offset > size ||
            size - offset < NXNATIVE_BASE_V0_MEMORY_HEAP ||
            image & 0x1fffffu || image > SIZE_MAX - NXNATIVE_BASE_V0_MEMORY_HEAP)
        return -1;
    *out = (nxnative_base_v0_memory){.base=base, .alias=(uint8_t*)base+prefix,
        .stack=(uint8_t*)base+prefix+alias, .heap=(uint8_t*)base+offset,
        .size=size, .alias_size=alias, .stack_size=stack, .heap_size=size-offset,
        .image_size=image, .pool=image+NXNATIVE_BASE_V0_MEMORY_HEAP};
    return 0;
}
static int actual_inputs(botw_base_v0_heap_inputs *out, char *error, size_t capacity) {
    size_t live;
    if (!out || !retained.base || retained.heap_size<NXNATIVE_BASE_V0_MEMORY_HEAP ||
            botw_base_v0_heap_live_available(&live, error, capacity)) return -1;
    *out = (botw_base_v0_heap_inputs){NXNATIVE_BASE_V0_MEMORY_HEAP, live, retained.image_size};
    return 0;
}
int nxnative_base_v0_memory_reserve(size_t image, nxnative_base_v0_memory *out) {
    if (!out) return -1;
    pthread_mutex_lock(&initialize_lock);
    int result = -1;
    if (initialized) {
        if (image == retained.image_size) { *out=retained; result=0; }
        goto done;
    }
    if (attempted) goto done;
    attempted=1;
    botw_whole_address_space chosen;
    int error_number=0;
    /* physical_ios=0 selects exactly one real 5-GiB reservation in the
     * complete provider. Its smaller iOS fallback sequence is not selected. */
    if (botw_whole_address_space_reserve(&chosen, 0, &error_number)) goto done;
    nxnative_base_v0_memory candidate;
    if (nxnative_base_v0_memory_layout(chosen.base, chosen.size, image, &candidate)) {
        munmap(chosen.base, chosen.size); goto done;
    }
    retained=candidate;
    char error[256];
    int have_heap_key=0, have_root_key=0;
    if (botw_base_v0_heap_select_original_logical(error, sizeof error)) goto rejected;
    if (pthread_key_create(&private_heap_key, NULL)) goto rejected;
    have_heap_key=1;
    if (pthread_key_create(&private_root_key, NULL)) goto rejected;
    have_root_key=1;
    if (botw_base_v0_heap_thread_init(private_heap_key, private_root_key,
            actual_inputs, error, sizeof error)) goto rejected;
    if ((uintptr_t)pthread_getspecific(private_heap_key)!=NXNATIVE_BASE_V0_MEMORY_HEAP ||
            (uintptr_t)pthread_getspecific(private_root_key)!=NXNATIVE_BASE_V0_MEMORY_ROOT)
        goto rejected;
    initialized=1; *out=retained; result=0; goto done;
rejected:
    if (have_root_key) pthread_key_delete(private_root_key);
    if (have_heap_key) pthread_key_delete(private_heap_key);
    munmap(chosen.base, chosen.size);
    retained=(nxnative_base_v0_memory){0};
done:
    pthread_mutex_unlock(&initialize_lock); return result;
}
int nxnative_base_v0_memory_admit(size_t requested, size_t previous) {
    char error[256];
    return botw_base_v0_heap_admit(requested, previous, error, sizeof error);
}
int nxnative_base_v0_memory_record(size_t accepted) {
    char error[256];
    return botw_base_v0_heap_record(accepted, error, sizeof error);
}
int nxnative_base_v0_memory_verify(size_t accepted) {
    char error[256];
    return botw_base_v0_heap_verify(accepted, error, sizeof error);
}
int nxnative_base_v0_memory_initial_pending(void) {
    return botw_base_v0_heap_initial_pending();
}
int nxnative_base_v0_memory_guard(size_t physical_growth) {
    char error[256]; size_t heap,root;
    botw_base_v0_heap_inputs inputs;
    if(actual_inputs(&inputs,error,sizeof error)) return -1;
    if(botw_base_v0_heap_plan_original_logical(inputs.kernel_available,
            inputs.live_available,&heap,&root,error,sizeof error)) return -1;
    return physical_growth>inputs.live_available-((size_t)256<<20)?-1:0;
}
_Noreturn void nxnative_base_v0_memory_failure(void) {
    static const char message[]="[nxnative normal] memory admission failed\n";
    (void)write(STDERR_FILENO,message,sizeof message-1);
    _Exit(70);
}
