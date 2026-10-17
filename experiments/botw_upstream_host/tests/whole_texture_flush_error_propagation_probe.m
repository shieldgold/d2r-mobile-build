/* Complete original import fixture: real registry, dirty pages, certificates
 * and ownership ledger. Metal terminates in the original owned CPU protocols. */
#include <stdarg.h>
#include "owned_error_base_probe.inc"

static unsigned error_lines;
static const char *error_kind, *error_function, *error_reason;
static const char *generic_reason = "non-packaged texture storage lacks complete, unambiguous write tracking; CPU stores cannot be reconciled with its Metal content";
static void owned_log(const char *format,...) {
    if (strcmp(format, "%s NVN %s: %s (format %#x target %d %ux%ux%u levels %u flags %#x)")) return;
    va_list args; va_start(args,format);
    error_kind=va_arg(args,const char*);
    error_function=va_arg(args,const char*);
    error_reason=va_arg(args,const char*);
    va_end(args);
    /* The seam retains only the existing fixed strings. It never consumes or
     * prints the remaining format operands, memory contents or addresses. */
    error_lines++;
}
static void expect_reason(const tex_t *texture,const char *reason) {
    error_lines=0;error_kind=error_function=error_reason=NULL;
    assert(flush_stopped(texture));
    assert(error_lines==1&&error_kind&&error_function&&error_reason);
    assert(!strcmp(error_kind,"UNIMPL")&&!strcmp(error_function,"nvnTextureFlushTexels"));
    assert(!strcmp(error_reason,reason)&&"existing fixed import error was replaced by a generic error");
}
int main(int argc,char **argv) {@autoreleasepool{
    assert(argc==2);setenv("DM_STOP_ON_UNIMPL","1",1);
    owned_size=2*NVN_VPOOL_PAGE;owned=mmap(NULL,owned_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(owned!=MAP_FAILED);
    OwnedDevice *device=[OwnedDevice new];device.maxBufferLength=owned_size*4;g_dev=(id<MTLDevice>)device;
    g_keep=[NSMutableArray new];g_res=(id<MTLResidencySet>)[OwnedResidency new];g_res_objects=[NSHashTable weakObjectsHashTable];encoded_rows=[NSMutableArray new];
    NVNdevice raw_device={0};((dev_t_*)&raw_device)->magic=MAGIC('D','E','V',0);
    NVNmemoryPool pool={0};tex_t texture;initialize_texture(&pool,&raw_device,&texture);
    /* An owned pool canary is never a logging operand or a report field. */
    memcpy((unsigned char*)owned+owned_size-64,"OWNED_SENSITIVE_POOL_CANARY",27);
    memcpy(expected_storage,owned,owned_size);
    if(!strcmp(argv[1],"unsupported")){
        /* Exercise the real unsupported-target predicate on an owned typed
         * descriptor; this is not a claim that this descriptor is a legal NVN input. */
        texture.d.samples=2;
        expect_reason(&texture,"no direct Metal upload for this texture format or target");
        assert(!uploads&&!commits&&!pool_write_prepares&&!serial_get(texture.serial));
    }else if(!strcmp(argv[1],"live")||!strcmp(argv[1],"retired")){
        nvn_texown_claim(99,owned,texture.L.size);
        int retired=!strcmp(argv[1],"retired");if(retired)nvn_texown_retire(99);
        expect_reason(&texture,retired
            ?"texture storage overlaps released Metal storage whose GPU writes never reached the pool; its bytes are not authoritative"
            :"texture storage overlaps live Metal storage of another texture whose GPU writes never reach the pool; its bytes are not authoritative");
        assert(!uploads&&!commits&&!pool_write_prepares&&!serial_get(texture.serial));
        nvn_texown_hit hit={0};assert(nvn_texown_find(owned,texture.L.size,&hit)&&hit.serial==99&&hit.retired==retired);
    }else if(!strcmp(argv[1],"serial")||!strcmp(argv[1],"alias")){
        OwnedTexture *existing=[OwnedTexture new];res_add((id<MTLResource>)existing);
        if(!strcmp(argv[1],"serial"))serial_put_pool(texture.serial,existing,&pool);
        else {alias_key key;alias_key_of(&key,&texture);nvn_texown_claim(99,owned,texture.L.size);pthread_mutex_lock(&g_smu);alias_insert(&key,(id<MTLTexture>)existing,99);pthread_mutex_unlock(&g_smu);}
        expect_reason(&texture,generic_reason);
        assert(!uploads&&!commits&&!pool_write_prepares&&serial_get(texture.serial)==existing);
    }else if(!strcmp(argv[1],"fresh")){
        assert(!flush_stopped(&texture)&&!error_lines&&uploads==1&&commits==1&&encoded==6);
        assert(nvn_texflush_verify(texture.serial,texture.mem,texture.L.layer_size,2,0,2)==NVN_TEXFLUSH_CLEAN);
    }else{
        if(!strcmp(argv[1],"protection"))fail_protect=1;
        else{assert(!strcmp(argv[1],"changed"));change_during_upload=1;}
        expect_reason(&texture,generic_reason);
        assert(commits==1&&serial_get(texture.serial)&&nvn_texown_find(owned,texture.L.size,NULL));
        assert(nvn_texflush_verify(texture.serial,texture.mem,texture.L.layer_size,2,0,2)!=NVN_TEXFLUSH_CLEAN);
    }
    assert(!pool_write_prepares);
    if(!change_during_upload)assert(!memcmp(owned,expected_storage,owned_size));
    fail_protect=0;nvn_texflush_forget(texture.serial);h_nvnMemoryPoolFinalize(&pool);
    free(expected_storage);assert(!munmap(owned,owned_size));
    puts("WHOLE_FLUSH_ERROR_PROPAGATION_CPU_OK");return 0;
}}
