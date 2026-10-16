/* SPDX-License-Identifier: MIT */
#pragma once
#include <stddef.h>
#include <stdint.h>

/* Fixed private six-module base-v0 Guest. The SHA/build-ID source identity is
 * tied to exact offline image bytes, including Host39, bounded heap and v4
 * stack transformations. No unknown profile is selected. Loader-only, serial,
 * one-shot API; all failures remain sticky. Never an execution permission. */
typedef struct {
    uint64_t text_end, ro_off, data_off, patch_off, key_page_off, key_page_size;
    unsigned key_idx[8];
} base_v0_force_layout;
typedef struct {
    const char *name, *source_sha256, *build_id, *image_sha256, *padded_sha256;
    size_t image_bytes, padded_bytes;
    base_v0_force_layout layout;
} base_v0_force_module;
const base_v0_force_module *base_v0_force_manifest(size_t *count);
int base_v0_force_begin(const char *const *names, size_t count);
/* Hash immutable pre-key-page/pre-relocation byte streams without copying the
 * full image. A single raw image or three signed, 16-KiB-padded sections only. */
int base_v0_force_image(const char *name, const base_v0_force_layout *layout,
    const void *const *parts, const size_t *lengths, size_t count, int padded);
int base_v0_force_registry_begin(void);
int base_v0_force_registry_entry(const char *name, unsigned titles,
    const char *why, int candidate, int implementation_present, uint8_t *selected);
int base_v0_force_registry_end(void);
/* Called only inside the original force_lookup successful-match branch. */
int base_v0_force_reference(const char *module, const char *name);
int base_v0_force_references_end(void);
