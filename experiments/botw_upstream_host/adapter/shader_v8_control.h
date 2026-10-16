/* SPDX-License-Identifier: MIT
 * Exact fixed base-v0 graphics control profile, independently bounded before
 * existing shared code/SPH/C1 validation. No byte rewriting or v9 coercion. */
#pragma once
#include <stddef.h>
#include <stdint.h>

static inline uint32_t botw_v8_u32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static inline int botw_base_v0_shader_control_v8(const uint8_t *control, size_t bytes) {
    if (!control || bytes != 2000) return 0;
    return botw_v8_u32(control) == UINT32_C(0x98761234) &&
           botw_v8_u32(control + 4) == 1 && botw_v8_u32(control + 8) == 8 &&
           botw_v8_u32(control + 12) == 0x120 && botw_v8_u32(control + 16) == 11 &&
           botw_v8_u32(control + 20) == bytes && botw_v8_u32(control + 0x20) == bytes &&
           botw_v8_u32(control + 0x6f0) == bytes && botw_v8_u32(control + 0x708) == 0x30 &&
           botw_v8_u32(control + 0x714) <= 2;
}
