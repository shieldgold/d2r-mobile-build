/* SPDX-License-Identifier: MIT
 * Runs the actual product-environment block with real libc state in this owned
 * process. Full prepare, UIKit, Guest, resources and graphics are not run. */
#include <stdlib.h>
#include <errno.h>
#include <assert.h>
static int observed_setenv(const char *,const char *,int);
#define setenv observed_setenv
#include "../frontend/whole_runtime_configuration.m"
#undef setenv

static unsigned setter_calls;
static const char *rejected_key;
static int observed_setenv(const char *key,const char *value,int overwrite) {
    setter_calls++;assert(overwrite==1);
    if(rejected_key&&!strcmp(key,rejected_key)) {errno=EACCES;return -1;}
    return setenv(key,value,overwrite);
}
static int product_setup(runtime_context *context,const char *save,char *error,size_t capacity) {
    int code=0;
#include "actual_product_environment.inc"
    return 0;
failed:
    return code?code:EIO;
}
static const char *const keys[]={"DM_ROOT","DM_ROMFS","DM_SAVE","DM_TEMP","DM_ARGV0",
    "DM_GAME_VERSION","DM_LANG","DM_OPMODE","DM_BOTW_AUDIO_CPU","DM_AUDIO",
    "DM_BOTW_SHADER_MANIFEST","DM_BOTW_POOL_POLICY"};
int main(int argc,char **argv) {
    assert(argc==2);
    runtime_context context={0};
    strcpy(context.documents,"/owned/documents");strcpy(context.romfs,"/owned/romfs.bin");
    strcpy(context.temporary,"/owned/temporary");strcpy(context.manifest,"/owned/manifest.json");
    const char *save="/owned/save";char error[192]={0};
    const char *values[]={context.documents,context.romfs,save,context.temporary,"BotW","1.0.0",
        "en-US","0","0","1",context.manifest,"ordinary-pools-base-v0-v1"};
    for(size_t i=0;i<sizeof keys/sizeof *keys;i++)assert(!unsetenv(keys[i]));
    assert(!setenv("OWNED_UNRELATED_PRODUCT_SENTINEL","retained",1));
    if(!strcmp(argv[1],"inherited")) {
        assert(!setenv("DM_BOTW_POOL_POLICY","ordinary-pools-1.9.0-probe-v1",1));
    } else if(!strcmp(argv[1],"unknown")) {
        assert(!setenv("DM_BOTW_POOL_POLICY","unreviewed-owned-policy",1));
    } else if(!strcmp(argv[1],"empty")) {
        assert(!setenv("DM_BOTW_POOL_POLICY","",1));
    } else if(!strcmp(argv[1],"policy-failed")) {
        assert(!setenv("DM_BOTW_POOL_POLICY","unreviewed-owned-policy",1));
        rejected_key="DM_BOTW_POOL_POLICY";
    } else if(!strcmp(argv[1],"existing-failed")) {
        assert(!setenv("DM_SAVE","unchanged-after-early-failure",1));rejected_key="DM_ROMFS";
    } else assert(!strcmp(argv[1],"missing"));
    int result=product_setup(&context,save,error,sizeof error);
    if(rejected_key) {
        assert(result==EACCES&&strstr(error,"setting explicit product environment failed"));
        if(!strcmp(argv[1],"policy-failed")) {
            assert(setter_calls==12);
            assert(!strcmp(getenv("DM_BOTW_POOL_POLICY"),"unreviewed-owned-policy"));
            for(size_t i=0;i+1<sizeof keys/sizeof *keys;i++)assert(!strcmp(getenv(keys[i]),values[i]));
        } else {
            assert(setter_calls==2&&!strcmp(getenv("DM_ROOT"),context.documents));
            assert(!getenv("DM_ROMFS")&&!strcmp(getenv("DM_SAVE"),"unchanged-after-early-failure"));
            assert(!getenv("DM_BOTW_POOL_POLICY"));
        }
    } else {
        assert(!result&&!error[0]);
        /* The old implementation completes normally but leaves this absent or
         * inherited; that is the behavior RED, not a missing symbol. */
        assert(getenv("DM_BOTW_POOL_POLICY")&&!strcmp(getenv("DM_BOTW_POOL_POLICY"),values[11]));
        assert(setter_calls==12);
        for(size_t i=0;i<sizeof keys/sizeof *keys;i++)assert(!strcmp(getenv(keys[i]),values[i]));
    }
    assert(!strcmp(getenv("OWNED_UNRELATED_PRODUCT_SENTINEL"),"retained"));
    assert(atomic_load(&context.phase)==RUNTIME_COLD&&!context.services&&!context.audren&&!context.ipc_bound);
    puts("WHOLE_PRODUCT_POOL_POLICY_ENV_OK");return 0;
}
