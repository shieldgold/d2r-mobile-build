/* SPDX-License-Identifier: MIT
 * One normal resource-copy syscall; callers retain their exact-read policy.
 * The public iPhoneOS SDK has vm_read_overwrite, not mach_vm.h. */
#pragma once
#include <TargetConditionals.h>
#include <mach/mach.h>
#if !TARGET_OS_IPHONE
#include <mach/mach_vm.h>
#endif

static inline kern_return_t botw_backend_vm_read_overwrite(
        vm_map_t task, mach_vm_address_t source, mach_vm_size_t bytes,
        mach_vm_address_t destination, mach_vm_size_t *copied) {
    if (!copied) return KERN_INVALID_ARGUMENT;
    *copied = 0;
    const mach_vm_address_t maximum = ~(mach_vm_address_t)0;
    if (bytes > maximum - source || bytes > maximum - destination)
        return KERN_INVALID_ARGUMENT;
#if TARGET_OS_IPHONE
    if ((mach_vm_address_t)(vm_address_t)source != source ||
        (mach_vm_address_t)(vm_address_t)destination != destination ||
        (mach_vm_size_t)(vm_size_t)bytes != bytes)
        return KERN_INVALID_ARGUMENT;
    const mach_vm_address_t public_maximum = (vm_address_t)~(vm_address_t)0;
    if (bytes > public_maximum - source || bytes > public_maximum - destination)
        return KERN_INVALID_ARGUMENT;
    vm_size_t actual = 0;
    kern_return_t result = vm_read_overwrite(task, (vm_address_t)source,
            (vm_size_t)bytes, (vm_address_t)destination, &actual);
    *copied = (mach_vm_size_t)actual;
    return result;
#else
    return mach_vm_read_overwrite(task, source, bytes, destination, copied);
#endif
}
