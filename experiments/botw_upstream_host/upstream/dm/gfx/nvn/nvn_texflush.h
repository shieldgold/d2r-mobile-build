// CPU texel flushes (nvnTextureFlushTexels), see nvn_texflush.c.
// Pure C: native layer selection and conservative host coherence certificates. Pool and Metal
// policy belong to the caller; concurrent operations on one texture/storage require caller serialization.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "nvn_tex.h"

enum { NVN_TEXFLUSH_OK = 0, NVN_TEXFLUSH_EMPTY = 1, NVN_TEXFLUSH_INVALID = -1 };
enum { NVN_TEXFLUSH_CLEAN = 0, NVN_TEXFLUSH_CHANGED = 1, NVN_TEXFLUSH_UNTRACKED = 2 };

// Selected whole-layer allocations of a flush. has_layers / min_layer / nlayers come from the view
// (unset = all layers); a layer count of 0 means the remaining layers. A 3D texture always selects
// its single allocation and ignores the view. Returns OK, EMPTY or INVALID with a static reason.
int nvn_texflush_layers(const nvn_texdesc *d, int has_layers, int min_layer, int nlayers,
                        uint32_t *first, uint32_t *count, const char **why);

// 64-bit content hash used as an additional certificate check, not as proof that no CPU store occurred.
uint64_t nvn_texflush_hash(const uint8_t *p, uint64_t n);

// Coherence records are keyed by texture serial and also bind the exact storage pointer, layer size
// and layer count. Fresh layers are unknown. note updates only [first, first + count): establish != 0
// asks it to arm dirty tracking and certify those caller-proven layers; establish == 0 invalidates them.
// verify returns UNTRACKED for unknown coverage, CHANGED for a formerly valid certificate invalidated
// by ambiguous/re-registered/dirty storage or a hash mismatch, and CLEAN only for a current certificate.
void nvn_texflush_note(uint64_t serial, const uint8_t *storage, uint64_t layer_size,
                       uint32_t layers, uint32_t first, uint32_t count, int establish);
int nvn_texflush_verify(uint64_t serial, const uint8_t *storage, uint64_t layer_size,
                        uint32_t layers, uint32_t first, uint32_t count);
void nvn_texflush_forget(uint64_t serial);
size_t nvn_texflush_records(void);   // Live record count (tests and diagnostics).
