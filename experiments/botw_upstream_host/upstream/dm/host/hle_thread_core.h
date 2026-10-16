// Logical CPU core bookkeeping behind the thread SVCs 0x08 CreateThread, 0x0E/0x0F Get/SetThreadCoreMask and 0x10 GetCurrentProcessorNumber.
// Host policy, not a firmware or NPDM extraction: the process may use logical cores 0-2 (the same mask GetInfo CoreMask reports in hle_svc.c)
// and its default core is 0. Every guest thread still runs on its own host pthread in parallel: this is only the logical core the guest sees.
// It is not a physical CPU affinity, and guest threads that share a logical core are not serialized against each other.
// Argument checks and results follow the public reference kernels (Ryubing Syscall.cs / KThread.cs, Atmosphere mesosphere kern_svc_thread.cpp).
// Pure functions without locking: the caller serializes access to a core_state_t.
#ifndef HLE_THREAD_CORE_H
#define HLE_THREAD_CORE_H
#include <stdint.h>

#define CORE_PROCESS_MASK    7ull      // Logical cores the process may use
#define CORE_PROCESS_DEFAULT 0         // Core behind the "process default" ideal core
#define CORE_COUNT           4         // Core ids the SVC ABI can name (0-3); core 3 exists but is outside the process mask
#define IDEAL_DONT_CARE      (-1)      // No preferred core
#define IDEAL_PROCESS        (-2)      // Use the process default core
#define IDEAL_NO_UPDATE      (-3)      // Keep the stored preferred core (SetThreadCoreMask only)
#define R_INVALID_CORE       0xE201u   // Kernel module 1, description 113
#define R_INVALID_COMBO      0xE801u   // 116

typedef struct core_state {
    int32_t pref;    // Preferred (ideal) core reported by GetThreadCoreMask: an allowed core, or IDEAL_DONT_CARE
    int32_t cur;     // Logical core reported by GetCurrentProcessorNumber: always a bit of mask, never negative
    uint64_t mask;   // Affinity mask: nonzero subset of CORE_PROCESS_MASK
} core_state_t;

static inline int core_valid_id(int32_t c) { return c >= 0 && c < CORE_COUNT; }
static inline int core_in_mask(uint64_t mask, int32_t c) { return core_valid_id(c) && (mask >> c & 1); }
static inline int32_t core_highest(uint64_t mask) { return 63 - __builtin_clzll(mask); }   // mask != 0

// CreateThread: IDEAL_PROCESS resolves to the default core; any other negative id or a core outside the process mask is R_INVALID_CORE.
// The new thread prefers, runs on and is confined to that single core
static inline uint32_t core_create(int32_t core, core_state_t *out) {
    if (core == IDEAL_PROCESS) core = CORE_PROCESS_DEFAULT;
    if (!core_in_mask(CORE_PROCESS_MASK, core)) return R_INVALID_CORE;
    out->pref = out->cur = core; out->mask = 1ull << core;
    return 0;
}

// SetThreadCoreMask argument checks, run before the handle lookup (Ryubing order). IDEAL_PROCESS resolves to the default core and replaces
// the supplied mask with that core's single bit, skipping the mask checks. Otherwise, in order: a bit outside the process mask -> R_INVALID_CORE;
// zero mask -> R_INVALID_COMBO; an id that is neither 0-3 nor IDEAL_DONT_CARE / IDEAL_NO_UPDATE -> R_INVALID_CORE; a core id missing from
// the mask -> R_INVALID_COMBO (so core 3 with mask 7 is a combination error, while mask bit 3 is a core error)
static inline uint32_t core_set_check(int32_t *pref, uint64_t *mask) {
    if (*pref == IDEAL_PROCESS) { *pref = CORE_PROCESS_DEFAULT; *mask = 1ull << CORE_PROCESS_DEFAULT; return 0; }
    if (*mask & ~CORE_PROCESS_MASK) return R_INVALID_CORE;
    if (!*mask) return R_INVALID_COMBO;
    if (core_valid_id(*pref)) return (*mask >> *pref & 1) ? 0 : R_INVALID_COMBO;
    return *pref == IDEAL_DONT_CARE || *pref == IDEAL_NO_UPDATE ? 0 : R_INVALID_CORE;
}

// Apply checked arguments to a thread. IDEAL_NO_UPDATE keeps the stored preference, which must be a core of the new mask.
// Host policy when the stored preference is IDEAL_DONT_CARE: IDEAL_NO_UPDATE is rejected with R_INVALID_COMBO. This matches the
// reference-kernel rejection without depending on a negative shift; it is not proof of Nintendo kernel behavior. Nothing changes on error.
// The current core is kept while the new mask still allows it; otherwise it moves to the preferred core, or without a preference to the
// highest allowed core (the same fallback as the reference kernels)
static inline uint32_t core_set_apply(core_state_t *s, int32_t pref, uint64_t mask) {
    if (pref == IDEAL_NO_UPDATE) {
        if (!core_in_mask(mask, s->pref)) return R_INVALID_COMBO;
        pref = s->pref;
    }
    s->pref = pref; s->mask = mask;
    if (!core_in_mask(mask, s->cur)) s->cur = pref >= 0 ? pref : core_highest(mask);
    return 0;
}
#endif
