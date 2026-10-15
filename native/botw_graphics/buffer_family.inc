/* Complete fixed MIT BotW buffer module behind checked base-52 adapters.
 * Upstream field/range/slice functions remain in vendor/botw-buffer. */
#define BWU_MAGIC(a,b,c,d) ((uint32_t)(a)|((uint32_t)(b)<<8)|((uint32_t)(c)<<16)|((uint32_t)(d)<<24))
static uint64_t bwu_new_serial(void){return ++serial;}
#define BWU_LOG(...) report(__VA_ARGS__)
#define BWU_DIE(message) fatal("buffer cache range: " message)
#define BUFFER_UPSTREAM(name,ret,...) static ret bwu_##name(__VA_ARGS__)
#include "vendor/botw-buffer/buffer.inc"
#undef BUFFER_UPSTREAM
#undef BWU_DIE
#undef BWU_LOG
static void bufferMemory(const void *raw,size_t bytes,BOOL write,BOOL builder){
    if(!raw||(memoryAccessValidator&&!memoryAccessValidator(raw,bytes,write)))fatal(builder?"buffer builder memory is not fully accessible":"buffer object memory is not fully accessible");
    if(write){validateObjectStorage((void*)raw,bytes);for(NSValue *v in objectSpans.allValues){NSRange span=v.rangeValue;if(memoryOverlap(raw,bytes,(void*)span.location,span.length)){
        if(builder||span.location!=(NSUInteger)raw||span.length!=sizeof(NVNbuffer)||![objects[@(((BWObject*)raw)->serial)] isKindOfClass:[BWBuffer class]])fatal("buffer storage overlaps live NVN object");}}}
}
static BWBuffer *bufferObject(const NVNbuffer *raw,BOOL requirePool){
    bufferMemory(raw,sizeof *raw,NO,NO);BWBuffer *b=get(raw,[BWBuffer class]);
    if(requirePool&&(!b.pool.live||b.pool.generation!=b.poolGeneration||objects[@(b.poolGeneration)]!=b.pool))fatal("stale buffer pool generation");
    if(requirePool&&(![objects.allValues containsObject:b.device]||b.pool.device!=b.device))fatal("buffer device lifetime mismatch");
    return b;
}
NVN(nvnBufferBuilderSetDevice,void,NVNbufferBuilder *b,NVNdevice *d){bufferMemory(b,sizeof *b,YES,YES);get(d,[BWDevice class]);bwu_nvnBufferBuilderSetDevice(b,d);}
NVN(nvnBufferBuilderSetDefaults,void,NVNbufferBuilder *b){bufferMemory(b,sizeof *b,YES,YES);bwu_nvnBufferBuilderSetDefaults(b);}
NVN(nvnBufferBuilderSetStorage,void,NVNbufferBuilder *b,NVNmemoryPool *p,ptrdiff_t offset,size_t bytes){bufferMemory(b,sizeof *b,YES,YES);bwu_nvnBufferBuilderSetStorage(b,p,offset,bytes);}
NVN(nvnBufferBuilderGetMemoryPool,NVNmemoryPool*,const NVNbufferBuilder *b){bufferMemory(b,sizeof *b,NO,YES);return bwu_nvnBufferBuilderGetMemoryPool(b);}
NVN(nvnBufferBuilderGetMemoryOffset,ptrdiff_t,const NVNbufferBuilder *b){bufferMemory(b,sizeof *b,NO,YES);return bwu_nvnBufferBuilderGetMemoryOffset(b);}
NVN(nvnBufferBuilderGetSize,size_t,const NVNbufferBuilder *b){bufferMemory(b,sizeof *b,NO,YES);return bwu_nvnBufferBuilderGetSize(b);}
NVN(nvnBufferInitialize,NVNboolean,NVNbuffer *raw,const NVNbufferBuilder *builder){
    bufferMemory(builder,sizeof *builder,NO,YES);bufferMemory(raw,sizeof *raw,YES,NO);
    if(memoryOverlap(raw,sizeof *raw,builder,sizeof *builder))return report("buffer object overlaps its builder");
    for(NSValue *v in objectSpans.allValues){NSRange span=v.rangeValue;if(memoryOverlap(raw,sizeof *raw,(void*)span.location,span.length))return report("buffer object storage is already live");}
    struct bwu_builder snapshot;memcpy(&snapshot,builder,sizeof snapshot);BWDevice *device=get(snapshot.device,[BWDevice class]);BWPool *pool=get(snapshot.pool,[BWPool class]);
    if(!pool.live||pool.device!=device)return report("buffer device mismatch or expired pool");
    if(pool.flags&(NVN_MEMORY_POOL_FLAGS_PHYSICAL|NVN_MEMORY_POOL_FLAGS_VIRTUAL))return report("buffer storage does not support physical/virtual pools");
    BWBuffer *b=[BWBuffer new];b.pool=pool;b.device=device;b.rawPool=(NVNmemoryPool*)snapshot.pool;b.poolGeneration=pool.generation;
    b->backing=(bwu_pool){BWU_MAGIC('P','O','O','L'),(unsigned)pool.flags,pool.memory,pool.size};snapshot.pool=&b->backing;
    struct bwu_buffer value;if(!bwu_nvnBufferInitialize((NVNbuffer*)&value,(const NVNbufferBuilder*)&snapshot))return report("buffer storage range is outside its memory pool");
    if(value.size&&!pool.buffer)return report("buffer storage has no Metal backing");b.value=value;put(raw,b);return 1;
}
NVN(nvnBufferFinalize,void,NVNbuffer *raw){bufferMemory(raw,sizeof *raw,YES,NO);BWBuffer *b=bufferObject(raw,NO);struct bwu_buffer value=b.value;bwu_nvnBufferFinalize((NVNbuffer*)&value);b.value=value;removeObject(raw);}
NVN(nvnBufferSetDebugLabel,void,NVNbuffer *raw,const char *label){BWBuffer *b=bufferObject(raw,YES);if(!label){b.label=nil;return;}size_t n=0;while(n<256){if(memoryAccessValidator&&!memoryAccessValidator(label+n,1,0))fatal("buffer debug label is not readable");if(!label[n])break;n++;}if(n==256)fatal("buffer debug label exceeds 255 bytes");b.label=[[NSString alloc]initWithBytes:label length:n encoding:NSUTF8StringEncoding];if(!b.label)fatal("buffer debug label is not UTF-8");}
NVN(nvnBufferMap,void*,const NVNbuffer *raw){BWBuffer *b=bufferObject(raw,YES);struct bwu_buffer value=b.value;return bwu_nvnBufferMap((const NVNbuffer*)&value);}
NVN(nvnBufferGetAddress,NVNbufferAddress,const NVNbuffer *raw){BWBuffer *b=bufferObject(raw,YES);if(b.pool.flags&NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS)fatal("buffer pool disallows GPU address access");struct bwu_buffer value=b.value;return bwu_nvnBufferGetAddress((const NVNbuffer*)&value);}
NVN(nvnBufferGetMemoryPool,NVNmemoryPool*,const NVNbuffer *raw){BWBuffer *b=bufferObject(raw,YES);struct bwu_buffer value=b.value;if((void*)bwu_nvnBufferGetMemoryPool((const NVNbuffer*)&value)!=&b->backing)fatal("buffer pool identity mismatch");return b.rawPool;}
NVN(nvnBufferGetMemoryOffset,ptrdiff_t,const NVNbuffer *raw){BWBuffer *b=bufferObject(raw,YES);struct bwu_buffer value=b.value;return bwu_nvnBufferGetMemoryOffset((const NVNbuffer*)&value);}
NVN(nvnBufferGetSize,size_t,const NVNbuffer *raw){BWBuffer *b=bufferObject(raw,YES);struct bwu_buffer value=b.value;return bwu_nvnBufferGetSize((const NVNbuffer*)&value);}
/* Base-52 returns zero without debug enable levels (device flag mask 0x75). */
NVN(nvnBufferGetDebugID,uint64_t,const NVNbuffer *raw){BWBuffer *b=bufferObject(raw,YES);if(!(b.device.flags&0x75))return 0;struct bwu_buffer value=b.value;return bwu_nvnBufferGetDebugID((const NVNbuffer*)&value);}
NVN(nvnBufferFlushMappedRange,void,const NVNbuffer *raw,ptrdiff_t offset,size_t bytes){BWBuffer *b=bufferObject(raw,YES);struct bwu_buffer value=b.value;bwu_nvnBufferFlushMappedRange((const NVNbuffer*)&value,offset,bytes);if(bytes&&(b.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS))fatal("buffer cache range requires CPU access");if((size_t)offset>PTRDIFF_MAX-(size_t)value.offset)fatal("buffer cache range offset overflow");h_nvnMemoryPoolFlushMappedRange(b.rawPool,value.offset+offset,bytes);}
NVN(nvnBufferInvalidateMappedRange,void,const NVNbuffer *raw,ptrdiff_t offset,size_t bytes){BWBuffer *b=bufferObject(raw,YES);struct bwu_buffer value=b.value;bwu_nvnBufferInvalidateMappedRange((const NVNbuffer*)&value,offset,bytes);if(bytes&&(b.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS))fatal("buffer cache range requires CPU access");if((size_t)offset>PTRDIFF_MAX-(size_t)value.offset)fatal("buffer cache range offset overflow");h_nvnMemoryPoolInvalidateMappedRange(b.rawPool,value.offset+offset,bytes);}
#undef BWU_MAGIC
