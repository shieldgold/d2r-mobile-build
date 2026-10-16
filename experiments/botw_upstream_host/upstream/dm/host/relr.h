// ELF64 RELR address / bitmap decoder, independent of game data; the caller owns target page permissions and writes.
#pragma once
#include <stdint.h>
#include <stddef.h>

static inline int dm_relr_walk(const uint64_t *entries, size_t count, size_t image_size,
                               int (*visit)(uint64_t, void *), void *ctx) {
    uint64_t next = 0;
    int have_address = 0;
    for (size_t i = 0; i < count; i++) {
        uint64_t e = entries[i];
        if (!(e & 1)) {
            if ((e & 7) || image_size < 8 || e > image_size - 8 || !visit(e, ctx)) return 0;
            next = e + 8;
            have_address = 1;
        } else {
            if (!have_address || next > UINT64_MAX - 63 * 8) return 0;
            for (unsigned bit = 1; bit < 64; bit++) if (e & (UINT64_C(1) << bit)) {
                uint64_t off = next + (bit - 1) * 8;
                if (image_size < 8 || off > image_size - 8 || !visit(off, ctx)) return 0;
            }
            next += 63 * 8;
        }
    }
    return 1;
}
