// BotW 1.9.0 NVN descriptor handles. Texture IDs occupy 20 bits and sampler IDs
// occupy 12 bits. Native texture-bearing handles set bit 32 after sign-extending
// the packed 32-bit value; consumers must not treat that marker as a sampler bit.
#pragma once
#include <stdint.h>

static inline uint64_t nvn_combined_handle(int32_t texture, int32_t sampler) {
    uint32_t packed = (uint32_t)texture | ((uint32_t)sampler << 20);
    return (uint64_t)(int64_t)(int32_t)packed | UINT64_C(0x100000000);
}
static inline uint64_t nvn_texture_handle(int32_t texture) {
    return (uint64_t)(int64_t)texture | UINT64_C(0x100000000);
}
static inline uint64_t nvn_sampler_handle(int32_t sampler) {
    return (uint64_t)(int64_t)sampler << 20;
}
static inline unsigned nvn_handle_sampler_id(uint64_t handle) {
    return (unsigned)((handle >> 20) & UINT64_C(0xfff));
}
