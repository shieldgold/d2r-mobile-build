/* Synthetic numeric expectations; no original executable bytes. */
#include "graphics.h"
#include "nvn_protos.h"
#include <inttypes.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
static sigjmp_buf jump;
static void error(const char *s){fprintf(stderr,"ERROR:%s\n",s);siglongjmp(jump,1);}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK:%s message=%s\n",#x,botw_graphics_last_error());return 1;}}while(0)
#define API(name) NVNT_##name name=(NVNT_##name)botw_nvn_bootstrap_loader(#name)
int main(void){
 if(!botw_graphics_init(NULL,error,NULL))return 77;
 API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceFinalize);API(nvnDeviceGetTextureHandle);
 NVNdeviceBuilder b;NVNdevice d;nvnDeviceBuilderSetDefaults(&b);CHECK(nvnDeviceInitialize(&d,&b));
 static const struct{int texture,sampler;uint64_t expected;} cases[]={
  {0,0,UINT64_C(0x0000000100000000)},{0,1,UINT64_C(0x0000000100100000)},
  {0,0x7ff,UINT64_C(0x000000017ff00000)},{0,0x800,UINT64_C(0xffffffff80000000)},
  {0,0xfff,UINT64_C(0xfffffffffff00000)},{0x7ffff,0x800,UINT64_C(0xffffffff8007ffff)},
  {0x80000,0x7ff,UINT64_C(0x000000017ff80000)},{0x80000,0x800,UINT64_C(0xffffffff80080000)},
  {0xfffff,0,UINT64_C(0x00000001000fffff)},{0xfffff,0x7ff,UINT64_C(0x000000017fffffff)},
  {0xfffff,0x800,UINT64_C(0xffffffff800fffff)},{0xfffff,0xfff,UINT64_MAX}};
 for(size_t i=0;i<sizeof cases/sizeof *cases;i++){
  uint64_t actual=nvnDeviceGetTextureHandle(&d,cases[i].texture,cases[i].sampler);
  if(actual!=cases[i].expected){fprintf(stderr,"case=%zu actual=%016" PRIx64 " expected=%016" PRIx64 "\n",i,actual,cases[i].expected);return 1;}
 }
 static const int invalid[][2]={{-1,0},{0x100000,0},{0,-1},{0,0x1000}};
 for(size_t i=0;i<sizeof invalid/sizeof *invalid;i++){
  if(!sigsetjmp(jump,1)){(void)nvnDeviceGetTextureHandle(&d,invalid[i][0],invalid[i][1]);CHECK(0);}
  CHECK(strstr(botw_graphics_last_error(),"texture/sampler descriptor index"));
  char expected[192];snprintf(expected,sizeof expected,"texture=%d(0x%x) sampler=%d(0x%x)",invalid[i][0],(unsigned)invalid[i][0],invalid[i][1],(unsigned)invalid[i][1]);CHECK(strstr(botw_graphics_last_error(),expected));
 }
 nvnDeviceFinalize(&d);botw_graphics_shutdown();CHECK(botw_graphics_init(NULL,error,NULL));botw_graphics_shutdown();
 puts("TEXTURE_HANDLE_SIGN_EXTENSION_BIT32_AND_DOMAIN_OK");return 0;
}
