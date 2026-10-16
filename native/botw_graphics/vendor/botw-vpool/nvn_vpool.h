// NVN virtual memory pools as logical GPU address intervals (nvn_vpool.c)
//
// A virtual pool owns a PROT_NONE host reservation that serves only as a unique logical
// address token: its base is the pool's GPU address, and no host page is ever mapped into it.
// MapVirtual records intervals [voff, voff + len) -> (physical pool, physical offset). Readers
// resolve a logical range under a lease and read the *current* physical bytes, either through
// one contiguous physical CPU pointer or by a fresh gather-copy across spans. There are no
// persistent shadow copies and no OS aliases, so physical storage only needs 4 KiB alignment
// even though host pages are 16 KiB.
//
// Locking. One writer-preferring reader/writer lock guards the whole registry.
//   - Readers hold a lease (nvn_vpool_lease_acquire ... _release). Nested leases on one thread
//     are allowed and never deadlock against a waiting writer.
//   - Writers are register/finalize/map. They wait for every lease to be released.
//   - Lock order: g_rmu -> vpool lease (read) -> g_mu. Writers must not be entered while
//     holding g_mu, and never call back into the host while holding the write side.
//   - A writer entered while the same thread holds a lease returns NVN_VPOOL_E_LOCK_ORDER
//     without mutating anything.
//   - Nothing here is async-signal-safe. The page-fault / dirty-tracking signal path must never
//     call into this module.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "../../pool_policy.h"

#define NVN_VPOOL_PAGE BW_POOL_MAPPING_PAGE_BYTES        // Mapping granularity reported to the guest (device integer 0x3a)
#define NVN_VPOOL_PHYS_ALIGN BW_POOL_CPU_BACKING_ALIGNMENT   // Required alignment of physical pool CPU storage
#define NVN_VPOOL_VA_LIMIT BW_POOL_PREFERRED_TOKEN_LIMIT   // Preferred upper bound for logical GPU addresses

enum {
    NVN_VPOOL_OK = 0,
    NVN_VPOOL_NOT_VIRTUAL,     // Address is outside every virtual reservation (live or retired): use the physical path
    NVN_VPOOL_UNMAPPED,        // Inside a live virtual pool, but part of the range has no mapping
    NVN_VPOOL_STALE,           // Inside a finalized virtual pool's tombstone: never fall back to physical lookup
    NVN_VPOOL_INVALID,         // Malformed range or request (zero length, overflow, out of bounds, misaligned)
    NVN_VPOOL_E_UNKNOWN_POOL,  // Owner is not a live pool of the expected kind
    NVN_VPOOL_E_DUPLICATE,     // Owner already registered and live
    NVN_VPOOL_E_CLASS,         // Unsupported storage class
    NVN_VPOOL_E_OVERLAP,       // Physical storage overlaps a virtual reservation
    NVN_VPOOL_E_NOMEM,         // Host allocation or reservation failed; nothing was changed
    NVN_VPOOL_E_LOCK_ORDER,    // Writer entered while this thread holds a lease
};
const char *nvn_vpool_status_name(int status);

// Physical pools. mem must be 4 KiB aligned and stay valid until finalize.
int nvn_vpool_phys_register(const void *owner, void *mem, uint64_t size);
// Removes every span referring to the pool (they become holes, so reads fail as UNMAPPED
// instead of touching reused memory). *invalidated_spans (optional) reports how many.
int nvn_vpool_phys_finalize(const void *owner, uint64_t *invalidated_spans);

// Virtual pools. size must be a nonzero multiple of NVN_VPOOL_PAGE. *base receives the logical
// GPU address (64 KiB aligned). *above_va_limit (optional) is set when no reservation below
// NVN_VPOOL_VA_LIMIT could be found without overwriting existing mappings.
int nvn_vpool_virt_register(const void *owner, uint64_t size, uint64_t *base, int *above_va_limit);
// Host opt-in: require the full reservation below the preferred token bound. This is a
// host support policy, not a hardware GPU address-width claim. Failure publishes no token.
int nvn_vpool_virt_register_strict(const void *owner, uint64_t size, uint64_t *base);
// Drops all mappings and keeps the reservation as a PROT_NONE tombstone: its addresses then
// resolve as NVN_VPOOL_STALE and are never handed to another pool.
int nvn_vpool_virt_finalize(const void *owner);

// nvnMemoryPoolMapVirtual. The whole batch is validated before anything changes; on any error
// nothing is mutated. Requests apply in order, so a later overlapping request wins. Every
// request needs voff and size to be multiples of NVN_VPOOL_PAGE; zero-size requests are
// rejected as NVN_VPOOL_INVALID because their native meaning is unverified. Mapping requests
// also need poff to be a page multiple and storage class 0, 0x100, 0x1fe, 0x101 or 0x17b. Unmap requests
// (physicalPool == NULL) ignore physicalOffset and storageClass.
// count == 0 succeeds on a live virtual pool without reading reqs. err (optional) receives an
// English description of the first failing request.
int nvn_vpool_map(const void *virt_owner, const NVNmappingRequest *reqs, int count, char *err, size_t errlen);

// A held lease is noncopyable and must not be reacquired into until released. Its acquiring
// thread must remain alive and release it; no cancellation or cross-thread transfer is supported.
// Read lease over [addr, addr + len). Only NVN_VPOOL_OK leaves the lease held; any other
// status leaves nothing to release. While held, spans, physical pointers and physical owners
// stay valid and no mapping can change.
typedef struct {
    uint64_t addr, len;
    uint64_t gen;           // Highest mapping generation among covering spans (compare with a cache stamp)
    unsigned nspans;
    int contiguous;         // One physical pool, continuous physical offsets and one raw kind
    // Valid when contiguous:
    const void *phys_owner;
    uint8_t *cpu;           // Current physical CPU address of addr
    uint64_t phys_id;       // Unique id of the physical registration (owner pointers can be reused)
    uint32_t raw_kind;      // Valid when contiguous: 0 (pitch), 0xfe (color), 1 (D16) or 0x7b (D32F)
    // Private:
    const void *v_;
    unsigned first_;
    int held_;
} nvn_vpool_lease;

typedef struct {
    uint64_t off;           // Offset of this piece from lease addr
    uint64_t len;
    const void *phys_owner;
    uint64_t phys_id;
    uint8_t *cpu;           // Physical CPU address backing the piece's first byte
    uint64_t phys_off;      // Offset inside the physical pool
    uint64_t gen;           // Generation at which this mapping was established
    uint32_t raw_kind;      // Exact raw kind of this piece (0, 0xfe, 1 or 0x7b)
} nvn_vpool_span;

int nvn_vpool_lease_acquire(nvn_vpool_lease *l, uint64_t addr, uint64_t len);
int nvn_vpool_lease_span(const nvn_vpool_lease *l, unsigned i, nvn_vpool_span *out);
// Fresh copy of [off, off + n) of the leased range from the current physical bytes.
int nvn_vpool_lease_read(const nvn_vpool_lease *l, uint64_t off, void *dst, uint64_t n);
// Must be called on the acquiring thread.
void nvn_vpool_lease_release(nvn_vpool_lease *l);

// Lock-free read; advances on every mapping change anywhere.
uint64_t nvn_vpool_generation(void);
// Generation of one virtual pool (0 if the owner is not a live virtual pool). Takes the lease side.
uint64_t nvn_vpool_virt_gen(const void *virt_owner);

typedef struct {
    int phys_live, virt_live, tombstones;
    unsigned active_readers, waiting_readers, waiting_writers; // Diagnostic lock snapshot
    uint64_t spans, tombstone_bytes, reserved_bytes, above_va_limit;
} nvn_vpool_stats_t;
void nvn_vpool_stats(nvn_vpool_stats_t *out);

#ifdef NVN_VPOOL_TESTING
// Test build only. Set controls only while all test users are quiescent. A nonnegative
// countdown permits that many allocator/VM attempts, then fails all until reset to -1.
// The reservation floor uses real mmap hints (never MAP_FIXED), refusing lower results.
// Stats are intended for quiescent assertions; registry capacities use a read lease.
typedef struct {
    uint64_t allocation_calls, active_allocations;
    uint64_t reservation_calls, active_reservations, reserved_bytes;
    uint64_t physical_capacity, virtual_capacity;
} nvn_vpool_test_stats;
void nvn_vpool_test_fail_allocation_after(long attempts);
void nvn_vpool_test_fail_reservation_after(long attempts);
void nvn_vpool_test_reservation_floor(uint64_t floor);
void nvn_vpool_test_get_stats(nvn_vpool_test_stats *out);
#endif
