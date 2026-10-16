/* Owned integer-only SDK contract probe: no backend, GPU or guest memory. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "public_vm_read.h"

static unsigned public_calls, mach_calls, total_public_calls, total_mach_calls, checks;
static uint64_t seen_task, seen_source, seen_bytes, seen_destination;
static uint64_t backend_copied;
static kern_return_t backend_result;
static int backend_writes_copied = 1;

static void observe(uint64_t task, uint64_t source, uint64_t bytes, uint64_t destination) {
    seen_task = task; seen_source = source; seen_bytes = bytes; seen_destination = destination;
}

kern_return_t vm_read_overwrite(vm_map_t task, vm_address_t source, vm_size_t bytes,
                                vm_address_t destination, vm_size_t *copied) {
    public_calls++; total_public_calls++;
    observe(task, source, bytes, destination);
    if (backend_writes_copied) *copied = (vm_size_t)backend_copied;
    return backend_result;
}

#if !TARGET_OS_IPHONE
kern_return_t mach_vm_read_overwrite(vm_map_t task, mach_vm_address_t source, mach_vm_size_t bytes,
                                     mach_vm_address_t destination, mach_vm_size_t *copied) {
    mach_calls++; total_mach_calls++;
    observe(task, source, bytes, destination);
    if (backend_writes_copied) *copied = backend_copied;
    return backend_result;
}
#endif

static void reset(void) {
    public_calls = mach_calls = 0;
    seen_task = seen_source = seen_bytes = seen_destination = UINT64_MAX;
    backend_result = KERN_SUCCESS; backend_copied = 0; backend_writes_copied = 1;
}

static void valid(uint64_t source, uint64_t bytes, uint64_t destination,
                  kern_return_t result, uint64_t actual, int write_result) {
    reset(); backend_result = result; backend_copied = actual; backend_writes_copied = write_result;
    struct { uint64_t before; mach_vm_size_t copied; uint64_t after; } guard = {
        UINT64_C(0x1122334455667788), UINT64_MAX, UINT64_C(0x8877665544332211)
    };
    kern_return_t r = botw_backend_vm_read_overwrite((vm_map_t)0x1234, source, bytes,
                                                    destination, &guard.copied);
    assert(r == result && guard.copied == (write_result ? actual : 0));
    assert(guard.before == UINT64_C(0x1122334455667788));
    assert(guard.after == UINT64_C(0x8877665544332211));
    assert(seen_task == 0x1234 && seen_source == source && seen_bytes == bytes &&
           seen_destination == destination);
#if TARGET_OS_IPHONE
    assert(public_calls == 1 && mach_calls == 0);
#else
    assert(public_calls == 0 && mach_calls == 1);
#endif
    /* Short/oversized results are preserved for the existing exact-read caller. */
    checks++;
}

static void invalid(uint64_t source, uint64_t bytes, uint64_t destination) {
    reset(); mach_vm_size_t copied = UINT64_MAX;
    kern_return_t r = botw_backend_vm_read_overwrite((vm_map_t)0x1234, source, bytes,
                                                    destination, &copied);
    assert(r == KERN_INVALID_ARGUMENT && copied == 0);
    assert(public_calls == 0 && mach_calls == 0);
    checks++;
}

int main(void) {
    const uint64_t maximum = VM_TEST_BITS == 32 ? UINT32_MAX : UINT64_MAX;
    reset();
    assert(botw_backend_vm_read_overwrite(0x1234, 1, 1, 2, NULL) == KERN_INVALID_ARGUMENT);
    assert(!public_calls && !mach_calls); checks++;
    valid(0x10203040, 32, 0x50607080, KERN_SUCCESS, 32, 1);
    valid(0x10203040, 32, 0x50607080, KERN_SUCCESS, 7, 1);
    valid(0x10203040, 32, 0x50607080, KERN_PROTECTION_FAILURE, 0, 1);
    valid(0x10203040, 32, 0x50607080, KERN_PROTECTION_FAILURE, 7, 1);
    valid(0x10203040, 32, 0x50607080, KERN_INVALID_ADDRESS, 0, 0);
    valid(maximum, 0, maximum, KERN_SUCCESS, 0, 1);
    valid(0, maximum, 0, KERN_SUCCESS, maximum, 1);
    /* Oversized output remains observable to the original exact-read caller. */
    valid(1, 8, 2, KERN_SUCCESS, 9, 1);
#if !TARGET_OS_IPHONE
    /* Public vm_* widths must not narrow the original macOS mach_vm_* path. */
    valid(UINT64_MAX - 7, 7, UINT64_MAX - 7, KERN_SUCCESS, 7, 1);
    valid(0, UINT64_MAX, 0, KERN_SUCCESS, UINT64_MAX, 1);
#endif
#if VM_TEST_BITS == 32 && TARGET_OS_IPHONE
    invalid((uint64_t)UINT32_MAX + 1, 0, 0);
    invalid(0, 0, (uint64_t)UINT32_MAX + 1);
    invalid(0, (uint64_t)UINT32_MAX + 1, 0);
    invalid(UINT32_MAX, 1, 0);
    invalid(0, 1, UINT32_MAX);
    invalid((uint64_t)UINT32_MAX - 7, 8, 0);
    invalid(0, 8, (uint64_t)UINT32_MAX - 7);
#endif
    invalid(UINT64_MAX, 1, 0);
    invalid(0, 1, UINT64_MAX);
    invalid(UINT64_MAX - 7, 8, 0);
    invalid(0, 8, UINT64_MAX - 7);
    printf("{\"vm_bits\":%d,\"iphone\":%d,\"checks\":%u,"
           "\"public_calls\":%u,\"mach_calls\":%u}\n",
           VM_TEST_BITS, TARGET_OS_IPHONE, checks, total_public_calls, total_mach_calls);
    return 0;
}
