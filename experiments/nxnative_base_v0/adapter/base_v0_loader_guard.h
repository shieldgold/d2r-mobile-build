/* SPDX-License-Identifier: MIT */
#pragma once
#include <stddef.h>
#include <stdint.h>
#if defined(NXNATIVE_BASE_V0_LOADER_GUARD) && NXNATIVE_BASE_V0_LOADER_GUARD && \
    (!(defined(NXNATIVE_NORMAL_POLICY) && NXNATIVE_NORMAL_POLICY) || \
     !(defined(NXNATIVE_BASE_V0_MEMORY) && NXNATIVE_BASE_V0_MEMORY))
#error "normal loader guard requires normal policy and base-v0 memory"
#endif
/* Selection authority only. Function implementations remain in native swforce. */
typedef struct { const char *name; void *fn; const char *why; } nxnative_base_v0_loader_force;
int nxnative_base_v0_loader_preload(const char *signed_bundle);
int nxnative_base_v0_loader_begin(const char *metadata, const char *const *names, size_t count);
int nxnative_base_v0_loader_layout(const char *name, const uint64_t fields[6], const unsigned keys[8], const char *build_id);
int nxnative_base_v0_loader_image(const char *name, const void *const parts[3], const size_t lengths[3]);
int nxnative_base_v0_loader_gate_count(const char *name, unsigned count);
int nxnative_base_v0_loader_registry(const void *, size_t count, uint8_t *selected);
int nxnative_base_v0_loader_reference(const char *module, const char *name);
int nxnative_base_v0_loader_complete(void);
int nxnative_base_v0_loader_entry(const char *name, const char *module, uint64_t offset);
