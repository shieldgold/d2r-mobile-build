"""Base-v0 sampler POD contract; fake-device CPU tests never create a GPU."""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

CPU_SOURCE = r'''
#import <Metal/Metal.h>
static unsigned samplerCreations;
static BOOL failSampler;
static MTLSamplerDescriptor *capturedDescriptor;
@interface SamplerCPUDevice:NSObject @end
@implementation SamplerCPUDevice
- (id<MTLCommandQueue>)newCommandQueue{return (id<MTLCommandQueue>)[NSObject new];}
- (id<MTLSamplerState>)newSamplerStateWithDescriptor:(MTLSamplerDescriptor*)descriptor{samplerCreations++;if(failSampler)return nil;capturedDescriptor=[descriptor copy];return (id<MTLSamplerState>)[NSObject new];}
@end
static id<MTLDevice> samplerCPUDevice(void){return (id<MTLDevice>)[SamplerCPUDevice new];}
#define MTLCreateSystemDefaultDevice samplerCPUDevice
#include "graphics.m"
#undef MTLCreateSystemDefaultDevice
#include <setjmp.h>
static sigjmp_buf recovery;
static BOOL recoverNow;
static unsigned notifications;
static const void *deniedAddress;
static void captureError(const char *message){(void)message;notifications++;if(recoverNow)siglongjmp(recovery,1);}
static int samplerAccess(const void *raw,size_t bytes,int write){(void)bytes;(void)write;return !deniedAddress||!memoryOverlap(raw,bytes,deniedAddress,sizeof(NVNsamplerBuilder));}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"SAMPLER_CPU_CHECK line%d %s\n",__LINE__,#x);return 91;}}while(0)
#define API(name) NVNT_##name name=(NVNT_##name)botw_nvn_bootstrap_loader(#name)
static uint32_t bits(float value){uint32_t result;memcpy(&result,&value,4);return result;}
static float floatBits(uint32_t value){float result;memcpy(&result,&value,4);return result;}
static uint32_t word(const void *raw,size_t offset){uint32_t value;memcpy(&value,(const char*)raw+offset,4);return value;}
int main(int argc,char **argv){@autoreleasepool{
 CHECK(argc==2);CHECK(botw_graphics_init(NULL,captureError,NULL));
 NVNdevice device={0};BWDevice *hostDevice=[[BWDevice alloc]initWithTextureKindPolicy:BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH];put(&device,hostDevice);
 NVNsamplerBuilder b;memset(&b,0xa5,sizeof b);NVNsampler raw={0},other={0};const char *mode=argv[1];
 API(nvnSamplerBuilderSetDefaults);API(nvnSamplerBuilderSetDevice);API(nvnSamplerBuilderSetMinMagFilter);API(nvnSamplerBuilderSetWrapMode);API(nvnSamplerBuilderSetLodClamp);API(nvnSamplerBuilderSetMaxAnisotropy);API(nvnSamplerBuilderSetCompare);API(nvnSamplerInitialize);API(nvnSamplerFinalize);
 if(!strcmp(mode,"defaults")){
  uint64_t old=UINT64_C(0x123456789abcdef0);memcpy(&b,&old,8);nvnSamplerBuilderSetDefaults(&b);uint64_t preserved;memcpy(&preserved,&b,8);CHECK(preserved==old);CHECK(word(&b,8)==1&&word(&b,12)==1);for(unsigned i=16;i<=24;i+=4)CHECK(word(&b,i)==1);CHECK(word(&b,28)==0&&word(&b,32)==bits(1000)&&word(&b,36)==0&&word(&b,40)==0&&word(&b,44)==2);for(unsigned i=48;i<64;i++)CHECK(((unsigned char*)&b)[i]==0);CHECK(word(&b,64)==bits(1)&&word(&b,68)==0);for(unsigned i=72;i<sizeof b;i++)CHECK(((unsigned char*)&b)[i]==0xa5);
 }else{
  memset(&b,0,sizeof b);nvnSamplerBuilderSetDefaults(&b);nvnSamplerBuilderSetDevice(&b,&device);
  if(!strcmp(mode,"offsets")){
   nvnSamplerBuilderSetMinMagFilter(&b,5,0);nvnSamplerBuilderSetWrapMode(&b,7,6,3);nvnSamplerBuilderSetLodClamp(&b,2.5f,9.5f);nvnSamplerBuilderSetCompare(&b,1,8);nvnSamplerBuilderSetMaxAnisotropy(&b,4);
   CHECK(word(&b,8)==5&&word(&b,12)==0&&word(&b,16)==7&&word(&b,20)==6&&word(&b,24)==3);CHECK(word(&b,28)==bits(2.5f)&&word(&b,32)==bits(9.5f)&&word(&b,36)==0&&word(&b,40)==1&&word(&b,44)==8&&word(&b,64)==bits(4));
  }else if(!strcmp(mode,"bias-bits")){
   API(nvnSamplerBuilderSetLodBias);API(nvnSamplerBuilderGetLodBias);const uint32_t values[]={0,0x80000000,0x3f000000,0xbf800000,0x7f800000,0xff800000,0x7fc01234};for(unsigned i=0;i<sizeof values/sizeof *values;i++){nvnSamplerBuilderSetLodBias(&b,floatBits(values[i]));CHECK(word(&b,36)==values[i]&&bits(nvnSamplerBuilderGetLodBias(&b))==values[i]);}
  }else if(!strcmp(mode,"bias-reject")){
   API(nvnSamplerBuilderSetLodBias);const uint32_t values[]={0x3f000000,0xbf800000,0x7f800000,0xff800000,0x7fc01234};for(unsigned i=0;i<sizeof values/sizeof *values;i++){nvnSamplerBuilderSetLodBias(&b,floatBits(values[i]));memset(&raw,0xa5,sizeof raw);NVNsampler before=raw;unsigned callbacks=notifications,created=samplerCreations;CHECK(!nvnSamplerInitialize(&raw,&b));CHECK(notifications==callbacks+1&&samplerCreations==created&&!memcmp(&raw,&before,sizeof raw));CHECK(strstr(lastError,"sampler LOD bias")&&strstr(lastError,"bits=0x"));CHECK(objects.count==1&&!graphicsDepth&&!pendingError&&!pendingFatal);}
  }else if(!strcmp(mode,"snapshot")){
   API(nvnSamplerBuilderSetLodBias);API(nvnSamplerGetLodBias);API(nvnSamplerGetLodClamp);API(nvnSamplerGetMinMagFilter);API(nvnSamplerGetWrapMode);API(nvnSamplerGetMaxAnisotropy);API(nvnSamplerGetCompare);
   nvnSamplerBuilderSetLodBias(&b,floatBits(0x80000000));nvnSamplerBuilderSetMinMagFilter(&b,5,0);nvnSamplerBuilderSetWrapMode(&b,7,6,3);nvnSamplerBuilderSetLodClamp(&b,2,9);nvnSamplerBuilderSetMaxAnisotropy(&b,4);nvnSamplerBuilderSetCompare(&b,1,8);CHECK(nvnSamplerInitialize(&raw,&b));CHECK(samplerCreations==1&&capturedDescriptor.minFilter==MTLSamplerMinMagFilterLinear&&capturedDescriptor.magFilter==MTLSamplerMinMagFilterNearest&&capturedDescriptor.mipFilter==MTLSamplerMipFilterLinear&&capturedDescriptor.lodMinClamp==2&&capturedDescriptor.lodMaxClamp==9&&capturedDescriptor.maxAnisotropy==4&&capturedDescriptor.compareFunction==MTLCompareFunctionAlways);nvnSamplerBuilderSetDefaults(&b);float lo,hi;NVNminFilter min;NVNmagFilter mag;NVNwrapMode s,t,r;NVNcompareMode cm;NVNcompareFunc cf;nvnSamplerGetLodClamp(&raw,&lo,&hi);nvnSamplerGetMinMagFilter(&raw,&min,&mag);nvnSamplerGetWrapMode(&raw,&s,&t,&r);nvnSamplerGetCompare(&raw,&cm,&cf);CHECK(bits(nvnSamplerGetLodBias(&raw))==0x80000000&&lo==2&&hi==9&&min==5&&mag==0&&s==7&&t==6&&r==3&&nvnSamplerGetMaxAnisotropy(&raw)==4&&cm==1&&cf==8);nvnSamplerFinalize(&raw);unsigned callbacks=notifications;recoverNow=YES;if(!sigsetjmp(recovery,1)){(void)nvnSamplerGetLodBias(&raw);CHECK(0);}recoverNow=NO;CHECK(notifications==callbacks+1&&strstr(lastError,"object lifetime/type"));
  }else if(!strcmp(mode,"zero")){
   CHECK(nvnSamplerInitialize(&raw,&b));CHECK(samplerCreations==1&&capturedDescriptor.minFilter==MTLSamplerMinMagFilterLinear&&capturedDescriptor.magFilter==MTLSamplerMinMagFilterLinear&&capturedDescriptor.mipFilter==MTLSamplerMipFilterNotMipmapped&&capturedDescriptor.lodMinClamp==0&&capturedDescriptor.lodMaxClamp==15);nvnSamplerFinalize(&raw);
  }else if(!strcmp(mode,"all-fields")){
   API(nvnSamplerBuilderSetBorderColor);API(nvnSamplerBuilderSetBorderColori);API(nvnSamplerBuilderSetBorderColorui);API(nvnSamplerBuilderSetReductionFilter);API(nvnSamplerBuilderGetBorderColor);API(nvnSamplerBuilderGetBorderColori);API(nvnSamplerBuilderGetBorderColorui);API(nvnSamplerBuilderGetMinMagFilter);API(nvnSamplerBuilderGetWrapMode);API(nvnSamplerBuilderGetLodClamp);API(nvnSamplerBuilderGetCompare);API(nvnSamplerBuilderGetMaxAnisotropy);API(nvnSamplerBuilderGetReductionFilter);
   uint32_t values[]={0x80000000,0x7fc01234,0xffffffff,0x12345678},out[4];float floats[4];int integers[4];memcpy(floats,values,16);memcpy(integers,values,16);
   nvnSamplerBuilderSetBorderColor(&b,floats);nvnSamplerBuilderGetBorderColorui(&b,out);CHECK(!memcmp(values,out,16));nvnSamplerBuilderSetBorderColori(&b,integers);nvnSamplerBuilderGetBorderColor(&b,floats);CHECK(!memcmp(values,floats,16));nvnSamplerBuilderSetBorderColorui(&b,values);nvnSamplerBuilderGetBorderColori(&b,integers);CHECK(!memcmp(values,integers,16));nvnSamplerBuilderSetReductionFilter(&b,NVN_SAMPLER_REDUCTION_MAX);CHECK(word(&b,0x44)==2&&nvnSamplerBuilderGetReductionFilter(&b)==2);
   nvnSamplerBuilderSetMinMagFilter(&b,3,0);nvnSamplerBuilderSetWrapMode(&b,3,6,7);nvnSamplerBuilderSetLodClamp(&b,floatBits(0x7fc01234),floatBits(0x80000000));nvnSamplerBuilderSetCompare(&b,1,7);nvnSamplerBuilderSetMaxAnisotropy(&b,floatBits(0x7f800000));NVNminFilter min;NVNmagFilter mag;NVNwrapMode a,c,d;NVNcompareMode mode;NVNcompareFunc func;float lo,hi;nvnSamplerBuilderGetMinMagFilter(&b,&min,&mag);nvnSamplerBuilderGetWrapMode(&b,&a,&c,&d);nvnSamplerBuilderGetLodClamp(&b,&lo,&hi);nvnSamplerBuilderGetCompare(&b,&mode,&func);CHECK(min==3&&mag==0&&a==3&&c==6&&d==7&&bits(lo)==0x7fc01234&&bits(hi)==0x80000000&&mode==1&&func==7&&bits(nvnSamplerBuilderGetMaxAnisotropy(&b))==0x7f800000);
  }else if(!strcmp(mode,"compare")){
   API(nvnSamplerCompare);CHECK(nvnSamplerInitialize(&raw,&b)&&nvnSamplerInitialize(&other,&b));CHECK(nvnSamplerCompare(&raw,&other)&&nvnSamplerCompare(&raw,&raw));BWSampler *a=get(&raw,[BWSampler class]),*c=get(&other,[BWSampler class]);NSData *original=[a valueForKey:@"parameters"];CHECK(original.length==72);NSMutableData *changed=[original mutableCopy];
   const unsigned floatOffsets[]={0x1c,0x20,0x24,0x40};for(unsigned i=0;i<4;i++){[changed replaceBytesInRange:NSMakeRange(0,72) withBytes:original.bytes];uint32_t z=0;memcpy((char*)changed.mutableBytes+floatOffsets[i],&z,4);[a setValue:changed forKey:@"parameters"];z=0x80000000;memcpy((char*)changed.mutableBytes+floatOffsets[i],&z,4);[c setValue:changed forKey:@"parameters"];CHECK(nvnSamplerCompare(&raw,&other));z=0x7fc01234;memcpy((char*)changed.mutableBytes+floatOffsets[i],&z,4);[a setValue:changed forKey:@"parameters"];[c setValue:changed forKey:@"parameters"];CHECK(!nvnSamplerCompare(&raw,&other)&&!nvnSamplerCompare(&raw,&raw));}
   const unsigned integerOffsets[]={8,12,16,20,24,40,44,68};for(unsigned i=0;i<sizeof integerOffsets/sizeof *integerOffsets;i++){[a setValue:original forKey:@"parameters"];[changed replaceBytesInRange:NSMakeRange(0,72) withBytes:original.bytes];((uint8_t*)changed.mutableBytes)[integerOffsets[i]]^=1;[c setValue:changed forKey:@"parameters"];CHECK(!nvnSamplerCompare(&raw,&other)&&!nvnSamplerCompare(&other,&raw));}
   [changed replaceBytesInRange:NSMakeRange(0,72) withBytes:original.bytes];uint32_t z=0x80000000;memcpy((char*)changed.mutableBytes+48,&z,4);[c setValue:changed forKey:@"parameters"];CHECK(!nvnSamplerCompare(&raw,&other));z=0x7fc01234;memcpy((char*)changed.mutableBytes+48,&z,4);[a setValue:changed forKey:@"parameters"];[c setValue:changed forKey:@"parameters"];CHECK(nvnSamplerCompare(&raw,&other));nvnSamplerFinalize(&other);nvnSamplerFinalize(&raw);
  }else if(!strcmp(mode,"lod-quantization")){
   const float inputs[][2]={{-5,1000},{1.003f,3.009f},{5,2},{20,-2}};const float expected[][2]={{0,15},{1,3.0078125f},{5,5},{15,15}};for(unsigned i=0;i<4;i++){nvnSamplerBuilderSetLodClamp(&b,inputs[i][0],inputs[i][1]);CHECK(nvnSamplerInitialize(&raw,&b));CHECK(capturedDescriptor.lodMinClamp==expected[i][0]&&capturedDescriptor.lodMaxClamp==expected[i][1]);nvnSamplerFinalize(&raw);}
  }else if(!strcmp(mode,"snapshot-border")){
   API(nvnSamplerBuilderSetBorderColorui);API(nvnSamplerGetBorderColor);API(nvnSamplerGetBorderColori);API(nvnSamplerGetBorderColorui);API(nvnSamplerGetReductionFilter);uint32_t values[]={0x80000000,0x7fc01234,0xffffffff,0x12345678},out[4];float floats[4];int integers[4];nvnSamplerBuilderSetBorderColorui(&b,values);CHECK(nvnSamplerInitialize(&raw,&b));memset(&b,0,sizeof b);nvnSamplerGetBorderColor(&raw,floats);nvnSamplerGetBorderColori(&raw,integers);nvnSamplerGetBorderColorui(&raw,out);CHECK(!memcmp(values,floats,16)&&!memcmp(values,integers,16)&&!memcmp(values,out,16)&&nvnSamplerGetReductionFilter(&raw)==0);nvnSamplerFinalize(&raw);
  }else if(!strcmp(mode,"invalid-state")){
   API(nvnSamplerBuilderSetReductionFilter);for(unsigned i=0;i<7;i++){nvnSamplerBuilderSetDefaults(&b);if(i<3)nvnSamplerBuilderSetLodClamp(&b,floatBits(i?0xff800000:0x7fc01234),1000);else if(i==3)nvnSamplerBuilderSetMaxAnisotropy(&b,floatBits(0x7fc01234));else if(i==4)nvnSamplerBuilderSetMaxAnisotropy(&b,1.5f);else if(i==5)nvnSamplerBuilderSetReductionFilter(&b,1);else nvnSamplerBuilderSetCompare(&b,-1,2);memset(&raw,0xa5,sizeof raw);NVNsampler before=raw;unsigned callbacks=notifications,created=samplerCreations;CHECK(!nvnSamplerInitialize(&raw,&b)&&notifications==callbacks+1&&created==samplerCreations&&!memcmp(&raw,&before,sizeof raw)&&objects.count==1);}
  }else if(!strcmp(mode,"minimal-spans")){
   API(nvnSamplerBuilderGetLodBias);size_t page=getpagesize();void *memory=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(memory!=MAP_FAILED);CHECK(!mprotect((char*)memory+page,page,PROT_NONE));NVNsamplerBuilder *shortBuilder=(NVNsamplerBuilder*)((char*)memory+page-0x28);uint32_t value=0x3f000000;memcpy((char*)shortBuilder+0x24,&value,4);CHECK(bits(nvnSamplerBuilderGetLodBias(shortBuilder))==value);CHECK(!munmap(memory,page*2));
  }else if(!strcmp(mode,"anisotropy")){
   const float accepted[]={1,2,4,8,16};for(unsigned i=0;i<5;i++){nvnSamplerBuilderSetMaxAnisotropy(&b,accepted[i]);CHECK(nvnSamplerInitialize(&raw,&b));CHECK(capturedDescriptor.maxAnisotropy==(NSUInteger)accepted[i]);nvnSamplerFinalize(&raw);}const float refused[]={0,1.5f,3,5,6,10,12,17};for(unsigned i=0;i<sizeof refused/sizeof *refused;i++){nvnSamplerBuilderSetMaxAnisotropy(&b,refused[i]);unsigned count=samplerCreations;CHECK(!nvnSamplerInitialize(&raw,&b)&&samplerCreations==count&&objects.count==1);}
  }else if(!strcmp(mode,"device-compare")){
   API(nvnSamplerCompare);NVNdevice alias=device;CHECK(nvnSamplerInitialize(&raw,&b));nvnSamplerBuilderSetDevice(&b,&alias);CHECK(nvnSamplerInitialize(&other,&b));CHECK(!nvnSamplerCompare(&raw,&other)&&!nvnSamplerCompare(&other,&raw));nvnSamplerFinalize(&raw);nvnSamplerFinalize(&other);
  }else if(!strcmp(mode,"bias-fpcr")){
#if defined(__aarch64__)
   API(nvnSamplerBuilderSetLodBias);const uint32_t values[]={0x00000001,0x80000001,0x007fffff,0x807fffff};for(unsigned i=0;i<4;i++){nvnSamplerBuilderSetLodBias(&b,floatBits(values[i]));uint64_t saved;__asm__ volatile("mrs %0, fpcr":"=r"(saved));uint64_t changed=saved|UINT64_C(0x1000000);memset(&raw,0xa5,sizeof raw);NVNsampler before=raw;unsigned callbacks=notifications,created=samplerCreations;__asm__ volatile("msr fpcr, %0"::"r"(changed));int accepted=nvnSamplerInitialize(&raw,&b);__asm__ volatile("msr fpcr, %0"::"r"(saved));CHECK(!accepted&&notifications==callbacks+1&&samplerCreations==created&&objects.count==1&&!memcmp(&raw,&before,sizeof raw));char expected[32];snprintf(expected,sizeof expected,"bits=0x%08x",values[i]);CHECK(strstr(lastError,expected)&&!strstr(lastError,"value=0 ")&&!strstr(lastError,"value=-0 "));}
#endif
  }else if(!strcmp(mode,"readonly-finalize")){
   API(nvnSamplerGetLodBias);size_t page=getpagesize();NVNsampler *placed=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(placed!=MAP_FAILED&&nvnSamplerInitialize(placed,&b));uint64_t identity=word(placed,0);CHECK(!mprotect(placed,page,PROT_READ));unsigned callbacks=notifications;recoverNow=YES;if(!sigsetjmp(recovery,1)){nvnSamplerFinalize(placed);CHECK(0);}recoverNow=NO;CHECK(notifications==callbacks+1&&word(placed,0)==identity&&objects.count==2&&nvnSamplerGetLodBias(placed)==0);CHECK(!mprotect(placed,page,PROT_READ|PROT_WRITE));nvnSamplerFinalize(placed);CHECK(objects.count==1);CHECK(!munmap(placed,page));
  }else if(!strcmp(mode,"allocation-failure")){
   failSampler=YES;memset(&raw,0xa5,sizeof raw);NVNsampler before=raw;CHECK(!nvnSamplerInitialize(&raw,&b)&&samplerCreations==1&&objects.count==1&&!memcmp(&raw,&before,sizeof raw)&&notifications==1&&strstr(lastError,"Metal sampler creation failed"));
  }else if(!strcmp(mode,"output-transaction")){
   API(nvnSamplerBuilderGetMinMagFilter);NVNminFilter min=0x1234;NVNmagFilter mag=0x5678;deniedAddress=&mag;memoryAccessValidator=samplerAccess;unsigned callbacks=notifications;recoverNow=YES;if(!sigsetjmp(recovery,1)){nvnSamplerBuilderGetMinMagFilter(&b,&min,&mag);CHECK(0);}recoverNow=NO;CHECK(notifications==callbacks+1&&min==0x1234&&mag==0x5678);deniedAddress=NULL;memoryAccessValidator=NULL;
  }else if(!strcmp(mode,"memory")){
   memoryAccessValidator=samplerAccess;deniedAddress=&b;unsigned callbacks=notifications;recoverNow=YES;if(!sigsetjmp(recovery,1)){nvnSamplerBuilderSetLodClamp(&b,1,2);CHECK(0);}recoverNow=NO;CHECK(notifications==callbacks+1&&!samplerCreations);deniedAddress=NULL;memoryAccessValidator=NULL;
  }else{(void)other;CHECK(0);}
 }
 CHECK(botw_graphics_shutdown_checked(NULL,0));printf("SAMPLER_CPU_OK mode=%s callbacks=%u no_real_gpu=true\n",mode,notifications);return 0;
}}
'''

@unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'),
                     'requires Darwin Foundation; no real GPU')
class SamplerContractCPUTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-sampler-cpu-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        source = folder / 'sampler.m'
        source.write_text(CPU_SOURCE)
        cls.executable = folder / 'sampler'
        built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc',
            '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-O1', '-g', '-fsanitize=address,undefined',
            '-I', os.environ.get('BOTW_SAMPLER_GRAPHICS_ROOT', str(ROOT / 'native/botw_graphics')), str(source), '-framework',
            'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore',
            '-o', str(cls.executable)], capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def mode(self, name):
        result = subprocess.run([str(self.executable), name], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('SAMPLER_CPU_OK', result.stdout)
        self.assertNotIn('ERROR: AddressSanitizer', result.stderr)
        self.assertNotIn('runtime error:', result.stderr)
        return result

    def test_defaults_preserve_device_and_tail_with_original_64_byte_fields(self):
        self.mode('defaults')

    def test_existing_setters_use_original_base_v0_offsets(self):
        self.mode('offsets')

    def test_bias_builder_roundtrips_every_float_bit_pattern_without_clamping(self):
        self.mode('bias-bits')

    def test_unrepresentable_bias_rejects_before_metal_and_object_publication(self):
        self.mode('bias-reject')

    def test_snapshot_getters_keep_original_value_and_object_lifetime(self):
        self.mode('snapshot')

    def test_zero_bias_defaults_create_the_correct_metal_descriptor(self):
        self.mode('zero')

    def test_builder_memory_rejection_precedes_mutation(self):
        self.mode('memory')

    def test_all_proven_builder_getters_and_border_aliases_preserve_raw_values(self):
        self.mode('all-fields')

    def test_sampler_compare_uses_numeric_float_equality_and_bitwise_border_equality(self):
        self.mode('compare')

    def test_gpu_lod_clamps_follow_original_q8_encoding_without_changing_snapshot(self):
        self.mode('lod-quantization')

    def test_sampler_border_getters_use_an_immutable_snapshot(self):
        self.mode('snapshot-border')

    def test_unrepresentable_states_do_not_allocate_or_publish(self):
        self.mode('invalid-state')

    def test_scalar_builder_getter_requires_only_its_original_field_span(self):
        self.mode('minimal-spans')

    def test_only_proven_anisotropy_encodings_reach_metal(self):
        self.mode('anisotropy')

    def test_compare_preserves_native_raw_device_pointer_value_semantics(self):
        self.mode('device-compare')

    @unittest.skipUnless(platform.machine() == 'arm64', 'requires the actual ARM64 FPCR')
    def test_nonzero_subnormal_bias_is_rejected_even_when_fpcr_flushes_float_inputs(self):
        self.mode('bias-fpcr')

    def test_readonly_finalize_retains_original_object_until_writable_retry(self):
        self.mode('readonly-finalize')

    def test_native_sampler_allocation_failure_preserves_registry_and_output_canary(self):
        self.mode('allocation-failure')

    def test_multi_output_getter_checks_every_destination_before_writing(self):
        self.mode('output-transaction')


@unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'),
                     'requires real Metal sampler and texture sampling')
class SamplerMetalTests(unittest.TestCase):
    def test_zero_bias_mip_sampling_clamps_and_descriptor_snapshot_lifetime(self):
        import test_botw_graphics as baseline
        from unittest.mock import patch
        feature = baseline.ARRAY_FEATURE
        feature = feature.replace('    API(nvnDeviceGetTextureHandle);',
            '    API(nvnSamplerBuilderSetLodBias);API(nvnSamplerBuilderSetLodClamp);API(nvnSamplerFinalize);\n    API(nvnDeviceGetTextureHandle);')
        feature = feature.replace('    CHECK(nvnSamplerInitialize(&sampler,&sb));nvnSamplerPoolRegisterSampler(&sp,4,&sampler);', r'''
    int maxZero=argc>1&&!strcmp(argv[1],"clamp-zero"),onlyOne=argc>1&&!strcmp(argv[1],"clamp-one"),negativeZero=argc>1&&!strcmp(argv[1],"negative-zero"),retained=argc>1&&!strcmp(argv[1],"retained-snapshot");
    nvnSamplerBuilderSetLodBias(&sb,negativeZero?-0.0f:0.0f);
    if(maxZero)nvnSamplerBuilderSetLodClamp(&sb,0,0);if(onlyOne)nvnSamplerBuilderSetLodClamp(&sb,1,1);
    if(argc>1&&(!strcmp(argv[1],"positive-bias")||!strcmp(argv[1],"negative-bias"))){
      float bias=!strcmp(argv[1],"positive-bias")?.5f:-1;nvnSamplerBuilderSetLodBias(&sb,bias);memset(&sampler,0xa5,sizeof sampler);NVNsampler before=sampler;CHECK(!nvnSamplerInitialize(&sampler,&sb)&&!memcmp(&sampler,&before,sizeof sampler));CHECK(strstr(botw_graphics_last_error(),"sampler LOD bias")&&strstr(botw_graphics_last_error(),!strcmp(argv[1],"positive-bias")?"bits=0x3f000000":"bits=0xbf800000"));puts("SAMPLER_NONZERO_BIAS_REJECTED_WITH_EXACT_VALUE_AND_NO_OBJECT_OK");goto array_done;
    }
    CHECK(nvnSamplerInitialize(&sampler,&sb));nvnSamplerPoolRegisterSampler(&sp,4,&sampler);if(retained){nvnSamplerFinalize(&sampler);nvnSamplerBuilderSetLodBias(&sb,4);}
''')
        old = 'float4 color=t.sample(s,float2(.01),uint(pos.x)>=2u?1u:0u,level(float(min(uint(pos.y),2u))));'
        # Implicit derivatives request a smaller mip; clamp modes must select
        # each independently colored level without an explicit shader LOD.
        feature = feature.replace('char arrayMSL[1200];',
            'char arrayMSL[1200];const char *sampleCall=(maxZero||onlyOne)?"t.sample(s,float2(pos.x/4.0f,pos.y/4.0f),uint(pos.x)>=2u?1u:0u)":"t.sample(s,float2(.01),uint(pos.x)>=2u?1u:0u,level(float(min(uint(pos.y),2u))))";')
        feature = feature.replace(old, 'float4 color=%s;')
        feature = feature.replace('r8?"color.rrr":"color.rgb");', 'sampleCall,r8?"color.rrr":"color.rgb");')
        feature = feature.replace('level=y<3?y:2;', 'level=maxZero?0:onlyOne?1:y<3?y:2;')
        # A source-CPU mutation affects level0 only; expected comparison follows
        # the sampled level, rather than the screen row used by the old probe.
        feature = feature.replace('if(pass){if(r8)', 'if(pass&&!maxZero&&!onlyOne){if(r8)')
        feature = feature.replace('if(pass&&!y&&layer)', 'if(pass&&!maxZero&&!onlyOne&&!level&&layer)')
        feature = feature.replace('puts("GPU_ARRAY_LAYER_MIP_COLORS_OK");',
            'puts("SAMPLER_ZERO_BIAS_REAL_MIP_COLORS_CLAMPS_AND_RETAINED_DESCRIPTOR_OK");')
        source = baseline.METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);',
            feature + '    nvnMemoryPoolFinalize(&pool);')
        original_run = subprocess.run
        def checked_run(*args, **kwargs):
            result = original_run(*args, **kwargs)
            self.assertNotIn('failed assertion', result.stderr)
            self.assertNotIn('MTLReportFailure', result.stderr)
            if Path(args[0][0]).name != 'xcrun' and result.returncode != 77:
                self.assertIn('Metal API Validation Enabled', result.stderr)
            return result
        with patch.dict(os.environ, {'MTL_DEBUG_LAYER': '1'}), \
                patch.object(baseline.subprocess, 'run', side_effect=checked_run):
            baseline.MetalBridgeTests.compile_render_probe(self, source, [
                (name, 'SAMPLER_ZERO_BIAS_REAL_MIP_COLORS_CLAMPS_AND_RETAINED_DESCRIPTOR_OK', True)
                for name in ('zero', 'negative-zero', 'clamp-zero', 'clamp-one', 'retained-snapshot')
            ] + [(name, 'SAMPLER_NONZERO_BIAS_REJECTED_WITH_EXACT_VALUE_AND_NO_OBJECT_OK', True)
                 for name in ('positive-bias', 'negative-bias')])
