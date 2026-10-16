/* Execute only the patched normal selection/transaction helpers against owned
 * metadata and a CPU compiler port. No Metal device or guest is created. */
#import "whole_shader_conditional.h"
#import "botw_shader_runtime.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nvn_obj.h"
@interface NvShader : NSObject { @public botw_shader_identity ident;BotwShaderArtifact *artifact; } @end
@implementation NvShader @end
@interface NvProg : NSObject { @public NSMutableDictionary<NSString *,NvProg *> *conditional;NvShader *sh[6]; } @end
@implementation NvProg @end
static pthread_mutex_t g_rmu=PTHREAD_MUTEX_INITIALIZER;
static _Atomic uint64_t g_reg_gen=4;
static struct {int has_rt;p_set_rt rt;} R;
static struct {uint32_t conditional_color0_format,conditional_color0_known;} g_pass;
static NSMutableDictionary<NSString *,NvShader *> *g_shcache;
static _Atomic uint32_t g_shcache_n;
static NSMutableDictionary<NSNumber *,NvProg *> *serials;
static BotwShaderManifest *manifest;
static botw_shader_info fixture_infos[2];
static int mutation,compile_calls;
static dev_t_ device;
static tex_t attachment;
static NvProg *serial_get(uint64_t n){return serials[@(n)];}
static void serial_put(uint64_t n,NvProg *p){if(!serials)serials=[NSMutableDictionary dictionary];serials[@(n)]=p;}
BotwShaderManifest *botw_shader_runtime_manifest(void){return manifest;}
int botw_shader_get_info(const NVNshaderData *sd,botw_shader_info *out){*out=fixture_infos[(unsigned)(uintptr_t)sd->data];return 1;}
void botw_shader_unsupported(const char *why,const char *key){fprintf(stderr,"unsupported: %s %s\n",why,key?key:"");exit(50);}
static NvShader *shader_load_artifact(NSString *label,BotwShaderArtifact *a,const botw_shader_info *info){
    (void)label;++compile_calls;
    if(pthread_mutex_trylock(&g_rmu))exit(60);pthread_mutex_unlock(&g_rmu);
    botw_conditional_modes_lock();
    if(mutation==1)device.origin^=1;
    if(mutation==2)device.depth_mode^=1;
    botw_conditional_modes_unlock();
    if(mutation==3){pthread_mutex_lock(&g_rmu);g_reg_gen++;pthread_mutex_unlock(&g_rmu);}
    if(mutation==4)g_pass.conditional_color0_known=0;
    if(mutation==6)g_pass.conditional_color0_format=41;
    if(mutation==5)return nil;
    NvShader *s=[NvShader new];s->artifact=a;botw_shader_identity_set(&s->ident,info);return s;
}
#include "conditional_runtime_helpers.inc"
int main(int argc,char **argv){
    if(argc!=4)return 2;
    @autoreleasepool {
        NSError *error=nil;manifest=[WholeShaderConditionalManifest conditionalManifestAtAbsolutePath:@(argv[1]) error:&error];if(!manifest)return 3;
        snprintf(fixture_infos[0].key,17,"%.16s",argv[2]);snprintf(fixture_infos[0].sha256,65,"%s",argv[2]);fixture_infos[0].stage=0;
        device.magic=MAGIC('D','E','V',0);attachment.d.format=37;R.has_rt=1;R.rt.n=1;R.rt.color[0]=&attachment;
        g_pass.conditional_color0_format=37;g_pass.conditional_color0_known=1;
        prog_t program={0};program.serial=7;program.dev=(NVNdevice *)&device;program.nstage=1;program.sd[0].data=0;
        if(atoi(argv[3])==4||atoi(argv[3])==6||atoi(argv[3])==7){
            fixture_infos[1]=fixture_infos[0];fixture_infos[1].stage=1;program.nstage=2;program.sd[1].data=1;
        }
        mutation=atoi(argv[3]);
        botw_conditional_draw_state snapshot;
        NvProg *p=conditional_program_for_draw(&program,&snapshot);if(!p||compile_calls!=(int)program.nstage)return 4;
        conditional_draw_guard guard __attribute__((cleanup(conditional_draw_unlock)))={0};
        int ok=conditional_draw_commit(&guard,&program,p,&snapshot);
        if(mutation==1||mutation==2||mutation==3||mutation==4||mutation==6){if(ok){fprintf(stderr,"changed metadata was accepted after compilation\n");return 7;}return 0;}
        if(!ok||!guard.held)return 8;
        if(pthread_mutex_trylock(&g_rmu)==0)return 9;
        conditional_draw_unlock(&guard);
        if(pthread_mutex_trylock(&g_rmu))return 10;pthread_mutex_unlock(&g_rmu);
        if(mutation==8){
            prog_t second=program;second.serial++;
            fixture_infos[0].sha256[63]='1';
            NvProg *other=conditional_program_for_draw(&second,&snapshot);
            if(other==p||other->sh[0]==p->sh[0]||compile_calls!=2||g_shcache.count!=2)return 21;
            if(conditional_program_for_draw(&second,&snapshot)!=other||compile_calls!=2)return 22;
            return 0;
        }
        if(mutation==9){
            botw_conditional_modes_lock();device.origin=1;botw_conditional_modes_unlock();
            NvProg *origin=conditional_program_for_draw(&program,&snapshot);
            if(origin==p||origin->sh[0]==p->sh[0]||compile_calls!=2)return 23;
            botw_conditional_modes_lock();device.depth_mode=1;botw_conditional_modes_unlock();
            NvProg *depth=conditional_program_for_draw(&program,&snapshot);
            if(depth==origin||depth->sh[0]==origin->sh[0]||compile_calls!=3)return 24;
            if(conditional_program_for_draw(&program,&snapshot)!=depth||compile_calls!=3)return 25;
            return 0;
        }
        /* Same exact tuple reuses the original immutable shader/owner. */
        if(conditional_program_for_draw(&program,&snapshot)!=p||compile_calls!=(int)program.nstage)return 11;
        return 0;
    }
}
