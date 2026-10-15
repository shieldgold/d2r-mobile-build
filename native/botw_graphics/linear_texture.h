/* Host linear storage policy. Guest ABI fields and the vendored Tegra
 * block-linear layout stay unchanged. No game-specific constants or offsets. */
#pragma once
#include <limits.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    size_t textureAlignment,renderAlignment,formatMax,formatCount;
    int valid,renderSupported;
} BWLinearPolicy;
typedef struct { size_t alignment,stride,size; } BWLinearLayout;

static inline int bw_linear_policy_make(const size_t *alignments,size_t count,
                                        int renderSupported,BWLinearPolicy *out){
    if(!out)return 0;
    *out=(BWLinearPolicy){0};
    if(!alignments||!count)return 0;
    size_t maximum=0;
    for(size_t i=0;i<count;i++){
        size_t a=alignments[i];
        /* The original NVN consumer rounds with (bytes+A-1)&-A. For powers
         * of two, maximum is exactly their LCM; no multiplication can wrap. */
        if(!a||(a&(a-1))||a>(size_t)INT_MAX)return 0;
        if(a>maximum)maximum=a;
    }
    *out=(BWLinearPolicy){maximum>32?maximum:32,maximum>128?maximum:128,
                          maximum,count,1,!!renderSupported};
    return 1;
}
static inline size_t bw_linear_alignment(const BWLinearPolicy *policy,unsigned flags){
    if(!policy||!policy->valid)return 0;
    if(flags&0x40)return policy->renderSupported?policy->renderAlignment:0;
    return (flags&0x10)?policy->textureAlignment:0;
}
static inline int bw_linear_layout(const BWLinearPolicy *policy,unsigned flags,
                                   size_t width,size_t height,size_t stride,
                                   size_t bytesPerPixel,BWLinearLayout *out){
    size_t alignment=bw_linear_alignment(policy,flags);
    if(!out||!alignment||!width||!height||!bytesPerPixel||width>SIZE_MAX/bytesPerPixel)return 0;
    size_t row=width*bytesPerPixel;
    if(!stride){if(row>SIZE_MAX-(alignment-1))return 0;stride=(row+alignment-1)&~(alignment-1);}
    if(stride<row||stride%alignment||height>SIZE_MAX/stride)return 0;
    *out=(BWLinearLayout){alignment,stride,stride*height};return 1;
}
