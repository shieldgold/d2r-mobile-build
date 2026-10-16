
// Write-protect dirty-page tracking for guest memory (nvn_dirty.c)
#pragma once
#include <stddef.h>
#include <stdint.h>

void nvn_dirty_add_range(const void *mem, size_t size);           //Register a memory pool as a tracked range (the first call registers the page-fault hook and the host pre-write hook)
void nvn_dirty_stat(int *live, int *peak, int *cap);                //number of registered memory pools, peak, tracking table capacity (for the memory readout line)
// Caller must stop new users of the backing storage before removal/free. Removal
// drains dirty API/signal readers and makes the pages it alone owns writable;
// host pages shared with a surviving registration keep their canonical state and
// protection. It does not pin a whole upload, guest write, syscall, or
// asynchronous GPU operation.
void nvn_dirty_remove_range(const void *mem);
// Full-span queries below use any live, non-retiring registration that covers the
// whole span; a shorter overlapping registration never masks a covering one.
// Registrations sharing a rounded host page share one canonical page state and
// write generation, so any owner observes a write made through any address.
int nvn_dirty_tracked(const void *addr, size_t len);
// Flush-certificate eligibility: a live, non-retiring registration covers the
// whole span. Overlap no longer disqualifies, because overlapping owners share
// canonical page records and retiring one owner keeps shared pages protected for
// the others. Currently equivalent to nvn_dirty_tracked. Empty/untracked/retiring
// or straddling spans return false. This neither pins storage lifetime nor proves
// protection; callers must recheck when publishing and verifying certificates
// under their lifecycle protocol.
int nvn_dirty_unambiguous(const void *addr, size_t len);
void nvn_dirty_protect(const void *addr, size_t len);              //Set the covered pages read-only
// Take the upload stamp BEFORE protection. A false result (including BUSY pages)
// requires a non-cacheable stamp of zero; it advances the epoch for outer caches.
int nvn_dirty_protect_checked(const void *addr, size_t len);
uint64_t nvn_dirty_now(void);                                      //Current generation: taken before protection and upload
// Untracked/BUSY/newer pages or stamp zero -> 1. Also 1 when every covering
// registration is newer than the stamp (re-registered storage).
int nvn_dirty_since(const void *addr, size_t len, uint64_t stamp);
void nvn_dirty_stats(uint64_t *faults, uint64_t *preps);           //Cumulative: number of page faults triggered by game writes, number of pages unprotected before host writes
void nvn_dirty_mp_stats(uint64_t *calls, uint64_t *pages, uint64_t *ns);   //Cumulative: this module's mprotect call count, pages covered, time spent
void nvn_dirty_slot_stats(int *live, int *used_slots, uint64_t *adds_total); //Currently live slots, slot table high-water mark, total number of registrations
