/* Complete actual flush/import and real dirty/claim algorithms. Metal calls
 * terminate in owned CPU protocol objects; this fixture never creates a GPU. */
#define BOTW_WHOLE_POLICY_IMPLEMENTATION
#include "whole_policy.h"
#include <errno.h>
#include "nvn_dirty.h"
#include "nvn_texflush.h"
#include "nvn_texown.h"
#include "nvn_texwrite.h"
#include "public_vm_read.h"
#include "../tex/nvn_fmt_mtl.h"
int dm_guest_write_prepare_checked(void *,size_t);
void dm_guest_write_prepare(void *,size_t);
#define WHOLE_POOL_OWNED_MAP_HOOKS 1
#define main original_flush_pool_fixture_main
#include "owned_flush_protocols.inc"
#undef main
static void owned_log(const char *format,...){(void)format;}
#undef LOG
#undef MLOG
#undef OLOG
#define LOG(...) owned_log(__VA_ARGS__)
#define MLOG(...) owned_log(__VA_ARGS__)
#define OLOG(...) owned_log(__VA_ARGS__)
static int (*owned_prepare)(void *,size_t);
static int (*owned_fault)(void *,int,void *);
static int fail_protect,fail_upload,change_during_upload,claim_during_upload;
static unsigned uploads,commits,encoded,finished,pool_write_prepares;
static tex_t *current_texture;
static unsigned char *expected_storage;
static NSMutableArray *encoded_rows;
int dm_guest_write_prep_register_checked(int (*fn)(void *,size_t)){assert(fn&&!owned_prepare);owned_prepare=fn;return 1;}
void dm_fault_hook_register(int (*fn)(void *,int,void *)){assert(fn&&!owned_fault);owned_fault=fn;}
int dm_guest_write_prepare_checked(void *address,size_t size){return !owned_prepare||owned_prepare(address,size);}
void dm_guest_write_prepare(void *address,size_t size){pool_write_prepares++;assert(dm_guest_write_prepare_checked(address,size));}
int botw_whole_heap_pin_overlap(uint64_t address,size_t size){assert(logical_range((void*)(uintptr_t)address,size));return 0;}
int owned_dirty_mprotect(void *address,size_t size,int protection){if(fail_protect&&protection==PROT_READ){errno=EACCES;return -1;}return mprotect(address,size,protection);}
int botw_whole_pool_map_preflight(const void *owner,const NVNmappingRequest *requests,int count,char *error,size_t capacity){(void)owner;(void)requests;(void)count;(void)error;(void)capacity;stop("ordinary flush unexpectedly reached virtual Map");}
void botw_whole_pool_map_committed(const void *owner){(void)owner;stop("ordinary flush unexpectedly committed virtual Map");}
static void snap_drop(const void **snapshot,uint64_t serial,uint64_t offset){(void)snapshot;(void)serial;(void)offset;stop("flush fixture unexpectedly reinitialized a live texture");}
void mtl_tex_release(tex_t *texture){(void)texture;stop("flush fixture unexpectedly released a texture directly");}
#define TX(t) ((tex_t *)(t))
#define CTX(t) ((const tex_t *)(t))
static void owned_stop(int status){assert(status==3);stop("actual texwrite_stop");}
static int on_sub_thread(void){return 0;}
static void mtl_finish_gpu(void){assert(drains);finished++;}
static void zskip_del(uint64_t serial){assert(serial);}
static int bc_unpack(uint32_t format,uint32_t *pixel,int *bytes){(void)format;(void)pixel;(void)bytes;return 0;}
int nvn_bc_decode(uint32_t format,const void *source,size_t pitch,unsigned width,unsigned height,void *output,size_t row){(void)format;(void)source;(void)pitch;(void)width;(void)height;(void)output;(void)row;stop("RGBA8 fixture unexpectedly requested BC decode");}
static MTLTextureDescriptor *tex_desc(const tex_t *texture,int packaged){
    assert(!packaged&&texture->d.format==0x25);MTLTextureDescriptor *d=[MTLTextureDescriptor new];
    d.pixelFormat=MTLPixelFormatRGBA8Unorm;return d;
}
@interface OwnedDevice (FlushUpload)
- (id<MTLTexture>)newTextureWithDescriptor:(MTLTextureDescriptor*)descriptor;
- (id<MTLBuffer>)newBufferWithLength:(NSUInteger)length options:(MTLResourceOptions)options;
@end
@implementation OwnedDevice (FlushUpload)
- (id<MTLTexture>)newTextureWithDescriptor:(MTLTextureDescriptor*)descriptor{assert(descriptor.pixelFormat==MTLPixelFormatRGBA8Unorm);return (id<MTLTexture>)[OwnedTexture new];}
- (id<MTLBuffer>)newBufferWithLength:(NSUInteger)length options:(MTLResourceOptions)options{(void)options;if(fail_upload)return nil;OwnedBuffer *b=[OwnedBuffer new];b.allocation=calloc(1,length);assert(b.allocation);b.contents=b.allocation;b.length=length;return (id<MTLBuffer>)b;}
@end
@interface OwnedUploadCommand:NSObject
@property(strong) NSError *error;
- (void)commit;
- (void)waitUntilCompleted;
@end
@implementation OwnedUploadCommand
- (void)commit{commits++;if(change_during_upload){assert(dm_guest_write_prepare_checked(owned,1));((unsigned char*)owned)[0]^=0x7f;}if(claim_during_upload)nvn_texown_claim(87,owned,current_texture->L.size);}
- (void)waitUntilCompleted{assert(commits);}
@end
@interface OwnedUploadEncoder:NSObject
- (void)copyFromBuffer:(id<MTLBuffer>)buffer sourceOffset:(NSUInteger)offset sourceBytesPerRow:(NSUInteger)row sourceBytesPerImage:(NSUInteger)image sourceSize:(MTLSize)size toTexture:(id<MTLTexture>)texture destinationSlice:(NSUInteger)slice destinationLevel:(NSUInteger)level destinationOrigin:(MTLOrigin)origin;
@end
@implementation OwnedUploadEncoder
- (void)copyFromBuffer:(id<MTLBuffer>)buffer sourceOffset:(NSUInteger)offset sourceBytesPerRow:(NSUInteger)row sourceBytesPerImage:(NSUInteger)image sourceSize:(MTLSize)size toTexture:(id<MTLTexture>)texture destinationSlice:(NSUInteger)slice destinationLevel:(NSUInteger)level destinationOrigin:(MTLOrigin)origin{
    assert(texture&&current_texture&&level<current_texture->d.levels&&slice<current_texture->d.depth);
    assert(!origin.x&&!origin.y&&!origin.z&&size.depth==1&&size.width==MAX(current_texture->d.width>>level,1u)&&size.height==MAX(current_texture->d.height>>level,1u));
    assert(row==size.width*4&&image==row*size.height&&offset+image<=buffer.length);
    nvn_texwrite_plan plan;const char *why=NULL;assert(!nvn_texwrite_plan_level(&current_texture->d,(unsigned)level,&plan,&why));
    unsigned char *linear=malloc(plan.src_bytes);assert(linear);nvn_texwrite_extract(&current_texture->d,&plan,expected_storage,linear);
    assert(!memcmp((unsigned char*)buffer.contents+offset,linear+slice*plan.src_img,image));free(linear);
    [encoded_rows addObject:@[@(level),@(slice)]];encoded++;
}
@end
static id<MTLCommandBuffer> q_cb(void){uploads++;return (id<MTLCommandBuffer>)[OwnedUploadCommand new];}
static id<MTLBlitCommandEncoder> enc_b_begin(id<MTLCommandBuffer> command){assert(command);return (id<MTLBlitCommandEncoder>)[OwnedUploadEncoder new];}
static void enc_b_end(id<MTLBlitCommandEncoder> encoder){assert(encoder);}
/* These are existing upstream declarations, not fixture implementation
 * warnings: ARC ignores the return qualifier and SDK27 deprecates two cases. */
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wignored-qualifiers"
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#include "flush_metal_entries.inc"
#pragma clang diagnostic pop
#define _exit(status) owned_stop(status)
#include "flush_entries.inc"
#undef _exit

static int flush_stopped(const tex_t *texture){int rejected=0;@try{h_nvnTextureFlushTexels((const NVNtexture*)texture,NULL,NULL);}@catch(NSException *exception){assert([exception.name isEqualToString:@"OwnedPoolStop"]);rejected=1;}return rejected;}
static void initialize_texture(NVNmemoryPool *pool,NVNdevice *device,tex_t *texture){
    pool_builder_t builder={.dev=device,.mem=owned,.size=owned_size,.flags=0x22};assert(h_nvnMemoryPoolInitialize(pool,(NVNmemoryPoolBuilder*)&builder));
    tex_builder_t t={.pool=(pool_t*)pool,.off=0};t.d=(nvn_texdesc){.target=4,.format=0x25,.width=8,.height=8,.depth=2,.levels=3,.samples=1};
    memset(texture,0,sizeof *texture);assert(h_nvnTextureInitialize((NVNtexture*)texture,(const NVNtextureBuilder*)&t));
    assert(texture->L.size<owned_size&&nvn_dirty_tracked(texture->mem,texture->L.size)&&owned_prepare&&owned_fault);
    memset(owned,0xa5,owned_size);
    for(unsigned level=0;level<t.d.levels;level++){nvn_texwrite_plan p;const char *why=NULL;assert(!nvn_texwrite_plan_level(&t.d,level,&p,&why));unsigned char *linear=malloc(p.src_bytes);assert(linear);for(size_t i=0;i<p.src_bytes;i++)linear[i]=(unsigned char)(17+level*31+i*7);nvn_texwrite_apply(&t.d,&p,linear,owned);free(linear);}
    expected_storage=malloc(owned_size);assert(expected_storage);memcpy(expected_storage,owned,owned_size);current_texture=texture;
    assert(nvn_texflush_verify(texture->serial,texture->mem,texture->L.layer_size,2,0,2)==NVN_TEXFLUSH_UNTRACKED);
}
int main(int argc,char **argv){@autoreleasepool{
    assert(argc==2);setenv("DM_STOP_ON_UNIMPL","1",1);owned_size=2*NVN_VPOOL_PAGE;owned=mmap(NULL,owned_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(owned!=MAP_FAILED);
    OwnedDevice *device=[OwnedDevice new];device.maxBufferLength=owned_size*4;g_dev=(id<MTLDevice>)device;g_keep=[NSMutableArray new];g_res=(id<MTLResidencySet>)[OwnedResidency new];g_res_objects=[NSHashTable weakObjectsHashTable];encoded_rows=[NSMutableArray new];
    NVNdevice raw_device={0};((dev_t_*)&raw_device)->magic=MAGIC('D','E','V',0);NVNmemoryPool pool={0};tex_t texture;initialize_texture(&pool,&raw_device,&texture);
    if(!strcmp(argv[1],"fresh")||!strcmp(argv[1],"changed")){
        assert(!flush_stopped(&texture)&&"fresh ordinary FlushTexels must actually import its authoritative pool snapshot");
        assert(uploads==1&&commits==1&&encoded==6&&finished>=1&&encoded_rows.count==6&&!pool_write_prepares&&!memcmp(owned,expected_storage,owned_size));
        assert(nvn_texflush_verify(texture.serial,texture.mem,texture.L.layer_size,2,0,2)==NVN_TEXFLUSH_CLEAN);
        assert(!flush_stopped(&texture)&&uploads==1);
        if(!strcmp(argv[1],"changed")){assert(dm_guest_write_prepare_checked(owned,1));((unsigned char*)owned)[0]^=1;assert(nvn_texflush_verify(texture.serial,texture.mem,texture.L.layer_size,2,0,2)==NVN_TEXFLUSH_CHANGED);assert(flush_stopped(&texture)&&uploads==1);}
    }else if(!strcmp(argv[1],"existing")){
        OwnedTexture *existing=[OwnedTexture new];res_add((id<MTLResource>)existing);serial_put_pool(texture.serial,existing,&pool);assert(flush_stopped(&texture)&&!uploads);
        serial_del(texture.serial);alias_key key;alias_key_of(&key,&texture);nvn_texown_claim(99,owned,texture.L.size);pthread_mutex_lock(&g_smu);alias_insert(&key,(id<MTLTexture>)existing,99);pthread_mutex_unlock(&g_smu);assert(flush_stopped(&texture)&&!uploads&&nvn_texown_find(owned,texture.L.size,NULL));
    }else if(!strcmp(argv[1],"claims")){
        nvn_texown_claim(99,owned,texture.L.size);assert(flush_stopped(&texture)&&!uploads);nvn_texown_retire(99);assert(flush_stopped(&texture)&&!uploads&&nvn_texown_count(1)==1);
    }else{
        if(!strcmp(argv[1],"upload-failure"))fail_upload=1;
        else if(!strcmp(argv[1],"protection-failure"))fail_protect=1;
        else if(!strcmp(argv[1],"claim-during-upload"))claim_during_upload=1;
        else{assert(!strcmp(argv[1],"changed-during-upload"));change_during_upload=1;}
        assert(flush_stopped(&texture));assert(nvn_texflush_verify(texture.serial,texture.mem,texture.L.layer_size,2,0,2)!=NVN_TEXFLUSH_CLEAN);
        if(fail_upload)assert(!serial_get(texture.serial)&&!nvn_texown_find(owned,texture.L.size,NULL)&&!commits);
        else if(claim_during_upload)assert(commits==1&&!serial_get(texture.serial)&&nvn_texown_find(owned,texture.L.size,NULL));
        else assert(commits==1&&serial_get(texture.serial)&&nvn_texown_find(owned,texture.L.size,NULL));
    }
    fail_protect=0;nvn_texflush_forget(texture.serial);h_nvnMemoryPoolFinalize(&pool);free(expected_storage);assert(!munmap(owned,owned_size));puts("WHOLE_TEXTURE_FLUSH_IMPORT_CPU_OK");return 0;
}}
