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
static void textureLive(BWTexture *t){
    BWTexture *base=t.base?:t;
    if(!base.pool.live||base.pool.generation!=base.poolGeneration||objects[@(base.poolGeneration)]!=base.pool)fatal("stale texture pool");
    if(!base.pool.device||base.pool.device!=base.device||![objects.allValues containsObject:base.device])fatal("stale texture device");
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
    BOOL array=b->target==NVN_TEXTURE_TARGET_2D_ARRAY;
    if((b->target!=NVN_TEXTURE_TARGET_2D&&!array)||b->width<1||b->height<1||b->width>16384||b->height>16384||b->depth<1||b->depth>(array?2048:1)||b->levels<1||b->levels>15||b->samples<0||b->samples>1||texturePackagedHeight(b)>6||(b->packaged&0xff00)||(array&&(b->flags&(0x10|0x40))))return 0;
    unsigned dimension=MAX(b->width,b->height),levels=1;while(dimension>1){dimension>>=1;levels++;}if((unsigned)b->levels>levels)return 0;
    if(b->flags&(0x10|0x40)){BWLinearLayout layout;return textureLinearLayout(b,&layout)?layout.size:0;}
    nvn_texdesc d=textureDescriptor(b);nvn_texlayout l;nvn_tex_layout(&d,&l);return l.size;
}
NVN(nvnTextureBuilderGetStorageSize,size_t,const NVNtextureBuilder *raw){TextureBuilder b=textureBuilder(raw);if(b.target==NVN_TEXTURE_TARGET_BUFFER){int bytes=nvn_fmt_bpb(b.format,NULL,NULL);return b.width>0&&bytes?(size_t)b.width*bytes:0;}return textureBytes(&b);}
/* Original base-v0 0x34-byte format records: +0x30 is nonzero and +0xe/+0xf
 * depth/stencil bytes are zero for exactly these 113 declared color formats.
 * This is independent of Metal's narrower format/creation capability. */
static BOOL textureOrdinaryStorageFormat(uint32_t format){
    return (format>=NVN_FORMAT_R8&&format<=NVN_FORMAT_RG32I)||(format>=NVN_FORMAT_RGB32F&&format<=NVN_FORMAT_RGBA32I)||
        (format>=NVN_FORMAT_RGBX8_SRGB&&format<=NVN_FORMAT_BPTC_UFLOAT)||(format>=NVN_FORMAT_RGBX8&&format<=NVN_FORMAT_BGRA8_SRGB);
}
static void textureStorageDevice(const NVNdevice *raw){
    /* Establish exact live-object provenance before reading a guest header. */
    BWDevice *provider=nil;
    for(NSNumber *key in objectSpans){NSRange span=objectSpans[key].rangeValue;id object=objects[key];
        if(span.location==(uintptr_t)raw&&[object isKindOfClass:[BWDevice class]]){provider=object;break;}}
    if(!provider)fatal("object lifetime/type expected=BWDevice storage-class provider is not registered");
    textureMemory(raw,sizeof(uint64_t),NO);
    if(get(raw,[BWDevice class])!=provider)fatal("object lifetime/type expected=BWDevice storage-class provider identity mismatch");
}
/* Base-v0 returns 0x100 | rawKind. BUFFER and flags&0x50 take rawKind=0
 * before accessing the device. Otherwise GK100/GM200 share ChooseKind+0x68:
 * uncompressed color is the pure 0x316c0 leaf returning 0xfe. The caller
 * still requires a live device provider; no pool or Metal creation is needed. */
static NVNstorageClass textureMetadataStorageClass(const TextureBuilder *b){
    if(b->target==NVN_TEXTURE_TARGET_BUFFER||(b->flags&0x50))return 0x100;
    if(!(b->flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&textureOrdinaryStorageFormat(b->format)){
        textureStorageDevice(b->device);return 0x1fe;
    }
    char detail[256];snprintf(detail,sizeof detail,"texture storage class requires unimplemented native ChooseKind target=%u flags=0x%x format=0x%x samples=%d source=base-v0-storage-metadata",b->target,b->flags,b->format,b->samples);fatal(detail);
}
NVN(nvnTextureBuilderGetStorageClass,NVNstorageClass,const NVNtextureBuilder *raw){TextureBuilder b=textureBuilder(raw);return textureMetadataStorageClass(&b);}
NVN(nvnTextureBuilderGetStorageAlignment,size_t,const NVNtextureBuilder *raw){TextureBuilder b=textureBuilder(raw);if(b.flags&(0x10|0x40)){BWDevice *device=get(b.device,[BWDevice class]);BWLinearPolicy policy=device.linearPolicy;return bw_linear_alignment(&policy,b.flags);}if(b.target==NVN_TEXTURE_TARGET_BUFFER){int bytes=nvn_fmt_bpb(b.format,NULL,NULL);return bytes==12?4:bytes;}if(!textureBytes(&b))return 0;nvn_texdesc d=textureDescriptor(&b);nvn_texlayout l;nvn_tex_layout(&d,&l);return l.align;}
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
NVN(nvnTextureInitialize,NVNboolean,NVNtexture *raw,const NVNtextureBuilder *builder){
    TextureBuilder snapshot=textureBuilder(builder);const TextureBuilder *b=&snapshot;BWDevice *device=get(b->device,[BWDevice class]);
    if(!b->pool||!textureBytes(b)||(b->flags&~(0x10|0x40|NVN_TEXTURE_FLAGS_DISPLAY|NVN_TEXTURE_FLAGS_COMPRESSIBLE|NVN_TEXTURE_FLAGS_IMAGE))||b->glName||b->packagedData)return report("Unsupported texture dimension/flags/samples/levels/layout/linear/GL/packaged data");
    uint32_t format=0;uint8_t swz[4];if(nvn_fmt_to_mtl(b->format,&format,swz)!=1||(b->format>=0x41&&b->format<=0x50))return report("Texture format requires an unimplemented conversion/BC decoder");
    if(b->format>=NVN_FORMAT_RGBA_ASTC_4x4&&b->format<=NVN_FORMAT_RGBA_ASTC_12x12_SRGB&&!supportsASTC())return report("ASTC textures require Metal Apple2 family support");
    for(unsigned i=0;i<4;i++)if(swz[i]!=i+NVN_SWZ_R)return report("Texture component conversion is not implemented");
    BWPool *pool=get(b->pool,[BWPool class]);size_t size=textureBytes(b);if(!pool.live||pool.device!=device)return report("texture device/pool mismatch");
    if(!size||b->offset<0||!range(b->offset,size,pool.size))return report("Texture storage bounds");
    BOOL linear=!!(b->flags&(0x10|0x40));BWLinearLayout linearLayout={0};
    if(linear){
        if(!pool.coherent)return report("linear textures require coherent no-copy pool storage");
        if(!textureLinearLayout(b,&linearLayout)||(size_t)b->offset%linearLayout.alignment)return report("Invalid linear texture stride/offset/alignment");
        NSUInteger alignment=[gpu minimumLinearTextureAlignmentForPixelFormat:(MTLPixelFormat)format];
        if(!alignment||linearLayout.stride%alignment||(pool.bufferOffset+(size_t)b->offset)%alignment)return report("Invalid linear texture Metal backing alignment");
        nvn_texown_hit hit;if(nvn_texown_find((char*)pool.memory+b->offset,size,&hit))return report("linear storage authority belongs to live/retired private texture");
    }
    for(id object in objects.allValues)if([object isKindOfClass:[BWTexture class]]){
        BWTexture *other=object;
        if((linear||!other.blockLinear)&&other.pool.live&&other.pool.generation==other.poolGeneration&&memoryOverlap((char*)pool.memory+b->offset,size,(char*)other.pool.memory+other.offset,other.layout.size))return report("linear texture storage overlaps another live texture base");
    }
    if(memoryAccessValidator&&!memoryAccessValidator(raw,sizeof *raw,1))return report("texture object memory is not writable");texturePrepareWrite(raw,sizeof *raw);validateObjectStorage(raw,sizeof *raw);
    for(NSValue *v in objectSpans.allValues){NSRange r=v.rangeValue;if(memoryOverlap(raw,sizeof *raw,(void*)r.location,r.length))return report("texture object overlaps live NVN object");}
    BWTexture *t=[BWTexture new];t.metadata=*b;t.descriptor=textureDescriptor(b);nvn_texlayout layout;nvn_texdesc td=t.descriptor;nvn_tex_layout(&td,&layout);t.layout=layout;
    if(linear){td.stride=(uint32_t)linearLayout.stride;t.descriptor=td;layout.size=layout.layer_size=linearLayout.size;layout.align=(uint32_t)linearLayout.alignment;t.layout=layout;}
    t.blockLinear=!(td.flags&0x10);t.depth=b->format>=NVN_FORMAT_DEPTH16&&b->format<=NVN_FORMAT_DEPTH32F_STENCIL8;
    if(t.depth&&b->format!=NVN_FORMAT_DEPTH16&&b->format!=NVN_FORMAT_DEPTH32F)return report("Combined depth/stencil storage not implemented");
    if(t.depth&&b->target==NVN_TEXTURE_TARGET_2D_ARRAY)return report("Array depth texture semantics are not yet validated");
    MTLTextureDescriptor *desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:(MTLPixelFormat)format width:b->width height:b->height mipmapped:b->levels>1];desc.mipmapLevelCount=b->levels;
    if(b->target==NVN_TEXTURE_TARGET_2D_ARRAY){desc.textureType=MTLTextureType2DArray;desc.arrayLength=b->depth;}
    desc.storageMode=t.blockLinear?MTLStorageModePrivate:pool.buffer.storageMode;desc.usage=MTLTextureUsageShaderRead|MTLTextureUsagePixelFormatView;if((t.blockLinear&&!(b->format>=0x79&&b->format<=0x94))||(b->flags&0x40))desc.usage|=MTLTextureUsageRenderTarget;
    if(!t.blockLinear)t.texture=[pool.buffer newTextureWithDescriptor:desc offset:pool.bufferOffset+b->offset bytesPerRow:linearLayout.stride];
    else t.texture=[gpu newTextureWithDescriptor:desc];
    if(!t.texture)return report("Metal texture creation failed");t.pool=pool;t.device=device;t.poolGeneration=pool.generation;t.rawPool=b->pool;t.offset=b->offset;t.stride=linear?linearLayout.stride:b->stride;t.width=b->width;t.height=b->height;t.format=b->format;put(raw,t);t.identity=((BWObject*)raw)->serial;return 1;
}
static char texturePendingKey;
static NSMutableDictionary *texturePendingRecord(BWTexture *t,id<MTLCommandBuffer> cb,BOOL create){
    NSMutableDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);if(!records&&create){records=[NSMutableDictionary new];objc_setAssociatedObject(cb,&texturePendingKey,records,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}
    NSMutableDictionary *record=records[@(t.identity)];if(!record&&create){record=[@{@"texture":t} mutableCopy];records[@(t.identity)]=record;}return record;
}
static void textureAuthority(BWTexture *t,id<MTLCommandBuffer> cb){
    const void *storage=(char*)t.pool.memory+t.offset;nvn_texown_hit hit;
    if(nvn_texown_find(storage,t.layout.size,&hit)&&(hit.retired||hit.serial!=t.identity))fatal("texture storage authority belongs to another live/retired Metal resource");
    NSDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);
    for(NSDictionary *record in records.allValues)if([record[@"gpu"] boolValue]){BWTexture *other=record[@"texture"];if(other!=t&&(t.blockLinear||other.blockLinear)&&memoryOverlap(storage,t.layout.size,(char*)other.pool.memory+other.offset,other.layout.size))fatal("texture storage authority conflicts with pending GPU writes");}
}
static char textureBufferWritesKey;
static void textureBufferCopy(id<MTLCommandBuffer> cb,const void *source,const void *destination,size_t bytes){
    nvn_texown_hit hit;if(nvn_texown_find(source,bytes,&hit)||nvn_texown_find(destination,bytes,&hit))fatal("buffer address aliases private texture storage authority");
    NSDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);for(NSDictionary *record in records.allValues)if([record[@"gpu"] boolValue]){BWTexture *t=record[@"texture"];const void *storage=(char*)t.pool.memory+t.offset;if(t.blockLinear&&(memoryOverlap(source,bytes,storage,t.layout.size)||memoryOverlap(destination,bytes,storage,t.layout.size)))fatal("buffer address aliases private texture pending GPU writes");}
    NSMutableArray *writes=objc_getAssociatedObject(cb,&textureBufferWritesKey);if(!writes){writes=[NSMutableArray new];objc_setAssociatedObject(cb,&textureBufferWritesKey,writes,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}[writes addObject:[NSValue valueWithRange:NSMakeRange((NSUInteger)destination,bytes)]];
}
static void texturePrepareCommit(id<MTLCommandBuffer> cb){
    NSDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);
    for(NSDictionary *record in records.allValues)if([record[@"gpu"] boolValue]){BWTexture *t=record[@"texture"];if(t.blockLinear&&nvn_texown_claim_checked(t.identity,(char*)t.pool.memory+t.offset,t.layout.size))fatal("texture storage authority allocation failed");}
}
/* Encoding is speculative: no cache certificate, content hash or GPU-written
 * state becomes visible until this exact command buffer has been committed. */
static void texturePublishCommitted(id<MTLCommandBuffer> cb){
    NSDictionary *records=objc_getAssociatedObject(cb,&texturePendingKey);if(!records.count)return;
    for(NSDictionary *record in records.allValues){BWTexture *t=record[@"texture"];if(record[@"hash"])t.uploadHash=record[@"hash"];if([record[@"gpu"] boolValue])t.gpuWritten=YES;if(record[@"epoch"])t.drawEpoch=[record[@"epoch"] unsignedLongLongValue];}
    for(NSDictionary *record in records.allValues)if(record[@"hash"]){BWTexture *t=record[@"texture"];const void *storage=(char*)t.pool.memory+t.offset;
        if(nvn_texflush_note_checked(t.identity,storage,t.layout.layer_size,t.layout.layers,0,t.layout.layers,1))fatal("texture upload coherence certificate allocation failed");
        if(![digest(storage,t.layout.size) isEqual:record[@"hash"]])nvn_texflush_note_checked(t.identity,storage,t.layout.layer_size,t.layout.layers,0,t.layout.layers,0);
    }
    objc_setAssociatedObject(cb,&texturePendingKey,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}
static void uploadTexture(BWTexture *input,id<MTLCommandBuffer> cb){
    textureLive(input);BWTexture *t=input.base?:input;textureAuthority(t,cb);if(!t.blockLinear)return;
    const void *poolStorage=(char*)t.pool.memory+t.offset;
    NSArray *bufferWrites=objc_getAssociatedObject(cb,&textureBufferWritesKey);for(NSValue *value in bufferWrites){NSRange r=value.rangeValue;if(memoryOverlap(poolStorage,t.layout.size,(const void*)r.location,r.length))fatal("private texture import aliases pending buffer writes");}
    NSMutableData *snapshot=[NSMutableData dataWithLength:t.layout.size];if(!snapshot)fatal("texture pool snapshot allocation failed");vm_size_t got=0;
    if((memoryAccessValidator&&!memoryAccessValidator(poolStorage,t.layout.size,0))||vm_read_overwrite(mach_task_self(),(vm_address_t)poolStorage,t.layout.size,(vm_address_t)snapshot.mutableBytes,&got)!=KERN_SUCCESS||got!=t.layout.size)fatal("texture pool storage is not readable");
    const void *source=snapshot.bytes;NSData *hash=digest(source,t.layout.size);
    NSMutableDictionary *pending=texturePendingRecord(t,cb,NO);NSData *expected=pending[@"hash"]?:t.uploadHash;
    if(t.gpuWritten||[pending[@"gpu"] boolValue]){if(![hash isEqual:expected])fatal("texture CPU storage changed outside host writes; GPU content cannot be reconciled");return;}
    if([hash isEqual:expected])return;
    for(unsigned layer=0;layer<t.layout.layers;layer++)for(unsigned level=0;level<t.descriptor.levels;level++){
        uint32_t row,rows,slices,bpb;uint64_t base;unsigned th,td;nvn_texdesc d=t.descriptor;
        if(nvn_deswizzle_geom(&d,level,layer,&row,&rows,&slices,&base,&th,&td,&bpb)||slices!=1)fatal("block-linear texture geometry");
        size_t pitch=(row+255)&~255u;id<MTLBuffer> staging=[gpu newBufferWithLength:pitch*rows options:MTLResourceStorageModeShared];if(!staging||nvn_deswizzle(&d,level,layer,source,staging.contents,pitch))fatal("block-linear texture decode");
        id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];if(!blit)fatal("block-linear texture upload encoder allocation failed");[blit copyFromBuffer:staging sourceOffset:0 sourceBytesPerRow:pitch sourceBytesPerImage:pitch*rows sourceSize:MTLSizeMake(MAX(1,t.width>>level),MAX(1,t.height>>level),1) toTexture:t.texture destinationSlice:layer destinationLevel:level destinationOrigin:MTLOriginMake(0,0,0)];[blit endEncoding];
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
NVN(nvnTextureGetStorageSize,int,const NVNtexture *raw){return (int)textureObject(raw).layout.size;}
NVN(nvnTextureGetTextureAddress,NVNtextureAddress,const NVNtexture *raw){BWTexture *t=textureObject(raw);return (uintptr_t)t.pool.memory+t.offset;}
NVN(nvnTextureGetDebugID,uint64_t,const NVNtexture *raw){BWTexture *t=textureObject(raw);return (t.pool.device.flags&NVN_DEVICE_FLAG_DEBUG_ENABLE)?t.identity:0;}
NVN(nvnTextureCompare,NVNboolean,const NVNtexture *a,const NVNtexture *b){BWTexture *x=textureObject(a),*y=textureObject(b);TextureBuilder p=x.metadata,q=y.metadata;return x.pool==y.pool&&x.offset==y.offset&&!memcmp(&p.flags,&q.flags,0x34)&&p.stride==q.stride&&p.packaged==q.packaged;}
/* A selected view owns its Metal view and base storage. Descriptor and command
 * capture copy all guest view bytes, so later guest mutations cannot retarget it. */
static BWTexture *textureSelected(BWTexture *base,TextureView v,BOOL attachment){
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
    id<MTLTexture> texture=[base.texture newTextureViewWithPixelFormat:(MTLPixelFormat)pixel textureType:type levels:NSMakeRange(level,levels) slices:NSMakeRange(layer,layers) swizzle:MTLTextureSwizzleChannelsMake(selectors[swizzle[0]],selectors[swizzle[1]],selectors[swizzle[2]],selectors[swizzle[3]])];
    if(!texture)fatal("Metal texture view creation failed");
    BWTexture *t=[BWTexture new];t.base=base;t.texture=texture;t.pool=base.pool;t.poolGeneration=base.poolGeneration;t.device=base.device;t.metadata=base.metadata;t.format=format;t.width=MAX(1,base.width>>level);t.height=MAX(1,base.height>>level);t.depth=base.depth;t.descriptor=base.descriptor;t.layout=base.layout;
    return t;
}
NVN(nvnTextureGetViewOffset,ptrdiff_t,const NVNtexture *raw,const NVNtextureView *view){BWTexture *t=textureObject(raw);TextureView v=textureView(view);unsigned level=(v.flags&1)?v.baseLevel:0,layer=(v.flags&2)?v.minLayer:0;if(level>=t.descriptor.levels||layer>=t.layout.layers)fatal("texture view offset bounds");nvn_texdesc d=t.descriptor;return nvn_tex_level_offset(&d,level)+layer*t.layout.layer_size;}
static void textureDrain(void){for(id o in objects.allValues)if([o isKindOfClass:[BWQueue class]])finishBuffer(((BWQueue*)o).latest);}
static void textureReadBytes(const void *src,void *dst,size_t bytes,const char *reason){
    if(!src||bytes>UINTPTR_MAX-(uintptr_t)src||(memoryAccessValidator&&!memoryAccessValidator(src,bytes,0)))fatal(reason);
    vm_size_t got=0;if(vm_read_overwrite(mach_task_self(),(vm_address_t)src,bytes,(vm_address_t)dst,&got)!=KERN_SUCCESS||got!=bytes)fatal(reason);
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
    while(bytes){mach_msg_type_number_t count=(mach_msg_type_number_t)MIN(bytes,1024u*1024u);if(vm_write(mach_task_self(),(vm_address_t)destination,(vm_offset_t)source,count)!=KERN_SUCCESS)fatal(reason);destination=(char*)destination+count;source=(const char*)source+count;bytes-=count;}
}
static int textureWriteRun(void *opaque,uint64_t offset,const void *source,size_t bytes){return vm_write(mach_task_self(),(vm_address_t)((char*)opaque+offset),(vm_offset_t)source,(mach_msg_type_number_t)bytes)==KERN_SUCCESS?0:-1;}
static void textureCPUWrite(const NVNtexture *raw,const NVNtextureView *view,const NVNcopyRegion *region,const void *source,ptrdiff_t row,ptrdiff_t image){
    BWTexture *t=textureObject(raw);TextureView v=textureView(view);nvn_texwrite_plan p=texturePlan(t,v,region,row,image);if(!p.width)return;
    if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS)fatal("texture storage has no CPU mapping");
    NSMutableData *copy=textureSourceSnapshot(source,&p);void *storage=(char*)t.pool.memory+t.offset;
    int priorCoherence=nvn_texflush_verify(t.identity,storage,t.layout.layer_size,t.layout.layers,p.layer,p.depth);
    if(t.gpuWritten&&priorCoherence==NVN_TEXFLUSH_CHANGED)fatal("texture CPU storage changed outside host writes before region write");
    textureDrain();id<MTLCommandBuffer> cb=[commandQueue commandBuffer];if(!cb)fatal("texture write command allocation failed");uploadTexture(t,cb);
    /* All allocation, source reads and geometry checks precede pool mutation.
     * Import every untouched mip/layer before a first partial write. */
    size_t pitch=(p.wb+255ull)&~255ull,imagePitch=pitch*p.hb;
    id<MTLBuffer> staging=[gpu newBufferWithLength:imagePitch*p.depth options:MTLResourceStorageModeShared];if(!staging)fatal("texture write staging allocation failed");
    for(unsigned z=0;z<p.depth;z++)for(unsigned y=0;y<p.hb;y++)memcpy((char*)staging.contents+z*imagePitch+y*pitch,(const char*)copy.bytes+z*p.src_img+y*p.src_row,p.wb);
    id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];if(!blit)fatal("texture write blit allocation failed");
    for(unsigned z=0;z<p.depth;z++)[blit copyFromBuffer:staging sourceOffset:z*imagePitch sourceBytesPerRow:pitch sourceBytesPerImage:imagePitch sourceSize:MTLSizeMake(p.width,p.height,1) toTexture:t.texture destinationSlice:p.layer+z destinationLevel:p.level destinationOrigin:MTLOriginMake(p.x0,p.y0,0)];[blit endEncoding];
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
    for(unsigned z=0;z<p.depth;z++)[blit copyFromTexture:t.texture sourceSlice:p.layer+z sourceLevel:p.level sourceOrigin:MTLOriginMake(p.x0,p.y0,0) sourceSize:MTLSizeMake(p.width,p.height,1) toBuffer:buffer destinationOffset:z*imagePitch destinationBytesPerRow:pitch destinationBytesPerImage:imagePitch];[blit endEncoding];commitBuffer(cb);finishBuffer(cb);
    for(unsigned z=0;z<p.depth;z++)for(unsigned y=0;y<p.hb;y++)textureWriteBytes((char*)destination+z*p.src_img+y*p.src_row,(char*)buffer.contents+z*imagePitch+y*pitch,p.wb,"texture read destination copy failed");
}
NVN(nvnTextureWriteTexels,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r,const void *src){textureCPUWrite(t,v,r,src,0,0);}
NVN(nvnTextureWriteTexelsStrided,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r,const void *src,ptrdiff_t row,ptrdiff_t image){textureCPUWrite(t,v,r,src,row,image);}
NVN(nvnTextureReadTexels,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r,void *dst){textureCPURead(t,v,r,dst,0,0);}
NVN(nvnTextureReadTexelsStrided,void,const NVNtexture *t,const NVNtextureView *v,const NVNcopyRegion *r,void *dst,ptrdiff_t row,ptrdiff_t image){textureCPURead(t,v,r,dst,row,image);}

static void textureGPUWrite(BWTexture *input,id<MTLCommandBuffer> cb){BWTexture *t=input.base?:input;textureLive(t);textureAuthority(t,cb);texturePendingRecord(t,cb,YES)[@"gpu"]=@YES;}
static void textureFlush(const NVNtexture *raw,const NVNtextureView *view){
    BWTexture *t=textureObject(raw);if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS)return;
    TextureView v={0};if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_CACHED)v=textureView(view);
    uint32_t first,count;const char *why=NULL;nvn_texdesc d=t.descriptor;int status=nvn_texflush_layers(&d,!!(v.flags&2),v.minLayer,v.numLayers,&first,&count,&why);if(status<0)fatal(why);if(status==NVN_TEXFLUSH_EMPTY)return;
    const uint8_t *storage=(uint8_t*)t.pool.memory+t.offset;textureDrain();
    if(!t.blockLinear){if(memoryAccessValidator&&!memoryAccessValidator(storage,t.layout.size,0))fatal("texture flush source is not readable");textureVMAccess(storage,t.layout.size,NO);if(!t.pool.coherent)textureReadBytes(storage,(char*)t.pool.buffer.contents+t.pool.bufferOffset+t.offset,t.layout.size,"texture flush source copy failed");return;}
    if(t.gpuWritten){int state=nvn_texflush_verify(t.identity,storage,t.layout.layer_size,t.layout.layers,first,count);if(state!=NVN_TEXFLUSH_CLEAN)fatal(state==NVN_TEXFLUSH_CHANGED?"texture flush CPU storage changed outside host writes":"texture flush lacks complete write tracking");return;}
    /* No private GPU writes exist: a real upload can reconcile current CPU
     * bytes even without a fault bridge. Never certify a skipped host copy. */
    t.uploadHash=nil;id<MTLCommandBuffer> cb=[commandQueue commandBuffer];if(!cb)fatal("texture flush command allocation failed");uploadTexture(t,cb);commitBuffer(cb);finishBuffer(cb);
}
static void textureInvalidate(const NVNtexture *raw,const NVNtextureView *view){
    BWTexture *t=textureObject(raw);if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS)return;
    TextureView v={0};if(t.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_CACHED)v=textureView(view);
    uint32_t first,count;const char *why=NULL;nvn_texdesc d=t.descriptor;int status=nvn_texflush_layers(&d,!!(v.flags&2),v.minLayer,v.numLayers,&first,&count,&why);if(status<0)fatal(why);if(status==NVN_TEXFLUSH_EMPTY)return;
    textureDrain();uint8_t *storage=(uint8_t*)t.pool.memory+t.offset;texturePrepareWrite(storage+first*t.layout.layer_size,count*t.layout.layer_size);
    if(!t.blockLinear){if(!t.pool.coherent)textureWriteBytes(storage,(char*)t.pool.buffer.contents+t.pool.bufferOffset+t.offset,t.layout.size,"texture invalidate destination copy failed");return;}
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
    return memoryOverlap((char*)a.pool.memory+a.offset,a.layout.size,(char*)b.pool.memory+b.offset,b.layout.size);
}
static BOOL linearTextureBufferOverlap(BWTexture *input,NSDictionary *binding){
    BWTexture *t=input.base?:input;if(!t||t.blockLinear||!binding)return NO;
    return memoryOverlap((char*)t.pool.memory+t.offset,t.layout.size,(const void*)(uintptr_t)[binding[@"address"] unsignedLongLongValue],[binding[@"size"] unsignedLongLongValue]);
}
static void validateTextureSubmission(BWQueue *queue,NSArray<NSArray *> *groups){
    BWTexture *target=queue.replay.target,*depth=queue.replay.depth;BWDescriptors *texturePool=queue.replay.texturePool,*samplerPool=queue.replay.samplerPool;BWProgram *program=queue.replay.program;NSMutableDictionary *textures=[queue.replay.textures mutableCopy];
    NSMutableDictionary *storage=[queue.replay.storage mutableCopy],*uniforms=[queue.replay.uniforms mutableCopy],*vertices=[queue.replay.vertices mutableCopy];
    NSData *attributes=queue.replay.attributes;
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
        else if([op isEqual:@"clear"]||[op isEqual:@"depthClear"]||([op isEqual:@"draw"]&&[c[@"count"] unsignedLongLongValue]&&[c[@"instances"] unsignedLongLongValue])){
            if(target){textureLive(target);if(target.device!=queue.device)fatal("texture render target device mismatch");}if(depth){textureLive(depth);if(depth.device!=queue.device)fatal("texture depth target device mismatch");}
            if(![op isEqual:@"draw"])continue;
            NSMutableArray<BWTexture *> *sampled=[NSMutableArray new];NSMutableArray<NSDictionary *> *buffers=[NSMutableArray new],*writable=[NSMutableArray new];
            const VertexAttribute *active=attributes.bytes;
            for(size_t i=0;i<attributes.length/sizeof *active;i++)if(active[i].format){NSDictionary *binding=vertices[@(active[i].stream)];if(binding)[buffers addObject:binding];}
            for(NSNumber *stage in program.stageShaders){BWShader *shader=program.stageShaders[stage];const botw_graphics_shader_binding *bindings=shader.bindings.bytes;
                for(size_t i=0;i<shader.bindings.length/sizeof *bindings;i++){if(bindings[i].kind!=BOTW_GRAPHICS_TEXTURE&&bindings[i].kind!=BOTW_GRAPHICS_SAMPLER)continue;
                    NSNumber *h=textures[resourceKey(stage,@(bindings[i].nvn_slot))];if(!h)fatal("unbound texture descriptor");
                    BWDescriptors *p=bindings[i].kind==BOTW_GRAPHICS_TEXTURE?texturePool:samplerPool;validateTextureDescriptorPool(p,queue.device);
                    if(bindings[i].kind==BOTW_GRAPHICS_TEXTURE){BWTexture *t=p.entries[@(textureDescriptorIndex(h.unsignedLongLongValue))];if(!t)fatal("unbound texture descriptor");textureLive(t);if(t.device!=queue.device)fatal("texture descriptor device mismatch");[sampled addObject:t];}
                }
                for(size_t i=0;i<shader.bindings.length/sizeof *bindings;i++){
                    BOOL writes=bindings[i].kind==BOTW_GRAPHICS_STORAGE,isUniform=bindings[i].kind==BOTW_GRAPHICS_UNIFORM;
                    if(!writes&&!isUniform)continue;NSDictionary *binding=(writes?storage:uniforms)[resourceKey(stage,@(bindings[i].nvn_slot))];
                    if(binding){[buffers addObject:binding];if(writes)[writable addObject:binding];}
                }
            }
            /* Buffer-backed textures are coherent between encoders, not
             * within one render pass. Validate the entire submission before
             * a preceding counter segment can publish any GPU operation. */
            for(BWTexture *t in sampled){BWTexture *base=t.base?:t;
                if((!base.blockLinear||!(target.base?:target).blockLinear)&&textureStorageOverlap(t,target))fatal("linear render pass feedback aliases sampled texture and color attachment");
                if((!base.blockLinear||!(depth.base?:depth).blockLinear)&&textureStorageOverlap(t,depth))fatal("linear render pass feedback aliases sampled texture and depth attachment");
                for(NSDictionary *binding in writable)if(linearTextureBufferOverlap(t,binding))fatal("linear render pass feedback aliases sampled texture and storage buffer");
            }
            for(NSDictionary *binding in buffers)if(linearTextureBufferOverlap(target,binding)||linearTextureBufferOverlap(depth,binding))fatal("linear render pass feedback aliases attachment and shader/vertex buffer");
        }
    }
}
