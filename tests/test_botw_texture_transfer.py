"""Texture storage moves over owned fixtures; no original guest content."""
from pathlib import Path
import os
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CPU_SOURCE = r'''
#import <Metal/Metal.h>
#import <mach/mach.h>
static unsigned vmQueries,accessCalls,callbacks;
static kern_return_t observedRegion(vm_map_t task,vm_address_t *address,vm_size_t *size,natural_t *depth,vm_region_recurse_info_t info,mach_msg_type_number_t *count){vmQueries++;return vm_region_recurse_64(task,address,size,depth,info,count);}
@interface MoveCPUDevice:NSObject @end
@implementation MoveCPUDevice
- (id<MTLCommandQueue>)newCommandQueue{return (id<MTLCommandQueue>)[NSObject new];}
- (id<MTLSamplerState>)newSamplerStateWithDescriptor:(MTLSamplerDescriptor*)descriptor{(void)descriptor;return (id<MTLSamplerState>)[NSObject new];}
@end
static id<MTLDevice> moveCPUDevice(void){return (id<MTLDevice>)[MoveCPUDevice new];}
#define MTLCreateSystemDefaultDevice moveCPUDevice
#define vm_region_recurse_64 observedRegion
#include "graphics.m"
#undef MTLCreateSystemDefaultDevice
#undef vm_region_recurse_64
#include <dlfcn.h>
static unsigned spanFailure;
@interface MoveSpanMap:NSObject<NSFastEnumeration,NSMutableCopying>
@property NSMutableDictionary *map;
@property BOOL clone;
@end
@implementation MoveSpanMap
- (NSUInteger)count{return self.map.count;}
- (NSArray*)allValues{return self.map.allValues;}
- (id)objectForKeyedSubscript:(id)key{return self.map[key];}
- (void)setObject:(id)value forKeyedSubscript:(id<NSCopying>)key{if(self.clone&&spanFailure==3)@throw [NSException exceptionWithName:@"OwnedSpanSetFailure" reason:@"owned clone set exception" userInfo:nil];self.map[key]=value;}
- (void)removeObjectForKey:(id)key{if(self.clone&&spanFailure==2)@throw [NSException exceptionWithName:@"OwnedSpanRemoveFailure" reason:@"owned clone remove exception" userInfo:nil];[self.map removeObjectForKey:key];}
- (NSUInteger)countByEnumeratingWithState:(NSFastEnumerationState*)state objects:(id __unsafe_unretained[])buffer count:(NSUInteger)count{return [self.map countByEnumeratingWithState:state objects:buffer count:count];}
- (id)mutableCopyWithZone:(NSZone*)zone{(void)zone;if(spanFailure==1)@throw [NSException exceptionWithName:@"OwnedSpanCopyFailure" reason:@"owned mutableCopy exception" userInfo:nil];if(spanFailure==4)return nil;MoveSpanMap *result=[MoveSpanMap new];result.map=[self.map mutableCopy];result.clone=YES;return result;}
- (BOOL)isEqual:(id)other{return [self.map isEqual:other];}
@end
#define CHECK(x) do{if(!(x)){fprintf(stderr,"TEXTURE_MOVE_CPU_CHECK line%d %s\n",__LINE__,#x);return 91;}}while(0)
static void captureError(const char *error){(void)error;callbacks++;}
static int noAccess(const void *p,size_t n,int write){(void)p;(void)n;(void)write;accessCalls++;return 0;}
static NVNdevice device;
static NVNmemoryPool pool;
static NVNtexture *source,*destination,*third;
typedef struct {botw_graphics_texture_move_token token;int committed,aborted;} WrongThread;
static void *wrongThread(void *raw){WrongThread *state=raw;char error[128];state->committed=botw_graphics_texture_move_commit(&state->token,error,sizeof error);state->aborted=botw_graphics_texture_move_abort(&state->token,error,sizeof error);return NULL;}
static int setup(void){
 CHECK(botw_graphics_init(NULL,captureError,NULL));BWDevice *d=[[BWDevice alloc]initWithTextureKindPolicy:BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH];put(&device,d);BWPool *p=[BWPool new];p.kind=BW_POOL_VIRTUAL;p.device=d;p.flags=0x221;p.size=65536;p.live=YES;put(&pool,p);p.generation=((BWObject*)&pool)->serial;
 source=calloc(1,256);destination=calloc(1,256);third=calloc(1,256);CHECK(source&&destination&&third);
 NVNtextureBuilder builder={0};TextureBuilder *b=(TextureBuilder*)&builder;b->device=&device;b->pool=&pool;b->format=NVN_FORMAT_RGBA8;b->target=NVN_TEXTURE_TARGET_2D;b->width=b->height=4;b->depth=b->levels=1;for(unsigned i=0;i<4;i++)b->swizzle[i]=i+NVN_SWZ_R;CHECK(w_nvnTextureInitialize(source,&builder));return 0;
}
static int replacementScene(BWTexture **old,BWTexture **fresh,NVNtexture *alias){
 char error[512];botw_graphics_texture_move_token token;*old=textureObject(source);CHECK(botw_graphics_texture_move_preflight(source,destination,192,&token,error,sizeof error));memcpy(destination,source,192);CHECK(botw_graphics_texture_move_commit(&token,error,sizeof error));*alias=*destination;
 NVNtextureBuilder builder={0};TextureBuilder metadata=(*old).metadata;memcpy(&builder,&metadata,sizeof metadata);CHECK(w_nvnTextureInitialize(source,&builder));*fresh=textureObject(source);CHECK(*old!=*fresh&&(*old).storageMoveEpoch==1&&(*fresh).storageMoveEpoch==0);return 0;
}
int main(int argc,char **argv){@autoreleasepool{
 CHECK(argc==2&&!setup());const char *mode=argv[1];char error[128];botw_graphics_texture_move_token token={0};
 __typeof__(&botw_graphics_texture_move_preflight) prepare=(__typeof__(prepare))dlsym(RTLD_DEFAULT,"botw_graphics_texture_move_preflight");
 __typeof__(&botw_graphics_texture_move_commit) commit=(__typeof__(commit))dlsym(RTLD_DEFAULT,"botw_graphics_texture_move_commit");
 __typeof__(&botw_graphics_texture_move_abort) cancel=(__typeof__(cancel))dlsym(RTLD_DEFAULT,"botw_graphics_texture_move_abort");
 CHECK(prepare&&commit&&cancel);BWTexture *texture=textureObject(source);uint64_t identity=texture.identity;unsigned beforeCallbacks=callbacks;
 if(!strcmp(mode,"move")){
  NSValue *old=objectSpans[@(identity)];vmQueries=accessCalls=0;memoryAccessValidator=noAccess;
  CHECK(prepare(source,destination,192,&token,error,sizeof error));CHECK(!vmQueries&&!accessCalls&&!callbacks&&objectSpans[@(identity)]==old);
  memcpy(destination,source,192);CHECK(commit(&token,error,sizeof error));CHECK(!vmQueries&&!accessCalls&&!callbacks&&objects[@(identity)]==texture&&objectSpans[@(identity)].rangeValue.location==(uintptr_t)destination);CHECK(!commit(&token,error,sizeof error));CHECK(!cancel(&token,error,sizeof error));memoryAccessValidator=NULL;
  CHECK(textureObject(destination)==texture&&texture.identity==identity&&texture.pool==get(&pool,[BWPool class]));
  NVNsamplerBuilder samplerBuilder={0};w_nvnSamplerBuilderSetDefaults(&samplerBuilder);w_nvnSamplerBuilderSetDevice(&samplerBuilder,&device);CHECK(w_nvnSamplerInitialize((NVNsampler*)((char*)source+32),&samplerBuilder));CHECK(textureObject(destination)==texture);
 }else if(!strcmp(mode,"abort")){
  NSDictionary *spans=[objectSpans copy],*values=[objects copy];CHECK(prepare(source,destination,192,&token,error,sizeof error));CHECK(cancel(&token,error,sizeof error));CHECK(!cancel(&token,error,sizeof error)&&!commit(&token,error,sizeof error));CHECK([objectSpans isEqual:spans]&&[objects isEqual:values]);
 }else if(!strcmp(mode,"invalid")){
  NSDictionary *spans=[objectSpans copy],*values=[objects copy];CHECK(!prepare(source,destination,191,&token,error,sizeof error));CHECK(!prepare((char*)source+8,destination,192,&token,error,sizeof error));CHECK(!prepare(&device,destination,192,&token,error,sizeof error));CHECK(!prepare(source,source,192,&token,error,sizeof error));CHECK(!prepare(source,(char*)source+8,192,&token,error,sizeof error));CHECK(!prepare(source,&device,192,&token,error,sizeof error));CHECK(!prepare(source,(void*)(UINTPTR_MAX-10),192,&token,error,sizeof error));CHECK(!prepare(NULL,destination,192,&token,error,sizeof error));CHECK(!prepare(source,NULL,192,&token,error,sizeof error));CHECK([objectSpans isEqual:spans]&&[objects isEqual:values]&&!callbacks);
 }else if(!strcmp(mode,"epochs")){
  botw_graphics_texture_move_token other={0};CHECK(prepare(source,destination,192,&token,error,sizeof error));CHECK(prepare(source,third,192,&other,error,sizeof error));memcpy(destination,source,192);CHECK(commit(&token,error,sizeof error));NSDictionary *spans=[objectSpans copy],*values=[objects copy];memcpy(third,source,192);CHECK(!commit(&other,error,sizeof error));CHECK([objectSpans isEqual:spans]&&[objects isEqual:values]&&textureObject(destination)==texture);
 }else if(!strcmp(mode,"destination-conflict")){
  CHECK(prepare(source,destination,192,&token,error,sizeof error));BWDevice *other=[BWDevice new];put(destination,other);NSDictionary *spans=[objectSpans copy],*values=[objects copy];CHECK(!commit(&token,error,sizeof error));CHECK([objectSpans isEqual:spans]&&[objects isEqual:values]);
 }else if(!strcmp(mode,"shutdown-pin")){
  CHECK(prepare(source,destination,192,&token,error,sizeof error));CHECK(!botw_graphics_shutdown_checked(error,sizeof error)&&gpu&&objects[@(identity)]==texture&&!callbacks);CHECK(cancel(&token,error,sizeof error));
 }else if(!strcmp(mode,"lifecycle")){
  CHECK(prepare(source,destination,192,&token,error,sizeof error));CHECK(cancel(&token,error,sizeof error));CHECK(botw_graphics_shutdown_checked(error,sizeof error));CHECK(!setup());CHECK(!commit(&token,error,sizeof error)&&!callbacks);
 }else if(!strcmp(mode,"bounded")){
  botw_graphics_texture_move_token tokens[65]={{0}};for(unsigned i=0;i<64;i++)CHECK(prepare(source,destination,192,&tokens[i],error,sizeof error));CHECK(!prepare(source,destination,192,&tokens[64],error,sizeof error));for(unsigned i=0;i<64;i++)CHECK(cancel(&tokens[i],error,sizeof error));
 }else if(!strcmp(mode,"token-owner")){
  CHECK(prepare(source,destination,192,&token,error,sizeof error));WrongThread state={.token=token};pthread_t thread;CHECK(!pthread_create(&thread,NULL,wrongThread,&state));CHECK(!pthread_join(thread,NULL));CHECK(!state.committed&&!state.aborted&&textureMoves.count==1);
  botw_graphics_texture_move_token tampered=token;tampered.opaque[4]^=8;CHECK(!commit(&tampered,error,sizeof error)&&!cancel(&tampered,error,sizeof error)&&textureMoves.count==1);CHECK(cancel(&token,error,sizeof error)&&!textureMoves.count);
 }else if(!strcmp(mode,"destination-diagnostic")){
  NVNtextureBuilder builder={0};TextureBuilder metadata=texture.metadata;memcpy(&builder,&metadata,sizeof metadata);CHECK(w_nvnTextureInitialize(destination,&builder));BWTexture *occupied=textureObject(destination);occupied.storageMoveEpoch=7;
  NSDictionary *spans=[objectSpans copy],*values=[objects copy];NSUInteger pending=textureMoves.count;vmQueries=accessCalls=0;memoryAccessValidator=noAccess;char detail[512];
  const intptr_t offsets[]={0,16,-16};for(unsigned i=0;i<3;i++){occupied.storageMoveEpoch=i?7:0;void *target=(void*)((uintptr_t)destination+offsets[i]);CHECK(!prepare(source,target,192,&token,detail,sizeof detail));CHECK(strstr(detail,"texture storage transfer destination overlaps live NVN storage existing_class=BWTexture span_bytes=192"));CHECK(strstr(detail,i?"exact_base=false exact_span=false":"exact_base=true exact_span=true"));CHECK(strstr(detail,i?"existing_primary=true existing_move_epoch=7":"existing_primary=true existing_move_epoch=0"));char relative[64];snprintf(relative,sizeof relative,"relative_offset=%c%zu",offsets[i]<0?'-':'+',(size_t)(offsets[i]<0?-offsets[i]:offsets[i]));CHECK(strstr(detail,relative));CHECK([objectSpans isEqual:spans]&&[objects isEqual:values]&&textureMoves.count==pending&&!callbacks&&!vmQueries&&!accessCalls);}
  memoryAccessValidator=NULL;BWSampler *sampler=[BWSampler new];put(third,sampler);spans=[objectSpans copy];values=[objects copy];memoryAccessValidator=noAccess;CHECK(!prepare(source,third,192,&token,detail,sizeof detail));CHECK(strstr(detail,"existing_class=BWSampler span_bytes=48 exact_base=true exact_span=false")&&strstr(detail,"existing_primary=not_texture existing_move_epoch=0"));struct{char text[12];char guard;} shortError;memset(&shortError,0x5a,sizeof shortError);CHECK(!prepare(source,third,192,&token,shortError.text,sizeof shortError.text));CHECK(!shortError.text[11]&&shortError.guard==0x5a&&[objectSpans isEqual:spans]&&[objects isEqual:values]&&textureMoves.count==pending&&!callbacks&&!vmQueries&&!accessCalls);memoryAccessValidator=NULL;
 }else if(!strncmp(mode,"replacement",11)){
  BWTexture *old,*fresh;NVNtexture oldAlias;CHECK(!replacementScene(&old,&fresh,&oldAlias));uint64_t oldKey=old.identity,newKey=fresh.identity;NSDictionary *values=[objects copy],*spans=[objectSpans copy];
  if(!strcmp(mode,"replacement")){
   vmQueries=accessCalls=0;memoryAccessValidator=noAccess;CHECK(prepare(source,destination,192,&token,error,sizeof error));memcpy(destination,source,192);CHECK(commit(&token,error,sizeof error));CHECK(!vmQueries&&!accessCalls&&!callbacks&&[objects isEqual:values]&&!objectSpans[@(oldKey)]&&objectSpans[@(newKey)].rangeValue.location==(uintptr_t)destination&&old.storageMoveEpoch==2&&fresh.storageMoveEpoch==1);memoryAccessValidator=NULL;CHECK(textureObject(&oldAlias)==old&&textureObject(destination)==fresh);w_nvnTextureFinalize(&oldAlias);CHECK(objects[@(newKey)]==fresh&&objectSpans[@(newKey)].rangeValue.location==(uintptr_t)destination&&textureObject(destination)==fresh);
  }else if(!strcmp(mode,"replacement-abort")){
   CHECK(prepare(source,destination,192,&token,error,sizeof error));CHECK(cancel(&token,error,sizeof error));CHECK([objects isEqual:values]&&[objectSpans isEqual:spans]&&old.storageMoveEpoch==1&&fresh.storageMoveEpoch==0);
  }else if(!strcmp(mode,"replacement-limits")){
   old.storageMoveEpoch=0;CHECK(!prepare(source,destination,192,&token,error,sizeof error));old.storageMoveEpoch=UINT64_MAX;CHECK(!prepare(source,destination,192,&token,error,sizeof error));old.storageMoveEpoch=1;old.base=fresh;CHECK(!prepare(source,destination,192,&token,error,sizeof error));old.base=nil;old.identity=newKey;CHECK(!prepare(source,destination,192,&token,error,sizeof error));old.identity=oldKey;CHECK([objects isEqual:values]&&[objectSpans isEqual:spans]&&!textureMoves.count);
  }else if(!strcmp(mode,"replacement-target-epoch")||!strcmp(mode,"replacement-source-epoch")){
   CHECK(prepare(source,destination,192,&token,error,sizeof error));BOOL target=!strcmp(mode,"replacement-target-epoch");void *changing=target?(void*)destination:(void*)source;botw_graphics_texture_move_token otherToken;CHECK(prepare(changing,third,192,&otherToken,error,sizeof error));memcpy(third,changing,192);CHECK(commit(&otherToken,error,sizeof error));NSDictionary *current=[objectSpans copy];memcpy(destination,source,192);CHECK(!commit(&token,error,sizeof error)&&[objectSpans isEqual:current]&&[objects isEqual:values]);
  }else if(!strcmp(mode,"replacement-pending")){
   botw_graphics_texture_move_token otherToken;CHECK(prepare(destination,third,192,&otherToken,error,sizeof error));CHECK(!prepare(source,destination,192,&token,error,sizeof error));CHECK(cancel(&otherToken,error,sizeof error));CHECK(prepare(source,destination,192,&token,error,sizeof error));CHECK(prepare(destination,third,192,&otherToken,error,sizeof error));memcpy(destination,source,192);CHECK(!commit(&token,error,sizeof error)&&[objectSpans isEqual:spans]&&[objects isEqual:values]);CHECK(cancel(&otherToken,error,sizeof error));CHECK(old.storageMoveEpoch==1&&fresh.storageMoveEpoch==0);
  }else if(!strcmp(mode,"replacement-clone-failure")){
   NSMutableDictionary *original=objectSpans;for(unsigned failure=1;failure<=4;failure++){MoveSpanMap *proxy=[MoveSpanMap new];proxy.map=original;objectSpans=(NSMutableDictionary*)proxy;spanFailure=0;CHECK(prepare(source,destination,192,&token,error,sizeof error));memcpy(destination,source,192);spanFailure=failure;CHECK(!commit(&token,error,sizeof error)&&objectSpans==(id)proxy&&[original isEqual:spans]&&[objects isEqual:values]&&old.storageMoveEpoch==1&&fresh.storageMoveEpoch==0&&!textureMoves.count);spanFailure=0;objectSpans=original;}
  }else CHECK(0);
 }else CHECK(0);
 CHECK(callbacks==beforeCallbacks);CHECK(botw_graphics_shutdown_checked(error,sizeof error));free(source);free(destination);free(third);printf("TEXTURE_MOVE_CPU_OK mode=%s no_real_gpu=true\n",mode);return 0;
}}
'''

@unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'), 'Darwin CPU-only Objective-C harness')
class TextureMoveCPUTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-texture-move-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        source = folder / 'move.m'
        source.write_text(CPU_SOURCE)
        cls.executable = folder / 'move'
        built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
            '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-O1', '-g', '-fsanitize=address,undefined',
            '-Wl,-export_dynamic', '-I', str(ROOT / 'native/botw_graphics'), str(source), '-framework',
            'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(cls.executable)],
            capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stdout+built.stderr)

    def mode(self, name):
        run = subprocess.run([str(self.executable), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertIn('TEXTURE_MOVE_CPU_OK', run.stdout)
        self.assertNotIn('ERROR: AddressSanitizer', run.stderr)
        self.assertNotIn('runtime error:', run.stderr)

    def test_success_moves_only_owned_span_and_keeps_texture_value_resources_and_authority(self):
        self.mode('move')

    def test_abort_consumes_token_without_registry_changes(self):
        self.mode('abort')

    def test_invalid_source_destination_size_and_overlap_fail_without_state_or_callbacks(self):
        self.mode('invalid')

    def test_source_epoch_change_rejects_old_token_without_undoing_a_committed_move(self):
        self.mode('epochs')

    def test_commit_rechecks_new_destination_conflicts_without_registry_mutation(self):
        self.mode('destination-conflict')

    def test_pending_record_pins_shutdown_until_explicit_abort(self):
        self.mode('shutdown-pin')

    def test_old_lifecycle_token_cannot_target_reused_serials(self):
        self.mode('lifecycle')

    def test_pending_metadata_has_a_bounded_capacity(self):
        self.mode('bounded')

    def test_wrong_thread_and_tampered_token_do_not_consume_the_valid_record(self):
        self.mode('token-owner')

    def test_destination_conflict_reports_only_existing_host_metadata_without_state_or_accesses(self):
        self.mode('destination-diagnostic')

    def test_exact_migrated_target_replacement_keeps_old_value_and_alias_finalize(self):
        self.mode('replacement')

    def test_target_replacement_abort_keeps_both_spans_and_epochs(self):
        self.mode('replacement-abort')

    def test_target_replacement_refuses_unmoved_selected_wrong_identity_and_max_epoch(self):
        self.mode('replacement-limits')

    def test_target_replacement_rechecks_both_source_and_target_epochs(self):
        for mode in ['replacement-target-epoch', 'replacement-source-epoch']:
            with self.subTest(mode=mode):
                self.mode(mode)

    def test_target_replacement_does_not_retire_another_pending_transfer_source(self):
        self.mode('replacement-pending')

    def test_actual_clone_copy_remove_set_exceptions_and_nil_preserve_global_map(self):
        self.mode('replacement-clone-failure')


@unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'), 'Metal requires macOS')
class TextureMoveGPUTests(unittest.TestCase):
    def probe(self, source, cases):
        with tempfile.TemporaryDirectory(prefix='botw-texture-move-gpu-') as folder:
            folder = Path(folder)
            harness, executable = folder / 'move.c', folder / 'move'
            harness.write_text(source)
            graphics = ROOT / 'native/botw_graphics'
            build = subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                '-Wall', '-Wextra', '-Werror', '-I', str(graphics), str(graphics / 'graphics.m'),
                str(harness), '-framework', 'Foundation', '-framework', 'Metal', '-framework',
                'QuartzCore', '-o', str(executable)], capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stdout+build.stderr)
            for mode, expected, accepted in cases:
                with self.subTest(mode=mode):
                    run = subprocess.run([str(executable), mode], env={**os.environ, 'MTL_DEBUG_LAYER': '1'},
                        capture_output=True, text=True, timeout=30)
                    if run.returncode == 77:
                        self.skipTest('No Metal device')
                    self.assertIn('Metal API Validation Enabled', run.stderr)
                    self.assertNotIn('failed assertion', run.stderr)
                    self.assertNotIn('MTLReportFailure', run.stderr)
                    if accepted:
                        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                        self.assertIn(expected, run.stdout)
                    else:
                        self.assertNotEqual(run.returncode, 0, run.stdout+run.stderr)
                        self.assertIn(expected, run.stderr)

    def test_builder_field_writes_retire_moved_slot_without_losing_array_commands_descriptors_or_buffer_copy(self):
        from test_botw_graphics import ARRAY_FEATURE, METAL_SMOKE
        move = r'''
    NVNtexture movedArray,valueAlias;botw_graphics_texture_move_token moveToken;char moveError[256];
    CHECK(botw_graphics_texture_move_preflight(&arrayTexture,&movedArray,sizeof arrayTexture,&moveToken,moveError,sizeof moveError));
    memcpy(&movedArray,&arrayTexture,sizeof movedArray);CHECK(botw_graphics_texture_move_commit(&moveToken,moveError,sizeof moveError));valueAlias=movedArray;
'''
        write = r'''
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
    nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);
    nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,0,textureHandle);nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);
    NVNcommandHandle beforeBuilderWrite=nvnCommandBufferEndRecording(&cb);
    API(nvnBufferBuilderSetDevice);API(nvnBufferBuilderSetDefaults);API(nvnBufferBuilderSetStorage);
    API(nvnBufferInitialize);API(nvnBufferGetAddress);API(nvnBufferFinalize);
    NVNbufferBuilder *reclaimedBuilder=(NVNbufferBuilder*)((char*)&movedArray+48);
    nvnBufferBuilderSetDevice(reclaimedBuilder,&device);nvnBufferBuilderSetDefaults(reclaimedBuilder);nvnBufferBuilderSetStorage(reclaimedBuilder,&pool,2048,128);
    NVNbuffer copiedBuffer;CHECK(nvnBufferInitialize(&copiedBuffer,reclaimedBuilder));uint64_t copiedAddress=nvnBufferGetAddress(&copiedBuffer);
    const char copyMessage[]="builder value survives";memcpy(memory+2048,copyMessage,sizeof copyMessage);memset(memory+2112,0xa5,sizeof copyMessage);
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferCopyBufferToBuffer(&cb,copiedAddress,copiedAddress+64,sizeof copyMessage,0);handle=nvnCommandBufferEndRecording(&cb);
    nvnBufferFinalize(&copiedBuffer);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);CHECK(!memcmp(memory+2112,copyMessage,sizeof copyMessage));
    nvnQueueSubmitCommands(&queue,1,&beforeBuilderWrite);nvnQueueFinish(&queue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    for(unsigned y=0;y<4;y++)for(unsigned x=0;x<4;x++)CHECK(!memcmp(pixels+(y*4+x)*4,colors[x>=2][y<3?y:2],4));
    CHECK(nvnTextureGetDepth(&movedArray)==2&&nvnTextureGetLevels(&valueAlias)==3);
    nvnTexturePoolRegisterTexture(&tp,2,&valueAlias,NULL);uint64_t aliasHandle=nvnDeviceGetTextureHandle(&device,2,4);
    puts("BUILDER_FIELDS_RECORDED_ARRAY_AND_REAL_BUFFER_COPY_OK");
'''
        feature = ARRAY_FEATURE.replace('nvnTexturePoolRegisterTexture(&tp,3,&arrayTexture,NULL);',
            'nvnTexturePoolRegisterTexture(&tp,3,&arrayTexture,NULL);'+move)
        feature = feature.replace('    for(unsigned pass=0;pass<2;pass++){', write+'    for(unsigned pass=0;pass<2;pass++){')
        feature = feature.replace('if(pass){if(r8)', 'if(pass)textureHandle=aliasHandle;if(pass){if(r8)')
        feature = feature.replace('nvnTextureFinalize(&arrayTexture);', 'nvnTextureFinalize(&valueAlias);')
        source = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', feature+'    nvnMemoryPoolFinalize(&pool);')
        self.probe(source, [('builder-array', 'GPU_ARRAY_LAYER_MIP_COLORS_OK', True)])

    def test_builder_field_writes_preserve_private_content_and_live_retired_authority_before_any_gpu_copy(self):
        from test_botw_graphics import METAL_SMOKE
        feature = r'''
    API(nvnTextureFinalize);API(nvnBufferBuilderSetDevice);API(nvnBufferBuilderSetDefaults);API(nvnBufferBuilderSetStorage);
    NVNtexture privateTexture,movedPrivate,valueAlias;
    nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetSize2D(&tb,4,4);
    nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(&tb,&pool,6144);CHECK(nvnTextureInitialize(&privateTexture,&tb));
    const NVNtexture *privateTargets[]={&privateTexture};float privateColor[]={.125f,.5f,.875f,1};
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,privateTargets,NULL,NULL,NULL);
    nvnCommandBufferClearColor(&cb,0,privateColor,15);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
    unsigned char beforeWrite[64],afterWrite[64];CHECK(botw_graphics_read_texture(&privateTexture,beforeWrite,sizeof beforeWrite));
    for(unsigned i=0;i<16;i++)CHECK(abs((int)beforeWrite[i*4]-32)<=1&&abs((int)beforeWrite[i*4+1]-128)<=1&&abs((int)beforeWrite[i*4+2]-223)<=1&&beforeWrite[i*4+3]==255);
    botw_graphics_texture_move_token moveToken;char moveError[256];CHECK(botw_graphics_texture_move_preflight(&privateTexture,&movedPrivate,sizeof privateTexture,&moveToken,moveError,sizeof moveError));
    memcpy(&movedPrivate,&privateTexture,sizeof movedPrivate);CHECK(botw_graphics_texture_move_commit(&moveToken,moveError,sizeof moveError));valueAlias=movedPrivate;
    NVNbufferBuilder *reclaimedBuilder=(NVNbufferBuilder*)((char*)&movedPrivate+48);
    nvnBufferBuilderSetDevice(reclaimedBuilder,&device);nvnBufferBuilderSetDefaults(reclaimedBuilder);nvnBufferBuilderSetStorage(reclaimedBuilder,&pool,2048,128);
    CHECK(botw_graphics_read_texture(&movedPrivate,afterWrite,sizeof afterWrite)&&!memcmp(beforeWrite,afterWrite,sizeof afterWrite));
    CHECK(botw_graphics_read_texture(&valueAlias,afterWrite,sizeof afterWrite)&&!memcmp(beforeWrite,afterWrite,sizeof afterWrite));
    puts("BUILDER_FIELDS_PRIVATE_PIXELS_AND_VALUE_ALIAS_OK");
    if(strstr(argv[1],"retired"))nvnTextureFinalize(&valueAlias);
    if(strstr(argv[1],"alias")){
      unsigned char priorPixels[64];CHECK(botw_graphics_read_texture(&texture,priorPixels,sizeof priorPixels));memset(memory+2048,0x3c,32);memset(memory+2112,0xa5,32);
      nvnCommandBufferBeginRecording(&cb);nvnCommandBufferCopyBufferToBuffer(&cb,gpu+2048,gpu+2112,32,0);
      nvnCommandBufferCopyBufferToBuffer(&cb,gpu+6144,gpu+7168,64,0);handle=nvnCommandBufferEndRecording(&cb);
      builderGPUCallbacks=0;builderGPUEscaping=1;
      if(!sigsetjmp(builderGPURecovery,1)){nvnQueueSubmitCommands(&queue,1,&handle);CHECK(0);}builderGPUEscaping=0;
      CHECK(builderGPUCallbacks==1&&strstr(botw_graphics_last_error(),"buffer address aliases private texture storage authority"));
      printf("EXPECTED_BUILDER_AUTHORITY_REJECTION %s\n",botw_graphics_last_error());nvnQueueFinish(&queue);
      CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels)&&!memcmp(priorPixels,pixels,sizeof pixels));
      char shutdownError[512];CHECK(botw_graphics_shutdown_checked(shutdownError,sizeof shutdownError));
      for(unsigned i=0;i<32;i++)CHECK(memory[2112+i]==0xa5);
      free(memory);puts("BUILDER_FIELDS_PRIVATE_AUTHORITY_REJECTED_BEFORE_ANY_GPU_COPY_OK");return 0;
    }
    nvnTextureFinalize(&valueAlias);puts("BUILDER_FIELDS_PRIVATE_CONTENTS_OK");
'''
        source = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', feature+'    nvnMemoryPoolFinalize(&pool);')
        source = source.replace('#include <unistd.h>', r'''#include <unistd.h>
#include <setjmp.h>
static sigjmp_buf builderGPURecovery;static int builderGPUEscaping,builderGPUCallbacks;
static void builderGPUError(const char *message){if(builderGPUEscaping){builderGPUCallbacks++;siglongjmp(builderGPURecovery,1);}fprintf(stderr,"EXPECTED_OR_UNHANDLED %s\n",message);}
''').replace('botw_graphics_init(NULL, NULL, valid)', 'botw_graphics_init(NULL, builderGPUError, valid)')
        self.probe(source, [('builder-private', 'BUILDER_FIELDS_PRIVATE_CONTENTS_OK', True),
            ('builder-private-live-alias', 'BUILDER_FIELDS_PRIVATE_AUTHORITY_REJECTED_BEFORE_ANY_GPU_COPY_OK', True),
            ('builder-private-retired-alias', 'BUILDER_FIELDS_PRIVATE_AUTHORITY_REJECTED_BEFORE_ANY_GPU_COPY_OK', True)])

    def test_transferred_array_descriptor_keeps_layers_mips_and_reclaimed_source_storage(self):
        from test_botw_graphics import ARRAY_FEATURE, METAL_SMOKE
        move = r'''
    NVNtexture movedArray;botw_graphics_texture_move_token moveToken;char moveError[256];
    CHECK(botw_graphics_texture_move_preflight(&arrayTexture,&movedArray,sizeof arrayTexture,&moveToken,moveError,sizeof moveError));
    memcpy(&movedArray,&arrayTexture,sizeof movedArray);CHECK(botw_graphics_texture_move_commit(&moveToken,moveError,sizeof moveError));
    NVNsamplerBuilder reclaimedBuilder;nvnSamplerBuilderSetDefaults(&reclaimedBuilder);nvnSamplerBuilderSetDevice(&reclaimedBuilder,&device);
    CHECK(nvnSamplerInitialize((NVNsampler*)((char*)&arrayTexture+32),&reclaimedBuilder));
'''
        feature = ARRAY_FEATURE.replace('nvnTexturePoolRegisterTexture(&tp,3,&arrayTexture,NULL);',
            'nvnTexturePoolRegisterTexture(&tp,3,&arrayTexture,NULL);'+move)
        feature = feature.replace('if(pass){if(r8)', 'if(pass)nvnTexturePoolRegisterTexture(&tp,3,&movedArray,NULL);if(pass){if(r8)')
        feature = feature.replace('nvnTextureFinalize(&arrayTexture);', 'nvnTextureFinalize(&movedArray);')
        source = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', feature+'    nvnMemoryPoolFinalize(&pool);')
        self.probe(source, [('array-move', 'GPU_ARRAY_LAYER_MIP_COLORS_OK', True)])

    def test_private_gpu_contents_and_live_retired_authority_survive_storage_move(self):
        from test_botw_graphics import METAL_SMOKE
        feature = r'''
    API(nvnTextureFinalize);NVNtexture privateTexture,movedPrivate;
    nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetSize2D(&tb,4,4);
    nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(&tb,&pool,6144);CHECK(nvnTextureInitialize(&privateTexture,&tb));
    const NVNtexture *privateTargets[]={&privateTexture};float privateColor[]={.125f,.5f,.875f,1};
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,privateTargets,NULL,NULL,NULL);
    nvnCommandBufferClearColor(&cb,0,privateColor,15);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
    unsigned char beforeMove[64],afterMove[64];CHECK(botw_graphics_read_texture(&privateTexture,beforeMove,sizeof beforeMove));
    for(unsigned i=0;i<16;i++)CHECK(abs((int)beforeMove[i*4]-32)<=1&&abs((int)beforeMove[i*4+1]-128)<=1&&abs((int)beforeMove[i*4+2]-223)<=1&&beforeMove[i*4+3]==255);
    botw_graphics_texture_move_token moveToken;char moveError[256];CHECK(botw_graphics_texture_move_preflight(&privateTexture,&movedPrivate,sizeof privateTexture,&moveToken,moveError,sizeof moveError));
    memcpy(&movedPrivate,&privateTexture,sizeof movedPrivate);CHECK(botw_graphics_texture_move_commit(&moveToken,moveError,sizeof moveError));
    CHECK(botw_graphics_read_texture(&movedPrivate,afterMove,sizeof afterMove));CHECK(!memcmp(beforeMove,afterMove,sizeof afterMove));
    if(!strcmp(argv[1],"private-retired-alias"))nvnTextureFinalize(&movedPrivate);
    if(strstr(argv[1],"alias")){nvnCommandBufferBeginRecording(&cb);nvnCommandBufferCopyBufferToBuffer(&cb,gpu+6144,gpu+7168,64,0);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);return 2;}
    nvnTextureFinalize(&movedPrivate);puts("GPU_PRIVATE_CONTENTS_MOVE_OK");
'''
        source = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', feature+'    nvnMemoryPoolFinalize(&pool);')
        self.probe(source, [('private', 'GPU_PRIVATE_CONTENTS_MOVE_OK', True),
            ('private-alias', 'buffer address aliases private texture storage authority', False),
            ('private-retired-alias', 'buffer address aliases private texture storage authority', False)])

    def test_exact_target_replacement_preserves_old_and_new_array_descriptor_pixels(self):
        from test_botw_graphics import ARRAY_FEATURE, METAL_SMOKE
        first = r'''
    NVNtexture destinationArray,oldAlias;botw_graphics_texture_move_token moveToken;char moveError[512];
    CHECK(botw_graphics_texture_move_preflight(&arrayTexture,&destinationArray,192,&moveToken,moveError,sizeof moveError));memcpy(&destinationArray,&arrayTexture,192);CHECK(botw_graphics_texture_move_commit(&moveToken,moveError,sizeof moveError));oldAlias=destinationArray;
'''
        replacement = r'''
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,0,textureHandle);nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);NVNcommandHandle beforeReplacement=nvnCommandBufferEndRecording(&cb);
    unsigned char *freshMemory=NULL;CHECK(!posix_memalign((void**)&freshMemory,page,page*4));for(size_t i=0;i<page*4;i++)freshMemory[i]=(unsigned char)(255-arrayMemory[i]);NVNmemoryPool freshPool;nvnMemoryPoolBuilderSetStorage(&mb,freshMemory,page*4);CHECK(nvnMemoryPoolInitialize(&freshPool,&mb));NVNtexture freshArray;nvnTextureBuilderSetStorage(&tb,&freshPool,0);CHECK(nvnTextureInitialize(&freshArray,&tb));
    CHECK(botw_graphics_texture_move_preflight(&freshArray,&destinationArray,192,&moveToken,moveError,sizeof moveError));memcpy(&destinationArray,&freshArray,192);CHECK(botw_graphics_texture_move_commit(&moveToken,moveError,sizeof moveError));nvnTexturePoolRegisterTexture(&tp,2,&destinationArray,NULL);uint64_t freshHandle=nvnDeviceGetTextureHandle(&device,2,4);
    nvnQueueSubmitCommands(&queue,1,&beforeReplacement);nvnQueueFinish(&queue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));for(unsigned y=0;y<4;y++)for(unsigned x=0;x<4;x++)CHECK(!memcmp(pixels+(y*4+x)*4,colors[x>=2][y<3?y:2],4));
'''
        fresh_draw = r'''
      nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,0,freshHandle);nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
      for(unsigned y=0;y<4;y++)for(unsigned x=0;x<4;x++){unsigned layer=x>=2,level=y<3?y:2;if(pass&&!y&&layer)layer=0;for(unsigned c=0;c<3;c++)CHECK(pixels[(y*4+x)*4+c]==255-colors[layer][level][c]);CHECK(pixels[(y*4+x)*4+3]==255);}
'''
        feature = ARRAY_FEATURE.replace('nvnTexturePoolRegisterTexture(&tp,3,&arrayTexture,NULL);',
            'nvnTexturePoolRegisterTexture(&tp,3,&arrayTexture,NULL);'+first)
        feature = feature.replace('    for(unsigned pass=0;pass<2;pass++){',
            replacement+'    for(unsigned pass=0;pass<2;pass++){')
        feature = feature.replace('if(pass){if(r8)',
            'if(pass)for(unsigned c=0;c<4;c++)freshMemory[layerStride+c]=(unsigned char)(255-colors[0][0][c]);if(pass){if(r8)')
        feature = feature.replace('\n    }\n    nvnTextureFinalize(&arrayTexture);',
            fresh_draw+'\n    }\n    nvnTextureFinalize(&oldAlias);for(unsigned pass=1;pass<2;pass++){'+fresh_draw+'}nvnTextureFinalize(&destinationArray);nvnMemoryPoolFinalize(&freshPool);free(freshMemory);')
        source = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', feature+'    nvnMemoryPoolFinalize(&pool);')
        self.probe(source, [('array-replace', 'GPU_ARRAY_LAYER_MIP_COLORS_OK', True)])

    def test_exact_target_replacement_keeps_both_private_contents_and_authorities(self):
        from test_botw_graphics import METAL_SMOKE
        feature = r'''
    API(nvnTextureFinalize);NVNtexture oldSource,newSource,destinationPrivate,oldAlias;botw_graphics_texture_move_token moveToken;char moveError[512];
    NVNtexture *sources[]={&oldSource,&newSource};const float shades[2][4]={{.125f,.5f,.875f,1},{.75f,.25f,.5f,1}};
    for(unsigned which=0;which<2;which++){nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetSize2D(&tb,4,4);nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(&tb,&pool,which?7168:6144);CHECK(nvnTextureInitialize(sources[which],&tb));const NVNtexture *privateTargets[]={sources[which]};nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,privateTargets,NULL,NULL,NULL);nvnCommandBufferClearColor(&cb,0,shades[which],15);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);}
    CHECK(botw_graphics_texture_move_preflight(&oldSource,&destinationPrivate,192,&moveToken,moveError,sizeof moveError));memcpy(&destinationPrivate,&oldSource,192);CHECK(botw_graphics_texture_move_commit(&moveToken,moveError,sizeof moveError));oldAlias=destinationPrivate;
    CHECK(botw_graphics_texture_move_preflight(&newSource,&destinationPrivate,192,&moveToken,moveError,sizeof moveError));memcpy(&destinationPrivate,&newSource,192);CHECK(botw_graphics_texture_move_commit(&moveToken,moveError,sizeof moveError));
    unsigned char oldPixels[64],newPixels[64];CHECK(botw_graphics_read_texture(&oldAlias,oldPixels,sizeof oldPixels)&&botw_graphics_read_texture(&destinationPrivate,newPixels,sizeof newPixels));for(unsigned i=0;i<16;i++)for(unsigned c=0;c<4;c++){CHECK(abs((int)oldPixels[i*4+c]-(int)(shades[0][c]*255+.5f))<=1);CHECK(abs((int)newPixels[i*4+c]-(int)(shades[1][c]*255+.5f))<=1);}
    int old=strstr(argv[1],"old")!=NULL;if(strstr(argv[1],"retired"))nvnTextureFinalize(old?&oldAlias:&destinationPrivate);
    if(strstr(argv[1],"alias")){nvnCommandBufferBeginRecording(&cb);nvnCommandBufferCopyBufferToBuffer(&cb,gpu+(old?6144:7168),gpu+7680,64,0);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);return 2;}
    nvnTextureFinalize(&oldAlias);unsigned char after[64];CHECK(botw_graphics_read_texture(&destinationPrivate,after,sizeof after)&&!memcmp(after,newPixels,sizeof after));nvnTextureFinalize(&destinationPrivate);puts("GPU_PRIVATE_OLD_NEW_REPLACEMENT_CONTENTS_OK");
'''
        source = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', feature+'    nvnMemoryPoolFinalize(&pool);')
        self.probe(source, [('private-replace', 'GPU_PRIVATE_OLD_NEW_REPLACEMENT_CONTENTS_OK', True),
            *[(mode, 'buffer address aliases private texture storage authority', False) for mode in
              ['private-old-alias', 'private-new-alias', 'private-old-retired-alias', 'private-new-retired-alias']]])
