/* Owned replay metadata and complete production predicates; no Metal device. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import "whole_shader_conditional.h"
#import "base_v0_shader_provider.h"
#include "nvn_fmt_mtl.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#if NX_FRAGMENT_HELPERS
#include "fragment_helpers.inc"
#endif

static int guard_case(int mode) {
#if NX_FRAGMENT_HELPERS
    uint8_t types[8]={1,4};uint32_t formats[8]={MTLPixelFormatRGBA8Unorm,MTLPixelFormatRGBA32Uint};
    uint8_t map[8];for(unsigned i=0;i<8;i++)map[i]=(uint8_t)i;
    const void *program=(const void *)1,*function=(const void *)2;
    conditional_pso_state selected={0};conditional_pso_capture(&selected,program,7,formats,map,NULL);
    if(mode==20){botw_conditional_draw_state before={.colors_known=1,.color_present_mask=3};
        before.color_format[0]=37;before.color_type_mask[0]=1;botw_conditional_draw_state changed=before;
        changed.color_format[0]=41;assert(!botw_conditional_state_equal(&before,&changed));changed=before;
        changed.color_type_mask[0]=4;assert(!botw_conditional_state_equal(&before,&changed));changed=before;
        changed.color_present_mask=1;assert(!botw_conditional_state_equal(&before,&changed));changed=before;
        changed.colors_known=0;assert(!botw_conditional_state_equal(&before,&changed));
        assert(botw_conditional_state_equal(&before,&before));return 0;}
    if(mode==21){assert(conditional_fragment_map_shape(map));map[0]=9;
        assert(!conditional_fragment_map_shape(map));map[0]=0xff;assert(conditional_fragment_map_shape(map));
        assert(conditional_fragment_map_shape(NULL));return 0;}
    int expected=1;
    switch(mode){
        case 1:formats[1]=MTLPixelFormatRGBA32Float;expected=0;break;
        case 2:formats[0]=MTLPixelFormatRGBA8Unorm_sRGB;selected.formats[0]=formats[0];break;
        case 3:formats[0]=MTLPixelFormatRGBA8Sint;selected.formats[0]=formats[0];expected=0;break;
        case 4:selected.map[0]=1;expected=0;break;
        case 5:selected.map[1]=0xff;expected=0;break;
        case 6:selected.map[2]=1;formats[2]=MTLPixelFormatRGBA32Uint;selected.formats[2]=formats[2];expected=0;break;
        case 7:selected.fragment_override=(const void *)3;expected=0;break;
        case 8:selected.fragment_override=function;break;
        case 9:selected.program=(const void *)4;expected=0;break;
        case 10:selected.pass_serial++;expected=0;break;
        case 11:formats[1]=0;selected.formats[1]=0;break; /* An inactive output is not a fabricated attachment. */
        case 12:formats[1]=999;selected.formats[1]=999;expected=0;break;
        case 13:memset(types,0,sizeof types);break; /* No-color fragment. */
        case 14:types[0]=2;formats[0]=MTLPixelFormatRGBA16Sint;selected.formats[0]=formats[0];break;
        case 15:types[0]=4;formats[0]=MTLPixelFormatRGBA16Sint;selected.formats[0]=formats[0];expected=0;break;
        case 16:selected.map[7]=9;formats[7]=MTLPixelFormatRGBA8Unorm;selected.formats[7]=formats[7];expected=0;break;
        case 17:selected.map[7]=0xff;formats[7]=MTLPixelFormatRGBA8Unorm;selected.formats[7]=formats[7];break;
        case 18:{unsigned accepted=0,substitute=0;for(int fmt=0;fmt<256;fmt++){
            uint32_t pf=0;uint8_t swizzle[4];int exact=nvn_fmt_to_mtl(fmt,&pf,swizzle);
            unsigned type=conditional_fragment_format(fmt);
            if(exact!=1){assert(!type);substitute+=(exact==2);}else if(type){assert(type==conditional_pixel_type(pf));accepted++;}}
            assert(accepted&&substitute);return 0;}
        case 19:map[0]=7;formats[0]=MTLPixelFormatRGBA32Float;
            assert(selected.map[0]==0&&selected.formats[0]==MTLPixelFormatRGBA8Unorm);expected=0;break;
    }
    return conditional_fragment_pso_valid(types,&selected,program,function,7,formats)==expected?0:91;
#else
    (void)mode;return 77;
#endif
}

int main(int argc,char **argv){@autoreleasepool {
    if(argc==3&&!strcmp(argv[1],"bundle"))return [NxBaseShaderProvider providerAtBundle:@(argv[2]) error:NULL]?0:70;
    if(argc==3&&!strcmp(argv[1],"guard"))return guard_case(atoi(argv[2]));
    if(argc!=5)return 2;
    WholeShaderConditionalManifest *manifest=[WholeShaderConditionalManifest conditionalManifestAtAbsolutePath:@(argv[1]) error:NULL];
    if(!manifest)return 10;
    botw_shader_info info={.stage=1};strlcpy(info.sha256,argv[2],sizeof info.sha256);memcpy(info.key,argv[2],16);
    botw_conditional_draw_state state={.device_known=1};
#if NX_FRAGMENT_HELPERS
    state.colors_known=1;state.color_present_mask=3;state.color_type_mask[0]=1;state.color_type_mask[1]=(unsigned)atoi(argv[3]);
#endif
    WholeShaderConditionalArtifact *artifact=[manifest artifactForInfo:&info state:&state error:NULL];
    return !!artifact==atoi(argv[4])?0:12;
}}
