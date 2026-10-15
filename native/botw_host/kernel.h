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
    BOTW_KERNEL_RESULT_INVALID_STATE = 0xfa01,
    BOTW_KERNEL_RESULT_INVALID_PRIORITY = 0xe001,
    BOTW_KERNEL_RESULT_INVALID_CORE = 0xe201,
    BOTW_KERNEL_RESULT_INVALID_COMBINATION = 0xe801
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
    /* stack_top is the requested SDK top. The host pthread uses only whole
     * pages below it, inside the pinned MapMemory range, preserving SDK tail
     * headers. No bytes outside that owned mapping are borrowed. */
    int (*execute_thread)(void *opaque, uint64_t entry, uint64_t argument,
                          uint64_t stack_top, uint32_t handle,
                          char *error, size_t error_capacity);
    /* Required by configure_logical_memory only. Fresh host dirty-memory
     * admission before configuration (0,0) and every heap growth. Return 0 to
     * admit or -1 with a diagnostic. Called under the kernel lock: do not
     * reenter the kernel or access guest memory. Admission is not a promise
     * that all writable virtual pages can simultaneously reside in RAM. */
    int (*heap_admission)(void *opaque, size_t previous_size, size_t requested_size,
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
/* GetInfo(8): current native process debugger attachment. Handle must be 0
 * (a process pseudo-handle is invalid), then subtype must be 0. Darwin reads
 * public sysctl KERN_PROC_PID/P_TRACED on each query; it reflects the host
 * debugger observing this same process's native guest threads, not permission
 * to debug and not a separate Horizon KDebug session. A failed/short/wrong-PID
 * query is ERROR with registers untouched. Other hosts return UNSUPPORTED for
 * valid queries until a matching backend exists; invalid arguments are still
 * validated. The implemented process-info group (0..7,12..15,21,22) requires
 * subtype=0 before process-handle lookup; invalid subtype is 0xe801. */
/* SetThreadPriority(0x0d) changes base priority; GetThreadPriority(0x0c) reads
 * effective priority including mutex donation. Apple self updates apply and
 * read back a coarse QoS class. A remote live thread requiring a different
 * class is explicitly unsupported; no registers or priority state change. */
/* 0x0e returns ideal core in regs[1], affinity mask in regs[2], with handle in
 * regs[2] on input. 0x0f takes (handle,ideal,mask): -1 means don't care, -2
 * selects process default core 0 and its single-bit mask, -3 keeps the current
 * ideal if it remains allowed. 0x10 returns this thread's current virtual core.
 * Virtual IDs are process guest capabilities, not Apple physical CPU IDs.
 * Host scheduling/QoS remains in force; ARM's unsupported affinity API is not
 * called and physical core binding is never claimed. */
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
/* 0x15 borrows whole host pages of unborrowed RW heap. Owner permission is
 * None=0, Read=1 or ReadWrite=3. 0x51 maps the complete object once in the alias
 * region, always RW; its permission argument must equal the original owner
 * permission. 0x52 removes that mapping. Handles and guest mappings each pin
 * the source; only their final release restores owner RW access. Darwin uses
 * public vm_remap(copy=FALSE), without copying or charging the bytes twice.
 * Other hosts without an equivalent alias mechanism return UNSUPPORTED. */
typedef struct {
    void *address;
    size_t size;
    uint64_t source;
    unsigned owner_permission;
} botw_kernel_transfer_view;
/* Host services obtain actual RW shared bytes. Keep an independently duplicated
 * handle alive for the entire access, and close it only after all access ends.
 * This query itself creates no reference. Never expose address as guest memory. */
int botw_kernel_transfer_memory_view(botw_kernel *kernel, uint32_t handle,
    botw_kernel_transfer_view *view, char *error, size_t error_capacity);
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
/* Default allows 0..63. Configure the verified process/NPDM priority mask
 * before creating workers; it must include the initial thread's priority.
 * GetInfo(1) returns this mask; CreateThread/SetThreadPriority enforce it. */
int botw_kernel_configure_thread_priority_mask(botw_kernel *kernel, uint64_t mask,
    char *error, size_t error_capacity);
/* Default allows four guest virtual cores (mask=15). Configure the verified
 * process/NPDM capability before creating any worker. Only bits 0..3 are valid
 * and core 0 (the initial/default guest core) must remain allowed. GetInfo(0)
 * reports this same mask without allocating memory; CreateThread enforces it.
 * This is guest capability policy, not affinity to host physical CPU cores. */
int botw_kernel_configure_core_mask(botw_kernel *kernel, uint64_t mask,
    char *error, size_t error_capacity);
/* Configure reservation before first memory SVC. All capacities must be
 * multiples of 2 MiB. physical_budget includes heap and image_bytes. */
int botw_kernel_configure_memory(botw_kernel *kernel, size_t heap_capacity,
    size_t stack_capacity, size_t alias_capacity, size_t physical_budget,
    size_t image_bytes, char *error, size_t error_capacity);
/* Explicit sparse logical application pool, separate from physical_budget.
 * Requires a live heap_admission callback; does not allocate/touch heap pages.
 * GetInfo(6/21)=logical_pool, (7/22)=image+successful_heap, (5)=heap_capacity.
 * Every growth still requires real VM success before publishing any charge.
 * Only valid before the first memory reservation, like configure_memory. */
int botw_kernel_configure_logical_memory(botw_kernel *kernel, size_t heap_capacity,
    size_t stack_capacity, size_t alias_capacity, size_t logical_pool,
    size_t image_bytes, char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
#endif
