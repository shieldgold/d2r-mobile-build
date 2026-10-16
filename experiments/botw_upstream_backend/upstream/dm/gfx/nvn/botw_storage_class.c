// The explicit ordinary-pool experiment uses one host storage class. Its allocator
// has no virtual alias table; this token is not a recovered native hardware kind.
// Virtual/physical mapping remains unsupported by these query adapters.
#include "../../host/dm.h"
#include "nvn_protos.h"
#include "nvn_obj.h"
#include <unistd.h>

typedef struct { const char *name; void *fn; } storage_class_entry;
#define API(name, type) \
    static NVNstorageClass impl_##name(const type *object); \
    static const NVNT_##name check_##name __attribute__((unused)) = impl_##name; \
    __attribute__((used, section("__DATA,swnvn"))) static const storage_class_entry entry_##name = {#name, (void *)impl_##name}; \
    static NVNstorageClass impl_##name(const type *object)

static void class_unsupported(const char *reason) {
    LOG("UNIMPL NVN texture storage class: %s", reason);
    const char *stop = getenv("DM_STOP_ON_UNIMPL");
    if (stop && *stop && strcmp(stop, "0")) _exit(3);
    abort();
}
static NVNstorageClass ordinary_class(const pool_t *pool) {
    const char *policy = getenv("DM_BOTW_POOL_POLICY");
    if (!policy || strcmp(policy, "ordinary-pools-1.9.0-probe-v1"))
        class_unsupported("requires the explicit ordinary-pool experiment");
    if (pool && (pool->magic != MAGIC('P', 'O', 'O', 'L') ||
                 (pool->flags & (NVN_MEMORY_POOL_FLAGS_PHYSICAL | NVN_MEMORY_POOL_FLAGS_VIRTUAL))))
        class_unsupported("requires an ordinary memory pool");
    return (NVNstorageClass)0x100;
}
API(nvnTextureBuilderGetStorageClass, NVNtextureBuilder) {
    if (!object) class_unsupported("null texture builder");
    return ordinary_class(((const tex_builder_t *)object)->pool);
}
API(nvnTextureGetStorageClass, NVNtexture) {
    if (!object || ((const tex_t *)object)->magic != MAGIC('T', 'E', 'X', 0))
        class_unsupported("texture is not initialized");
    if (!((const tex_t *)object)->pool) class_unsupported("texture has no memory pool");
    return ordinary_class(((const tex_t *)object)->pool);
}
