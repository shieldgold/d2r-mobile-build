// NVN buffer objects for BotW's 55.15 API. SDK+0x4b8a90 initializes 0x30 bytes;
// builder defaults preserve the device and the memory-pool getters return pointers.
#include "../../host/dm.h"
#include "nvn_protos.h"
#include "nvn_obj.h"

struct buffer_builder { uint64_t reserved; NVNdevice *device; pool_t *pool; ptrdiff_t offset; size_t size; };
struct buffer { uint32_t magic, reserved; NVNdevice *device; pool_t *pool; ptrdiff_t offset; size_t size; uint64_t serial; };
_Static_assert(sizeof(struct buffer_builder) <= 0x40, "BotW buffer builder ABI");
_Static_assert(sizeof(struct buffer) == 0x30, "BotW buffer ABI");
FITS(struct buffer_builder, NVNbufferBuilder);
FITS(struct buffer, NVNbuffer);
uint64_t nvn_new_serial(void);

typedef struct { const char *name; void *fn; } buffer_entry;
#define API(name, ret, ...) \
    static ret impl_##name(__VA_ARGS__); \
    static const NVNT_##name check_##name __attribute__((unused)) = impl_##name; \
    __attribute__((used, section("__DATA,swnvn"))) static const buffer_entry entry_##name = {#name, (void *)impl_##name}; \
    static ret impl_##name(__VA_ARGS__)
#define BB(x) ((struct buffer_builder *)(x))
#define BC(x) ((const struct buffer_builder *)(x))
#define BUF(x) ((struct buffer *)(x))
#define CBUF(x) ((const struct buffer *)(x))
#define BUFFER_MAGIC MAGIC('B','U','F',0)
API(nvnBufferBuilderSetDevice, void, NVNbufferBuilder *b, NVNdevice *device) { BB(b)->device = device; }
API(nvnBufferBuilderSetDefaults, void, NVNbufferBuilder *b) { BB(b)->pool = NULL; BB(b)->offset = 0; BB(b)->size = 0; }
API(nvnBufferBuilderSetStorage, void, NVNbufferBuilder *b, NVNmemoryPool *pool, ptrdiff_t offset, size_t size) {
    BB(b)->pool = (pool_t *)pool; BB(b)->offset = offset; BB(b)->size = size;
}
API(nvnBufferBuilderGetDevice, NVNdevice *, const NVNbufferBuilder *b) { return BC(b)->device; }
API(nvnBufferBuilderGetMemoryPool, NVNmemoryPool *, const NVNbufferBuilder *b) { return (NVNmemoryPool *)BC(b)->pool; }
API(nvnBufferBuilderGetMemoryOffset, ptrdiff_t, const NVNbufferBuilder *b) { return BC(b)->offset; }
API(nvnBufferBuilderGetSize, size_t, const NVNbufferBuilder *b) { return BC(b)->size; }
API(nvnBufferInitialize, NVNboolean, NVNbuffer *buffer, const NVNbufferBuilder *builder) {
    const struct buffer_builder *b = BC(builder);
    if (!b->pool || b->pool->magic != MAGIC('P','O','O','L') || b->offset < 0 ||
        (uint64_t)b->offset > b->pool->size || b->size > b->pool->size - (uint64_t)b->offset) {
        LOG("[ERROR] NVN buffer storage lies outside its memory pool"); return 0;
    }
    if (b->pool->flags & NVN_MEMORY_POOL_FLAGS_PHYSICAL) {
        LOG("[ERROR] NVN buffers cannot use physical-only pools directly"); return 0;
    }
    *BUF(buffer) = (struct buffer){BUFFER_MAGIC, 0, b->device, b->pool, b->offset, b->size, nvn_new_serial()};
    return 1;
}
API(nvnBufferSetDebugLabel, void, NVNbuffer *b, const char *label) { (void)b; (void)label; }
API(nvnBufferFinalize, void, NVNbuffer *b) { BUF(b)->magic = 0; }
API(nvnBufferMap, void *, const NVNbuffer *b) {
    const struct buffer *o = CBUF(b);
    return o->magic == BUFFER_MAGIC && o->pool->mem &&
           !(o->pool->flags & (NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS | NVN_MEMORY_POOL_FLAGS_VIRTUAL))
           ? o->pool->mem + o->offset : NULL;
}
API(nvnBufferGetAddress, NVNbufferAddress, const NVNbuffer *b) {
    const struct buffer *o = CBUF(b);
    return o->magic == BUFFER_MAGIC && o->pool->mem
           ? (NVNbufferAddress)(uintptr_t)(o->pool->mem + o->offset) : 0;
}
static void range_check(const NVNbuffer *b, ptrdiff_t offset, size_t size) {
    const struct buffer *o = CBUF(b);
    if (o->magic != BUFFER_MAGIC || offset < 0 || (size_t)offset > o->size || size > o->size - (size_t)offset)
        die("NVN buffer cache operation is out of bounds");
}
// Buffers use shared coherent Metal storage; callers still synchronize GPU access with NVN fences.
API(nvnBufferFlushMappedRange, void, const NVNbuffer *b, ptrdiff_t offset, size_t size) { range_check(b, offset, size); }
API(nvnBufferInvalidateMappedRange, void, const NVNbuffer *b, ptrdiff_t offset, size_t size) { range_check(b, offset, size); }
API(nvnBufferGetMemoryPool, NVNmemoryPool *, const NVNbuffer *b) { return (NVNmemoryPool *)CBUF(b)->pool; }
API(nvnBufferGetMemoryOffset, ptrdiff_t, const NVNbuffer *b) { return CBUF(b)->offset; }
API(nvnBufferGetSize, size_t, const NVNbuffer *b) { return CBUF(b)->size; }
API(nvnBufferGetDebugID, uint64_t, const NVNbuffer *b) { return CBUF(b)->serial; }
