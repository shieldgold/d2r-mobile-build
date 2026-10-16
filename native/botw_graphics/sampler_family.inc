/* Complete base-v0 sampler parameter adapter. The two MIT upstream sampler
 * families inform the Metal mapping, but their private POD is not this ABI.
 * Fixed SDK setters/getters/Defaults/Compare establish the offsets below.
 * See THIRD_PARTY_LICENSE.txt for provenance and unsupported GPU semantics. */
typedef struct {
    int32_t min,mag,wrap[3];
    float lodMin,lodMax,lodBias;
    int32_t compareMode,compareFunc;
    uint32_t border[4];
    float aniso;
    int32_t reduction;
} BWSamplerParameters;
typedef struct {NVNdevice *device;BWSamplerParameters p;} SamplerBuilder;
_Static_assert(sizeof(BWSamplerParameters)==0x40,"base-v0 sampler parameters");
_Static_assert(sizeof(SamplerBuilder)==0x48,"base-v0 sampler builder prefix");
_Static_assert(offsetof(SamplerBuilder,p.lodBias)==0x24,"base-v0 sampler bias");
_Static_assert(offsetof(SamplerBuilder,p.border)==0x30,"base-v0 sampler border");
_Static_assert(offsetof(SamplerBuilder,p.aniso)==0x40,"base-v0 sampler anisotropy");
_Static_assert(sizeof(SamplerBuilder)<=sizeof(NVNsamplerBuilder),"sampler builder budget");

static void *samplerField(const void *raw,size_t offset,size_t bytes,BOOL write){
    if(!raw||offset>UINTPTR_MAX-(uintptr_t)raw)fatal("sampler metadata address overflow");
    void *at=(void*)((uintptr_t)raw+offset);textureMemory(at,bytes,write);return at;
}
static SamplerBuilder samplerBuilder(const NVNsamplerBuilder *raw){
    SamplerBuilder b;memcpy(&b,samplerField(raw,0,sizeof b,NO),sizeof b);return b;
}
static SamplerBuilder samplerBuilderFields(const NVNsamplerBuilder *raw,size_t offset,size_t bytes){
    SamplerBuilder b={0};memcpy((char*)&b+offset,samplerField(raw,offset,bytes,NO),bytes);return b;
}
static SamplerBuilder samplerSnapshot(const NVNsampler *raw){
    BWSampler *sampler=get(raw,[BWSampler class]);
    if(sampler.parameters.length!=sizeof(SamplerBuilder))fatal("sampler parameter snapshot is invalid");
    SamplerBuilder b;memcpy(&b,sampler.parameters.bytes,sizeof b);return b;
}
static SamplerBuilder samplerSnapshotFields(const NVNsampler *raw,size_t offset,size_t bytes){(void)offset;(void)bytes;return samplerSnapshot(raw);}
static void samplerOutputs(void *a,size_t na,void *b,size_t nb,void *c,size_t nc){
    textureMemory(a,na,YES);if(nb)textureMemory(b,nb,YES);if(nc)textureMemory(c,nc,YES);
}
NVN(nvnSamplerBuilderSetDefaults,void,NVNsamplerBuilder *raw){
    BWSamplerParameters p={.min=1,.mag=1,.wrap={1,1,1},.lodMax=1000,.compareFunc=2,.aniso=1};
    memcpy(samplerField(raw,8,sizeof p,YES),&p,sizeof p);
}
NVN(nvnSamplerBuilderSetDevice,void,NVNsamplerBuilder *raw,NVNdevice *device){memcpy(samplerField(raw,0,8,YES),&device,8);}
NVN(nvnSamplerBuilderSetMinMagFilter,void,NVNsamplerBuilder *raw,NVNminFilter min,NVNmagFilter mag){
    int32_t v[]={min,mag};memcpy(samplerField(raw,8,sizeof v,YES),v,sizeof v);
}
NVN(nvnSamplerBuilderSetWrapMode,void,NVNsamplerBuilder *raw,NVNwrapMode s,NVNwrapMode t,NVNwrapMode r){
    int32_t v[]={s,t,r};memcpy(samplerField(raw,0x10,sizeof v,YES),v,sizeof v);
}
NVN(nvnSamplerBuilderSetLodClamp,void,NVNsamplerBuilder *raw,float min,float max){
    float v[]={min,max};memcpy(samplerField(raw,0x1c,sizeof v,YES),v,sizeof v);
}
NVN(nvnSamplerBuilderSetLodBias,void,NVNsamplerBuilder *raw,float value){memcpy(samplerField(raw,0x24,4,YES),&value,4);}
NVN(nvnSamplerBuilderSetCompare,void,NVNsamplerBuilder *raw,NVNcompareMode mode,NVNcompareFunc func){
    int32_t v[]={mode,func};memcpy(samplerField(raw,0x28,sizeof v,YES),v,sizeof v);
}
static void samplerBorderSet(NVNsamplerBuilder *raw,const void *color){
    uint32_t value[4];memcpy(value,samplerField(color,0,sizeof value,NO),sizeof value);
    memcpy(samplerField(raw,0x30,sizeof value,YES),value,sizeof value);
}
NVN(nvnSamplerBuilderSetBorderColor,void,NVNsamplerBuilder *raw,const float *color){samplerBorderSet(raw,color);}
NVN(nvnSamplerBuilderSetBorderColori,void,NVNsamplerBuilder *raw,const int *color){samplerBorderSet(raw,color);}
NVN(nvnSamplerBuilderSetBorderColorui,void,NVNsamplerBuilder *raw,const uint32_t *color){samplerBorderSet(raw,color);}
NVN(nvnSamplerBuilderSetMaxAnisotropy,void,NVNsamplerBuilder *raw,float value){memcpy(samplerField(raw,0x40,4,YES),&value,4);}
NVN(nvnSamplerBuilderSetReductionFilter,void,NVNsamplerBuilder *raw,NVNsamplerReduction value){int32_t v=value;memcpy(samplerField(raw,0x44,4,YES),&v,4);}

/* Both getter families use the same value projection. The object form reads
 * an immutable host snapshot, never a fabricated native sampler header. */
static void samplerMinMag(SamplerBuilder b,NVNminFilter *min,NVNmagFilter *mag){samplerOutputs(min,4,mag,4,NULL,0);*min=b.p.min;*mag=b.p.mag;}
static void samplerWrap(SamplerBuilder b,NVNwrapMode *s,NVNwrapMode *t,NVNwrapMode *r){samplerOutputs(s,4,t,4,r,4);*s=b.p.wrap[0];*t=b.p.wrap[1];*r=b.p.wrap[2];}
static void samplerClamp(SamplerBuilder b,float *min,float *max){samplerOutputs(min,4,max,4,NULL,0);*min=b.p.lodMin;*max=b.p.lodMax;}
static void samplerCompareValues(SamplerBuilder b,NVNcompareMode *mode,NVNcompareFunc *func){samplerOutputs(mode,4,func,4,NULL,0);*mode=b.p.compareMode;*func=b.p.compareFunc;}
static void samplerBorder(SamplerBuilder b,void *color){textureMemory(color,16,YES);memcpy(color,b.p.border,16);}
#define SAMPLER_GETTERS(Prefix,Type,Read) \
 NVN(Prefix##GetMinMagFilter,void,const Type *raw,NVNminFilter *min,NVNmagFilter *mag){samplerMinMag(Read(raw,8,8),min,mag);} \
 NVN(Prefix##GetWrapMode,void,const Type *raw,NVNwrapMode *s,NVNwrapMode *t,NVNwrapMode *r){samplerWrap(Read(raw,0x10,12),s,t,r);} \
 NVN(Prefix##GetLodClamp,void,const Type *raw,float *min,float *max){samplerClamp(Read(raw,0x1c,8),min,max);} \
 NVN(Prefix##GetLodBias,float,const Type *raw){return Read(raw,0x24,4).p.lodBias;} \
 NVN(Prefix##GetCompare,void,const Type *raw,NVNcompareMode *mode,NVNcompareFunc *func){samplerCompareValues(Read(raw,0x28,8),mode,func);} \
 NVN(Prefix##GetBorderColor,void,const Type *raw,float *color){samplerBorder(Read(raw,0x30,16),color);} \
 NVN(Prefix##GetBorderColori,void,const Type *raw,int *color){samplerBorder(Read(raw,0x30,16),color);} \
 NVN(Prefix##GetBorderColorui,void,const Type *raw,uint32_t *color){samplerBorder(Read(raw,0x30,16),color);} \
 NVN(Prefix##GetMaxAnisotropy,float,const Type *raw){return Read(raw,0x40,4).p.aniso;} \
 NVN(Prefix##GetReductionFilter,NVNsamplerReduction,const Type *raw){return Read(raw,0x44,4).p.reduction;}
SAMPLER_GETTERS(nvnSamplerBuilder,NVNsamplerBuilder,samplerBuilderFields)
SAMPLER_GETTERS(nvnSampler,NVNsampler,samplerSnapshotFields)
#undef SAMPLER_GETTERS

static MTLSamplerAddressMode samplerWrapMode(int value){
    switch(value){
        case NVN_WRAP_MODE_REPEAT:return MTLSamplerAddressModeRepeat;
        case NVN_WRAP_MODE_MIRRORED_REPEAT:return MTLSamplerAddressModeMirrorRepeat;
        case NVN_WRAP_MODE_CLAMP_TO_EDGE:return MTLSamplerAddressModeClampToEdge;
        case NVN_WRAP_MODE_MIRROR_CLAMP_TO_EDGE:return MTLSamplerAddressModeMirrorClampToEdge;
        default:fatal("sampler wrap mode requires unimplemented border semantics");
    }
}
/* Decode the already captured binary32 value through normal-range doubles.
 * A host thread's FPCR.FZ must not turn a nonzero subnormal bias into either
 * an admitted zero or a misleading value=0 error message. */
static double samplerFloatValue(uint32_t bits){
    unsigned exponent=(bits>>23)&255,significand=bits&0x7fffff;
    double value=exponent==255?(significand?NAN:INFINITY):ldexp((double)(significand|(exponent?0x800000:0)),exponent?(int)exponent-150:-149);
    return (bits>>31)?-value:value;
}
/* Object construction can replace this exact primary sampler slot. Builder
 * metadata writes cannot. Retain value records for already copied serials,
 * and do not retire old storage records until the replacement is published. */
static NSArray<NSNumber*> *samplerOutputStorage(NVNsampler *raw){
    size_t bytes=sizeof *raw;
    if(!raw||bytes>UINTPTR_MAX-(uintptr_t)raw||(memoryAccessValidator&&!memoryAccessValidator(raw,bytes,1)))fatal("sampler object memory is not writable");
    validateObjectStorage(raw,bytes);NSMutableArray *replaced=[NSMutableArray new];
    for(NSNumber *key in objectSpans){NSRange span=objectSpans[key].rangeValue;
        if(!memoryOverlap(raw,bytes,(void*)span.location,span.length))continue;
        id existing=objects[key];BOOL exact=span.location==(uintptr_t)raw;
        if(!exact||span.length!=bytes||![existing isKindOfClass:[BWSampler class]]){
            char reason[256];snprintf(reason,sizeof reason,"sampler object storage overlaps incompatible live NVN object existing_class=%s span_bytes=%zu exact_base=%s",existing?class_getName([existing class]):"<none>",(size_t)span.length,exact?"true":"false");fatal(reason);
        }
        [replaced addObject:key];
    }
    texturePrepareWrite(raw,bytes);return replaced;
}
NVN(nvnSamplerInitialize,NVNboolean,NVNsampler *raw,const NVNsamplerBuilder *builder){
    SamplerBuilder b=samplerBuilder(builder);BWDevice *device=get(b.device,[BWDevice class]);BWSamplerParameters p=b.p;
    /* Metal's sampler-state bias is OS26/Apple10. The supported backend has
     * no equivalent transport; retain the exact Builder value and fail here. */
    uint32_t biasBits;memcpy(&biasBits,&p.lodBias,4);
    if(biasBits&UINT32_C(0x7fffffff))return report("sampler LOD bias is not implemented by this Metal backend value=%.9g bits=0x%08x",samplerFloatValue(biasBits),biasBits);
    if(p.reduction!=NVN_SAMPLER_REDUCTION_AVERAGE)return report("sampler reduction filter is not implemented value=%d",p.reduction);
    if(p.min<0||p.min>5||p.mag<0||p.mag>1||p.compareMode<0||p.compareMode>1||p.compareFunc<1||p.compareFunc>8||
       !isfinite(p.lodMin)||!isfinite(p.lodMax)||!isfinite(p.aniso)||(p.aniso!=1&&p.aniso!=2&&p.aniso!=4&&p.aniso!=8&&p.aniso!=16))return report("invalid or unrepresentable sampler state");
    MTLSamplerAddressMode s=samplerWrapMode(p.wrap[0]),t=samplerWrapMode(p.wrap[1]),r=samplerWrapMode(p.wrap[2]);
    NSArray<NSNumber*> *replaced=samplerOutputStorage(raw);
    MTLSamplerDescriptor *descriptor=[MTLSamplerDescriptor new];
    descriptor.minFilter=(p.min&1)?MTLSamplerMinMagFilterLinear:MTLSamplerMinMagFilterNearest;
    descriptor.magFilter=p.mag?MTLSamplerMinMagFilterLinear:MTLSamplerMinMagFilterNearest;
    descriptor.mipFilter=p.min<2?MTLSamplerMipFilterNotMipmapped:p.min<4?MTLSamplerMipFilterNearest:MTLSamplerMipFilterLinear;
    descriptor.sAddressMode=s;descriptor.tAddressMode=t;descriptor.rAddressMode=r;
    descriptor.lodMinClamp=floorf(fminf(15,fmaxf(0,p.lodMin))*256)/256;
    descriptor.lodMaxClamp=floorf(fminf(15,fmaxf(0,fmaxf(p.lodMin,p.lodMax)))*256)/256;
    descriptor.maxAnisotropy=(NSUInteger)p.aniso;
    descriptor.compareFunction=p.compareMode?(MTLCompareFunction)(p.compareFunc-1):MTLCompareFunctionNever;
    BWSampler *sampler=[BWSampler new];sampler.device=device;sampler.parameters=[NSData dataWithBytes:&b length:sizeof b];
    sampler.state=[gpu newSamplerStateWithDescriptor:descriptor];if(!sampler.state)return report("Metal sampler creation failed");
    put(raw,sampler);for(NSNumber *key in replaced)[objectSpans removeObjectForKey:key];return 1;
}
NVN(nvnSamplerCompare,NVNboolean,const NVNsampler *rawA,const NVNsampler *rawB){
    SamplerBuilder a=samplerSnapshot(rawA),b=samplerSnapshot(rawB);BWSamplerParameters p=a.p,q=b.p;
    return a.device==b.device&&p.min==q.min&&p.mag==q.mag&&p.wrap[0]==q.wrap[0]&&p.wrap[1]==q.wrap[1]&&p.wrap[2]==q.wrap[2]&&
      p.lodMin==q.lodMin&&p.lodMax==q.lodMax&&p.lodBias==q.lodBias&&p.compareMode==q.compareMode&&p.compareFunc==q.compareFunc&&
      !memcmp(p.border,q.border,sizeof p.border)&&p.aniso==q.aniso&&p.reduction==q.reduction;
}
NVN(nvnSamplerFinalize,void,NVNsampler *raw){
    BWSampler *sampler=get(raw,[BWSampler class]);
    /* Native Finalize is RET. A normal caller copies the value to its cache
     * before finalizing the source. Keep raw and its immutable registry value;
     * retire only this looked-up value's primary storage, never an alias's. */
    for(NSNumber *key in objectSpans.allKeys){NSRange span=objectSpans[key].rangeValue;
        if(objects[key]==sampler&&span.location==(uintptr_t)raw&&span.length==sizeof *raw)[objectSpans removeObjectForKey:key];
    }
}
