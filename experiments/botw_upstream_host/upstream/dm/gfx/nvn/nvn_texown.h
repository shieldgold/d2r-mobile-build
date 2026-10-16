// CPU storage ownership for separately allocated Metal textures (nvn_texown.c).
// Pure C and thread-safe. Lock order: callers may hold the Metal serial-table lock; this module never calls out.
//
// On hardware a texture's texels are its pool bytes, so GPU writes land in memory every alias observes. The host
// gives ordinary and packaged textures their own Metal storage: GPU writes stay there and never
// reach the pool. A claim records that the newest content of [addr, addr + size) may live only in such storage, so
// the pool bytes there are not authoritative. Claims are byte exact and outlive the Metal storage: after release the
// GPU writes would still be in memory on hardware, but the host lost them, so a retired claim keeps the range
// non-authoritative. Nothing currently clears a claim. Storage without a CPU address (virtual pools) is not placed;
// the host does not map virtual pools onto physical storage, so it cannot alias claimed bytes.
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct { uint64_t serial; uintptr_t lo, hi; int retired; } nvn_texown_hit;   // First overlapping claim; retired claims keep one contributing serial

// Records live Metal storage of serial over [addr, addr + size). An identical live claim is not duplicated.
// Empty or overflowing ranges are ignored.
void nvn_texown_claim(uint64_t serial, const void *addr, uint64_t size);
// Claims only when no live or retired claim overlaps the range, atomically with the check. Returns 1 when claimed,
// 0 on overlap (fills *hit, changes nothing), -1 for an empty or overflowing range or serial 0.
int nvn_texown_claim_fresh(uint64_t serial, const void *addr, uint64_t size, nvn_texown_hit *hit);
// Returns 1 and fills *hit (optional) when a live or retired claim overlaps the range, 0 otherwise or for an empty range.
int nvn_texown_find(const void *addr, uint64_t size, nvn_texown_hit *hit);
// Like nvn_texown_find, but skips live claims of serial itself and scans every record, so a hit proves another
// owner. Retired claims are never skipped: a merged retired record cannot prove which serials it contains.
int nvn_texown_find_other(uint64_t serial, const void *addr, uint64_t size, nvn_texown_hit *hit);
// The serial's Metal storage was released: its claims become retired and merge with touching retired claims.
void nvn_texown_retire(uint64_t serial);
// Storage sharing (nvn_mtl.m sharing table): serial now uses the Metal storage claimed by owner and makes no claim of
// its own; nvn_texown_find_other treats owner's live claims as serial's. Unshare when serial drops the storage. The
// owner's claims retire only when the shared storage itself is released (nvn_texown_retire(owner)).
void nvn_texown_share(uint64_t serial, uint64_t owner);
void nvn_texown_unshare(uint64_t serial);
uint64_t nvn_texown_owner(uint64_t serial);   // owner of serial's storage (serial itself when not sharing)
size_t nvn_texown_count(int retired);   // Live (0) or retired (1) records, for tests and diagnostics
