/* Owned normal operands and protocol objects; no Metal device or GPU. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import "whole_shader_conditional.h"
#import "base_v0_shader_provider.h"
#include "nvn_obj.h"
#include "nvn_refl.h"
#include <assert.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

static void owned_fallback(int code,const char *format,...) {(void)code;(void)format;abort();}
#define RED_ONCE(...) owned_fallback(__VA_ARGS__)
static struct {const prog_t *bst[5];va_st attr[16];int nattr;vs_st stream[16];int nstream;NVNbufferRange vbuf[16];
    struct {uint32_t blend_en;} color;struct {uint32_t mask;} chmask;struct {uint32_t a2c;} ms;blend_st blend[8];} S;
static unsigned char owned_vertices[256],owned_indices[128];
static unsigned range_calls;
static int mtl_gpu_addr(uint64_t address,uint64_t length,void **buffer,uint64_t *offset,uint64_t *gpu) {
    range_calls++;
    uint64_t vertices=(uint64_t)owned_vertices,indices=(uint64_t)owned_indices;
    int valid=(address>=vertices&&address-vertices<=sizeof owned_vertices&&length<=sizeof owned_vertices-(address-vertices))||
        (address>=indices&&address-indices<=sizeof owned_indices&&length<=sizeof owned_indices-(address-indices));
    if(valid){*buffer=NULL;*offset=0;*gpu=1;}return valid;
}
#ifndef NX_TYPED_BASELINE
#include "vertex_format.inc"
#include "vertex_binding.inc"
static struct {int has_rt;p_set_rt rt;} R;
static _Atomic uint64_t g_reg_gen=4;
static dev_t_ device;
const void *nx_base_program_device(const void *program){assert(program);return &device;}
#include "vertex_snapshot.inc"
#define VB_SLOT(s) ((s)<14?(s)+16:(s))
typedef struct {MTLVertexFormat format;unsigned offset,bufferIndex;} owned_attr;
typedef struct {unsigned stride,stepFunction,stepRate;} owned_layout;
typedef struct {owned_attr attributes[32];owned_layout layouts[32];} owned_descriptor;
typedef struct {nvn_refl rf;} owned_shader;
#include "vertex_descriptor.inc"
#endif

static int guard_case(int mode) {
#ifdef NX_TYPED_BASELINE
    (void)mode;(void)S;return 77; /* The original runtime has no typed draw admission. */
#else
    if(mode==24){
        for(int fmt=0;fmt<256;fmt++)for(int ct=NVN_CT_FLOAT;ct<=NVN_CT_INT;ct++){
            unsigned width=0,mask=conditional_vertex_format(fmt,&width);
            unsigned wanted=ct==NVN_CT_FLOAT?1:ct==NVN_CT_INT?2:4;
            if(mask&wanted){assert(width>0&&width<=16);assert(vfmt_for_ct(vfmt(fmt),ct,fmt)!=MTLVertexFormatInvalid);}
            else if(!mask)assert(!width);
        }
        return 0;
    }
    memset(&S,0,sizeof S);S.nattr=1;S.nstream=1;S.attr[0].fmt=0x2e;
    S.stream[0].stride=16;S.vbuf[0]=(NVNbufferRange){(uint64_t)owned_vertices,sizeof owned_vertices};
    if(mode>=26){
        owned_shader shader={0};shader.rf.nv=1;shader.rf.v[0].ct=NVN_CT_FLOAT;
        int expected=mode==26;S.nstream=16;S.attr[0].stream=15;
        switch(mode){
            case 27:S.attr[0].stream=16;break;
            case 28:shader.rf.v[0].loc=16;break;
            case 29:shader.rf.v[0].nvn=16;break;
            case 30:shader.rf.v[0].nvn=-1;break;
            case 31:shader.rf.v[0].ct=99;break;
            case 32:S.attr[0].fmt=0xff;break;
            case 33:S.attr[0].fmt=0x27;break;
            case 34:shader.rf.nv=2;shader.rf.v[1]=shader.rf.v[0];break;
            case 35:S.nstream=0;S.attr[0].stream=0;break;
        }
        unsigned before=range_calls;int admitted=1;
#if NX_SHAPE_GATE
        admitted=conditional_vertex_shape_valid(&shader.rf);
#endif
        assert(range_calls==before);
        if(admitted){pso_key key;uint32_t colors[8]={0};
            pso_key_fill(&key,&shader,NULL,colors,0,0,1,0);owned_descriptor_loop(&key,&shader);}
        return admitted==expected?0:93;
    }
    if(mode==25){
        device.magic=MAGIC('D','E','V',0);prog_t program={0};S.bst[0]=&program;
        botw_conditional_draw_state before=conditional_snapshot_locked();
        assert(before.vertex_known&&before.vertex_count==1&&before.vertex_type_mask[0]==7);
#define CHANGED(statement,restore) do{statement;botw_conditional_draw_state after=conditional_snapshot_locked(); \
        assert(!botw_conditional_state_equal(&before,&after));restore;}while(0)
        CHANGED(S.attr[0].fmt=0x27,S.attr[0].fmt=0x2e);
        CHANGED(S.attr[0].offset=4,S.attr[0].offset=0);
        CHANGED(S.attr[0].stream=1,S.attr[0].stream=0);
        CHANGED(S.stream[0].stride=32,S.stream[0].stride=16);
        CHANGED(S.stream[0].divisor=2,S.stream[0].divisor=0);
        CHANGED(S.vbuf[0].address++,S.vbuf[0].address--);
        CHANGED(S.vbuf[0].size--,S.vbuf[0].size++);
        CHANGED(S.nattr=0,S.nattr=1);
        CHANGED(S.nstream=0,S.nstream=1);
#undef CHANGED
        botw_conditional_draw_state same=conditional_snapshot_locked();assert(botw_conditional_state_equal(&before,&same));
        return 0;
    }
    nvn_refl rf={0};rf.nv=1;rf.v[0].ct=NVN_CT_FLOAT;
    p_draw draw={.count=16,.instances=1};p_draw_idx indexed={.itype=2,.count=16,.instances=1,.ibuf=(uint64_t)owned_indices};
    const p_draw *dw=&draw;const p_draw_idx *di=NULL;int expected=1;
    switch(mode){
        case 1:S.attr[0].fmt=0xff;expected=0;break;
        case 2:S.attr[0].fmt=0x27;expected=0;break; /* float shader/raw integer format */
        case 3:S.attr[0].fmt=0x25;rf.v[0].ct=NVN_CT_UINT;expected=0;break;
        case 4:S.attr[0].fmt=0x27;rf.v[0].ct=NVN_CT_UINT;S.stream[0].stride=4;break;
        case 5:rf.v[0].ct=NVN_CT_INT;break; /* original float32/int bit-pattern mapping */
        case 6:S.nattr=0;expected=0;break;
        case 7:S.attr[0].stream=16;expected=0;break;
        case 8:S.vbuf[0].size++;expected=0;break;
        case 9:S.attr[0].offset=250;expected=0;break;
        case 10:draw.count++;expected=0;break;
        case 11:S.stream[0].stride=0;S.vbuf[0].size=16;draw.count=100000;break;
        case 12:S.stream[0].divisor=2;draw.instances=32;draw.count=100000;break;
        case 13:S.stream[0].divisor=2;draw.instances=35;expected=0;break;
        case 14:dw=NULL;di=&indexed;break;
        case 15:dw=NULL;di=&indexed;indexed.count=33;expected=0;break;
        case 16:dw=NULL;di=&indexed;indexed.itype=3;expected=0;break;
        case 17:S.vbuf[0].address=UINT64_MAX-8;expected=0;break;
        case 18:rf.nv=2;rf.v[1]=rf.v[0];expected=0;break;
        case 19:draw.first=1;expected=0;break;
        case 20:draw.count=-1;expected=0;break;
        case 21:rf.v[0].loc=16;expected=0;break;
        case 22:S.vbuf[0].address=(uint64_t)owned_vertices+16;S.vbuf[0].size=240;draw.count=15;break;
        case 23:S.stream[0].divisor=1;draw.base_instance=1;draw.instances=16;expected=0;break;
    }
    int result=conditional_vertex_draw_valid(&rf,dw,di);
    if(result!=expected)return 91;
    if(expected&&!range_calls)return 92;
    return 0;
#endif
}

int main(int argc,char **argv){@autoreleasepool {
    if(argc==3&&!strcmp(argv[1],"bundle"))return [NxBaseShaderProvider providerAtBundle:@(argv[2]) error:NULL]?0:70;
    if(argc==3&&!strcmp(argv[1],"guard"))return guard_case(atoi(argv[2]));
    if(argc!=5)return 2;
    NSError *error=nil;WholeShaderConditionalManifest *manifest=[WholeShaderConditionalManifest conditionalManifestAtAbsolutePath:@(argv[1]) error:&error];
    if(!manifest)return 10;
    botw_shader_info info={0};strlcpy(info.sha256,argv[2],sizeof info.sha256);memcpy(info.key,argv[2],16);
    botw_conditional_draw_state state={.device_known=1};
#ifndef NX_TYPED_BASELINE
    state.vertex_known=1;state.vertex_count=1;state.stream_count=1;
    state.vertex_type_mask[0]=(unsigned)atoi(argv[3]);state.vertex_format[0]=0x2e;
    botw_conditional_draw_state before=state;state.vertex_offset[0]=4;
    if(botw_conditional_state_equal(&state,&before))return 80;state=before;
#endif
    WholeShaderConditionalArtifact *artifact=[manifest artifactForInfo:&info state:&state error:&error];
    if(!!artifact!=atoi(argv[4]))return 12;
    if(artifact&&(!artifact.metalSource.length||artifact.metallib.length))return 13;
    return 0;
}}
