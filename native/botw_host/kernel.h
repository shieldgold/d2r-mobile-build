/* Horizon kernel host backend. Thread/TSD integration design derived from
 * jhaizhou-ops/d2r-mobile (MIT), commit e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8.
 * See THIRD_PARTY_LICENSE.txt. No game-specific addresses or successful stubs. */
#ifndef BOTW_HOST_KERNEL_H
#define BOTW_HOST_KERNEL_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct botw_kernel botw_kernel;
enum {
    BOTW_KERNEL_ERROR = -1, BOTW_KERNEL_UNSUPPORTED = 0, BOTW_KERNEL_HANDLED = 1,
    BOTW_KERNEL_RESULT_INVALID_SIZE = 0xca01,
    BOTW_KERNEL_RESULT_INVALID_ADDRESS = 0xcc01,
    BOTW_KERNEL_RESULT_OUT_OF_MEMORY = 0xd001,
    BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE = 0xd401,
    BOTW_KERNEL_RESULT_OUT_OF_RANGE = 0xee01,
    BOTW_KERNEL_RESULT_INVALID_HANDLE = 0xe401,
    BOTW_KERNEL_RESULT_TIMED_OUT = 0xea01,
    BOTW_KERNEL_RESULT_CANCELLED = 0xec01,
    BOTW_KERNEL_RESULT_INVALID_STATE = 0xfa01
};
#define BOTW_KERNEL_CURRENT_THREAD UINT32_C(0xffff8000)
#define BOTW_KERNEL_CURRENT_PROCESS UINT32_C(0xffff8001)
#define BOTW_KERNEL_INITIAL_THREAD UINT32_C(0x1001)
#define BOTW_KERNEL_MUTEX_HAS_WAITERS UINT32_C(0x40000000)
typedef struct {
    void (*log)(void *opaque, const char *message);
    /* Validate external signed-module/TLS/TLR/initial-stack ranges only. Do not
     * recurse into this kernel; owned ranges are checked internally first. */
    int (*memory)(void *opaque, uint64_t address, size_t length, int writable);
    int (*executable)(void *opaque, uint64_t address);
    /* Runs on a real new pthread using the mapped guest stack. Initialize this
     * thread's TSD/TLS/TLR, then enter guest code with x0=argument, x1=handle.
     * The callback must install pthread cleanup for its context allocations.
     * Guest code exits through SVC ExitThread. Returning is a host error. */
    int (*execute_thread)(void *opaque, uint64_t entry, uint64_t argument,
                          uint64_t stack_top, uint32_t handle,
                          char *error, size_t error_capacity);
} botw_kernel_callbacks;
botw_kernel *botw_kernel_create(const botw_kernel_callbacks *callbacks, void *opaque);
/* Cooperative stop wakes backend waits/sleeps. Returns 0 only after all worker
 * callbacks exited and their pthreads were joined. On -1, retain the kernel,
 * guest code, TLS and mappings and retry after the callback stops. No cancel. */
int botw_kernel_shutdown(botw_kernel *kernel, char *error, size_t error_capacity);
/* Guest execution must be stopped first. Refuses to free on shutdown failure. */
void botw_kernel_destroy(botw_kernel *kernel);
/* HANDLED returns an actual Horizon result in regs[0], even for invalid guest
 * arguments. UNSUPPORTED leaves registers unchanged. ERROR is a host failure
 * and the runtime must stop guest execution and show error. */
int botw_kernel_dispatch(botw_kernel *kernel, unsigned number, uint64_t *regs,
                         char *error, size_t error_capacity);
/* Synchronization ABI: 0x1a(owner,mutex,self_tag), 0x1b(mutex),
 * 0x1c(mutex,condition_key,self_tag,timeout_ns), 0x1d(condition_key,count).
 * Mutex tags are real thread handles plus MUTEX_HAS_WAITERS; condition keys
 * hold 1 while queued. Successful condition signal reacquires the mutex before
 * return; timeout returns with it released, and the SDK must lock it again.
 * 0x19 CancelSynchronization is pending until a cancellable 0x18 wait; it does
 * not interrupt mutex/condition waits. Cooperative shutdown wakes all waits. */
/* Handles start at 0x20000, independently of IPC's 0x10000 range. Each event
 * endpoint owns one reference; duplicates own independent references. A write
 * endpoint signals, a read endpoint waits, either endpoint may ClearEvent. */
int botw_kernel_create_event(botw_kernel *kernel, int initial_signaled,
    uint32_t *write_handle, uint32_t *read_handle, char *error, size_t error_capacity);
int botw_kernel_duplicate_handle(botw_kernel *kernel, uint32_t source,
    uint32_t *duplicate, char *error, size_t error_capacity);
int botw_kernel_signal_event(botw_kernel *kernel, uint32_t handle, char *error, size_t error_capacity);
int botw_kernel_clear_event(botw_kernel *kernel, uint32_t handle, char *error, size_t error_capacity);
int botw_kernel_close_handle(botw_kernel *kernel, uint32_t handle, char *error, size_t error_capacity);
int botw_kernel_is_process_handle(botw_kernel *kernel, uint32_t handle);
/* Real shared backing: host always has RW access, guest mappings are limited
 * to remote_permission (Read=1 or ReadWrite=3). size must cover whole host pages.
 * The returned pointer is owned by the shared-memory object until its final
 * handle and guest mapping close. SVC 0x13/0x14 map/unmap in the alias region. */
int botw_kernel_create_shared_memory(botw_kernel *kernel, size_t size,
    unsigned remote_permission, uint32_t *handle, void **backing,
    char *error, size_t error_capacity);
/* Contains only memory actually allocated by this backend. The caller should
 * combine this with its signed-module, TLR/TLS and initial-stack validation. */
int botw_kernel_memory(botw_kernel *kernel, uint64_t address, size_t length, int writable);
typedef struct {
    uint64_t base, size;
    uint32_t type, attribute, permission, ipc_count, device_count, padding;
} botw_kernel_memory_info;
/* 1=owned region described, 0=outside kernel-owned reservation, -1=bad args. */
int botw_kernel_query_memory(botw_kernel *kernel, uint64_t address,
                             botw_kernel_memory_info *info);
/* Read an existing reservation without allocating it. Returns 1 if present. */
int botw_kernel_reservation(botw_kernel *kernel, uint64_t *base, uint64_t *size);
/* Default is 36 bits. A target-adapted SDK may explicitly select 39 bits;
 * this does not rewrite guest pointer encodings. Configure before reservation.
 * Only 36/39 are supported. Existing capacities must fit the selected limit. */
int botw_kernel_configure_address_space(botw_kernel *kernel, unsigned bits,
    char *error, size_t error_capacity);
/* Exclusive guest address bound. Does not allocate a reservation; null => 0. */
uint64_t botw_kernel_address_space_limit(botw_kernel *kernel);
/* Configure reservation before first memory SVC. All capacities must be
 * multiples of 2 MiB. physical_budget includes heap and image_bytes. */
int botw_kernel_configure_memory(botw_kernel *kernel, size_t heap_capacity,
    size_t stack_capacity, size_t alias_capacity, size_t physical_budget,
    size_t image_bytes, char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
#endif
