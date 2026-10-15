/* Synthetic state/ABI boundaries. No game shader or executable bytes. */
#include "graphics.h"
#include "nvn_protos.h"
#include "vendor/d2r-state/states.h"
#include <setjmp.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
static sigjmp_buf recovery;
static const void *blocked;
static int blockedWrite;
static void error(const char *message){fprintf(stderr,"ERROR:%s\n",message);siglongjmp(recovery,1);}
static int accessMemory(const void *p,size_t n,int write){(void)n;return p!=blocked||write!=blockedWrite;}
#define API(name) NVNT_##name name=(NVNT_##name)botw_nvn_bootstrap_loader(#name)
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK:%s %s\n",#x,botw_graphics_last_error());return 1;}}while(0)
#define REJECT(call,reason) do{if(!sigsetjmp(recovery,1)){call;CHECK(0);}CHECK(strstr(botw_graphics_last_error(),reason));}while(0)
#define SNAP(T,name,object) T name;memcpy(&name,&object,sizeof name)
int main(void){
 if(!botw_graphics_init(NULL,error,NULL))return 77;
 botw_graphics_set_memory_access_validator(accessMemory);
 API(nvnBlendStateSetDefaults);API(nvnBlendStateSetBlendTarget);API(nvnBlendStateSetBlendFunc);API(nvnBlendStateSetBlendEquation);
 API(nvnColorStateSetDefaults);API(nvnColorStateSetBlendEnable);API(nvnChannelMaskStateSetDefaults);API(nvnChannelMaskStateSetChannelMask);
 API(nvnMultisampleStateSetDefaults);API(nvnMultisampleStateSetMultisampleEnable);API(nvnMultisampleStateSetSamples);API(nvnMultisampleStateSetAlphaToCoverageEnable);API(nvnMultisampleStateSetSampleLocationsEnable);
 API(nvnPolygonStateSetDefaults);API(nvnPolygonStateSetCullFace);API(nvnPolygonStateSetFrontFace);API(nvnPolygonStateSetPolygonMode);API(nvnPolygonStateSetPolygonOffsetEnables);
 API(nvnDepthStencilStateSetDefaults);API(nvnDepthStencilStateSetDepthTestEnable);API(nvnDepthStencilStateSetDepthWriteEnable);API(nvnDepthStencilStateSetDepthFunc);API(nvnDepthStencilStateSetStencilTestEnable);API(nvnDepthStencilStateSetStencilFunc);API(nvnDepthStencilStateSetStencilOp);
 API(nvnCommandBufferBindColorState);API(nvnCommandBufferSetDepthRange);API(nvnCommandBufferSetBlendColor);API(nvnCommandBufferSetPolygonOffsetClamp);
 static struct{uint64_t a;NVNblendState state;uint64_t b;} guard;
 guard.a=UINT64_C(0x1122334455667788);guard.b=UINT64_C(0x8877665544332211);
 nvnBlendStateSetDefaults(&guard.state);SNAP(blend_st,blend,guard.state);
 CHECK(blend.target==0&&blend.src_rgb==NVN_BLEND_FUNC_ONE&&blend.dst_rgb==NVN_BLEND_FUNC_ZERO&&blend.src_a==NVN_BLEND_FUNC_ONE&&blend.dst_a==NVN_BLEND_FUNC_ZERO&&blend.eq_rgb==NVN_BLEND_EQUATION_ADD&&blend.eq_a==NVN_BLEND_EQUATION_ADD);
 nvnBlendStateSetBlendTarget(&guard.state,7);nvnBlendStateSetBlendFunc(&guard.state,NVN_BLEND_FUNC_SRC_ALPHA,NVN_BLEND_FUNC_DST_COLOR,NVN_BLEND_FUNC_CONSTANT_ALPHA,NVN_BLEND_FUNC_ONE_MINUS_CONSTANT_ALPHA);nvnBlendStateSetBlendEquation(&guard.state,NVN_BLEND_EQUATION_MIN,NVN_BLEND_EQUATION_MAX);
 memcpy(&blend,&guard.state,sizeof blend);CHECK(blend.target==7&&blend.src_rgb==5&&blend.dst_rgb==9&&blend.src_a==0x63&&blend.dst_a==0x64&&blend.eq_rgb==4&&blend.eq_a==5);
 CHECK(guard.a==UINT64_C(0x1122334455667788)&&guard.b==UINT64_C(0x8877665544332211));
 static NVNcolorState color;nvnColorStateSetDefaults(&color);nvnColorStateSetBlendEnable(&color,7,0x101);nvnColorStateSetBlendEnable(&color,0,1);nvnColorStateSetBlendEnable(&color,7,0x100);SNAP(color_st,c,color);CHECK(c.blend_en==1&&c.logic_op==NVN_LOGIC_OP_COPY&&c.alpha_func==NVN_ALPHA_FUNC_ALWAYS);
 static NVNchannelMaskState mask;nvnChannelMaskStateSetDefaults(&mask);nvnChannelMaskStateSetChannelMask(&mask,7,0x101,0x100,0x102,0x100);SNAP(chmask_st,m,mask);CHECK(m.mask==UINT32_C(0x5fffffff));
 static NVNmultisampleState ms;nvnMultisampleStateSetDefaults(&ms);SNAP(ms_st,samples,ms);CHECK(samples.enable==1&&samples.samples==0&&samples.a2c==0&&samples.sl_en==0);
 nvnMultisampleStateSetMultisampleEnable(&ms,0x100);nvnMultisampleStateSetSamples(&ms,16);nvnMultisampleStateSetAlphaToCoverageEnable(&ms,0x101);nvnMultisampleStateSetSampleLocationsEnable(&ms,0x101);memcpy(&samples,&ms,sizeof samples);CHECK(!samples.enable&&samples.samples==16&&samples.a2c==1&&samples.sl_en==1);
 static NVNpolygonState polygon;nvnPolygonStateSetDefaults(&polygon);SNAP(poly_st,p,polygon);CHECK(p.cull==NVN_FACE_NONE&&p.front==NVN_FRONT_FACE_CCW&&p.mode==NVN_POLYGON_MODE_FILL&&!p.offset_en);
 nvnPolygonStateSetCullFace(&polygon,NVN_FACE_BACK);nvnPolygonStateSetFrontFace(&polygon,NVN_FRONT_FACE_CW);nvnPolygonStateSetPolygonMode(&polygon,NVN_POLYGON_MODE_LINE);nvnPolygonStateSetPolygonOffsetEnables(&polygon,7);memcpy(&p,&polygon,sizeof p);CHECK(p.cull==2&&p.front==0&&p.mode==1&&p.offset_en==7);
 static NVNdepthStencilState depth;nvnDepthStencilStateSetDefaults(&depth);SNAP(ds_st,d,depth);CHECK(!d.dt&&d.dw&&d.df==NVN_DEPTH_FUNC_LESS&&!d.st&&d.sf_f==NVN_STENCIL_FUNC_ALWAYS&&d.fail_b==NVN_STENCIL_OP_KEEP);
 nvnDepthStencilStateSetDepthTestEnable(&depth,0x101);nvnDepthStencilStateSetDepthWriteEnable(&depth,0x100);nvnDepthStencilStateSetDepthFunc(&depth,NVN_DEPTH_FUNC_GEQUAL);nvnDepthStencilStateSetStencilTestEnable(&depth,0x101);nvnDepthStencilStateSetStencilFunc(&depth,NVN_FACE_FRONT_AND_BACK,NVN_STENCIL_FUNC_NEVER);nvnDepthStencilStateSetStencilOp(&depth,NVN_FACE_FRONT,NVN_STENCIL_OP_REPLACE,NVN_STENCIL_OP_INCR,NVN_STENCIL_OP_DECR_WRAP);
 memcpy(&d,&depth,sizeof d);CHECK(d.dt&&!d.dw&&d.df==NVN_DEPTH_FUNC_GEQUAL&&d.st&&d.sf_f==NVN_STENCIL_FUNC_NEVER&&d.sf_b==NVN_STENCIL_FUNC_NEVER&&d.fail_f==NVN_STENCIL_OP_REPLACE&&d.zfail_f==NVN_STENCIL_OP_INCR&&d.zpass_f==NVN_STENCIL_OP_DECR_WRAP&&d.fail_b==NVN_STENCIL_OP_KEEP);
 REJECT(nvnBlendStateSetBlendTarget(&guard.state,8),"blend target");REJECT(nvnBlendStateSetBlendTarget(&guard.state,-1),"blend target");
 REJECT(nvnBlendStateSetBlendFunc(&guard.state,0,1,1,1),"blend factor");REJECT(nvnBlendStateSetBlendEquation(&guard.state,0,1),"blend equation");
 REJECT(nvnColorStateSetBlendEnable(&color,8,1),"color target");REJECT(nvnChannelMaskStateSetChannelMask(&mask,-1,1,1,1,1),"channel target");
 REJECT(nvnMultisampleStateSetSamples(&ms,3),"sample count");REJECT(nvnMultisampleStateSetSamples(&ms,-1),"sample count");
 REJECT(nvnPolygonStateSetCullFace(&polygon,4),"face enum");REJECT(nvnPolygonStateSetFrontFace(&polygon,2),"front face");REJECT(nvnPolygonStateSetPolygonMode(&polygon,3),"polygon mode");REJECT(nvnPolygonStateSetPolygonOffsetEnables(&polygon,8),"offset enable");
 REJECT(nvnDepthStencilStateSetDepthFunc(&depth,0),"depth comparison");REJECT(nvnDepthStencilStateSetStencilFunc(&depth,4,1),"face enum");REJECT(nvnDepthStencilStateSetStencilFunc(&depth,1,0),"stencil comparison");REJECT(nvnDepthStencilStateSetStencilOp(&depth,1,1,0,1),"stencil operation");
 unsigned char before[sizeof color];memcpy(before,&color,sizeof color);blocked=&color;blockedWrite=1;
 REJECT(nvnColorStateSetDefaults(&color),"memory is not fully accessible");CHECK(!memcmp(before,&color,sizeof color));
 blockedWrite=0;NVNcommandBuffer nonexistent;REJECT(nvnCommandBufferBindColorState(&nonexistent,&color),"memory is not fully accessible");blocked=NULL;
 REJECT(nvnCommandBufferSetDepthRange(&nonexistent,-.01,1),"depth range outside");REJECT(nvnCommandBufferSetDepthRange(&nonexistent,0,1.01),"depth range outside");REJECT(nvnCommandBufferSetDepthRange(&nonexistent,NAN,1),"depth range outside");
 static const float badColor[]={0,NAN,0,1};REJECT(nvnCommandBufferSetBlendColor(&nonexistent,badColor),"nonfinite blend color");REJECT(nvnCommandBufferSetPolygonOffsetClamp(&nonexistent,0,INFINITY,0),"nonfinite polygon depth bias");
 nvnColorStateSetDefaults(&color);botw_graphics_shutdown();
 puts("FIXED_UPSTREAM_SIX_STATE_FAMILIES_TYPED_BOUNDS_RW_AND_UNLOCK_OK");return 0;
}
