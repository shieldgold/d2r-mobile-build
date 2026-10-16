// Submit-time snapshot of deferred texture bindings (nvnCommandBufferBindTexturesDeferred, OP_BIND_TEX_DEFERRED).
//
// Native contract (BotW 1.9.0): the setter, EndRecording, CallCommands and CopyCommands keep only the caller's
// handle-array pointer; QueueSubmitCommands reads exactly count raw 64-bit handles synchronously before it
// returns; a zero count never reads the pointer. Valid stages are 0..5 and first + count fits the native
// 32-slot combined binding capacity.
//
// The host mirrors this with one immutable overlay per submit, keyed by the original deferred record address.
// dtex_capture walks the submitted command graph on the submitting thread (sequential records, JUMP, CALL,
// END, the exact-BEGIN entry rule of replay) with fault-safe reads, validates every reachable deferred record
// and copies its raw handles. Command bytes, record addresses and command-memory lifetimes are unchanged; no
// texture or sampler object is resolved here, so replay looks the numeric handles up at its normal time.
//
// The translation side activates the overlay on its own thread before the first lookahead of the submit and
// deactivates it after the submit is committed; dtex_resolve then returns a p_bind_handles-shaped immutable
// view of the captured values. Resolved pointers must stay local to the consumer; state copies the values.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "nvn_obj.h"

#define DTEX_MAX_DEPTH 16              //Host call-nesting limit, identical to replay (mtl_exec)
#define DTEX_REC_BUDGET (1u << 22)     //Records walked per submit, same order as the command audit's walk limit
#define DTEX_LIST_BUDGET (1u << 16)    //Distinct lists (top-level and CALL targets) per submit
#define DTEX_SLOTS 32                  //Native combined texture binding capacity (device query 9)

enum {
    DTEX_OK = 0,
    DTEX_E_ARGS,            //Negative handle count or NULL handle array
    DTEX_E_NOMEM,           //Host allocation failed
    DTEX_E_BUDGET,          //Record budget exhausted
    DTEX_E_LISTS,           //List budget exhausted
    DTEX_E_DEPTH,           //Call nesting beyond DTEX_MAX_DEPTH
    DTEX_E_CALL_CYCLE,      //A CALL reaches a list that is still being walked
    DTEX_E_JUMP_CYCLE,      //A list revisits one of its own JUMP targets
    DTEX_E_UNREADABLE,      //A record header or structural pointer cannot be read
    DTEX_E_MALFORMED,       //Record header defect (see detail)
    DTEX_E_ENTRY,           //Top-level or CALL target does not start with an exact BEGIN, size 8 record
    DTEX_E_NULL_TARGET,     //CALL or JUMP target is NULL
    DTEX_E_ADDRESS,         //Address arithmetic would overflow or leave the user address range
    DTEX_E_RANGE,           //Deferred binding stage / first / count outside the native range
    DTEX_E_NULL_ARRAY,      //Nonzero count with a NULL handle array
    DTEX_E_ARRAY,           //Nonzero-count handle array unreadable
    DTEX_E_COUNT_
};

typedef struct dtex_overlay dtex_overlay;

typedef struct {
    int code;               //DTEX_OK or DTEX_E_*
    const char *what;       //Static English description of code
    const char *detail;     //Static record-defect text for DTEX_E_MALFORMED, otherwise NULL
    const void *at;         //Record, handle or array address where the capture stopped
    uint32_t op, size;      //Header read at `at` (zeros when unreadable or not applicable)
    int depth;              //Call depth at the stop (0 = top-level list)
    int32_t stage, first, count;   //Deferred fields for DTEX_E_RANGE / NULL_ARRAY / ARRAY
    uint64_t records, lists, entries, array_reads, array_bytes;   //Walk statistics (also on success)
} dtex_info;

const char *dtex_code_text(int code);

// Called by the deferred setter before it records anything. Until the first call no deferred record can exist,
// so dtex_capture returns an empty result without walking (pre-existing submits keep their exact cost).
void dtex_note_recorded(void);

// Captures every deferred binding reachable from the n submitted handles. Returns DTEX_OK and stores the
// overlay in *out (NULL when nothing deferred is reachable), or an error code with *out NULL and no partial
// allocation left behind. info (optional) receives the diagnostic and statistics.
int dtex_capture(const uint64_t *h, int n, dtex_overlay **out, dtex_info *info);
void dtex_free(dtex_overlay *ov);

// Raw view of one captured record in a given overlay (tests and diagnostics); NULL when absent.
const p_bind_handles *dtex_lookup(const dtex_overlay *ov, const uint8_t *rec);
size_t dtex_entries(const dtex_overlay *ov);

// Thread-scoped activation. Activating while another overlay (or NULL activation) is active on the thread,
// or deactivating anything but the active one, stops the process with an English diagnostic.
void dtex_activate(const dtex_overlay *ov);
void dtex_deactivate(const dtex_overlay *ov);
int dtex_active(void);

// View for a deferred record on the current thread; NULL with *why set when no overlay is active or the record
// is missing from it (callers fail closed).
const p_bind_handles *dtex_resolve(const uint8_t *rec, const char **why);
