/* Protected self-task copies for the native host's independent snapshots.
 *
 * Callers own both buffers' lifetimes and keep at least one operand in an
 * independent host-owned buffer. Different VAs with overlapping physical
 * backing are outside this contract. Numeric VA overlap in one mapping is
 * supported in memmove order; this is not a general physical-alias snapshot.
 * Logical guest permissions and dirty-write preparation remain caller duties.
 *
 * Small Mach copies avoid the VM-entry copy path on the reviewed XNU versions.
 * 4095 is a conservative bridge limit below their private kernel-buffer
 * thresholds, not a public no-split guarantee. NoCopy admission must still
 * verify the resulting VM region. Concurrent remapping can cause a later
 * failure after partial progress; errors never fall back to raw CPU access.
 */
#ifndef BOTW_HOST_PROTECTED_COPY_H
#define BOTW_HOST_PROTECTED_COPY_H

#include <mach/mach.h>
#include <stddef.h>
#include <stdint.h>
#include <unistd.h>

static inline kern_return_t botw_protected_vm_span(const void *memory,
        size_t bytes, vm_prot_t protection) {
    if (!bytes) return KERN_SUCCESS;
    uintptr_t address = (uintptr_t)memory;
    if (!address || bytes > UINTPTR_MAX - address) return KERN_INVALID_ADDRESS;
    vm_address_t cursor = (vm_address_t)address;
    while (bytes) {
        natural_t depth = 0;
        for (unsigned submaps = 0;; submaps++) {
            vm_address_t region = cursor;
            vm_size_t size = 0;
            vm_region_submap_info_data_64_t info = {0};
            mach_msg_type_number_t count = VM_REGION_SUBMAP_INFO_COUNT_64;
            kern_return_t result = vm_region_recurse_64(mach_task_self(), &region,
                &size, &depth, (vm_region_recurse_info_t)&info, &count);
            if (result != KERN_SUCCESS) return result;
            if (count < VM_REGION_SUBMAP_INFO_COUNT_64) return KERN_FAILURE;
            if (region > cursor || !size || cursor - region >= size)
                return KERN_INVALID_ADDRESS;
            if (info.is_submap) {
                if (submaps >= 64 || depth == UINT32_MAX) return KERN_FAILURE;
                depth++;
                continue; /* Query the same address inside the submap. */
            }
            if ((info.protection & protection) != protection)
                return KERN_PROTECTION_FAILURE;
            vm_size_t available = size - (cursor - region);
            size_t step = available < bytes ? (size_t)available : bytes;
            cursor += step;
            bytes -= step;
            break;
        }
    }
    return KERN_SUCCESS;
}

static inline kern_return_t botw_protected_vm_copy_chunks(void *destination,
        const void *source, size_t bytes, int read_operation) {
    if (!bytes) return KERN_SUCCESS;
    kern_return_t result = botw_protected_vm_span(source, bytes, VM_PROT_READ);
    if (result != KERN_SUCCESS) return result;
    result = botw_protected_vm_span(destination, bytes, VM_PROT_WRITE);
    if (result != KERN_SUCCESS) return result;
    int host_page = getpagesize();
    if (host_page <= 0 || ((unsigned)host_page & ((unsigned)host_page - 1)))
        return KERN_FAILURE;
    size_t page = (size_t)host_page;
    uintptr_t src = (uintptr_t)source, dst = (uintptr_t)destination;
    int backward = dst > src && dst - src < bytes;
    size_t remaining = bytes;
    while (remaining) {
        size_t step = remaining < 4095 ? remaining : 4095;
        uintptr_t s, d;
        if (backward) {
            /* The byte before an aligned end belongs to the preceding page. */
            size_t src_room = ((src + remaining - 1) & (page - 1)) + 1;
            size_t dst_room = ((dst + remaining - 1) & (page - 1)) + 1;
            if (step > src_room) step = src_room;
            if (step > dst_room) step = dst_room;
            s = src + remaining - step;
            d = dst + remaining - step;
        } else {
            size_t offset = bytes - remaining;
            s = src + offset;
            d = dst + offset;
            size_t src_room = page - (s & (page - 1));
            size_t dst_room = page - (d & (page - 1));
            if (step > src_room) step = src_room;
            if (step > dst_room) step = dst_room;
        }
        if (read_operation) {
            vm_size_t copied = 0;
            result = vm_read_overwrite(mach_task_self(), (vm_address_t)s,
                (vm_size_t)step, (vm_address_t)d, &copied);
            if (result == KERN_SUCCESS && copied != step) return KERN_FAILURE;
        } else {
            result = vm_write(mach_task_self(), (vm_address_t)d,
                (vm_offset_t)s, (mach_msg_type_number_t)step);
        }
        if (result != KERN_SUCCESS) return result;
        remaining -= step;
    }
    return KERN_SUCCESS;
}

static inline kern_return_t botw_protected_vm_read(void *destination,
        const void *source, size_t bytes) {
    return botw_protected_vm_copy_chunks(destination, source, bytes, 1);
}

static inline kern_return_t botw_protected_vm_write(void *destination,
        const void *source, size_t bytes) {
    return botw_protected_vm_copy_chunks(destination, source, bytes, 0);
}

#endif
