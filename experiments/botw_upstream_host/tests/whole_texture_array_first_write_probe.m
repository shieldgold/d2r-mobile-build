/* Actual complete Write/Flush/import producers and real owner/dirty modules; Metal uses owned CPU protocols. */
#include <stdarg.h>
#include "owned_error_base_probe.inc"
@interface OwnedTexture (ArrayWriteProtocol)
- (MTLPixelFormat)pixelFormat;
@end
@implementation OwnedTexture (ArrayWriteProtocol)
- (MTLPixelFormat)pixelFormat{return MTLPixelFormatRGBA8Unorm;}
@end
/* Unexecuted packaged and submit-only platform dependencies fail explicitly.
 * They do not replace a texture coherence or storage algorithm. */
static _Atomic int g_up_lost_n,g_sub_thread_set;
static _Atomic uint64_t g_up_lost_redo;
static uint64_t g_pkg_miss,g_pkg_reuploads,g_pkg_uploads,g_tex_zeroed;
static id<MTLCommandBuffer> g_cb;
static NSMutableArray *g_up_texs;
static int dirtycheck(void){stop("unexecuted packaged branch");}
static int up_lost_take(id object){(void)object;stop("unexecuted packaged branch");}
static uint64_t full_hash(const void *memory,uint64_t size){(void)memory;(void)size;stop("unexecuted packaged branch");}
static id<MTLTexture> pkg_upload(const tex_t *texture,uint64_t *stamp){(void)texture;(void)stamp;stop("unexecuted packaged branch");}
static void up_begin(void){stop("normal non-submit thread must skip zero-fill");}
static void up_flush(void){stop("normal non-submit thread must skip zero-fill");}
static void up_blit_add(id<MTLBuffer> source,size_t offset,size_t row,size_t image,MTLSize size,id<MTLTexture> target,NSUInteger slice,NSUInteger level){(void)source;(void)offset;(void)row;(void)image;(void)size;(void)target;(void)slice;(void)level;stop("normal non-submit thread must skip zero-fill");}
#define RED_ONCE(tag,...) owned_log(__VA_ARGS__)
#define PD(...) owned_log(__VA_ARGS__)
#include "actual_tex_mtl.inc"
typedef struct { uint64_t ser,tag;uint32_t lv,sl;uint8_t state; } xv_ent;
enum { XV_INVALID=1,XV_PENDING_CLEAR=2,XV_PROBE=3 };
static xv_ent g_xv[64];static int g_xv_n;
static void xv_violation(const xv_ent *entry,const char *why){(void)entry;(void)why;stop("owned empty XV ledger cannot contain a violation");}
#include "actual_empty_xv.inc"
#include "actual_mtl_write.inc"
static unsigned failure_lines;
static const char *failure_reason,*failure_function;
static void owned_log(const char *format,...){
 if(strcmp(format,"%s NVN %s: %s (format %#x target %d %ux%ux%u levels %u flags %#x)"))return;
 va_list args;va_start(args,format);const char *kind=va_arg(args,const char*),*fn=va_arg(args,const char*);failure_reason=va_arg(args,const char*);va_end(args);
 assert(!strcmp(kind,"UNIMPL"));failure_function=fn;failure_lines++;
}
static int actual_flush_stopped(const tex_t *texture,const NVNcopyRegion *region){
 int rejected=0;failure_lines=0;failure_reason=failure_function=NULL;
 @try{h_nvnTextureFlushTexels((const NVNtexture*)texture,NULL,region);}
 @catch(NSException *exception){assert([exception.name isEqualToString:@"OwnedPoolStop"]);rejected=1;}
 return rejected;
}
int main(int argc,char **argv){@autoreleasepool{
 assert(argc==2);const char *mode=argv[1];setenv("DM_STOP_ON_UNIMPL","1",1);
 unsigned layers=!strcmp(mode,"whole")?1:2;
 owned_size=2*NVN_VPOOL_PAGE;owned=mmap(NULL,owned_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(owned!=MAP_FAILED);
 OwnedDevice *device=[OwnedDevice new];device.maxBufferLength=owned_size*4;g_dev=(id<MTLDevice>)device;
 g_keep=[NSMutableArray new];g_res=(id<MTLResidencySet>)[OwnedResidency new];g_res_objects=[NSHashTable weakObjectsHashTable];encoded_rows=[NSMutableArray new];
 NVNdevice raw_device={0};((dev_t_*)&raw_device)->magic=MAGIC('D','E','V',0);NVNmemoryPool pool={0};
 pool_builder_t pb={.dev=&raw_device,.mem=owned,.size=owned_size,.flags=0x22};assert(h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&pb));
 tex_builder_t tb={.pool=(pool_t*)&pool,.off=0};tb.d=(nvn_texdesc){.target=4,.format=0x25,.width=8,.height=8,.depth=layers,.levels=1,.samples=1};
 tex_t texture={0};assert(h_nvnTextureInitialize((NVNtexture*)&texture,(const NVNtextureBuilder*)&tb));current_texture=&texture;
 assert(texture.L.size<owned_size&&nvn_dirty_tracked(owned,texture.L.size));memset(owned,0xa5,owned_size);
 nvn_texwrite_plan full;const char *why=NULL;assert(!nvn_texwrite_plan_level(&texture.d,0,&full,&why)&&full.depth==layers);
 unsigned char *initial=malloc(full.src_bytes);assert(initial);for(size_t i=0;i<full.src_bytes;i++)initial[i]=(unsigned char)(13+i*5);
 nvn_texwrite_apply(&texture.d,&full,initial,owned);free(initial);
 expected_storage=malloc(owned_size);assert(expected_storage);memcpy(expected_storage,owned,owned_size);
 int partial=!strcmp(mode,"partial-negative"),late_change=!strcmp(mode,"changed");
 unsigned selected_layer=(!strcmp(mode,"fresh1")||late_change)?1:0;
 NVNcopyRegion region={0,0,(int)selected_layer,partial?4:8,8,1};nvn_texwrite_plan selected;
 assert(!nvn_texwrite_plan_make(&texture.d,0,0,-1,0,&region,0,0,&selected,&why));
 assert(selected.layer==selected_layer&&selected.depth==1&&!!texflush_full_layer(&texture,&selected)==!partial);
 unsigned char *source=malloc(selected.src_bytes);assert(source);for(size_t i=0;i<selected.src_bytes;i++)source[i]=(unsigned char)(91+i*3);
 nvn_texwrite_apply(&texture.d,&selected,source,expected_storage);
 int serial=!strcmp(mode,"serial"),alias=!strcmp(mode,"alias"),live=!strcmp(mode,"live"),retired=!strcmp(mode,"retired");
 if(serial){POOL_CAPTURE();assert(tex_mtl(&texture));}
 if(alias){OwnedTexture *existing=[OwnedTexture new];res_add((id<MTLResource>)existing);alias_key key;alias_key_of(&key,&texture);nvn_texown_claim(99,owned,texture.L.size);pthread_mutex_lock(&g_smu);alias_insert(&key,(id<MTLTexture>)existing,99);pthread_mutex_unlock(&g_smu);}
 if(live||retired||partial){nvn_texown_claim(99,(char*)owned+texture.L.layer_size,texture.L.layer_size);if(retired)nvn_texown_retire(99);}
 if(!strcmp(mode,"protection"))fail_protect=1;if(late_change)change_during_upload=1;
 int write_rejected=0;
 @try{h_nvnTextureWriteTexels((NVNtexture*)&texture,NULL,&region,source);}
 @catch(NSException *exception){assert([exception.name isEqualToString:@"OwnedPoolStop"]);write_rejected=1;}
 free(source);
 if(partial){
  assert(write_rejected&&failure_lines==1&&!strcmp(failure_function,"nvnTextureWriteTexels"));
  assert(!strcmp(failure_reason,"texture storage overlaps live Metal storage of another texture whose GPU writes never reach the pool; its bytes are not authoritative"));
  assert(!uploads&&!commits&&!encoded&&!pool_write_prepares);
 }else{
  assert(!write_rejected&&!failure_lines&&uploads==1&&commits==1&&pool_write_prepares==1&&!g_tex_zeroed);
  int fallback=serial||alias||live||retired;
  assert(encoded==(fallback||layers==1?1u:2u));
  assert(serial_get(texture.serial)&&nvn_texown_find(owned,texture.L.size,NULL));
  if(live||retired){nvn_texown_hit hit={0};assert(nvn_texown_find_other(texture.serial,(char*)owned+texture.L.layer_size,texture.L.layer_size,&hit)&&hit.serial==99&&hit.retired==retired);}
  if(alias)assert(nvn_texown_owner(texture.serial)==99);
  if(layers==1)assert(finished==1); /* No fresh precheck on the original all-layer fast path. */
  if(!late_change)assert(!memcmp(owned,expected_storage,owned_size));
  int uncertain=fallback||fail_protect||late_change;
  if(uncertain){
   assert(nvn_texflush_verify(texture.serial,owned,texture.L.layer_size,layers,0,layers)!=NVN_TEXFLUSH_CLEAN);
   assert(actual_flush_stopped(&texture,&region));
   assert(failure_lines==1&&!strcmp(failure_function,"nvnTextureFlushTexels")&&!strcmp(failure_reason,"non-packaged texture storage lacks complete, unambiguous write tracking; CPU stores cannot be reconciled with its Metal content"));
  }else{
   assert(nvn_texflush_verify(texture.serial,owned,texture.L.layer_size,layers,0,layers)==NVN_TEXFLUSH_CLEAN);
   assert(!actual_flush_stopped(&texture,&region)&&!failure_lines);
  }
  assert(uploads==1&&commits==1&&pool_write_prepares==1);
 }
 fail_protect=0;nvn_texflush_forget(texture.serial);h_nvnMemoryPoolFinalize(&pool);free(expected_storage);assert(!munmap(owned,owned_size));
 puts("WHOLE_TEXTURE_ARRAY_FIRST_WRITE_CPU_OK");return 0;
}}
