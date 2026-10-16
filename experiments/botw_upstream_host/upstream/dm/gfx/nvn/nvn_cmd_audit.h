// Command-stream header/record validation and optional provenance audit (DM_NVN_CMD_AUDIT=1).
// The audit keeps fixed-size metadata only: addresses, headers, sizes, generations and checksums of
// host-recorded command streams. It never copies or logs command payloads. Its purpose is to tell,
// for a stream that fails to decode, whether the handle was malformed when created, re-recorded at
// the same address, overwritten after a guest AddCommandMemory re-donation, or overwritten by some
// other writer. Every verification states one honest outcome: unchanged, changed, unreadable, or
// incomplete (bounded coverage); a truncated recording is never reported as fully unchanged, and an
// address outside the bounded tables is never claimed to be outside every recording.
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define CMD_REC_PREFIX_MAX 32u   //Maximum prefix read by cmd_rec_defect; embedded pointers are never followed.
#define CMD_REC_MAX_SIZE (64u << 20)   //Largest record the host emits is bounded by one donated segment; anything larger is corrupt

// Header-only check (no payload access): generic header rules plus the per-op exact size for
// fixed-size records and the minimum size for count-derived records. Returns NULL for a well-formed
// header, else a static description of the defect.
const char *cmd_hdr_defect(uint32_t op, uint32_t size);

// Whole-record check used by the replay walkers: everything above plus count/length consistency for
// the variable-size records (BIND_VATTR/VSTREAM/VBUF/TEX/SAMP/IMG, UPDATE_UB), read from the payload
// of the record itself. The header check runs first, so the count/length fields lie inside the
// declared record and no walker can read a payload past a size-8 record. BIND_TEX_DEFERRED range
// fields are checked too, but its handle-array pointer is never dereferenced. After a valid header,
// only min(header.size, CMD_REC_PREFIX_MAX) readable bytes are required. Returns NULL when valid.
const char *cmd_rec_defect(const uint8_t *rec);

// Fault-safe entry check for a top-level or OP_CALL handle: reads the first 8 bytes through the
// audit's bounded safe-read primitive and requires an exact OP_BEGIN, size 8 record. On defect
// returns a static description; *op_out/*size_out receive the header read (zeros when unreadable) so
// the caller can log it. JUMP targets inside a stream are not entry points and are not affected.
const char *cmd_entry_defect(const uint8_t *handle, uint32_t *op_out, uint32_t *size_out);

// The same bounded safe-read primitive the audit uses (chunked mach_vm_read_overwrite): returns false
// for unmapped or protected ranges instead of faulting. Also used by the deferred-binding capture (nvn_dtex.h).
bool cmd_safe_read(const void *addr, size_t n, void *out);

int cmda_on(void);
void cmda_cb_init(const void *cb);
void cmda_cb_final(const void *cb);
// jump_at: where a linking JUMP is written into the previous segment (NULL when not recording).
// ra: guest return address of the NVN call (the thunks tail-branch, so the implementation's return address is the guest caller).
void cmda_add(const void *cb, const uint8_t *base, const uint8_t *end, int host, const uint8_t *jump_at, const void *ra);
void cmda_query(const void *cb, int which, size_t value, const void *ra);   //which: 0 used, 1 free, 2 size
void cmda_callback(const void *cb, const uint8_t *cur, const uint8_t *end, size_t need);
void cmda_begin(const void *cb, const uint8_t *p);
void cmda_end(const void *cb, const uint8_t *handle);
void cmda_call_rec(const void *cb, const uint8_t *rec, uint64_t handle);
void cmda_submit(int n, const uint64_t *h);
// Replay side (translation thread). call_rec is the OP_CALL record that referenced the handle, NULL for a top-level submit.
void cmda_exec(uint64_t handle, const uint8_t *call_rec, int depth);
// Called by the replay just before it stops on a malformed record; dumps recent metadata when the audit is enabled.
void cmda_bad(const uint8_t *p, uint32_t op, uint32_t size, const char *why, const uint8_t *const *ret, int ret_n);
