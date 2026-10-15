/* Native NVN -> Metal bridge. No game assets, bytecode, or shader output. */
#ifndef BOTW_GRAPHICS_H
#define BOTW_GRAPHICS_H
#include <stddef.h>
#include <stdint.h>
#include "nvn.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Configure before guest entry; coordinate init/shutdown with guest lifetime.
 * Public NVN calls, resource tables and submission state serialize internally.
 * layer is a CAMetalLayer (NULL permits offscreen tests). The bridge retains it.
 * validator must confirm every control-section span before it can be hashed.
 * error runs on the invoking thread after all graphics locks are released and
 * may escape to the host. If it returns, unsupported void calls abort and
 * boolean operations fail with 0. Never reports a no-op as success. */
typedef void (*botw_graphics_error)(const char *message);
typedef int (*botw_graphics_memory_range)(const void *address, size_t bytes);
int botw_graphics_init(void *layer, botw_graphics_error error,
                       botw_graphics_memory_range validator);
void botw_graphics_shutdown(void);
void *botw_nvn_bootstrap_loader(const char *name);
const char *botw_graphics_last_error(void);
/* Register locally translated, independently validated MSL for an exact input.
 * NVN data includes its 0x30-byte prefix. No guessed/dummy shader fallback.
 * Returns 0 on invalid identity, stage, Metal compiler failure, or entry point.
 * Binding/vertex-layout validation remains the offline translator's job. */
int botw_graphics_register_shader(const void *control, size_t control_bytes,
                                  const void *code, size_t code_bytes,
                                  NVNshaderStage stage, const char *msl,
                                  const char *entry);
enum botw_graphics_binding_kind {
    BOTW_GRAPHICS_UNIFORM = 0, BOTW_GRAPHICS_TEXTURE = 1, BOTW_GRAPHICS_SAMPLER = 2,
    BOTW_GRAPHICS_STORAGE = 3
};
typedef struct {
    enum botw_graphics_binding_kind kind;
    unsigned nvn_slot, metal_slot;
} botw_graphics_shader_binding;
int botw_graphics_register_shader_with_bindings(const void *control, size_t control_bytes,
    const void *code, size_t code_bytes, NVNshaderStage stage, const char *msl,
    const char *entry, const botw_graphics_shader_binding *bindings, size_t binding_count);
/* Counts only completed on-screen presents containing a new translated draw.
 * Clear-only and offscreen submits do not count. Screenshot capture is opt-in. */
uint64_t botw_graphics_presented_frame_count(void);
void botw_graphics_set_capture_enabled(int enabled);
int botw_graphics_copy_latest_frame(void *rgba, size_t capacity, uint32_t *width,
                                    uint32_t *height, uint64_t *frame_number);
/* Wait for GPU completion and read a supported linear 2D color texture. */
int botw_graphics_read_texture(const NVNtexture *texture, void *pixels, size_t bytes);
#ifdef __cplusplus
}
#endif
#endif
