#if !defined(NX_PROVIDER_RUNTIME) && !defined(NX_PROVIDER_PROGRAM)
/* Owned protocol ports only. The actual conditional loader/compiler and native
 * integration functions are compiled by the harness; no MTL device is made. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include "nvn_obj.h"
#define CHECK(v) do {if(!(v)){fprintf(stderr,"owned provider contract line %d\n",__LINE__);return 91;}}while(0)
#define MLOG(...) ((void)0)
static unsigned eager;
@interface NvShader : NSObject @end
@implementation NvShader @end
@interface NvProg : NSObject { @public NvShader *sh[6]; } @end
@implementation NvProg @end
static struct {const prog_t *bst[5];} S;
static NSMutableDictionary<NSNumber*,NvProg*> *serials;
static uint64_t next_serial=10;
uint64_t nvn_new_serial(void){return ++next_serial;}
static void serial_put(uint64_t n,NvProg *p){if(!serials)serials=[NSMutableDictionary dictionary];serials[@(n)]=p;}
static void serial_del(uint64_t n){[serials removeObjectForKey:@(n)];}
static NvProg *owned_program;
static uint64_t g_ser_gen;
static NSMutableDictionary<NSString*,NSString*> *g_shmap;
static NvProg *serial_get(uint64_t n){(void)n;eager++;if(!owned_program)owned_program=[NvProg new];return owned_program;}
static void shmap_load(void){abort();}
static NvShader *builtin_get(const char *key){(void)key;abort();}
static NvShader *shader_get(NSString *key,int stage){(void)key;(void)stage;abort();}
static int stage_of(NSString *stage){(void)stage;abort();}
int prog_key(const prog_t *p,unsigned st,uint8_t key[8]){(void)p;(void)st;(void)key;abort();}
#ifdef NX_BASE_V0_PROVIDER
static int nx_base_shader_enabled(void){return 1;}
#endif
#include "prewalk.inc"
#include "bind_stages.inc"
int main(int argc,char **argv){@autoreleasepool {
 CHECK(argc==2);(void)argv;
 prog_t vertex={.serial=1},fragment={.serial=2};
 if(!strcmp(argv[1],"prewalk")){CHECK(prog_get(&vertex)==nil);CHECK(eager==0);return 0;}
 const prog_t *p=bind_stages(&vertex,1);CHECK(eager==0);CHECK(p==&vertex);
 p=bind_stages(&fragment,2);CHECK(p==&vertex);CHECK(S.bst[0]==&vertex&&S.bst[1]==&fragment);CHECK(eager==0);
 return 0;
}}

#elif defined(NX_PROVIDER_RUNTIME)
/* Complete original manifest/source compiler with CPU Objective-C ports. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <CommonCrypto/CommonDigest.h>
#import "base_v0_shader_provider.h"
#import "nvn_refl.h"
#include "../tex/nvn_fmt_mtl.h"
#include "nvn_obj.h"
#include <pthread.h>
#include <stdatomic.h>
#define CHECK(v) do{if(!(v)){fprintf(stderr,"owned runtime contract line %d\n",__LINE__);return 91;}}while(0)
#define MLOG(...) ((void)0)
#define MDIE(...) do{fprintf(stderr,"owned terminal source route\n");exit(50);}while(0)
@interface NxBaseShaderProvider (OwnedPrivateConstructor)
+ (instancetype)checkedManifest:(WholeShaderConditionalManifest *)manifest bridge:(NSDictionary *)bridge admission:(NSDictionary *)admission error:(NSError **)error;
@end
static NxBaseShaderProvider *owned_provider;
static NxBaseShaderProvider *fixture_provider(void){return owned_provider;}
static pthread_mutex_t g_rmu=PTHREAD_MUTEX_INITIALIZER;
static _Atomic uint64_t g_reg_gen=4;
static struct {int has_rt;p_set_rt rt;} R;
static struct {uint32_t conditional_color0_format,conditional_color0_known,cfmt[8];} g_pass;
static struct {const prog_t *bst[5];} S;
static dev_t_ device;
static tex_t attachment;
static botw_shader_info fixture_info[2];
static unsigned mutation,compile_calls;
static void mutate(void){
 if(pthread_mutex_trylock(&g_rmu))exit(61);pthread_mutex_unlock(&g_rmu);
 botw_conditional_modes_lock();
 if(mutation==1&&compile_calls==1)device.origin^=1;if(mutation==2&&compile_calls==1)device.depth_mode^=1;
 botw_conditional_modes_unlock();
 if(mutation==3){pthread_mutex_lock(&g_rmu);g_reg_gen++;pthread_mutex_unlock(&g_rmu);}
 if(mutation==4)g_pass.conditional_color0_known=0;
 if(mutation==5)g_pass.conditional_color0_format=41;
}
@interface OwnedFunction : NSObject
@property(nonatomic) MTLFunctionType functionType;
@end
@implementation OwnedFunction @end
@interface OwnedLibrary : NSObject
@property(nonatomic) MTLFunctionType type;
@end
@implementation OwnedLibrary
- (id)newFunctionWithName:(NSString *)name {if(![name isEqual:@"main0"])return nil;OwnedFunction *f=[OwnedFunction new];f.functionType=self.type;return f;}
@end
@interface OwnedCompiler : NSObject @end
@implementation OwnedCompiler
- (id)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options error:(NSError **)error {
 (void)error;assert(source.length&&options&&!options.fastMathEnabled);if(mutation==13)abort();compile_calls++;mutate();
 if(mutation==6)return nil;OwnedLibrary *library=[OwnedLibrary new];
 library.type=mutation==7?MTLFunctionTypeKernel:([source containsString:@"fragment "]?MTLFunctionTypeFragment:MTLFunctionTypeVertex);return library;
}
@end
static id<MTLDevice> g_dev;
@interface NvShader : NSObject { @public id<MTLFunction> fn;nvn_refl rf;int stage;char key[24];id<MTLArgumentEncoder> ae;NSUInteger arg_len; } @end
@implementation NvShader @end
@interface NvProg : NSObject { @public NvShader *sh[6]; } @end
@implementation NvProg @end
static NSMutableDictionary<NSString*,NvShader*> *g_shcache;
static void c1_store(NvShader *shader){(void)shader;abort();}
static uint8_t owned_control[2001],owned_code[3][16];
static int mtl_addr(uint64_t address,uint64_t length,void **buffer,uint64_t *offset){(void)buffer;(void)offset;
 for(unsigned i=0;i<3;i++)if(address==(uint64_t)owned_code[i]&&length==16)return 1;return 0;}
static int fixture_shader_info(const uint8_t full[32],uint32_t control,uint32_t code,botw_shader_info *out){return [owned_provider infoForNativeDigest:full controlSize:control codeSize:code output:out];}
#define nx_base_shader_info fixture_shader_info
#define LOG(...) ((void)0)
#define OLOG(...) ((void)0)
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__)
static void nx_program_device_bind(const void *,const void *);
void mtl_prog_release(prog_t *p){(void)p;}
uint64_t nvn_new_serial(void){static uint64_t serial=5;return ++serial;}
#include "program_entries.inc"
#undef nx_base_shader_info
static uint64_t g_ser_gen;
static NSMutableDictionary<NSString*,NSString*> *g_shmap;
static NvProg *serial_get(uint64_t n){(void)n;abort();}
static void serial_put(uint64_t n,NvProg *p){(void)n;(void)p;abort();}
static void serial_del(uint64_t n){(void)n;abort();}
static void shmap_load(void){abort();}
static NvShader *builtin_get(const char *key){(void)key;abort();}
static NvShader *shader_get(NSString *key,int st){(void)key;(void)st;abort();}
static int stage_of(NSString *s){(void)s;abort();}
#include "prewalk.inc"
#include "bind_stages.inc"
#include "conditional_helpers.inc"
@interface OwnedAttachment : NSObject
@property(nonatomic,strong) id texture;
@end
@implementation OwnedAttachment @end
@interface OwnedPass : NSObject
@property(nonatomic,strong) NSArray<OwnedAttachment*> *colorAttachments;
@end
@implementation OwnedPass @end
static void realized_format(OwnedPass *pd){
#include "attachment_format.inc"
}
static NSDictionary *json(const char *path){NSData *raw=[NSData dataWithContentsOfFile:@(path)];return [NSJSONSerialization JSONObjectWithData:raw options:0 error:NULL];}
int main(int argc,char **argv){@autoreleasepool {
 if(argc==3&&!strcmp(argv[1],"bundle")){NSError *error=nil;CHECK([NxBaseShaderProvider providerAtBundle:@(argv[2]) error:&error]);return 0;}
 CHECK(argc==5);NSDictionary *bridge=json(argv[2]),*admission=json(argv[3]);NSError *error=nil;
 WholeShaderConditionalManifest *manifest=[WholeShaderConditionalManifest conditionalManifestAtAbsolutePath:@(argv[1]) error:&error];CHECK(manifest);
 owned_provider=[NxBaseShaderProvider checkedManifest:manifest bridge:bridge admission:admission error:&error];if(atoi(argv[4])==10){CHECK(!owned_provider);return 0;}CHECK(owned_provider);
 for(unsigned st=0;st<2;st++) {NSDictionary *row=bridge[@"rows"][st];uint8_t sum[32];const char *hex=[row[@"native_sha256"] UTF8String];
  for(unsigned i=0;i<32;i++){unsigned n;sscanf(hex+2*i,"%2x",&n);sum[i]=(uint8_t)n;}
  CHECK([owned_provider infoForNativeDigest:sum controlSize:[row[@"control_size"] unsignedIntValue] codeSize:[row[@"code_size"] unsignedIntValue] output:&fixture_info[st]]);
  botw_shader_info untouched;memset(&untouched,0x5a,sizeof untouched);botw_shader_info original=untouched;sum[31]^=1;
  CHECK(![owned_provider infoForNativeDigest:sum controlSize:1 codeSize:2 output:&untouched]&&!memcmp(&untouched,&original,sizeof original));
 }
 device.magic=MAGIC('D','E','V',0);attachment.d.format=37;R.has_rt=1;R.rt.n=1;R.rt.color[0]=&attachment;
 g_pass.conditional_color0_known=1;g_pass.conditional_color0_format=37;
 prog_t vs={0},fs={0};uint32_t length=sizeof owned_control,ncode=16;
 memcpy(owned_control+0x20,&length,4);memcpy(owned_control+0x704,&ncode,4);owned_code[1][0]=1;owned_code[2][0]=2;
 CHECK(h_nvnProgramInitialize((NVNprogram *)&vs,(NVNdevice *)&device)&&h_nvnProgramInitialize((NVNprogram *)&fs,(NVNdevice *)&device));
 NVNshaderData vd={(uint64_t)owned_code[0],owned_control},fd={(uint64_t)owned_code[1],owned_control};
 CHECK(h_nvnProgramSetShaders((NVNprogram *)&vs,1,&vd)&&h_nvnProgramSetShaders((NVNprogram *)&fs,1,&fd));
 CHECK(bind_stages(&vs,1)==&vs&&bind_stages(&fs,2)==&vs&&compile_calls==0);
 if(atoi(argv[4])==14){NVNshaderData identity={(uint64_t)owned_code[2],owned_control};
  CHECK(h_nvnProgramSetShaders((NVNprogram *)&vs,1,&identity));botw_shader_info info;CHECK(nx_base_program_info(&vs,0,&info));
  CHECK(prog_get(&vs)==nil&&compile_calls==0);botw_conditional_draw_state state;conditional_program_for_draw(&state);CHECK(0);
 }
 if(atoi(argv[4])==12){
  OwnedAttachment *a=[OwnedAttachment new];a.texture=[NSObject new];OwnedPass *pd=[OwnedPass new];pd.colorAttachments=@[a];
  uint32_t pf;CHECK(nvn_fmt_to_mtl(37,&pf,NULL));g_pass.cfmt[0]=pf;g_pass.conditional_color0_known=0;realized_format(pd);
  CHECK(g_pass.conditional_color0_known&&g_pass.conditional_color0_format==37);
  R.rt.has_cview[0]=1;R.rt.cview[0].set_format=1;R.rt.cview[0].format=154;CHECK(nvn_fmt_to_mtl(154,&pf,NULL));g_pass.cfmt[0]=pf;
  g_pass.conditional_color0_known=0;realized_format(pd);CHECK(g_pass.conditional_color0_known&&g_pass.conditional_color0_format==154);
  g_pass.cfmt[0]=999;g_pass.conditional_color0_known=0;realized_format(pd);CHECK(!g_pass.conditional_color0_known);
  R.rt.cview[0].format=999;realized_format(pd);CHECK(!g_pass.conditional_color0_known);return 0;
 }
 g_dev=(id<MTLDevice>)[OwnedCompiler new];mutation=(unsigned)atoi(argv[4]);
 botw_conditional_draw_state snapshot;NvProg *resolved=conditional_program_for_draw(&snapshot);CHECK(resolved&&compile_calls==2);
 conditional_draw_guard guard __attribute__((cleanup(conditional_draw_unlock)))={0};int ok=conditional_draw_commit(&guard,resolved,&snapshot);
 if(mutation>=1&&mutation<=5){CHECK(!ok&&!guard.held);CHECK(compile_calls==2);return 0;}
 CHECK(ok&&guard.held);CHECK(pthread_mutex_trylock(&g_rmu)!=0);conditional_draw_unlock(&guard);
 CHECK(pthread_mutex_trylock(&g_rmu)==0);pthread_mutex_unlock(&g_rmu);
 CHECK(conditional_program_for_draw(&snapshot)==resolved&&compile_calls==2);
 if(mutation==8){
  for(unsigned mode=1;mode<4;mode++){botw_conditional_modes_lock();device.origin=mode>>1;device.depth_mode=mode&1;botw_conditional_modes_unlock();
   NvProg *next=conditional_program_for_draw(&snapshot);CHECK(next!=resolved&&compile_calls==2*(mode+1));CHECK(conditional_draw_commit(&guard,next,&snapshot));conditional_draw_unlock(&guard);}
 }
 if(mutation==11){prog_keys_t *k=pk_get(&vs,0);k->info[0].sha256[63]='1';conditional_program_for_draw(&snapshot);CHECK(0);}
 if(mutation==9){S.bst[1]=NULL;NvProg *vertex=conditional_program_for_draw(&snapshot);CHECK(vertex&&vertex!=resolved&&vertex->sh[0]==resolved->sh[0]&&!vertex->sh[1]);}
 return 0;
}}
#else
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#import "base_v0_shader_provider.h"
#include "nvn_obj.h"
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
#define CHECK(v) do{if(!(v)){fprintf(stderr,"owned program contract line %d\n",__LINE__);return 91;}}while(0)
#define LOG(...) ((void)0)
#define OLOG(...) ((void)0)
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__)
static uint8_t code[16],expected[32];static unsigned releases;
int nx_base_shader_enabled(void){return 1;}
int nx_base_shader_info(const uint8_t full[32],uint32_t ctl,uint32_t n,botw_shader_info *out){
 if(memcmp(full,expected,32)||ctl!=2000||n!=16)return 0;
 memset(out,0,sizeof *out);strcpy(out->key,"0123456789abcdef");memset(out->sha256,'a',64);out->stage=0;return 1;
}
static int mtl_addr(uint64_t address,uint64_t size,void **buffer,uint64_t *offset){(void)buffer;(void)offset;return address==(uint64_t)code&&size==sizeof code;}
void mtl_prog_release(prog_t *p){(void)p;releases++;}
uint64_t nvn_new_serial(void){static uint64_t serial=5;return ++serial;}
static void nx_program_device_bind(const void *,const void *);
static pthread_mutex_t program_modes=PTHREAD_MUTEX_INITIALIZER;
void botw_conditional_modes_lock(void){pthread_mutex_lock(&program_modes);}
void botw_conditional_modes_unlock(void){pthread_mutex_unlock(&program_modes);}
#include "program_entries.inc"
int main(int argc,char **argv){@autoreleasepool{
 CHECK(argc==2);(void)argv;NVNprogram program;NVNdevice device;memset(&program,0,sizeof program);
 CHECK(h_nvnProgramInitialize(&program,&device));CHECK(nx_base_program_device(&program)==&device);
 uint8_t *control=calloc(1,2000);uint32_t len=2000,n=sizeof code;memcpy(control+0x20,&len,4);memcpy(control+0x704,&n,4);
 CC_SHA256_CTX hash;CC_SHA256_Init(&hash);CC_SHA256_Update(&hash,control,2000);CC_SHA256_Update(&hash,code,sizeof code);CC_SHA256_Final(expected,&hash);
 NVNshaderData data={(uint64_t)code,control};CHECK(h_nvnProgramSetShaders(&program,1,&data));
 botw_shader_info first;CHECK(nx_base_program_info(&program,0,&first));uint64_t serial=((prog_t *)&program)->serial;unsigned released=releases;
 uint8_t key[8];CHECK(prog_key((prog_t *)&program,0,key));CHECK(!memcmp(key,expected,8));
 NVNshaderData failed[2]={data,data};code[0]^=1;CHECK(!h_nvnProgramSetShaders(&program,2,failed));code[0]^=1;
 CHECK(((prog_t *)&program)->serial==serial&&releases==released);botw_shader_info now;CHECK(nx_base_program_info(&program,0,&now)&&!memcmp(&now,&first,sizeof first));
 free(control);((prog_t *)&program)->sd[0].control=(void *)1;
 CHECK(prog_key((prog_t *)&program,0,key)&&!memcmp(key,expected,8));
 prog_t missing={.nstage=1};missing.sd[0].control=(void *)1;CHECK(!prog_key(&missing,0,key));
 memset(&now,0x5a,sizeof now);botw_shader_info untouched=now;CHECK(!nx_base_program_info(&missing,0,&now)&&!memcmp(&now,&untouched,sizeof now));
 h_nvnProgramFinalize(&program);CHECK(!nx_base_program_info(&program,0,&now));return 0;
}}
#endif
