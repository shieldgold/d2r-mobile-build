/* Checked adapters around the fixed MIT D2R pure render-state module.
 * See vendor/d2r-state/SOURCE.json. Recording always owns an immutable snapshot. */
#define D2R_STATE(name,ret,...) static ret d2r_##name(__VA_ARGS__)
#include "vendor/d2r-state/states.inc"
#undef D2R_STATE
static void stateMemory(const void *p,size_t n,BOOL write){
    if(!p||(memoryAccessValidator&&!memoryAccessValidator(p,n,write)))fatal("render state memory is not fully accessible");
    if(write)validateObjectStorage((void*)p,n);
}
static BOOL validBlendFactor(int n){return (n>=1&&n<=11)||(n>=0x10&&n<=0x13)||(n>=0x61&&n<=0x64);}
static void stateFace(int n){if(n<NVN_FACE_NONE||n>NVN_FACE_FRONT_AND_BACK)fatal("render state face enum");}
#define STATE_DEFAULT(name,T) NVN(name,void,T *s){stateMemory(s,sizeof *s,YES);d2r_##name(s);}
#define STATE_BOOL(name,T) NVN(name,void,T *s,uint32_t e){stateMemory(s,sizeof *s,YES);d2r_##name(s,e);}
STATE_DEFAULT(nvnBlendStateSetDefaults,NVNblendState)
NVN(nvnBlendStateSetBlendTarget,void,NVNblendState *s,int target){stateMemory(s,sizeof *s,YES);if(target<0||target>7)fatal("blend target index");d2r_nvnBlendStateSetBlendTarget(s,target);}
NVN(nvnBlendStateSetBlendFunc,void,NVNblendState *s,NVNblendFunc sr,NVNblendFunc dr,NVNblendFunc sa,NVNblendFunc da){stateMemory(s,sizeof *s,YES);if(!validBlendFactor(sr)||!validBlendFactor(dr)||!validBlendFactor(sa)||!validBlendFactor(da))fatal("blend factor enum");d2r_nvnBlendStateSetBlendFunc(s,sr,dr,sa,da);}
NVN(nvnBlendStateSetBlendEquation,void,NVNblendState *s,NVNblendEquation rgb,NVNblendEquation alpha){stateMemory(s,sizeof *s,YES);if(rgb<1||rgb>5||alpha<1||alpha>5)fatal("blend equation enum");d2r_nvnBlendStateSetBlendEquation(s,rgb,alpha);}
STATE_DEFAULT(nvnColorStateSetDefaults,NVNcolorState)
NVN(nvnColorStateSetBlendEnable,void,NVNcolorState *s,int target,uint32_t enable){stateMemory(s,sizeof *s,YES);if(target<0||target>7)fatal("color target index");d2r_nvnColorStateSetBlendEnable(s,target,enable);}
STATE_DEFAULT(nvnChannelMaskStateSetDefaults,NVNchannelMaskState)
NVN(nvnChannelMaskStateSetChannelMask,void,NVNchannelMaskState *s,int target,uint32_t r,uint32_t g,uint32_t b,uint32_t a){stateMemory(s,sizeof *s,YES);if(target<0||target>7)fatal("channel target index");d2r_nvnChannelMaskStateSetChannelMask(s,target,r,g,b,a);}
STATE_DEFAULT(nvnMultisampleStateSetDefaults,NVNmultisampleState)
STATE_BOOL(nvnMultisampleStateSetMultisampleEnable,NVNmultisampleState)
NVN(nvnMultisampleStateSetSamples,void,NVNmultisampleState *s,int n){stateMemory(s,sizeof *s,YES);if(n<0||n>16||(n&&(n&(n-1))))fatal("multisample sample count");d2r_nvnMultisampleStateSetSamples(s,n);}
STATE_BOOL(nvnMultisampleStateSetAlphaToCoverageEnable,NVNmultisampleState)
STATE_BOOL(nvnMultisampleStateSetSampleLocationsEnable,NVNmultisampleState)
STATE_DEFAULT(nvnPolygonStateSetDefaults,NVNpolygonState)
NVN(nvnPolygonStateSetCullFace,void,NVNpolygonState *s,NVNface face){stateMemory(s,sizeof *s,YES);stateFace(face);d2r_nvnPolygonStateSetCullFace(s,face);}
NVN(nvnPolygonStateSetFrontFace,void,NVNpolygonState *s,NVNfrontFace face){stateMemory(s,sizeof *s,YES);if(face<0||face>1)fatal("front face enum");d2r_nvnPolygonStateSetFrontFace(s,face);}
NVN(nvnPolygonStateSetPolygonMode,void,NVNpolygonState *s,NVNpolygonMode mode){stateMemory(s,sizeof *s,YES);if(mode<0||mode>2)fatal("polygon mode enum");d2r_nvnPolygonStateSetPolygonMode(s,mode);}
NVN(nvnPolygonStateSetPolygonOffsetEnables,void,NVNpolygonState *s,int enables){stateMemory(s,sizeof *s,YES);if(enables<0||enables>7)fatal("polygon offset enable mask");d2r_nvnPolygonStateSetPolygonOffsetEnables(s,enables);}
STATE_DEFAULT(nvnDepthStencilStateSetDefaults,NVNdepthStencilState)
STATE_BOOL(nvnDepthStencilStateSetDepthTestEnable,NVNdepthStencilState)
STATE_BOOL(nvnDepthStencilStateSetDepthWriteEnable,NVNdepthStencilState)
NVN(nvnDepthStencilStateSetDepthFunc,void,NVNdepthStencilState *s,NVNdepthFunc f){stateMemory(s,sizeof *s,YES);if(f<1||f>8)fatal("depth comparison enum");d2r_nvnDepthStencilStateSetDepthFunc(s,f);}
STATE_BOOL(nvnDepthStencilStateSetStencilTestEnable,NVNdepthStencilState)
NVN(nvnDepthStencilStateSetStencilFunc,void,NVNdepthStencilState *s,NVNface face,NVNstencilFunc f){stateMemory(s,sizeof *s,YES);stateFace(face);if(f<1||f>8)fatal("stencil comparison enum");d2r_nvnDepthStencilStateSetStencilFunc(s,face,f);}
NVN(nvnDepthStencilStateSetStencilOp,void,NVNdepthStencilState *s,NVNface face,NVNstencilOp fail,NVNstencilOp zfail,NVNstencilOp zpass){stateMemory(s,sizeof *s,YES);stateFace(face);if(fail<1||fail>8||zfail<1||zfail>8||zpass<1||zpass>8)fatal("stencil operation enum");d2r_nvnDepthStencilStateSetStencilOp(s,face,fail,zfail,zpass);}
#undef STATE_DEFAULT
#undef STATE_BOOL
static void bindRenderState(NVNcommandBuffer *c,const void *s,size_t objectBytes,size_t packedBytes,NSString *kind){stateMemory(s,objectBytes,NO);record(c,@{@"op":@"renderState",@"kind":kind,@"data":[NSData dataWithBytes:s length:packedBytes]});}
#define STATE_BIND(name,T,P,kind) NVN(name,void,NVNcommandBuffer *c,const T *s){bindRenderState(c,s,sizeof *s,sizeof(P),kind);}
STATE_BIND(nvnCommandBufferBindBlendState,NVNblendState,blend_st,@"blend")
STATE_BIND(nvnCommandBufferBindColorState,NVNcolorState,color_st,@"color")
STATE_BIND(nvnCommandBufferBindChannelMaskState,NVNchannelMaskState,chmask_st,@"channel")
STATE_BIND(nvnCommandBufferBindMultisampleState,NVNmultisampleState,ms_st,@"multisample")
STATE_BIND(nvnCommandBufferBindPolygonState,NVNpolygonState,poly_st,@"polygon")
#undef STATE_BIND
NVN(nvnCommandBufferSetBlendColor,void,NVNcommandBuffer *c,const float *color){stateMemory(color,16,NO);for(int i=0;i<4;i++)if(!isfinite(color[i]))fatal("nonfinite blend color");record(c,@{@"op":@"renderState",@"kind":@"blendColor",@"data":[NSData dataWithBytes:color length:16]});}
NVN(nvnCommandBufferSetDepthRange,void,NVNcommandBuffer *c,float near,float far){if(!isfinite(near)||!isfinite(far)||near<0||near>1||far<0||far>1)fatal("depth range outside Metal zero-to-one clip interval");record(c,@{@"op":@"depthRange",@"near":@(near),@"far":@(far)});}
NVN(nvnCommandBufferSetPolygonOffsetClamp,void,NVNcommandBuffer *c,float factor,float units,float clamp){if(!isfinite(factor)||!isfinite(units)||!isfinite(clamp))fatal("nonfinite polygon depth bias");record(c,@{@"op":@"polygonOffset",@"factor":@(factor),@"units":@(units),@"clamp":@(clamp)});}

typedef struct { color_st color;chmask_st channel;blend_st blend[8];poly_st polygon;ms_st multisample;ds_st depth; } BWRenderStates;
static BWRenderStates renderStates(BWReplay *state){
    BWRenderStates r={0};
    NVNcolorState color={0};NVNchannelMaskState channel={0};NVNblendState blend={0};NVNpolygonState poly={0};NVNmultisampleState ms={0};NVNdepthStencilState depth={0};
    d2r_nvnColorStateSetDefaults(&color);d2r_nvnChannelMaskStateSetDefaults(&channel);d2r_nvnBlendStateSetDefaults(&blend);d2r_nvnPolygonStateSetDefaults(&poly);d2r_nvnMultisampleStateSetDefaults(&ms);d2r_nvnDepthStencilStateSetDefaults(&depth);
    memcpy(&r.color,&color,sizeof r.color);memcpy(&r.channel,&channel,sizeof r.channel);memcpy(&r.polygon,&poly,sizeof r.polygon);memcpy(&r.multisample,&ms,sizeof r.multisample);memcpy(&r.depth,&depth,sizeof r.depth);
    for(unsigned i=0;i<8;i++){memcpy(&r.blend[i],&blend,sizeof r.blend[i]);r.blend[i].target=i;NSData *s=state.blendStates[@(i)];if(s)[s getBytes:&r.blend[i] length:sizeof r.blend[i]];}
#define READ_STATE(property,field) if(state.property.length)[state.property getBytes:&r.field length:sizeof r.field]
    READ_STATE(colorState,color);READ_STATE(channelState,channel);READ_STATE(polygonState,polygon);READ_STATE(multisampleState,multisample);READ_STATE(depthState,depth);
#undef READ_STATE
    /* Public setters preserve state, while unimplemented draw semantics fail at
     * use rather than returning successful rendering with the state ignored. */
    if(r.color.logic_op!=NVN_LOGIC_OP_COPY||r.color.alpha_func!=NVN_ALPHA_FUNC_ALWAYS)fatal("logic/alpha test is not implemented");
    if(r.multisample.samples>1||r.multisample.a2c||r.multisample.a2c_dither||r.multisample.raster_samples||r.multisample.cov_mod||r.multisample.cov2color_en||r.multisample.sl_en)fatal("multisample coverage/sample locations require additional Metal render-target semantics");
    if(r.polygon.mode!=NVN_POLYGON_MODE_FILL&&r.polygon.mode!=NVN_POLYGON_MODE_LINE)fatal("polygon point rasterization is not implemented");
    if(r.depth.st)fatal("stencil attachment/render semantics are not implemented");
    if(r.depth.df<1||r.depth.df>8)fatal("depth comparison enum");
    return r;
}
/* Generic fixed-upstream Metal mappings. No default enum fallback. */
static MTLBlendFactor stateBlendFactor(int f){
    switch(f){case 1:return MTLBlendFactorZero;case 2:return MTLBlendFactorOne;case 3:return MTLBlendFactorSourceColor;case 4:return MTLBlendFactorOneMinusSourceColor;case 5:return MTLBlendFactorSourceAlpha;case 6:return MTLBlendFactorOneMinusSourceAlpha;case 7:return MTLBlendFactorDestinationAlpha;case 8:return MTLBlendFactorOneMinusDestinationAlpha;case 9:return MTLBlendFactorDestinationColor;case 10:return MTLBlendFactorOneMinusDestinationColor;case 11:return MTLBlendFactorSourceAlphaSaturated;case 0x10:return MTLBlendFactorSource1Color;case 0x11:return MTLBlendFactorOneMinusSource1Color;case 0x12:return MTLBlendFactorSource1Alpha;case 0x13:return MTLBlendFactorOneMinusSource1Alpha;case 0x61:return MTLBlendFactorBlendColor;case 0x62:return MTLBlendFactorOneMinusBlendColor;case 0x63:return MTLBlendFactorBlendAlpha;case 0x64:return MTLBlendFactorOneMinusBlendAlpha;}fatal("blend factor enum");
}
static MTLBlendOperation stateBlendOperation(int e){switch(e){case 1:return MTLBlendOperationAdd;case 2:return MTLBlendOperationSubtract;case 3:return MTLBlendOperationReverseSubtract;case 4:return MTLBlendOperationMin;case 5:return MTLBlendOperationMax;}fatal("blend equation enum");}
static void applyRenderPipeline(MTLRenderPipelineDescriptor *desc,BWRenderStates r){
    desc.rasterSampleCount=1;
    /* Metal has no FrontAndBack cull mode. Replay uses an empty scissor while
     * retaining the real draw/vertex invocation, including storage writes. */
    MTLRenderPipelineColorAttachmentDescriptor *c=desc.colorAttachments[0];unsigned m=r.channel.mask&15;
    c.writeMask=(m&1?MTLColorWriteMaskRed:0)|(m&2?MTLColorWriteMaskGreen:0)|(m&4?MTLColorWriteMaskBlue:0)|(m&8?MTLColorWriteMaskAlpha:0);
    if(r.color.blend_en&1){blend_st b=r.blend[0];c.blendingEnabled=YES;c.sourceRGBBlendFactor=stateBlendFactor(b.src_rgb);c.destinationRGBBlendFactor=stateBlendFactor(b.dst_rgb);c.sourceAlphaBlendFactor=stateBlendFactor(b.src_a);c.destinationAlphaBlendFactor=stateBlendFactor(b.dst_a);c.rgbBlendOperation=stateBlendOperation(b.eq_rgb);c.alphaBlendOperation=stateBlendOperation(b.eq_a);}
}
static void applyRenderEncoder(BWReplay *state,id<MTLRenderCommandEncoder> encoder,BWRenderStates r){
    [encoder setFrontFacingWinding:r.polygon.front==NVN_FRONT_FACE_CCW?MTLWindingCounterClockwise:MTLWindingClockwise];
    [encoder setCullMode:r.polygon.cull==NVN_FACE_FRONT?MTLCullModeFront:r.polygon.cull==NVN_FACE_BACK?MTLCullModeBack:MTLCullModeNone];
    [encoder setTriangleFillMode:r.polygon.mode==NVN_POLYGON_MODE_LINE?MTLTriangleFillModeLines:MTLTriangleFillModeFill];
    unsigned offsetBit=r.polygon.mode==NVN_POLYGON_MODE_LINE?NVN_POLYGON_OFFSET_ENABLE_LINE:NVN_POLYGON_OFFSET_ENABLE_FILL;
    if(r.polygon.offset_en&offsetBit)[encoder setDepthBias:[state.polygonOffset[@"units"] floatValue] slopeScale:[state.polygonOffset[@"factor"] floatValue] clamp:[state.polygonOffset[@"clamp"] floatValue]];
    float color[4]={0};if(state.blendColor)[state.blendColor getBytes:color length:sizeof color];[encoder setBlendColorRed:color[0] green:color[1] blue:color[2] alpha:color[3]];
    NSNumber *key=@((r.depth.dt?1:0)|(r.depth.dw?2:0)|(r.depth.df<<2)|(state.depth?64:0));
    id<MTLDepthStencilState> ds=depthStates[key];
    if(!ds){MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=state.depth&&r.depth.dt?(MTLCompareFunction)(r.depth.df-1):MTLCompareFunctionAlways;dd.depthWriteEnabled=state.depth&&r.depth.dt&&r.depth.dw;
        ds=[gpu newDepthStencilStateWithDescriptor:dd];if(!ds)fatal("Metal depth state creation failed");depthStates[key]=ds;}
    [encoder setDepthStencilState:ds];
}
static void replayRenderState(BWReplay *state,NSDictionary *c){
    NSData *data=c[@"data"];NSString *kind=c[@"kind"];
    if([kind isEqual:@"blend"]){blend_st b;[data getBytes:&b length:sizeof b];state.blendStates[@(b.target)]=data;}
    else if([kind isEqual:@"color"])state.colorState=data;else if([kind isEqual:@"channel"])state.channelState=data;else if([kind isEqual:@"polygon"])state.polygonState=data;else if([kind isEqual:@"multisample"])state.multisampleState=data;else if([kind isEqual:@"blendColor"])state.blendColor=data;else fatal("unknown render-state record");
}
