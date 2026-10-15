/* Altered MIT source: base-52 layout and checked host adapter entry macro. */
#define BB(x) ((struct bwu_builder *)(x))
#define BC(x) ((const struct bwu_builder *)(x))
#define BUF(x) ((struct bwu_buffer *)(x))
#define CBUF(x) ((const struct bwu_buffer *)(x))
#define BUFFER_MAGIC BWU_MAGIC('B','U','F',0)
BUFFER_UPSTREAM(nvnBufferBuilderSetDevice, void, NVNbufferBuilder *b, NVNdevice *device) { BB(b)->device = device; }
BUFFER_UPSTREAM(nvnBufferBuilderSetDefaults, void, NVNbufferBuilder *b) { BB(b)->pool = NULL; BB(b)->offset = 0; BB(b)->size = 0; }
BUFFER_UPSTREAM(nvnBufferBuilderSetStorage, void, NVNbufferBuilder *b, NVNmemoryPool *pool, ptrdiff_t offset, size_t size) {
    BB(b)->pool = (bwu_pool *)pool; BB(b)->offset = offset; BB(b)->size = size;
}
BUFFER_UPSTREAM(nvnBufferBuilderGetMemoryPool, NVNmemoryPool *, const NVNbufferBuilder *b) { return (NVNmemoryPool *)BC(b)->pool; }
BUFFER_UPSTREAM(nvnBufferBuilderGetMemoryOffset, ptrdiff_t, const NVNbufferBuilder *b) { return BC(b)->offset; }
BUFFER_UPSTREAM(nvnBufferBuilderGetSize, size_t, const NVNbufferBuilder *b) { return BC(b)->size; }
BUFFER_UPSTREAM(nvnBufferInitialize, NVNboolean, NVNbuffer *buffer, const NVNbufferBuilder *builder) {
    const struct bwu_builder *b = BC(builder);
    if (!b->pool || b->pool->magic != BWU_MAGIC('P','O','O','L') || b->offset < 0 ||
        (uint64_t)b->offset > b->pool->size || b->size > b->pool->size - (uint64_t)b->offset) {
        BWU_LOG("[ERROR] NVN buffer storage lies outside its memory pool"); return 0;
    }
    if (b->pool->flags & NVN_MEMORY_POOL_FLAGS_PHYSICAL) {
        BWU_LOG("[ERROR] NVN buffers cannot use physical-only pools directly"); return 0;
    }
    *BUF(buffer) = (struct bwu_buffer){BUFFER_MAGIC, 0, b->device, b->pool, b->offset, b->size, bwu_new_serial()};
    return 1;
}
BUFFER_UPSTREAM(nvnBufferFinalize, void, NVNbuffer *b) { BUF(b)->magic = 0; }
BUFFER_UPSTREAM(nvnBufferMap, void *, const NVNbuffer *b) {
    const struct bwu_buffer *o = CBUF(b);
    return o->magic == BUFFER_MAGIC && o->pool->mem &&
           !(o->pool->flags & (NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS | NVN_MEMORY_POOL_FLAGS_VIRTUAL))
           ? o->pool->mem + o->offset : NULL;
}
BUFFER_UPSTREAM(nvnBufferGetAddress, NVNbufferAddress, const NVNbuffer *b) {
    const struct bwu_buffer *o = CBUF(b);
    return o->magic == BUFFER_MAGIC && o->pool->mem
           ? (NVNbufferAddress)(uintptr_t)(o->pool->mem + o->offset) : 0;
}
static void range_check(const NVNbuffer *b, ptrdiff_t offset, size_t size) {
    const struct bwu_buffer *o = CBUF(b);
    if (o->magic != BUFFER_MAGIC || offset < 0 || (size_t)offset > o->size || size > o->size - (size_t)offset)
        BWU_DIE("NVN buffer cache operation is out of bounds");
}
// Buffers use shared coherent Metal storage; callers still synchronize GPU access with NVN fences.
BUFFER_UPSTREAM(nvnBufferFlushMappedRange, void, const NVNbuffer *b, ptrdiff_t offset, size_t size) { range_check(b, offset, size); }
BUFFER_UPSTREAM(nvnBufferInvalidateMappedRange, void, const NVNbuffer *b, ptrdiff_t offset, size_t size) { range_check(b, offset, size); }
BUFFER_UPSTREAM(nvnBufferGetMemoryPool, NVNmemoryPool *, const NVNbuffer *b) { return (NVNmemoryPool *)CBUF(b)->pool; }
BUFFER_UPSTREAM(nvnBufferGetMemoryOffset, ptrdiff_t, const NVNbuffer *b) { return CBUF(b)->offset; }
BUFFER_UPSTREAM(nvnBufferGetSize, size_t, const NVNbuffer *b) { return CBUF(b)->size; }
BUFFER_UPSTREAM(nvnBufferGetDebugID, uint64_t, const NVNbuffer *b) { return CBUF(b)->serial; }

#undef BB
#undef BC
#undef BUF
#undef CBUF
#undef BUFFER_MAGIC
