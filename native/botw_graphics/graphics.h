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
typedef int (*botw_graphics_memory_access)(const void *address, size_t bytes, int writable);
/* The host contains guest failures inside this call and always returns to the
 * bridge: 1 on normal return, 0 with a bounded diagnostic on failure. Called
 * only with the graphics mutex released and the thread's graphics depth zero;
 * the guest callback may reenter AddCommandMemory/AddControlMemory. */
typedef int (*botw_graphics_command_memory_invoker)(
    PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC, NVNcommandBuffer*,
    NVNcommandBufferMemoryEvent, size_t, void*, char*, size_t);
int botw_graphics_init(void *layer, botw_graphics_error error,
                       botw_graphics_memory_range validator);
void botw_graphics_shutdown(void);
/* Configure after init and before guest entry. A readable-range validator
 * never authorizes writes. Init/shutdown clear this callback. */
void botw_graphics_set_memory_access_validator(botw_graphics_memory_access validator);
/* Configure after init. Init/shutdown clear this callback. A memory callback
 * that needs to run without a safe host invoker is rejected explicitly. */
void botw_graphics_set_command_memory_invoker(botw_graphics_command_memory_invoker invoker);
void *botw_nvn_bootstrap_loader(const char *name);
const char *botw_graphics_last_error(void);
/* Host VI backend. These return 1 only for a live configured CAMetalLayer,
 * write host-local outputs, and report rejection through last_error only.
 * They never call the error callback, so the runtime may hold its lifecycle
 * lock, validate/copy guest outputs, and attach a real kernel vsync event.
 * Display/layer tokens expire on shutdown. Native window identity is the
 * retained CAMetalLayer. Offscreen graphics init does not create a display. */
int botw_graphics_platform_initialize(void);
int botw_graphics_platform_open_default_display(uintptr_t *display);
int botw_graphics_platform_create_layer(uintptr_t display, uintptr_t *layer);
int botw_graphics_platform_set_scaling_mode(uintptr_t layer, int mode);
int botw_graphics_platform_get_native_window(uintptr_t layer, uintptr_t *native);
int botw_graphics_platform_display_size(uintptr_t display, uint32_t *width, uint32_t *height);
int botw_graphics_platform_display_is_live(uintptr_t display);
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
    BOTW_GRAPHICS_STORAGE = 3,
    /* A proven c0 storage-table address-low read, derived from the actual
     * NVN binding. This is the guest address, never a Metal GPU pointer. */
    BOTW_GRAPHICS_STORAGE_ADDRESS_LOW = 4
};
typedef struct {
    enum botw_graphics_binding_kind kind;
    unsigned nvn_slot, metal_slot;
} botw_graphics_shader_binding;
int botw_graphics_register_shader_with_bindings(const void *control, size_t control_bytes,
    const void *code, size_t code_bytes, NVNshaderStage stage, const char *msl,
    const char *entry, const botw_graphics_shader_binding *bindings, size_t binding_count);
/* An offline-verified finite input profile for non-indexed vertex shaders.
 * minimum_storage_bytes has max_instance_count+1 entries; entry N covers all
 * accesses from relative instances 0..N-1. The registration copies this table.
 * Never install a profile unless every dynamic access is independently proven. */
typedef struct {
    unsigned max_vertex_count, max_instance_count, storage_slot;
    const size_t *minimum_storage_bytes;
} botw_graphics_shader_draw_profile;
int botw_graphics_register_shader_with_profile(const void *control, size_t control_bytes,
    const void *code, size_t code_bytes, NVNshaderStage stage, const char *msl,
    const char *entry, const botw_graphics_shader_binding *bindings, size_t binding_count,
    const botw_graphics_shader_draw_profile *profile);
/* Counts only completed on-screen presents containing a new translated draw.
 * Clear-only and offscreen submits do not count. Screenshot capture is opt-in. */
uint64_t botw_graphics_presented_frame_count(void);
void botw_graphics_set_capture_enabled(int enabled);
int botw_graphics_copy_latest_frame(void *rgba, size_t capacity, uint32_t *width,
                                    uint32_t *height, uint64_t *frame_number);
/* Wait for GPU completion and read level 0, layer 0 of an RGBA8/BGRA8 texture. */
int botw_graphics_read_texture(const NVNtexture *texture, void *pixels, size_t bytes);
int botw_graphics_read_texture_subresource(const NVNtexture *texture,
    unsigned level, unsigned layer, void *pixels, size_t bytes);
#ifdef __cplusplus
}
#endif
#endif
