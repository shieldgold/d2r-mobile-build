/* SPDX-License-Identifier: MIT
 * One upstream-owned native VI layer, consumed by normal presentation. */
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum {
    BOTW_VI_SCALE_FIT_TO_LAYER = 2,
    BOTW_VI_SCALE_PRESERVE_ASPECT = 4
};
/* One atomic snapshot; no guest object is exposed to the Metal renderer. */
uint32_t botw_vi_layer_scaling_mode(void);
#ifdef __cplusplus
}
#endif
