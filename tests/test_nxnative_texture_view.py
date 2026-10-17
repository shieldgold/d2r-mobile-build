"""Real NVN52 module/record consumers on owned CPU memory; no Metal or Guest."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT/'experiments/nxnative_base_v0'
VENDOR = COMPONENT/'upstream'
ADAPTER = ROOT/'experiments/botw_upstream_host/adapter'
PATCH = COMPONENT/'patches/base-v0-texture-view.patch'
BASELINE = os.environ.get('NXNATIVE_VIEW_BASELINE') == '1'


def function(source, marker):
    start = source.index(marker)
    brace = source.index('{',start)
    depth = 1
    end = brace+1
    while depth:
        depth += (source[end] == '{')-(source[end] == '}')
        end += 1
    return source[start:end]


PROBE = r'''
#include <assert.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nvn_protos.h"
#include "nvn_obj.h"
#include "base_v0_abi.h"
#define CHECK(x) do { if(!(x)){fprintf(stderr,"owned view contract line %d\n",__LINE__);return 91;} } while(0)
static jmp_buf escape;
static int stopping;
static void die(const char *message, ...) {
 if(stopping){ assert(!strcmp(message,"base-v0 texture view swizzle is unsupported"));longjmp(escape,1);}
 abort();
}
static void owned_log(const char *message,...){(void)message;abort();}
#define LOG(...) owned_log(__VA_ARGS__)
#define ONCE_LOG(...) ((void)0)
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__); \
 static const NVNT_##name check_##name __attribute__((used))=h_##name; \
 static ret h_##name(__VA_ARGS__)
#include "view_entries.inc"
#include "view_lookup.inc"
typedef struct {const char *name;unsigned char *base;} module_t;
static module_t *module_of(const void *p){(void)p;return NULL;}
static _Alignas(16) uint8_t commands[4096];static size_t used;
static uint8_t *cmd_alloc(cmd_t *c,uint32_t n){(void)c;assert(n<=sizeof commands-used);uint8_t *p=commands+used;used+=n;return p;}
static void cs_op(uint32_t op,uint32_t size){(void)op;(void)size;}
#define CB(c) ((cmd_t *)(c))
#include "record_entries.inc"
enum {MTLTextureSwizzleZero=0,MTLTextureSwizzleOne=1,MTLTextureSwizzleRed=2,
 MTLTextureSwizzleGreen=3,MTLTextureSwizzleBlue=4,MTLTextureSwizzleAlpha=5};
typedef struct {tex_t t;view_t v;int has_v;} owned_registry_value;
#include "metal_selection.inc"
static void native_values(NVNtextureView *v){
 botw_base_v0_nvnTextureViewSetDefaults(v);
 botw_base_v0_nvnTextureViewSetLevels(v,0x123,3);
 botw_base_v0_nvnTextureViewSetLayers(v,0x8123,0xfffe);
 botw_base_v0_nvnTextureViewSetFormat(v,(NVNformat)0x81234567);
 botw_base_v0_nvnTextureViewSetSwizzle(v,5,4,3,2);
 botw_base_v0_nvnTextureViewSetDepthStencilMode(v,(NVNtextureDepthStencilMode)0x12345678);
 botw_base_v0_nvnTextureViewSetTarget(v,(NVNtextureTarget)0x12345678);
}
int main(int argc,char **argv){
 CHECK(argc==2);const char *mode=argv[1];
 _Alignas(8) uint8_t bytes[80];memset(bytes,0xa5,sizeof bytes);NVNtextureView *v=(void *)(bytes+8);
 if(!strcmp(mode,"defaults")){
  h_nvnTextureViewSetDefaults(v);for(int i=8;i<44;i++)CHECK(bytes[i]==0);CHECK(bytes[7]==0xa5&&bytes[44]==0xa5);
 }else if(!strcmp(mode,"registry")){
  CHECK(view_entry_count==14);native_values(v);
  NVNT_nvnTextureViewGetFormat get_format=(NVNT_nvnTextureViewGetFormat)view_lookup("nvnTextureViewGetFormat");
  NVNT_nvnTextureViewGetSwizzle get_swizzle=(NVNT_nvnTextureViewGetSwizzle)view_lookup("nvnTextureViewGetSwizzle");
  NVNT_nvnTextureViewGetDepthStencilMode get_depth=(NVNT_nvnTextureViewGetDepthStencilMode)view_lookup("nvnTextureViewGetDepthStencilMode");
  NVNT_nvnTextureViewCompare compare=(NVNT_nvnTextureViewCompare)view_lookup("nvnTextureViewCompare");
  CHECK(get_format&&get_swizzle&&get_depth&&compare);NVNformat f=0;NVNtextureSwizzle s[4];NVNtextureDepthStencilMode d=0;
  CHECK(get_format(v,&f)==1&&(uint32_t)f==0x81234567);CHECK(get_depth(v,&d)==1&&(uint32_t)d==0x12345678);
  CHECK(get_swizzle(v,s,s+1,s+2,s+3)==1&&s[0]==5&&s[3]==2);CHECK(compare(v,v)==1);
 }else if(!strcmp(mode,"wire")){
  h_nvnTextureViewSetDefaults(v);h_nvnTextureViewSetLevels(v,0x123,-2);h_nvnTextureViewSetLayers(v,0x18123,-2);
  h_nvnTextureViewSetFormat(v,(NVNformat)0x81234567);h_nvnTextureViewSetTarget(v,(NVNtextureTarget)0x12345678);
  h_nvnTextureViewSetSwizzle(v,5,4,3,2);h_nvnTextureViewSetDepthStencilMode(v,(NVNtextureDepthStencilMode)0x12345678);
  const botw_base_v0_texture_view *w=(const void *)v;
  CHECK(w->flags==63&&w->base_level==0x23&&w->nlevels==0xfe&&w->min_layer==0x8123&&w->nlayers==0xfffe);
  CHECK(w->format==0x81234567&&w->target==0x12345678&&w->depth_stencil_mode==0x12345678);
  CHECK(w->swizzle[0]==5&&w->swizzle[3]==2&&bytes[44]==0xa5);
 }else if(!strcmp(mode,"record")){
  native_values(v);uint8_t expected[36];memcpy(expected,v,36);
  NVNcommandBuffer c={0};NVNtexture t={0};tex_t *tex=(void *)&t;tex->magic=MAGIC('T','E','X',0);tex->serial=77;
  NVNcopyRegion region={0,0,0,8,8,1};used=0;memset(commands,0xcc,sizeof commands);
  h_nvnCommandBufferCopyBufferToTexture(&c,0x1111,&t,v,&region,0);
  p_copy_b2t *record=(void *)(commands+8);CHECK(record->has_view==1);
  CHECK(!memcmp(&record->view,expected,36));CHECK(record->tex!=(const tex_t *)&t&&record->tex->serial==77);
  CHECK(used==8+SNAP_OFF(p_copy_b2t)+sizeof(tex_t));CHECK(commands[used]==0xcc);
  memset(v,0xec,36);memset(&t,0,sizeof t);CHECK(!memcmp(&record->view,expected,36)&&record->tex->serial==77);
  owned_registry_value r={.has_v=1};r.v=record->view;uint8_t selected[4]={0};owned_select(r,selected);
  CHECK(selected[0]==5&&selected[1]==4&&selected[2]==3&&selected[3]==2);
 }else if(!strcmp(mode,"inherit")){
  botw_base_v0_nvnTextureViewSetDefaults(v);owned_registry_value r={.has_v=1};copy_view(&r.v,(uint8_t[]){0},v);
  r.t.set_swz=1;r.t.swz=5|(4<<3)|(3<<6)|(2<<9);uint8_t selected[4];owned_select(r,selected);
  CHECK(selected[0]==5&&selected[1]==4&&selected[2]==3&&selected[3]==2);
 }else if(!strncmp(mode,"invalid",7)){
  int index=mode[7]-'0';CHECK(index>=0&&index<4);NVNtextureSwizzle s[4]={2,3,4,5};s[index]=(NVNtextureSwizzle)0x10000002;
  h_nvnTextureViewSetDefaults(v);h_nvnTextureViewSetSwizzle(v,s[0],s[1],s[2],s[3]);
  owned_registry_value r={.has_v=1};copy_view(&r.v,(uint8_t[]){0},v);uint8_t selected[4];memset(selected,0x9b,4);
  stopping=1;if(setjmp(escape)==0){owned_select(r,selected);CHECK(0);}stopping=0;
  for(int i=0;i<4;i++)CHECK(selected[i]==0x9b);
 }else return 2;
 return 0;
}
'''


class NxnativeTextureView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='owned-nxnative-view-')
        cls.directory=Path(cls.tmp.name)
        for name in ('dm/gfx/nvn/include/nvn.h','dm/gfx/nvn/nvn_obj.h','dm/gfx/nvn/hle_nvn.c','dm/gfx/nvn/nvn_mtl.m'):
            target=cls.directory/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(VENDOR/name,target)
        if not BASELINE:
            subprocess.run(['patch','--fuzz=0','--batch','-p1','-i',str(PATCH)],cwd=cls.directory,check=True,capture_output=True)
        hle=(cls.directory/'dm/gfx/nvn/hle_nvn.c').read_text()
        mtl=(cls.directory/'dm/gfx/nvn/nvn_mtl.m').read_text()
        names=re.findall(r'NVN\((nvnTextureView\w+),',hle)
        cls.names=names
        entries=re.search(r'^#define VW.*$',hle,re.M)[0]+'\n'+'\n'.join(function(hle,'NVN('+name+',') for name in names)
        (cls.directory/'view_entries.inc').write_text(entries)
        lookup='static const unsigned view_entry_count='+str(len(names))+';\nstatic void *view_lookup(const char *name){\n'
        lookup+=''.join('if(!strcmp(name,"'+name+'"))return (void *)h_'+name+';\n' for name in names)+'return NULL;}\n'
        (cls.directory/'view_lookup.inc').write_text(lookup)
        record='\n'.join(function(hle,marker) for marker in ('static void put_hdr(', 'static void *rec(', 'static void copy_view(', 'static const tex_t *tex_snap(', 'NVN(nvnCommandBufferCopyBufferToTexture,'))
        record='#define SNAP_OFF(T) ((sizeof(T)+7)&~(size_t)7)\n#define REC_SNAP(c,OP,T,K) ((T *)rec((c),(OP),SNAP_OFF(T)+(size_t)(K)*sizeof(tex_t)))\n'+record
        (cls.directory/'record_entries.inc').write_text(record)
        marker='    uint8_t sw[4] = {MTLTextureSwizzleRed'
        start=mtl.index(marker);end=mtl.index('    if ((r.has_v',start)
        helper=function(mtl,'static void base_v0_texture_view_swizzle(') if 'static void base_v0_texture_view_swizzle(' in mtl else ''
        selection=helper+'\nstatic int owned_select(owned_registry_value r,uint8_t out[4]){\n'+mtl[start:end]+'memcpy(out,sw,4);return need_sw;}\n'
        (cls.directory/'metal_selection.inc').write_text(selection)
        (cls.directory/'owned_probe.c').write_text(PROBE)
        cls.binary=cls.directory/'owned_probe'
        command=['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined','-fno-omit-frame-pointer',
                 '-I',str(cls.directory),'-I',str(cls.directory/'dm/gfx/nvn/include'),'-I',str(cls.directory/'dm/gfx/nvn'),
                 '-I',str(VENDOR/'dm/gfx/nvn'),'-I',str(ADAPTER),str(cls.directory/'owned_probe.c'),str(ADAPTER/'base_v0_abi.c'),'-o',str(cls.binary)]
        built=subprocess.run(command,capture_output=True)
        if built.returncode:
            raise RuntimeError(built.stderr.decode())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_mode(self,mode):
        completed=subprocess.run([str(self.binary),mode],capture_output=True)
        self.assertEqual(completed.returncode,0,completed.stderr.decode())

    def test_defaults_exact_36byte_opaque_canary(self):self.run_mode('defaults')
    def test_complete_14_wrappers_and_fullwidth_outputs(self):self.run_mode('registry')
    def test_native_scalar_widths_flags_and_canaries(self):self.run_mode('wire')
    def test_real_command_record_copies_full_view_before_m4_selector_read(self):self.run_mode('record')
    def test_unset_view_inherits_original_builder_selector(self):self.run_mode('inherit')
    def test_illegal_fullwidth_selector_is_rejected_before_narrowing(self):
        for index in range(4):
            with self.subTest(index=index):self.run_mode('invalid'+str(index))

    def test_patch_reverse_restores_all_four_complete_vendor_files(self):
        names=('dm/gfx/nvn/include/nvn.h','dm/gfx/nvn/nvn_obj.h','dm/gfx/nvn/hle_nvn.c','dm/gfx/nvn/nvn_mtl.m')
        if not BASELINE:
            subprocess.run(['patch','--fuzz=0','--batch','-R','-p1','-i',str(PATCH)],cwd=self.directory,check=True,capture_output=True)
        for name in names:
            self.assertEqual((self.directory/name).read_bytes(),(VENDOR/name).read_bytes())
        if not BASELINE:
            subprocess.run(['patch','--fuzz=0','--batch','-p1','-i',str(PATCH)],cwd=self.directory,check=True,capture_output=True)


if __name__=='__main__':unittest.main()
