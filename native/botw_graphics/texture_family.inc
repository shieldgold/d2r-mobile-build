/* Texture family port: MIT jhaizhou 5f673ae, dm/gfx/nvn/hle_nvn.c and
 * nvn_mtl.m; see THIRD_PARTY_LICENSE.txt. Base-v0 POD and validation differ
 * from the upstream 1.9 SDK. Keep unsupported GPU semantics explicit. */
static void textureVMAccess(const void *raw,size_t bytes,BOOL writable){
    uintptr_t at=(uintptr_t)raw;if(!bytes)return;if(!raw||bytes>UINTPTR_MAX-at)fatal("texture destination/source address overflow");uintptr_t end=at+bytes;
    while(at<end){vm_address_t region=at;vm_size_t length=0;natural_t depth=0;vm_region_submap_info_data_64_t info;
        for(;;){mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;kern_return_t status=vm_region_recurse_64(mach_task_self(),&region,&length,&depth,(vm_region_recurse_info_t)&info,&count);
            if(status!=KERN_SUCCESS||region>at||!length||at-region>=length)fatal(writable?"texture destination memory is not writable":"texture source memory is not readable");
            if(info.is_submap){depth++;region=at;continue;}
            if(!(info.protection&(writable?VM_PROT_WRITE:VM_PROT_READ)))fatal(writable?"texture destination memory is not writable":"texture source memory is not readable");break;
        }
        size_t span=MIN((size_t)(end-at),(size_t)(length-(at-region)));at+=span;
    }
}
static void textureMemory(const void *raw,size_t bytes,BOOL write){
    if(!raw||bytes>UINTPTR_MAX-(uintptr_t)raw||(memoryAccessValidator&&!memoryAccessValidator(raw,bytes,write)))fatal("texture metadata memory is not fully accessible");
    if(write){validateObjectStorage((void*)raw,bytes);for(NSValue *v in objectSpans.allValues){NSRange r=v.rangeValue;if(memoryOverlap(raw,bytes,(void*)r.location,r.length))fatal("texture metadata overlaps live NVN object");}texturePrepareWrite((void*)raw,bytes);}else textureVMAccess(raw,bytes,NO);
}
static TextureBuilder textureBuilder(const NVNtextureBuilder *raw){textureMemory(raw,BWTextureBuilderBytes,NO);TextureBuilder b={0};memcpy(&b,raw,BWTextureBuilderBytes);return b;}
static TextureBuilder *textureBuilderWrite(NVNtextureBuilder *raw){textureMemory(raw,BWTextureBuilderBytes,YES);return (TextureBuilder*)raw;}
static TextureView textureView(const NVNtextureView *raw){TextureView v={0};if(raw){textureMemory(raw,sizeof v,NO);memcpy(&v,raw,sizeof v);}return v;}
static TextureView *textureViewWrite(NVNtextureView *raw){textureMemory(raw,sizeof(TextureView),YES);return (TextureView*)raw;}
static void textureOut(void *p,size_t n){if(!p||(memoryAccessValidator&&!memoryAccessValidator(p,n,1)))fatal("texture getter output is not writable");texturePrepareWrite(p,n);}
#include "../botw_host/protected_copy.h"
#include "texture_storage_policy.h"
static void textureEnsureMapping(BWTexture *t,const char *reason);
static id<MTLTexture> textureMakeSelected(BWTexture *base,TextureView view,BOOL attachment);
static BWDevice *textureStorageDevice(const NVNdevice *raw);
static BOOL textureColorSelected(const TextureBuilder *b);
static BOOL textureColorLayout(const TextureBuilder *b);
static BOOL textureDepthSelected(const TextureBuilder *b,unsigned *kind);
static BOOL textureDepthLayout(const TextureBuilder *b);
static BOOL textureCompressionGPU(const TextureBuilder *b);
/* Selected views can outlive their descriptor entry or object-table record in
 * recorded commands. Weak tracking finds all wrappers without extending them. */
static NSHashTable<BWTexture *> *textureReferences;
static void textureRememberReference(BWTexture *t){if(!textureReferences)textureReferences=[NSHashTable weakObjectsHashTable];[textureReferences addObject:t];}
static void textureLive(BWTexture *t){
    BWTexture *base=t.base?:t;
    if(!base.pool.live||base.pool.generation!=base.poolGeneration||objects[@(base.poolGeneration)]!=base.pool)fatal("stale texture pool");
    if(!base.pool.device||base.pool.device!=base.device||![objects.allValues containsObject:base.device])fatal("stale texture device");
    if(base.kindPolicy!=base.device.textureKindPolicy)fatal("texture device representation policy changed");
}
NVN(nvnTextureBuilderSetDefaults,void,NVNtextureBuilder *raw){TextureBuilder *b=textureBuilderWrite(raw);memset((char*)b+8,0,0x44);memset((char*)b+0x50,0,0xa);memset((char*)b+0x5c,0,8);b->target=NVN_TEXTURE_TARGET_2D;b->levels=1;for(unsigned i=0;i<4;i++)b->swizzle[i]=i+2;}
NVN(nvnTextureBuilderSetDevice,void,NVNtextureBuilder *b,NVNdevice *d){textureBuilderWrite(b)->device=d;}
#define TEXTURE_BUILDER_FIELD(Name,Type,field) \
 NVN(nvnTextureBuilderSet##Name,void,NVNtextureBuilder *b,Type value){textureBuilderWrite(b)->field=value;} \
 NVN(nvnTextureBuilderGet##Name,Type,const NVNtextureBuilder *raw){TextureBuilder b=textureBuilder(raw);return (Type)b.field;}
NVN(nvnTextureBuilderSetFlags,void,NVNtextureBuilder *raw,int value){textureBuilderWrite(raw)->flags=value;}
NVN(nvnTextureBuilderGetFlags,NVNtextureFlags,const NVNtextureBuilder *raw){return (NVNtextureFlags)textureBuilder(raw).flags;}
TEXTURE_BUILDER_FIELD(Target,NVNtextureTarget,target)
TEXTURE_BUILDER_FIELD(Width,int,width)
TEXTURE_BUILDER_FIELD(Height,int,height)
TEXTURE_BUILDER_FIELD(Depth,int,depth)
TEXTURE_BUILDER_FIELD(Levels,int,levels)
TEXTURE_BUILDER_FIELD(Format,NVNformat,format)
TEXTURE_BUILDER_FIELD(Samples,int,samples)
TEXTURE_BUILDER_FIELD(DepthStencilMode,NVNtextureDepthStencilMode,depthMode)
TEXTURE_BUILDER_FIELD(PackagedTextureData,const void*,packagedData)
TEXTURE_BUILDER_FIELD(GLTextureName,uint32_t,glName)
#undef TEXTURE_BUILDER_FIELD
NVN(nvnTextureBuilderSetStride,void,NVNtextureBuilder *b,ptrdiff_t value){textureBuilderWrite(b)->stride=(uint32_t)value;}
NVN(nvnTextureBuilderGetStride,ptrdiff_t,const NVNtextureBuilder *raw){return textureBuilder(raw).stride;}
NVN(nvnTextureBuilderSetSize1D,void,NVNtextureBuilder *raw,int w){TextureBuilder *b=textureBuilderWrite(raw);b->width=w;b->height=b->depth=1;}
NVN(nvnTextureBuilderSetSize2D,void,NVNtextureBuilder *raw,int w,int h){TextureBuilder *b=textureBuilderWrite(raw);b->width=w;b->height=h;b->depth=1;}
NVN(nvnTextureBuilderSetSize3D,void,NVNtextureBuilder *raw,int w,int h,int d){TextureBuilder *b=textureBuilderWrite(raw);b->width=w;b->height=h;b->depth=d;}
NVN(nvnTextureBuilderSetStorage,void,NVNtextureBuilder *raw,NVNmemoryPool *p,ptrdiff_t offset){TextureBuilder *b=textureBuilderWrite(raw);b->pool=p;b->offset=offset;}
NVN(nvnTextureBuilderGetMemoryPool,NVNmemoryPool*,const NVNtextureBuilder *raw){return textureBuilder(raw).pool;}
NVN(nvnTextureBuilderGetMemoryOffset,ptrdiff_t,const NVNtextureBuilder *raw){return textureBuilder(raw).offset;}
static void textureSwizzleOut(const uint32_t *s,NVNtextureSwizzle *r,NVNtextureSwizzle *g,NVNtextureSwizzle *b,NVNtextureSwizzle *a){textureOut(r,4);textureOut(g,4);textureOut(b,4);textureOut(a,4);*r=s[0];*g=s[1];*b=s[2];*a=s[3];}
NVN(nvnTextureBuilderSetSwizzle,void,NVNtextureBuilder *raw,NVNtextureSwizzle r,NVNtextureSwizzle g,NVNtextureSwizzle b,NVNtextureSwizzle a){TextureBuilder *t=textureBuilderWrite(raw);t->swizzle[0]=r;t->swizzle[1]=g;t->swizzle[2]=b;t->swizzle[3]=a;}
NVN(nvnTextureBuilderGetSwizzle,void,const NVNtextureBuilder *raw,NVNtextureSwizzle *r,NVNtextureSwizzle *g,NVNtextureSwizzle *b,NVNtextureSwizzle *a){TextureBuilder t=textureBuilder(raw);textureSwizzleOut(t.swizzle,r,g,b,a);}
NVN(nvnTextureBuilderSetPackagedTextureLayout,void,NVNtextureBuilder *raw,const NVNpackagedTextureLayout *layout){textureMemory(layout,4,NO);uint32_t bits;memcpy(&bits,layout,4);textureBuilderWrite(raw)->packaged=1|((bits&0xfff)<<4);}
static int texturePackagedHeight(const TextureBuilder *b){return (b->packaged&1)?((b->packaged>>4)&15)+1:0;}
static nvn_texdesc textureDescriptor(const TextureBuilder *b){return (nvn_texdesc){b->target,b->format,b->flags|((b->flags&0x40)?0x10:0),b->samples,b->width,b->height,b->depth,b->levels,b->stride,texturePackagedHeight(b)};}
static int textureLinearLayout(const TextureBuilder *b,BWLinearLayout *layout){
    uint32_t pixel;
    if(b->target!=NVN_TEXTURE_TARGET_2D||b->depth!=1||b->levels!=1||b->samples<0||b->samples>1||b->width<1||b->height<1||!linearColorFormat(b->format,&pixel))return 0;
    BWDevice *device=get(b->device,[BWDevice class]);BWLinearPolicy policy=device.linearPolicy;
    return bw_linear_layout(&policy,b->flags,b->width,b->height,b->stride,nvn_fmt_bpb(b->format,NULL,NULL),layout);
}
static size_t textureBytes(const TextureBuilder *b){
    unsigned depthKind=0;BOOL depthStorage=bw_texture_depth_storage_kind(b->format,b->flags,b->samples,&depthKind);
    if((b->flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&!(b->flags&0x50)&&bw_texture_depth_storage_format(b->format)){
        if(b->glName)return 0; /* Preserve original GL-before-provider Size order. */
        if(!textureDepthLayout(b))return 0;
        depthStorage=textureDepthSelected(b,&depthKind);
    }
    if(!(b->flags&0x50)&&bw_texture_depth_storage_format(b->format)&&!depthStorage)return 0;
    if(depthStorage&&b->glName)return 0; /* Original Builder Size bf1d4..bf1e0. */
    if((b->flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&!(b->flags&0x50)&&bw_texture_ordinary_color_format(b->format)){
        if(b->glName)return 0; /* Original Builder Size short-circuits GL before provider/layout. */
        if(!textureColorLayout(b))return 0;
    }
    BOOL array=b->target==NVN_TEXTURE_TARGET_2D_ARRAY;
    if((b->target!=NVN_TEXTURE_TARGET_2D&&!array)||b->width<1||b->height<1||b->width>16384||b->height>16384||b->depth<1||b->depth>(array?2048:1)||b->levels<1||b->levels>15||b->samples<0||b->samples>1||texturePackagedHeight(b)>6||(b->packaged&0xff00)||(array&&(b->flags&(0x10|0x40))))return 0;
    unsigned dimension=MAX(b->width,b->height),levels=1;while(dimension>1){dimension>>=1;levels++;}if((unsigned)b->levels>levels)return 0;
    if(b->flags&(0x10|0x40)){BWLinearLayout layout;return textureLinearLayout(b,&layout)?layout.size:0;}
    nvn_texdesc d=textureDescriptor(b);nvn_texlayout l;nvn_tex_layout(&d,&l);
    if(depthStorage){textureStorageDevice(b->device);size_t rounded=0;
        return bw_texture_storage_round_size(l.size,bw_pool_policy().mappingPageBytes,&rounded)?rounded:0;}
    return l.size;
}
static size_t textureStorageSizeResult(const TextureBuilder *b,size_t result){
    if(!result&&textureInitContext.zeroSizeDumps<BW_TEXTURE_LIFECYCLE_CAPACITY){
        unsigned ordinal=++textureInitContext.zeroSizeDumps;
        /* Snapshot-only normal API context. Do not report, read a provider or
         * inspect pointed-to packaged data to explain an unchanged zero. */
        fprintf(stderr,"[botw graphics] texture_storage_size_zero ordinal=%u target=%u flags=0x%x format=0x%x width=%d height=%d depth=%d levels=%d samples=%d stride=%u gl_name_nonzero=%s packaged_data_nonnull=%s\n",ordinal,(unsigned)b->target,(unsigned)b->flags,(unsigned)b->format,b->width,b->height,b->depth,b->levels,b->samples,b->stride,b->glName?"true":"false",b->packagedData?"true":"false");
    }
    return result;
}
NVN(nvnTextureBuilderGetStorageSize,size_t,const NVNtextureBuilder *raw){TextureBuilder b=textureBuilder(raw);size_t result;if(b.target==NVN_TEXTURE_TARGET_BUFFER){int bytes=nvn_fmt_bpb(b.format,NULL,NULL);result=b.width>0&&bytes?(size_t)b.width*bytes:0;}else result=textureBytes(&b);return textureStorageSizeResult(&b,result);}
/* Original base-v0 0x34-byte format records: +0x30 is nonzero and +0xe/+0xf
 * depth/stencil bytes are zero for exactly these 113 declared color formats.
 * This is independent of Metal's narrower format/creation capability. */
static BOOL textureOrdinaryStorageFormat(uint32_t format){return bw_texture_ordinary_color_format(format);}
static BWDevice *textureStorageDevice(const NVNdevice *raw){
    /* Establish exact live-object provenance before reading a guest header. */
    BWDevice *provider=nil;
    for(NSNumber *key in objectSpans){NSRange span=objectSpans[key].rangeValue;id object=objects[key];
        if(span.location==(uintptr_t)raw&&[object isKindOfClass:[BWDevice class]]){provider=object;break;}}
    if(!provider)fatal("object lifetime/type expected=BWDevice storage-class provider is not registered");
    textureMemory(raw,sizeof(uint64_t),NO);
    if(get(raw,[BWDevice class])!=provider)fatal("object lifetime/type expected=BWDevice storage-class provider identity mismatch");
    return provider;
}
static BOOL textureColorSelected(const TextureBuilder *b){
    BWDevice *device=textureStorageDevice(b->device);
    if(device.textureKindPolicy!=BW_TEXTURE_KIND_METAL_PLAIN_COLOR&&device.textureKindPolicy!=BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH)fatal("texture device representation policy is unavailable");
    return bw_texture_plain_color_selected(device.textureKindPolicy,b->format);
}
static BOOL textureColorLayout(const TextureBuilder *b){
    /* Reject unsupported layout inputs without adding provider reads. */
    if(!bw_texture_color_layout_inputs(b->format,b->flags,b->samples))return NO;
    return textureColorSelected(b);
}
static BOOL textureDepthSelected(const TextureBuilder *b,unsigned *kind){
    unsigned available=0;if(!(b->flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)||(b->flags&0x50)||!bw_texture_plain_depth_kind(b->format,b->samples,&available))return NO;
    BWDevice *device=textureStorageDevice(b->device);
    if(device.textureKindPolicy!=BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH)fatal("texture device plain-depth representation policy is unavailable");
    return bw_texture_plain_depth_selected(device.textureKindPolicy,b->format,b->flags,b->samples,kind);
}
static BOOL textureDepthLayout(const TextureBuilder *b){
    if(!bw_texture_plain_depth_layout_inputs(b->format,b->flags,b->samples))return NO;
    unsigned kind=0;return textureDepthSelected(b,&kind);
}
static BOOL textureCompressionGPU(const TextureBuilder *b){
    if(bw_texture_depth_storage_format(b->format)){
        if(!bw_texture_plain_depth_gpu_inputs(b->format,b->target,b->flags,b->samples))return NO;
        unsigned kind=0;return textureDepthSelected(b,&kind);
    }
    return textureColorLayout(b);
}
/* Base-v0 returns 0x100 | rawKind. BUFFER and flags&0x50 take rawKind=0
 * before accessing the device. Otherwise GK100/GM200 share ChooseKind+0x68:
 * uncompressed color is the pure 0x316c0 leaf returning 0xfe. The caller
 * still requires a live device provider; no pool or Metal creation is needed. */
static NVNstorageClass textureMetadataStorageClass(const TextureBuilder *b){
    if(b->target==NVN_TEXTURE_TARGET_BUFFER||(b->flags&0x50))return 0x100;
    unsigned depthKind=0;if(bw_texture_depth_storage_kind(b->format,b->flags,b->samples,&depthKind)||
        (bw_texture_depth_storage_format(b->format)&&(b->flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&textureDepthSelected(b,&depthKind))){
        textureStorageDevice(b->device);return 0x100|depthKind;
    }
    if(textureOrdinaryStorageFormat(b->format)&&(!(b->flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)||textureColorSelected(b))){
        textureStorageDevice(b->device);return 0x1fe;
    }
    char detail[256];snprintf(detail,sizeof detail,"texture storage class requires unimplemented native ChooseKind target=%u flags=0x%x format=0x%x samples=%d source=base-v0-storage-metadata",b->target,b->flags,b->format,b->samples);fatal(detail);
}
NVN(nvnTextureBuilderGetStorageClass,NVNstorageClass,const NVNtextureBuilder *raw){TextureBuilder b=textureBuilder(raw);return textureMetadataStorageClass(&b);}
NVN(nvnTextureBuilderGetStorageAlignment,size_t,const NVNtextureBuilder *raw){TextureBuilder b=textureBuilder(raw);if(b.flags&(0x10|0x40)){BWDevice *device=get(b.device,[BWDevice class]);BWLinearPolicy policy=device.linearPolicy;return bw_linear_alignment(&policy,b.flags);}if(b.target==NVN_TEXTURE_TARGET_BUFFER){int bytes=nvn_fmt_bpb(b.format,NULL,NULL);return bytes==12?4:bytes;}
    if((b.flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&textureOrdinaryStorageFormat(b.format)&&textureColorSelected(&b))return 512;
    if(bw_texture_depth_storage_format(b.format)){(void)textureMetadataStorageClass(&b);return bw_pool_policy().mappingPageBytes;}
    if(!textureBytes(&b))return 0;nvn_texdesc d=textureDescriptor(&b);nvn_texlayout l;nvn_tex_layout(&d,&l);return l.align;}
NVN(nvnTextureViewSetDefaults,void,NVNtextureView *raw){memset(textureViewWrite(raw),0,sizeof(TextureView));}
NVN(nvnTextureViewSetLevels,void,NVNtextureView *raw,int base,int count){TextureView *v=textureViewWrite(raw);v->flags|=1;v->baseLevel=base;v->numLevels=count;}
NVN(nvnTextureViewSetLayers,void,NVNtextureView *raw,int base,int count){TextureView *v=textureViewWrite(raw);v->flags|=2;v->minLayer=base;v->numLayers=count;}
#define TEXTURE_VIEW_FIELD(Name,Type,field,bit) \
 NVN(nvnTextureViewSet##Name,void,NVNtextureView *raw,Type value){TextureView *v=textureViewWrite(raw);v->flags|=bit;v->field=value;} \
 NVN(nvnTextureViewGet##Name,NVNboolean,const NVNtextureView *raw,Type *value){TextureView v=textureView(raw);textureOut(value,sizeof *value);*value=(Type)v.field;return !!(v.flags&bit);}
TEXTURE_VIEW_FIELD(Format,NVNformat,format,4)
TEXTURE_VIEW_FIELD(DepthStencilMode,NVNtextureDepthStencilMode,depthMode,16)
TEXTURE_VIEW_FIELD(Target,NVNtextureTarget,target,32)
#undef TEXTURE_VIEW_FIELD
NVN(nvnTextureViewGetLevels,NVNboolean,const NVNtextureView *raw,int *base,int *count){TextureView v=textureView(raw);textureOut(base,4);textureOut(count,4);*base=v.baseLevel;*count=v.numLevels;return !!(v.flags&1);}
NVN(nvnTextureViewGetLayers,NVNboolean,const NVNtextureView *raw,int *base,int *count){TextureView v=textureView(raw);textureOut(base,4);textureOut(count,4);*base=v.minLayer;*count=v.numLayers;return !!(v.flags&2);}
NVN(nvnTextureViewSetSwizzle,void,NVNtextureView *raw,NVNtextureSwizzle r,NVNtextureSwizzle g,NVNtextureSwizzle b,NVNtextureSwizzle a){TextureView *v=textureViewWrite(raw);v->flags|=8;v->swizzle[0]=r;v->swizzle[1]=g;v->swizzle[2]=b;v->swizzle[3]=a;}
NVN(nvnTextureViewGetSwizzle,NVNboolean,const NVNtextureView *raw,NVNtextureSwizzle *r,NVNtextureSwizzle *g,NVNtextureSwizzle *b,NVNtextureSwizzle *a){TextureView v=textureView(raw);textureSwizzleOut(v.swizzle,r,g,b,a);return !!(v.flags&8);}
NVN(nvnTextureViewCompare,NVNboolean,const NVNtextureView *a,const NVNtextureView *b){TextureView x=textureView(a),y=textureView(b);if((x.flags&63)!=(y.flags&63))return 0;if((x.flags&1)&&(x.baseLevel!=y.baseLevel||x.numLevels!=y.numLevels))return 0;if((x.flags&2)&&(x.minLayer!=y.minLayer||x.numLayers!=y.numLayers))return 0;if((x.flags&4)&&x.format!=y.format)return 0;if((x.flags&8)&&memcmp(x.swizzle,y.swizzle,sizeof x.swizzle))return 0;if((x.flags&16)&&x.depthMode!=y.depthMode)return 0;return !(x.flags&32)||x.target==y.target;}
static int textureMappingKind(TextureBuilder b,BWPool *pool){
    if(pool.kind==BW_POOL_PHYSICAL)fatal("physical pool cannot be a direct texture GPU resource");
    if(pool.kind!=BW_POOL_VIRTUAL)return -1;
    if((b.flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&!textureCompressionGPU(&b))fatal("virtual texture hardware compressed kind is not implemented");
    if(b.flags&0x50)return 0;
    if(b.format==NVN_FORMAT_DEPTH16||b.format==NVN_FORMAT_DEPTH32F){
        if(!bw_texture_plain_depth_gpu_inputs(b.format,b.target,b.flags,b.samples))fatal("virtual plain-depth textures require 2D flags=8 raw samples=0");
        unsigned kind=0;
        if(textureDepthSelected(&b,&kind))return (int)kind;
    }
    if(!textureOrdinaryStorageFormat(b.format))fatal("virtual texture depth/non-color kind is not implemented");
    return 0xfe;
}
static BOOL textureMappingEqual(BWPoolRange *a,BWPoolRange *b){
    return a&&b&&a.pool==b.pool&&a.physical==b.physical&&a.physicalID==b.physicalID&&
        a.physicalOffset==b.physicalOffset&&a.length==b.length&&a.rawKind==b.rawKind&&
        a.mappingGeneration==b.mappingGeneration&&a.buffer==b.buffer&&a.metalOffset==b.metalOffset;
}
static void textureCheckStorageAlias(BWTexture *t,BWPoolRange *mapping){
    if(!t.blockLinear){nvn_texown_hit hit;if(nvn_texown_find(mapping.canonical,t.layout.size,&hit))fatal("linear storage authority belongs to live/retired private texture");}
    for(id object in objects.allValues)if([object isKindOfClass:[BWTexture class]]){
        BWTexture *other=object;if(other==t||other.base||(t.blockLinear&&other.blockLinear))continue;
        if(!other.pool.live||other.pool.generation!=other.poolGeneration)continue;
        /* An unrelated base may currently have a hole. Never compare a stale
         * cached physical range, nor make that hole fail this resource. */
        const char *failure=NULL;BWPoolIdentity *current=poolTryIdentity(other.pool,other.offset,other.layout.size,-1,&failure);
        if(current&&memoryOverlap(mapping.canonical,t.layout.size,current.canonical,other.layout.size))fatal("linear texture storage overlaps another live texture base");
    }
}
static id<MTLTexture> textureMakeBase(BWTexture *t,BWPoolRange *mapping){
    uint32_t format;uint8_t swizzle[4];if(nvn_fmt_to_mtl(t.format,&format,swizzle)!=1)fatal("texture format conversion changed");
    MTLTextureDescriptor *desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:(MTLPixelFormat)format width:t.width height:t.height mipmapped:t.descriptor.levels>1];desc.mipmapLevelCount=t.descriptor.levels;
    if(t.metadata.target==NVN_TEXTURE_TARGET_2D_ARRAY){desc.textureType=MTLTextureType2DArray;desc.arrayLength=t.metadata.depth;}
    desc.storageMode=t.blockLinear?MTLStorageModePrivate:mapping.buffer.storageMode;desc.usage=MTLTextureUsageShaderRead|MTLTextureUsagePixelFormatView;
    if((t.blockLinear&&!(t.format>=0x79&&t.format<=0x94))||(t.metadata.flags&0x40))desc.usage|=MTLTextureUsageRenderTarget;
    if(t.blockLinear)return [gpu newTextureWithDescriptor:desc];
    if(!mapping.physical.coherent)fatal("linear textures require coherent no-copy pool storage");
    NSUInteger alignment=[gpu minimumLinearTextureAlignmentForPixelFormat:(MTLPixelFormat)format];
    if(!alignment||t.stride%alignment||mapping.metalOffset%alignment)fatal("Invalid linear texture Metal backing alignment");
    return [mapping.buffer newTextureWithDescriptor:desc offset:mapping.metalOffset bytesPerRow:t.stride];
}
static void textureEnsureMapping(BWTexture *input,const char *reason){
    textureLive(input);BWTexture *t=input.base?:input;
    BWPoolRange *current=poolResolve(t.pool,t.offset,t.layout.size,textureMappingKind(t.metadata,t.pool),reason);
    if(!textureMappingEqual(t.mapping,current)){
        if(t.gpuWritten&&t.blockLinear)fatal("private texture storage authority prevents mapping rebuild");
        textureCheckStorageAlias(t,current);id<MTLTexture> resource=textureMakeBase(t,current);if(!resource)fatal("Metal texture creation failed");
        /* Publish only after the new real resource exists. Old content hashes
         * and certificates describe the old physical generation. */
        nvn_texflush_forget(t.identity);t.uploadHash=nil;t.mapping=current;t.texture=resource;t.texture.label=t.label;
    }
    if(input.base&&input.mapping!=t.mapping){input.texture=textureMakeSelected(t,input.selection,input.selectedAttachment);input.mapping=t.mapping;}
}
static void *textureCanonical(BWTexture *input){textureEnsureMapping(input,"texture canonical storage");BWTexture *t=input.base?:input;return t.mapping.canonical;}
static void textureCheckBufferAuthority(const void *canonical,size_t bytes){
    nvn_texown_hit hit;if(nvn_texown_find(canonical,bytes,&hit))fatal("buffer address aliases private texture storage authority");
}
static void textureInvalidateBufferCaches(const void *canonical,size_t bytes){
    for(id object in objects.allValues)if([object isKindOfClass:[BWTexture class]]){
        BWTexture *t=object;if(t.base||!t.pool.live||t.pool.generation!=t.poolGeneration)continue;
        const char *failure=NULL;BWPoolIdentity *current=poolTryIdentity(t.pool,t.offset,t.layout.size,-1,&failure);
        if(current&&memoryOverlap(canonical,bytes,current.canonical,t.layout.size)){t.uploadHash=nil;nvn_texflush_forget(t.identity);}
    }
}
static void textureRetirePoolReferences(BWPool *pool){
    /* This hook follows successful dirty retirement. No snapshot/set allocation
     * may turn it into a fallible half-retirement. Clear selected views first,
     * while their base still exposes the physical identity needed to match. */
    for(BWTexture *t in textureReferences)if(t.base&&(t.pool==pool||t.mapping.physical==pool||t.base.mapping.physical==pool)){
        t.texture=nil;t.mapping=nil;t.uploadHash=nil;
    }
    for(BWTexture *t in textureReferences)if(!t.base&&(t.pool==pool||t.mapping.physical==pool)){
        if(t.blockLinear&&t.gpuWritten)nvn_texown_retire(t.identity);
        t.texture=nil;t.mapping=nil;t.uploadHash=nil;nvn_texflush_forget(t.identity);
    }
}
/* Existing entrypoint-error context from the validated Builder snapshot only.
 * No address, texture data, extra validator, or VM query is evaluated here. */
static int textureInitializeRejected(BWTextureInitToken attempt,const TextureBuilder *b,const char *reason){
    return textureInitReturnedFalse(attempt,report("%s target=%u flags=0x%x format=0x%x samples=%d",reason,(unsigned)b->target,(unsigned)b->flags,(unsigned)b->format,b->samples));
}
NVN(nvnTextureInitialize,NVNboolean,NVNtexture *raw,const NVNtextureBuilder *builder){
    textureInitEntry();TextureBuilder snapshot=textureBuilder(builder);const TextureBuilder *b=&snapshot;BWTextureInitToken attempt=textureInitAttempt(raw,b);
    if((b->flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&!(b->flags&0x50)&&!textureCompressionGPU(b))return textureInitializeRejected(attempt,b,"Texture hardware compression/layout request is not implemented for this format/flags/samples");
    BWDevice *device=get(b->device,[BWDevice class]);
    if(!b->pool||!textureBytes(b)||(b->flags&~(0x10|0x40|NVN_TEXTURE_FLAGS_DISPLAY|NVN_TEXTURE_FLAGS_COMPRESSIBLE|NVN_TEXTURE_FLAGS_IMAGE))||b->glName||b->packagedData)return textureInitReturnedFalse(attempt,report("Unsupported texture dimension/flags/samples/levels/layout/linear/GL/packaged data"));
    uint32_t format=0;uint8_t swz[4];if(nvn_fmt_to_mtl(b->format,&format,swz)!=1||(b->format>=0x41&&b->format<=0x50))return textureInitializeRejected(attempt,b,"Texture format requires an unimplemented conversion/BC decoder");
    if(b->format>=NVN_FORMAT_RGBA_ASTC_4x4&&b->format<=NVN_FORMAT_RGBA_ASTC_12x12_SRGB&&!supportsASTC())return textureInitReturnedFalse(attempt,report("ASTC textures require Metal Apple2 family support"));
    for(unsigned i=0;i<4;i++)if(swz[i]!=i+NVN_SWZ_R)return textureInitReturnedFalse(attempt,report("Texture component conversion is not implemented"));
    BWPool *pool=get(b->pool,[BWPool class]);size_t size=textureBytes(b);if(!pool.live||pool.device!=device)return textureInitReturnedFalse(attempt,report("texture device/pool mismatch"));
    if(!size||b->offset<0||!range(b->offset,size,pool.size))return textureInitReturnedFalse(attempt,report("Texture storage bounds"));
    unsigned depthKind=0;if((bw_texture_depth_storage_kind(b->format,b->flags,b->samples,&depthKind)||
        ((b->flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&bw_texture_depth_storage_format(b->format)&&textureDepthSelected(b,&depthKind)))&&
        (size_t)b->offset%bw_pool_policy().mappingPageBytes)return textureInitReturnedFalse(attempt,report("Texture depth storage offset is not aligned to native mapping page"));
    BOOL linear=!!(b->flags&(0x10|0x40));BWLinearLayout linearLayout={0};
    if(linear){
        if(pool.kind!=BW_POOL_VIRTUAL&&!pool.coherent)return textureInitReturnedFalse(attempt,report("linear textures require coherent no-copy pool storage"));
        if(!textureLinearLayout(b,&linearLayout)||(size_t)b->offset%linearLayout.alignment)return textureInitReturnedFalse(attempt,report("Invalid linear texture stride/offset/alignment"));
        NSUInteger alignment=[gpu minimumLinearTextureAlignmentForPixelFormat:(MTLPixelFormat)format];
        if(!alignment||linearLayout.stride%alignment)return textureInitReturnedFalse(attempt,report("Invalid linear texture Metal backing alignment"));
    }
    if(memoryAccessValidator&&!memoryAccessValidator(raw,sizeof *raw,1))return textureInitReturnedFalse(attempt,report("texture object memory is not writable"));texturePrepareWrite(raw,sizeof *raw);validateObjectStorage(raw,sizeof *raw);
    for(NSValue *v in objectSpans.allValues){NSRange r=v.rangeValue;if(memoryOverlap(raw,sizeof *raw,(void*)r.location,r.length))return textureInitReturnedFalse(attempt,report("texture object overlaps live NVN object"));}
    BWTexture *t=[BWTexture new];t.metadata=*b;t.storageSize=size;t.descriptor=textureDescriptor(b);nvn_texlayout layout;nvn_texdesc td=t.descriptor;nvn_tex_layout(&td,&layout);t.layout=layout;
    if(linear){td.stride=(uint32_t)linearLayout.stride;t.descriptor=td;layout.size=layout.layer_size=linearLayout.size;layout.align=(uint32_t)linearLayout.alignment;t.layout=layout;}
    t.blockLinear=!(td.flags&0x10);t.depth=b->format>=NVN_FORMAT_DEPTH16&&b->format<=NVN_FORMAT_DEPTH32F_STENCIL8;
    if(t.depth&&b->format!=NVN_FORMAT_DEPTH16&&b->format!=NVN_FORMAT_DEPTH32F)return textureInitReturnedFalse(attempt,report("Combined depth/stencil storage not implemented"));
    if(t.depth&&b->target==NVN_TEXTURE_TARGET_2D_ARRAY)return textureInitReturnedFalse(attempt,report("Array depth texture semantics are not yet validated"));
    t.pool=pool;t.device=device;t.kindPolicy=device.textureKindPolicy;t.poolGeneration=pool.generation;t.rawPool=b->pool;t.offset=b->offset;t.stride=linear?linearLayout.stride:b->stride;t.width=b->width;t.height=b->height;t.format=b->format;
    (void)textureMappingKind(*b,pool);
    /* Virtual initialization records metadata even before MapVirtual. Actual
     * GPU use resolves the current map and creates the matching real resource. */
    if(pool.kind!=BW_POOL_VIRTUAL){BWPoolRange *mapping=poolResolve(pool,t.offset,t.layout.size,-1,"texture initialization");textureCheckStorageAlias(t,mapping);t.texture=textureMakeBase(t,mapping);if(!t.texture)return textureInitReturnedFalse(attempt,report("Metal texture creation failed"));t.mapping=mapping;}
    put(raw,t);t.identity=((BWObject*)raw)->serial;textureInitPublished(attempt,t.identity);textureRememberReference(t);return 1;
}
static char texturePendingKey;
static NSMutableDictionary *texturePendingRecord(BWTexture *t,id<MTLCommandBuffer> cb,BOOL create){
    NSMutableDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);if(!records&&create){records=[NSMutableDictionary new];objc_setAssociatedObject(cb,&texturePendingKey,records,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}
    NSMutableDictionary *record=records[@(t.identity)];if(!record&&create){record=[@{@"texture":t} mutableCopy];records[@(t.identity)]=record;}return record;
}
static void textureAuthority(BWTexture *t,id<MTLCommandBuffer> cb){
    const void *storage=textureCanonical(t);nvn_texown_hit hit;
    if(nvn_texown_find(storage,t.layout.size,&hit)&&(hit.retired||hit.serial!=t.identity))fatal("texture storage authority belongs to another live/retired Metal resource");
    NSDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);
    for(NSDictionary *record in records.allValues)if([record[@"gpu"] boolValue]){BWTexture *other=record[@"texture"];if(other!=t&&(t.blockLinear||other.blockLinear)&&memoryOverlap(storage,t.layout.size,textureCanonical(other),other.layout.size))fatal("texture storage authority conflicts with pending GPU writes");}
}
static char textureBufferWritesKey;
static void textureBufferCopy(id<MTLCommandBuffer> cb,const void *source,const void *destination,size_t bytes){
    nvn_texown_hit hit;if(nvn_texown_find(source,bytes,&hit)||nvn_texown_find(destination,bytes,&hit))fatal("buffer address aliases private texture storage authority");
    NSDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);for(NSDictionary *record in records.allValues)if([record[@"gpu"] boolValue]){BWTexture *t=record[@"texture"];const void *storage=textureCanonical(t);if(t.blockLinear&&(memoryOverlap(source,bytes,storage,t.layout.size)||memoryOverlap(destination,bytes,storage,t.layout.size)))fatal("buffer address aliases private texture pending GPU writes");}
    NSMutableArray *writes=objc_getAssociatedObject(cb,&textureBufferWritesKey);if(!writes){writes=[NSMutableArray new];objc_setAssociatedObject(cb,&textureBufferWritesKey,writes,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}[writes addObject:[NSValue valueWithRange:NSMakeRange((NSUInteger)destination,bytes)]];
}
static void texturePrepareCommit(id<MTLCommandBuffer> cb){
    NSDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);
    for(NSDictionary *record in records.allValues)if([record[@"gpu"] boolValue]){BWTexture *t=record[@"texture"];if(t.blockLinear&&nvn_texown_claim_checked(t.identity,textureCanonical(t),t.layout.size))fatal("texture storage authority allocation failed");}
}
/* Encoding is speculative: no cache certificate, content hash or GPU-written
 * state becomes visible until this exact command buffer has been committed. */
static void texturePublishCommitted(id<MTLCommandBuffer> cb){
    NSDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);if(!records.count)return;
    for(NSDictionary *record in records.allValues){BWTexture *t=record[@"texture"];if(record[@"hash"])t.uploadHash=record[@"hash"];if([record[@"gpu"] boolValue])t.gpuWritten=YES;if(record[@"epoch"])t.drawEpoch=[record[@"epoch"] unsignedLongLongValue];}
    for(NSDictionary *record in records.allValues)if(record[@"hash"]){BWTexture *t=record[@"texture"];const void *storage=textureCanonical(t);
        if(nvn_texflush_note_checked(t.identity,storage,t.layout.layer_size,t.layout.layers,0,t.layout.layers,1))fatal("texture upload coherence certificate allocation failed");
        if(![digest(storage,t.layout.size) isEqual:record[@"hash"]])nvn_texflush_note_checked(t.identity,storage,t.layout.layer_size,t.layout.layers,0,t.layout.layers,0);
    }
    objc_setAssociatedObject(cb,&texturePendingKey,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}
static void uploadTexture(BWTexture *input,id<MTLCommandBuffer> cb){
    textureEnsureMapping(input,"texture upload");BWTexture *t=input.base?:input;poolRetainForGPU(cb,t.mapping,NO);textureAuthority(t,cb);if(!t.blockLinear)return;
    const void *poolStorage=t.mapping.canonical;
    NSArray *bufferWrites=objc_getAssociatedObject(cb,&textureBufferWritesKey);for(NSValue *value in bufferWrites){NSRange r=value.rangeValue;if(memoryOverlap(poolStorage,t.layout.size,(const void*)r.location,r.length))fatal("private texture import aliases pending buffer writes");}
    NSMutableData *snapshot=[NSMutableData dataWithLength:t.layout.size];if(!snapshot)fatal("texture pool snapshot allocation failed");
    if((memoryAccessValidator&&!memoryAccessValidator(poolStorage,t.layout.size,0))||botw_protected_vm_read(snapshot.mutableBytes,poolStorage,t.layout.size)!=KERN_SUCCESS)fatal("texture pool storage is not readable");
    const void *source=snapshot.bytes;NSData *hash=digest(source,t.layout.size);
    NSMutableDictionary *pending=texturePendingRecord(t,cb,NO);NSData *expected=pending[@"hash"]?:t.uploadHash;
    if(t.gpuWritten||[pending[@"gpu"] boolValue]){if(![hash isEqual:expected])fatal("texture CPU storage changed outside host writes; GPU content cannot be reconciled");return;}
    if([hash isEqual:expected])return;
    for(unsigned layer=0;layer<t.layout.layers;layer++)for(unsigned level=0;level<t.descriptor.levels;level++){
        uint32_t row,rows,slices,bpb;uint64_t base;unsigned th,td;nvn_texdesc d=t.descriptor;
        if(nvn_deswizzle_geom(&d,level,layer,&row,&rows,&slices,&base,&th,&td,&bpb)||slices!=1)fatal("block-linear texture geometry");
        size_t pitch=(row+255)&~255u;id<MTLBuffer> staging=[gpu newBufferWithLength:pitch*rows options:MTLResourceStorageModeShared];if(!staging||nvn_deswizzle(&d,level,layer,source,staging.contents,pitch))fatal("block-linear texture decode");
        id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];if(!blit)fatal("block-linear texture upload encoder allocation failed");
        @try{[blit copyFromBuffer:staging sourceOffset:0 sourceBytesPerRow:pitch sourceBytesPerImage:pitch*rows sourceSize:MTLSizeMake(MAX(1,t.width>>level),MAX(1,t.height>>level),1) toTexture:t.texture destinationSlice:layer destinationLevel:level destinationOrigin:MTLOriginMake(0,0,0)];}@finally{[blit endEncoding];}
    }
    texturePendingRecord(t,cb,YES)[@"hash"]=hash;
}
static BWTexture *textureObject(const NVNtexture *raw){BWTexture *t=get(raw,[BWTexture class]);textureLive(t);return t;}
NVN(nvnTextureGetStorageClass,NVNstorageClass,const NVNtexture *raw){TextureBuilder b=textureObject(raw).metadata;return textureMetadataStorageClass(&b);}
NVN(nvnTextureFinalize,void,NVNtexture *raw){BWTexture *t=get(raw,[BWTexture class]);if(memoryAccessValidator&&!memoryAccessValidator(raw,sizeof *raw,1))fatal("texture object memory is not writable");texturePrepareWrite(raw,sizeof *raw);nvn_texown_retire(t.identity);nvn_texflush_forget(t.identity);removeObject(raw);}
NVN(nvnTextureSetDebugLabel,void,NVNtexture *raw,const char *label){BWTexture *t=textureObject(raw);if(!label)fatal("texture debug label is null");size_t n=0;for(;n<4096;n++){textureMemory(label+n,1,NO);if(!label[n])break;}if(n==4096)fatal("texture debug label too long");t.label=[[NSString alloc] initWithBytes:label length:n encoding:NSUTF8StringEncoding];t.texture.label=t.label;}
#define TEXTURE_FIELD(Name,Type,field) NVN(nvnTextureGet##Name,Type,const NVNtexture *raw){return (Type)textureObject(raw).metadata.field;}
TEXTURE_FIELD(Flags,NVNtextureFlags,flags)
TEXTURE_FIELD(Target,NVNtextureTarget,target)
TEXTURE_FIELD(Width,int,width)
TEXTURE_FIELD(Height,int,height)
TEXTURE_FIELD(Depth,int,depth)
TEXTURE_FIELD(Levels,int,levels)
TEXTURE_FIELD(Format,NVNformat,format)
TEXTURE_FIELD(Samples,int,samples)
TEXTURE_FIELD(DepthStencilMode,NVNtextureDepthStencilMode,depthMode)
TEXTURE_FIELD(Stride,ptrdiff_t,stride)
#undef TEXTURE_FIELD
NVN(nvnTextureGetSwizzle,void,const NVNtexture *raw,NVNtextureSwizzle *r,NVNtextureSwizzle *g,NVNtextureSwizzle *b,NVNtextureSwizzle *a){TextureBuilder m=textureObject(raw).metadata;textureSwizzleOut(m.swizzle,r,g,b,a);}
NVN(nvnTextureGetMemoryPool,NVNmemoryPool*,const NVNtexture *raw){return textureObject(raw).rawPool;}
NVN(nvnTextureGetMemoryOffset,ptrdiff_t,const NVNtexture *raw){return textureObject(raw).offset;}
NVN(nvnTextureGetStorageSize,int,const NVNtexture *raw){return bw_texture_storage_size32(textureObject(raw).storageSize);}
NVN(nvnTextureGetTextureAddress,NVNtextureAddress,const NVNtexture *raw){BWTexture *t=textureObject(raw);return t.pool.gpuBase+t.offset;}
NVN(nvnTextureGetDebugID,uint64_t,const NVNtexture *raw){BWTexture *t=textureObject(raw);return (t.pool.device.flags&NVN_DEVICE_FLAG_DEBUG_ENABLE)?t.identity:0;}
NVN(nvnTextureCompare,NVNboolean,const NVNtexture *a,const NVNtexture *b){BWTexture *x=textureObject(a),*y=textureObject(b);TextureBuilder p=x.metadata,q=y.metadata;return x.pool==y.pool&&x.offset==y.offset&&!memcmp(&p.flags,&q.flags,0x34)&&p.stride==q.stride&&p.packaged==q.packaged;}
/* A selected view owns its Metal view and base storage. Descriptor and command
 * capture copy all guest view bytes, so later guest mutations cannot retarget it. */
static id<MTLTexture> textureMakeSelected(BWTexture *base,TextureView v,BOOL attachment){
    textureLive(base);unsigned level=(v.flags&1)?v.baseLevel:0,levels=(v.flags&1)?v.numLevels:0;
    if(attachment&&!base.blockLinear&&!(base.metadata.flags&0x40))fatal("linear render target requires LINEAR_RENDER_TARGET flag");
    unsigned layer=(v.flags&2)?v.minLayer:0,layers=(v.flags&2)?v.numLayers:0;
    if(level>=base.descriptor.levels||layer>=base.layout.layers)fatal("texture view level/layer bounds");
    if(!levels)levels=base.descriptor.levels-level;if(!layers)layers=base.layout.layers-layer;
    if(levels>base.descriptor.levels-level||layers>base.layout.layers-layer)fatal("texture view level/layer count bounds");
    int target=(v.flags&32)?v.target:base.metadata.target,format=(v.flags&4)?v.format:base.format;
    uint32_t mode=(v.flags&16)?v.depthMode:base.metadata.depthMode;
    if(mode!=NVN_TEXTURE_DEPTH_STENCIL_MODE_DEPTH)fatal("texture view stencil aspect is not implemented");
    MTLTextureType type=target==NVN_TEXTURE_TARGET_2D?MTLTextureType2D:MTLTextureType2DArray;
    if(target!=NVN_TEXTURE_TARGET_2D&&target!=NVN_TEXTURE_TARGET_2D_ARRAY)fatal("texture view target is not implemented");
    if(type==MTLTextureType2D&&layers!=1)fatal("2D texture view requires one layer");
    if(attachment){if(base.layout.layers>1&&!(v.flags&2))fatal("array render targets require explicit layer/view semantics");if(layers!=1)fatal("render target view requires one layer");levels=1;type=MTLTextureType2D;}
    uint32_t pixel;uint8_t conversion[4];if(nvn_fmt_to_mtl(format,&pixel,conversion)!=1)fatal("texture view format requires conversion");
    int bw,bh,vbw,vbh,bpb=nvn_fmt_bpb(base.format,&bw,&bh),vbpb=nvn_fmt_bpb(format,&vbw,&vbh);
    if(bpb!=vbpb||bw!=vbw||bh!=vbh)fatal("texture view format block layout differs");
    uint32_t swizzle[4];memcpy(swizzle,(v.flags&8)?v.swizzle:base.metadata.swizzle,sizeof swizzle);
    static const MTLTextureSwizzle selectors[]={MTLTextureSwizzleZero,MTLTextureSwizzleOne,MTLTextureSwizzleRed,MTLTextureSwizzleGreen,MTLTextureSwizzleBlue,MTLTextureSwizzleAlpha};
    for(unsigned i=0;i<4;i++){if(swizzle[i]>5||conversion[i]!=i+NVN_SWZ_R)fatal("texture view component conversion is not implemented");if(attachment&&swizzle[i]!=i+2)fatal("swizzled render target view is not implemented");}
    /* Validate all selection semantics even when virtual storage has not yet
     * been mapped. Rebuild this exact selection after a later resource map. */
    if(!base.texture)return nil;
    id<MTLTexture> texture=[base.texture newTextureViewWithPixelFormat:(MTLPixelFormat)pixel textureType:type levels:NSMakeRange(level,levels) slices:NSMakeRange(layer,layers) swizzle:MTLTextureSwizzleChannelsMake(selectors[swizzle[0]],selectors[swizzle[1]],selectors[swizzle[2]],selectors[swizzle[3]])];
    if(!texture)fatal("Metal texture view creation failed");
    return texture;
}
static BWTexture *textureSelected(BWTexture *base,TextureView v,BOOL attachment){
    id<MTLTexture> resource=textureMakeSelected(base,v,attachment);
    unsigned level=(v.flags&1)?v.baseLevel:0;int format=(v.flags&4)?v.format:base.format;
    BWTexture *t=[BWTexture new];t.base=base;t.texture=resource;t.mapping=base.mapping;t.selection=v;t.selectedAttachment=attachment;t.pool=base.pool;t.poolGeneration=base.poolGeneration;t.device=base.device;t.kindPolicy=base.kindPolicy;t.metadata=base.metadata;t.storageSize=base.storageSize;t.format=format;t.width=MAX(1,base.width>>level);t.height=MAX(1,base.height>>level);t.depth=base.depth;t.descriptor=base.descriptor;t.layout=base.layout;
    textureRememberReference(t);return t;
}
NVN(nvnTextureGetViewOffset,ptrdiff_t,const NVNtexture *raw,const NVNtextureView *view){BWTexture *t=textureObject(raw);TextureView v=textureView(view);unsigned level=(v.flags&1)?v.baseLevel:0,layer=(v.flags&2)?v.minLayer:0;if(level>=t.descriptor.levels||layer>=t.layout.layers)fatal("texture view offset bounds");nvn_texdesc d=t.descriptor;return nvn_tex_level_offset(&d,level)+layer*t.layout.layer_size;}
static void textureDrain(void){for(id o in objects.allValues)if([o isKindOfClass:[BWQueue class]])finishBuffer(((BWQueue*)o).latest);}
static void textureReadBytes(const void *src,void *dst,size_t bytes,const char *reason){
    if(!src||bytes>UINTPTR_MAX-(uintptr_t)src||(memoryAccessValidator&&!memoryAccessValidator(src,bytes,0)))fatal(reason);
    if(botw_protected_vm_read(dst,src,bytes)!=KERN_SUCCESS)fatal(reason);
}
static void texturePrepareWrite(void *dst,size_t bytes){if(memoryAccessValidator&&!memoryAccessValidator(dst,bytes,1))fatal("texture destination storage is not writable");char error[256];if(textureTrackingAvailable&&nvn_dirty_prepare(dst,bytes,error,sizeof error))fatal(error);textureVMAccess(dst,bytes,YES);}
static nvn_texwrite_plan texturePlan(BWTexture *t,TextureView v,const NVNcopyRegion *raw,ptrdiff_t row,ptrdiff_t image){
    NVNcopyRegion region;textureReadBytes(raw,&region,sizeof region,"texture copy region is not readable");
    nvn_texdesc d=t.descriptor;if(!t.blockLinear)d.flags&=~0x10;
    nvn_texwrite_plan plan;const char *why=NULL;int status=nvn_texwrite_plan_make(&d,(v.flags&1)?v.baseLevel:0,(v.flags&2)?v.minLayer:0,(v.flags&32)?(int)v.target:-1,(v.flags&4)?v.format:0,&region,row,image,&plan,&why);
    if(status<0)fatal(why);if(status==NVN_TEXWRITE_EMPTY){plan.width=0;return plan;}
    unsigned availableLayers=(v.flags&2)&&v.numLayers?v.numLayers:t.layout.layers-((v.flags&2)?v.minLayer:0);
    if((uint64_t)region.zoffset+plan.depth>availableLayers)fatal("texture copy exceeds view layers");
    if(!t.blockLinear){plan.span_lo=(uint64_t)plan.y0*t.stride+plan.xb;plan.span_hi=(uint64_t)(plan.y0+plan.hb-1)*t.stride+plan.xb+plan.wb;if(plan.span_hi>t.layout.size)fatal("linear texture copy storage bounds");}
    return plan;
}
static NSMutableData *textureSourceSnapshot(const void *source,nvn_texwrite_plan *p){
    size_t image,bytes;if(!source||p->src_bytes>UINTPTR_MAX-(uintptr_t)source||__builtin_mul_overflow((size_t)p->wb,(size_t)p->hb,&image)||__builtin_mul_overflow(image,(size_t)p->depth,&bytes))fatal("texture source bytes overflow");
    NSMutableData *copy=[NSMutableData dataWithLength:bytes];if(!copy)fatal("texture source snapshot allocation failed");
    for(unsigned z=0;z<p->depth;z++)for(unsigned y=0;y<p->hb;y++)textureReadBytes((const char*)source+z*p->src_img+y*p->src_row,(char*)copy.mutableBytes+z*image+y*p->wb,p->wb,"texture source bytes cannot be snapshotted");
    p->src_row=p->wb;p->src_img=image;p->src_bytes=bytes;return copy;
}
static void textureWriteBytes(void *destination,const void *source,size_t bytes,const char *reason){
    if(botw_protected_vm_write(destination,source,bytes)!=KERN_SUCCESS)fatal(reason);
}
static int textureWriteRun(void *opaque,uint64_t offset,const void *source,size_t bytes){
    uintptr_t address=(uintptr_t)opaque;if(offset>UINTPTR_MAX-address)return -1;
    return botw_protected_vm_write((void*)(address+(uintptr_t)offset),source,bytes)==KERN_SUCCESS?0:-1;
}
static void textureCPUWrite(const NVNtexture *raw,const NVNtextureView *view,const NVNcopyRegion *region,const void *source,ptrdiff_t row,ptrdiff_t image){
    BWTexture *t=textureObject(raw);TextureView v=textureView(view);nvn_texwrite_plan p=texturePlan(t,v,region,row,image);if(!p.width)return;
    if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS)fatal("texture storage has no CPU mapping");
    NSMutableData *copy=textureSourceSnapshot(source,&p);void *storage=textureCanonical(t);
    int priorCoherence=nvn_texflush_verify(t.identity,storage,t.layout.layer_size,t.layout.layers,p.layer,p.depth);
    if(t.gpuWritten&&priorCoherence==NVN_TEXFLUSH_CHANGED)fatal("texture CPU storage changed outside host writes before region write");
    textureDrain();id<MTLCommandBuffer> cb=[commandQueue commandBuffer];if(!cb)fatal("texture write command allocation failed");uploadTexture(t,cb);
    /* All allocation, source reads and geometry checks precede pool mutation.
     * Import every untouched mip/layer before a first partial write. */
    size_t pitch=(p.wb+255ull)&~255ull,imagePitch=pitch*p.hb;
    id<MTLBuffer> staging=[gpu newBufferWithLength:imagePitch*p.depth options:MTLResourceStorageModeShared];if(!staging)fatal("texture write staging allocation failed");
    for(unsigned z=0;z<p.depth;z++)for(unsigned y=0;y<p.hb;y++)memcpy((char*)staging.contents+z*imagePitch+y*pitch,(const char*)copy.bytes+z*p.src_img+y*p.src_row,p.wb);
    id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];if(!blit)fatal("texture write blit allocation failed");
    @try{for(unsigned z=0;z<p.depth;z++)[blit copyFromBuffer:staging sourceOffset:z*imagePitch sourceBytesPerRow:pitch sourceBytesPerImage:imagePitch sourceSize:MTLSizeMake(p.width,p.height,1) toTexture:t.texture destinationSlice:p.layer+z destinationLevel:p.level destinationOrigin:MTLOriginMake(p.x0,p.y0,0)];}@finally{[blit endEncoding];}
    texturePrepareWrite((char*)storage+p.span_lo,p.span_hi-p.span_lo);
    if(t.blockLinear){nvn_texdesc d=t.descriptor;if(nvn_texwrite_apply_checked(&d,&p,copy.bytes,textureWriteRun,storage))fatal("texture write destination copy failed");}else for(unsigned y=0;y<p.hb;y++)textureWriteBytes((char*)storage+(p.y0+y)*t.stride+p.xb,(const char*)copy.bytes+y*p.src_row,p.wb,"texture write destination copy failed");
    commitBuffer(cb);finishBuffer(cb);t.uploadHash=digest(storage,t.layout.size);
    if(nvn_texflush_note_checked(t.identity,storage,t.layout.layer_size,t.layout.layers,p.layer,p.depth,!t.gpuWritten||priorCoherence==NVN_TEXFLUSH_CLEAN))fatal("texture write coherence certificate allocation failed");
}
static void textureCPURead(const NVNtexture *raw,const NVNtextureView *view,const NVNcopyRegion *region,void *destination,ptrdiff_t row,ptrdiff_t image){
    BWTexture *t=textureObject(raw);nvn_texwrite_plan p=texturePlan(t,textureView(view),region,row,image);if(!p.width)return;
    if(!destination||p.src_bytes>UINTPTR_MAX-(uintptr_t)destination)fatal("texture read destination bounds");
    for(unsigned z=0;z<p.depth;z++)for(unsigned y=0;y<p.hb;y++)texturePrepareWrite((char*)destination+z*p.src_img+y*p.src_row,p.wb);
    textureDrain();size_t pitch=(p.wb+255ull)&~255ull,imagePitch=pitch*p.hb;
    id<MTLBuffer> buffer=[gpu newBufferWithLength:imagePitch*p.depth options:MTLResourceStorageModeShared];if(!buffer)fatal("texture read staging allocation failed");
    id<MTLCommandBuffer> cb=[commandQueue commandBuffer];if(!cb)fatal("texture read command allocation failed");uploadTexture(t,cb);id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];if(!blit)fatal("texture read blit allocation failed");
    @try{for(unsigned z=0;z<p.depth;z++)[blit copyFromTexture:t.texture sourceSlice:p.layer+z sourceLevel:p.level sourceOrigin:MTLOriginMake(p.x0,p.y0,0) sourceSize:MTLSizeMake(p.width,p.height,1) toBuffer:buffer destinationOffset:z*imagePitch destinationBytesPerRow:pitch destinationBytesPerImage:imagePitch];}@finally{[blit endEncoding];}
    commitBuffer(cb);finishBuffer(cb);
    for(unsigned z=0;z<p.depth;z++)for(unsigned y=0;y<p.hb;y++)textureWriteBytes((char*)destination+z*p.src_img+y*p.src_row,(char*)buffer.contents+z*imagePitch+y*pitch,p.wb,"texture read destination copy failed");
}
NVN(nvnTextureWriteTexels,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r,const void *src){textureCPUWrite(t,v,r,src,0,0);}
NVN(nvnTextureWriteTexelsStrided,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r,const void *src,ptrdiff_t row,ptrdiff_t image){textureCPUWrite(t,v,r,src,row,image);}
NVN(nvnTextureReadTexels,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r,void *dst){textureCPURead(t,v,r,dst,0,0);}
NVN(nvnTextureReadTexelsStrided,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r,void *dst,ptrdiff_t row,ptrdiff_t image){textureCPURead(t,v,r,dst,row,image);}

static void textureGPUWrite(BWTexture *input,id<MTLCommandBuffer> cb){BWTexture *t=input.base?:input;textureEnsureMapping(input,"texture GPU write");textureAuthority(t,cb);poolRetainForGPU(cb,t.mapping,!t.blockLinear);texturePendingRecord(t,cb,YES)[@"gpu"]=@YES;}
static void textureFlush(const NVNtexture *raw,const NVNtextureView *view){
    BWTexture *t=textureObject(raw);if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS)return;
    TextureView v={0};if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_CACHED)v=textureView(view);
    uint32_t first,count;const char *why=NULL;nvn_texdesc d=t.descriptor;int status=nvn_texflush_layers(&d,!!(v.flags&2),v.minLayer,v.numLayers,&first,&count,&why);if(status<0)fatal(why);if(status==NVN_TEXFLUSH_EMPTY)return;
    const uint8_t *storage=textureCanonical(t);textureDrain();
    if(!t.blockLinear){if(memoryAccessValidator&&!memoryAccessValidator(storage,t.layout.size,0))fatal("texture flush source is not readable");textureVMAccess(storage,t.layout.size,NO);if(!t.mapping.physical.coherent)textureReadBytes(storage,(char*)t.mapping.buffer.contents+t.mapping.metalOffset,t.layout.size,"texture flush source copy failed");return;}
    if(t.gpuWritten){int state=nvn_texflush_verify(t.identity,storage,t.layout.layer_size,t.layout.layers,first,count);if(state!=NVN_TEXFLUSH_CLEAN)fatal(state==NVN_TEXFLUSH_CHANGED?"texture flush CPU storage changed outside host writes":"texture flush lacks complete write tracking");return;}
    /* No private GPU writes exist: a real upload can reconcile current CPU
     * bytes even without a fault bridge. Never certify a skipped host copy. */
    t.uploadHash=nil;id<MTLCommandBuffer> cb=[commandQueue commandBuffer];if(!cb)fatal("texture flush command allocation failed");uploadTexture(t,cb);commitBuffer(cb);finishBuffer(cb);
}
static void textureInvalidate(const NVNtexture *raw,const NVNtextureView *view){
    BWTexture *t=textureObject(raw);if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS)return;
    TextureView v={0};if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_CACHED)v=textureView(view);
    uint32_t first,count;const char *why=NULL;nvn_texdesc d=t.descriptor;int status=nvn_texflush_layers(&d,!!(v.flags&2),v.minLayer,v.numLayers,&first,&count,&why);if(status<0)fatal(why);if(status==NVN_TEXFLUSH_EMPTY)return;
    textureDrain();uint8_t *storage=textureCanonical(t);texturePrepareWrite(storage+first*t.layout.layer_size,count*t.layout.layer_size);
    if(!t.blockLinear){if(!t.mapping.physical.coherent)textureWriteBytes(storage,(char*)t.mapping.buffer.contents+t.mapping.metalOffset,t.layout.size,"texture invalidate destination copy failed");return;}
    /* Gather every selected level before mutating any pool byte; a failed GPU
     * read never publishes a partially reconstructed texture. Padding survives. */
    NSMutableData *image=[NSMutableData dataWithLength:t.layout.size];if(!image)fatal("texture invalidate snapshot allocation failed");textureReadBytes(storage,image.mutableBytes,t.layout.size,"texture invalidate pool snapshot failed");
    for(unsigned level=0;level<d.levels;level++){
        NVNcopyRegion r={0,0,0,MAX(1,t.width>>level),MAX(1,t.height>>level),(int)count};TextureView selected={0};selected.flags=3;selected.baseLevel=level;selected.minLayer=first;selected.numLayers=count;
        nvn_texwrite_plan p=texturePlan(t,selected,&r,0,0);NSMutableData *linear=[NSMutableData dataWithLength:p.src_bytes];if(!linear)fatal("texture invalidate staging allocation failed");
        NVNtextureView native;memset(&native,0,sizeof native);memcpy(&native,&selected,sizeof selected);textureCPURead(raw,&native,&r,linear.mutableBytes,0,0);nvn_texwrite_apply(&d,&p,linear.bytes,image.mutableBytes);
    }
    /* The image is host owned, with only texel runs changed. Selected whole
     * allocations are native invalidation units; preserve all padding bytes. */
    textureWriteBytes(storage+first*t.layout.layer_size,(char*)image.bytes+first*t.layout.layer_size,count*t.layout.layer_size,"texture invalidate destination copy failed");
    t.uploadHash=digest(storage,t.layout.size);if(nvn_texflush_note_checked(t.identity,storage,t.layout.layer_size,t.layout.layers,first,count,1))fatal("texture invalidate coherence certificate allocation failed");
}
NVN(nvnTextureFlushTexels,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r){(void)r;textureFlush(t,v);}
NVN(nvnTextureInvalidateTexels,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r){(void)r;textureInvalidate(t,v);}
static void validateTextureDescriptorPool(BWDescriptors *p,BWDevice *device){if(!p||!p.pool.live||objects[@(p.pool.generation)]!=p.pool)fatal("stale texture descriptor pool");if(p.pool.device!=device)fatal("texture descriptor pool device mismatch");}
static BOOL textureStorageOverlap(BWTexture *left,BWTexture *right){
    if(!left||!right)return NO;BWTexture *a=left.base?:left,*b=right.base?:right;
    return memoryOverlap(textureCanonical(a),a.layout.size,textureCanonical(b),b.layout.size);
}
static BOOL linearTextureBufferOverlap(BWTexture *input,NSDictionary *binding){
    BWTexture *t=input.base?:input;if(!t||t.blockLinear||!binding)return NO;
    BWPoolRange *buffer=poolResolveAddress([binding[@"address"] unsignedLongLongValue],[binding[@"size"] unsignedLongLongValue],0,"linear texture buffer feedback");
    return memoryOverlap(textureCanonical(t),t.layout.size,buffer.canonical,buffer.length);
}
static void texturePreflightBuffer(BWPoolRange *range,NSArray<BWTexture *> *pendingPrivate){
    textureCheckBufferAuthority(range.canonical,range.length);
    for(BWTexture *t in pendingPrivate)if(memoryOverlap(range.canonical,range.length,textureCanonical(t),t.layout.size))fatal("buffer address aliases private texture pending GPU writes");
}
static void texturePreflightUse(BWTexture *input,NSArray<BWTexture *> *pendingPrivate,NSArray<BWPoolRange *> *pendingBufferWrites){
    BWTexture *t=input.base?:input;if(!t)return;textureEnsureMapping(input,"texture submission mapping");textureAuthority(t,nil);
    for(BWTexture *other in pendingPrivate)if(other!=t&&memoryOverlap(textureCanonical(t),t.layout.size,textureCanonical(other),other.layout.size))fatal("texture storage authority conflicts with pending GPU writes");
    if(t.blockLinear)for(BWPoolRange *write in pendingBufferWrites)if(memoryOverlap(textureCanonical(t),t.layout.size,write.canonical,write.length))fatal("private texture import aliases pending buffer writes");
}
static void textureRememberPrivate(BWTexture *input,NSMutableArray<BWTexture *> *pending){BWTexture *base=input.base?:input;if(base&&base.blockLinear&&![pending containsObject:base])[pending addObject:base];}
static void textureRememberAttachmentWrites(BWTexture *input,NSMutableArray<BWTexture *> *pendingPrivate,NSMutableArray<BWPoolRange *> *pendingBufferWrites,NSMutableArray<BWPoolRange *> *pendingRawWrites){
    BWTexture *base=input.base?:input;if(!base)return;
    [pendingRawWrites addObject:base.mapping];
    if(base.blockLinear)textureRememberPrivate(base,pendingPrivate);else [pendingBufferWrites addObject:base.mapping];
}
static BWPoolRange *texturePreflightDescriptor(BWDescriptors *pool,unsigned index,NSArray<BWTexture *> *pendingPrivate,NSArray<BWPoolRange *> *pendingRawWrites){
    if(!pool||index>=(unsigned)pool.count)fatal("descriptor slot index bounds");
    BWPoolRange *slot=poolResolve(pool.pool,pool.offset+(size_t)index*BWDescriptorBytes,BWDescriptorBytes,0,"descriptor submission mapping");texturePreflightBuffer(slot,pendingPrivate);
    for(BWPoolRange *write in pendingRawWrites)if(memoryOverlap(slot.canonical,slot.length,write.canonical,write.length))fatal("descriptor slot written earlier in submission; raw descriptor decoding is unsupported");
    return slot;
}
static void validateTextureSubmission(BWQueue *queue,NSArray<NSArray *> *groups){
    BWTexture *target=queue.replay.target,*depth=queue.replay.depth;BWDescriptors *texturePool=queue.replay.texturePool,*samplerPool=queue.replay.samplerPool;BWProgram *program=queue.replay.program;NSMutableDictionary *textures=[queue.replay.textures mutableCopy];
    NSMutableDictionary *storage=[queue.replay.storage mutableCopy],*uniforms=[queue.replay.uniforms mutableCopy],*vertices=[queue.replay.vertices mutableCopy];
    NSData *attributes=queue.replay.attributes;
    NSMutableArray<BWTexture *> *pendingPrivate=[NSMutableArray new];NSMutableArray<BWPoolRange *> *pendingBufferWrites=[NSMutableArray new],*pendingRawWrites=[NSMutableArray new];
    for(NSArray *commands in groups)for(NSDictionary *c in commands){NSString *op=c[@"op"];
        if([op isEqual:@"target"]){target=c[@"selected"];depth=c[@"depthSelected"];}
        else if([op isEqual:@"texturePool"])texturePool=c[@"pool"];
        else if([op isEqual:@"samplerPool"])samplerPool=c[@"pool"];
        else if([op isEqual:@"program"])program=objects[c[@"id"]];
        else if([op isEqual:@"texture"])textures[resourceKey(c[@"stage"],c[@"slot"])]=c[@"handle"];
        else if([op isEqual:@"storage"])storage[resourceKey(c[@"stage"],c[@"slot"])]=c;
        else if([op isEqual:@"uniform"])uniforms[resourceKey(c[@"stage"],c[@"slot"])]=c;
        else if([op isEqual:@"vertex"])vertices[c[@"slot"]]=c;
        else if([op isEqual:@"attributes"])attributes=c[@"data"];
        else if([op isEqual:@"copy"]){
            size_t bytes=[c[@"size"] unsignedLongLongValue];BWPoolRange *source=poolResolveAddress([c[@"src"] unsignedLongLongValue],bytes,0,"texture copy preflight source"),*destination=poolResolveAddress([c[@"dst"] unsignedLongLongValue],bytes,0,"texture copy preflight destination");
            texturePreflightBuffer(source,pendingPrivate);texturePreflightBuffer(destination,pendingPrivate);[pendingBufferWrites addObject:destination];[pendingRawWrites addObject:destination];
        }else if([op isEqual:@"counter"]){
            BWAddressRecord *destination=c[@"destination"];BWPoolRange *range=poolResolve(destination.pool,destination.offset,destination.bytes,0,"texture counter preflight");texturePreflightBuffer(range,pendingPrivate);
            /* Counter replay drains preceding raw GPU stores before the next
             * segment may import backing. Private texture authority remains. */
            [pendingBufferWrites removeAllObjects];[pendingRawWrites addObject:range];
        }
        else if([op isEqual:@"clear"]||[op isEqual:@"depthClear"]||([op isEqual:@"draw"]&&[c[@"count"] unsignedLongLongValue]&&[c[@"instances"] unsignedLongLongValue])){
            if(target){texturePreflightUse(target,pendingPrivate,pendingBufferWrites);if(target.device!=queue.device)fatal("texture render target device mismatch");}if(depth){texturePreflightUse(depth,pendingPrivate,pendingBufferWrites);if(depth.device!=queue.device)fatal("texture depth target device mismatch");}
            if(![op isEqual:@"draw"]){textureRememberAttachmentWrites(target,pendingPrivate,pendingBufferWrites,pendingRawWrites);textureRememberAttachmentWrites(depth,pendingPrivate,pendingBufferWrites,pendingRawWrites);continue;}
            NSMutableArray<BWTexture *> *sampled=[NSMutableArray new];NSMutableArray<NSDictionary *> *buffers=[NSMutableArray new],*writable=[NSMutableArray new];NSMutableArray<BWPoolRange *> *descriptorRanges=[NSMutableArray new];
            const VertexAttribute *active=attributes.bytes;
            for(size_t i=0;i<attributes.length/sizeof *active;i++)if(active[i].format){NSDictionary *binding=vertices[@(active[i].stream)];if(binding)[buffers addObject:binding];}
            for(NSNumber *stage in program.stageShaders){BWShader *shader=program.stageShaders[stage];const botw_graphics_shader_binding *bindings=shader.bindings.bytes;
                for(size_t i=0;i<shader.bindings.length/sizeof *bindings;i++){if(bindings[i].kind!=BOTW_GRAPHICS_TEXTURE&&bindings[i].kind!=BOTW_GRAPHICS_SAMPLER)continue;
                    NSNumber *h=textures[resourceKey(stage,@(bindings[i].nvn_slot))];if(!h)fatal("unbound texture descriptor");
                    BWDescriptors *p=bindings[i].kind==BOTW_GRAPHICS_TEXTURE?texturePool:samplerPool;validateTextureDescriptorPool(p,queue.device);
                    unsigned index=bindings[i].kind==BOTW_GRAPHICS_TEXTURE?textureDescriptorIndex(h.unsignedLongLongValue):samplerDescriptorIndex(h.unsignedLongLongValue);[descriptorRanges addObject:texturePreflightDescriptor(p,index,pendingPrivate,pendingRawWrites)];
                    if(bindings[i].kind==BOTW_GRAPHICS_TEXTURE){BWTexture *t=descriptorEntry(p,textureDescriptorIndex(h.unsignedLongLongValue));if(!t)fatal("unbound texture descriptor");texturePreflightUse(t,pendingPrivate,pendingBufferWrites);if(t.device!=queue.device)fatal("texture descriptor device mismatch");[sampled addObject:t];}
                    else if(!descriptorEntry(p,samplerDescriptorIndex(h.unsignedLongLongValue)))fatal("unbound sampler descriptor");
                }
                for(size_t i=0;i<shader.bindings.length/sizeof *bindings;i++){
                    BOOL writes=bindings[i].kind==BOTW_GRAPHICS_STORAGE,isUniform=bindings[i].kind==BOTW_GRAPHICS_UNIFORM;
                    if(!writes&&!isUniform)continue;NSDictionary *binding=(writes?storage:uniforms)[resourceKey(stage,@(bindings[i].nvn_slot))];
                    if(binding){[buffers addObject:binding];if(writes)[writable addObject:binding];}
                }
            }
            for(NSDictionary *binding in buffers){BWPoolRange *range=poolResolveAddress([binding[@"address"] unsignedLongLongValue],[binding[@"size"] unsignedLongLongValue],0,"texture shader buffer preflight");texturePreflightBuffer(range,pendingPrivate);}
            for(NSDictionary *binding in writable){BWPoolRange *write=poolResolveAddress([binding[@"address"] unsignedLongLongValue],[binding[@"size"] unsignedLongLongValue],0,"descriptor shader store preflight");for(BWPoolRange *slot in descriptorRanges)if(memoryOverlap(slot.canonical,slot.length,write.canonical,write.length))fatal("descriptor slot aliases shader store; raw descriptor decoding is unsupported");}
            /* Buffer-backed textures are coherent between encoders, not
             * within one render pass. Validate the entire submission before
             * a preceding counter segment can publish any GPU operation. */
            for(BWTexture *t in sampled){BWTexture *base=t.base?:t;
                if((!base.blockLinear||!(target.base?:target).blockLinear)&&textureStorageOverlap(t,target))fatal("linear render pass feedback aliases sampled texture and color attachment");
                if((!base.blockLinear||!(depth.base?:depth).blockLinear)&&textureStorageOverlap(t,depth))fatal("linear render pass feedback aliases sampled texture and depth attachment");
                for(NSDictionary *binding in writable)if(linearTextureBufferOverlap(t,binding))fatal("linear render pass feedback aliases sampled texture and storage buffer");
                if(base.blockLinear){if(textureStorageOverlap(t,target)||textureStorageOverlap(t,depth))fatal("private texture render pass feedback aliases sampled texture and attachment");for(NSDictionary *binding in writable){BWPoolRange *range=poolResolveAddress([binding[@"address"] unsignedLongLongValue],[binding[@"size"] unsignedLongLongValue],0,"private texture shader write feedback");if(memoryOverlap(textureCanonical(base),base.layout.size,range.canonical,range.length))fatal("private texture import aliases pending buffer writes");}}
            }
            for(NSDictionary *binding in buffers)if(linearTextureBufferOverlap(target,binding)||linearTextureBufferOverlap(depth,binding))fatal("linear render pass feedback aliases attachment and shader/vertex buffer");
            BWTexture *outputs[]={target,depth};for(unsigned output=0;output<2;output++){BWTexture *base=outputs[output].base?:outputs[output];if(base&&base.blockLinear){for(NSDictionary *binding in buffers){BWPoolRange *range=poolResolveAddress([binding[@"address"] unsignedLongLongValue],[binding[@"size"] unsignedLongLongValue],0,"private texture target buffer feedback");if(memoryOverlap(textureCanonical(base),base.layout.size,range.canonical,range.length))fatal("private texture render target aliases shader/vertex buffer");}}}
            /* Keep the existing render-feedback checks above first. Both a
             * direct backing store and private GPU-only authority conflict
             * with a descriptor slot consumed by this same draw. */
            for(unsigned output=0;output<2;output++){BWTexture *base=outputs[output].base?:outputs[output];if(!base)continue;for(BWPoolRange *slot in descriptorRanges)if(memoryOverlap(textureCanonical(base),base.layout.size,slot.canonical,slot.length))fatal("descriptor slot aliases attachment write intent; raw descriptor decoding is unsupported");}
            textureRememberAttachmentWrites(target,pendingPrivate,pendingBufferWrites,pendingRawWrites);textureRememberAttachmentWrites(depth,pendingPrivate,pendingBufferWrites,pendingRawWrites);
            for(NSDictionary *binding in writable){BWPoolRange *write=poolResolveAddress([binding[@"address"] unsignedLongLongValue],[binding[@"size"] unsignedLongLongValue],0,"texture pending shader store");[pendingBufferWrites addObject:write];[pendingRawWrites addObject:write];}
        }
    }
}
