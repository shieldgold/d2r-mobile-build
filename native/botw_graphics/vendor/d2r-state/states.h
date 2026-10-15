/* Copyright (c) 2026 jhaizhou, MIT. Full notice: ../../THIRD_PARTY_LICENSE.txt.
 * Extracted from d2r-mobile 9de307ecaff42b3c9d45e58992c848ee89b8f6a1.
 * Pure state module; entry macro renamed, unsupported no-op sample locations excluded. */
#ifndef BOTW_D2R_RENDER_STATES_H
#define BOTW_D2R_RENDER_STATES_H
#include <stdint.h>
#include "../../nvn.h"
#define FITS(T,H) _Static_assert(sizeof(T)<=sizeof(H),"state ABI size")
typedef struct { uint32_t blend_en : 8, logic_op : 4, alpha_func : 4; } color_st;            FITS(color_st, NVNcolorState);
typedef struct { uint32_t mask; } chmask_st;            /* 每目标 4 位：r=1 g=2 b=4 a=8，置位 = 写 | 4 bits per target: r=1 g=2 b=4 a=8, bit set = write */ FITS(chmask_st, NVNchannelMaskState);
typedef struct { uint64_t target : 3, src_rgb : 7, dst_rgb : 7, src_a : 7, dst_a : 7, eq_rgb : 3, eq_a : 3; } blend_st;
FITS(blend_st, NVNblendState);
typedef struct { uint8_t enable, samples, a2c, a2c_dither, raster_samples, cov_mod, cov2color_en, cov2color_out, sl_en, sl_grid; } ms_st;
FITS(ms_st, NVNmultisampleState);
typedef struct { uint32_t cull : 2, front : 1, mode : 2, offset_en : 3; } poly_st;           FITS(poly_st, NVNpolygonState);
typedef struct {
    uint64_t dt : 1, dw : 1, df : 4, st : 1, sf_f : 4, sf_b : 4,
             fail_f : 4, zfail_f : 4, zpass_f : 4, fail_b : 4, zfail_b : 4, zpass_b : 4;
} ds_st;                                                                                     FITS(ds_st, NVNdepthStencilState);

#undef FITS
#endif
