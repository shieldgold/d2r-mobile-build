"""Independent timestamp boundaries through the real Metal/NVN bridge."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from botw_metal_counter_capability import require_counter_sampling, require_metal


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#include "graphics.h"
#include "nvn_protos.h"
#include <stdint.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#define API(n) NVNT_##n n = (NVNT_##n)botw_nvn_bootstrap_loader(#n)
#define CHECK(x) do { if(!(x)){fprintf(stderr,"CHECK: %s (%s)\n",#x,botw_graphics_last_error());return 1;} } while(0)
static sigjmp_buf recovery;
static int rejecting;
static uintptr_t denied_start,denied_end;
static int memory_access(const void *p,size_t bytes,int writable){
 uintptr_t start=(uintptr_t)p;
 return !writable||!denied_end||start>=denied_end||
        (start<=denied_start&&bytes<=denied_start-start);
}
static void graphics_error(const char *message){
 if(rejecting)siglongjmp(recovery,1);
 fprintf(stderr,"UNEXPECTED: %s\n",message);exit(2);
}
#define REJECT(call,text) do {rejecting=1;if(!sigsetjmp(recovery,1)){call;CHECK(0);}rejecting=0;CHECK(strstr(botw_graphics_last_error(),text));}while(0)
int main(int argc,char **argv){
 const char *mode=argc>1?argv[1]:"ordered";
 if(!botw_graphics_init(NULL,graphics_error,NULL))return 77;
 API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);
 API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);
 API(nvnMemoryPoolBuilderSetFlags);
 API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolInitialize);
 API(nvnMemoryPoolGetBufferAddress);API(nvnMemoryPoolFinalize);
 API(nvnQueueBuilderSetDefaults);API(nvnQueueBuilderSetDevice);API(nvnQueueInitialize);
 API(nvnQueueSubmitCommands);API(nvnQueueFinish);
 API(nvnCommandBufferInitialize);API(nvnCommandBufferBeginRecording);
 API(nvnCommandBufferEndRecording);API(nvnCommandBufferReportCounter);
 API(nvnCommandBufferCopyBufferToBuffer);
 API(nvnDeviceGetTimestampInNanoseconds);API(nvnDeviceGetCurrentTimestampInNanoseconds);
 API(nvnTextureBuilderSetDefaults);API(nvnTextureBuilderSetDevice);
 API(nvnTextureBuilderSetSize2D);API(nvnTextureBuilderSetFormat);
 API(nvnTextureBuilderSetStorage);API(nvnTextureInitialize);API(nvnTextureFinalize);
 API(nvnCommandBufferSetRenderTargets);API(nvnCommandBufferClearColor);
 NVNdeviceBuilder db;NVNdevice d;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&d,&db));
 size_t page=getpagesize();unsigned char *memory=NULL;
 CHECK(!posix_memalign((void**)&memory,page,page*4));memset(memory,0xa5,page*4);
 NVNmemoryPoolBuilder mb;NVNmemoryPool pool;nvnMemoryPoolBuilderSetDefaults(&mb);
 nvnMemoryPoolBuilderSetDevice(&mb,&d);nvnMemoryPoolBuilderSetStorage(&mb,memory,page*4);
 nvnMemoryPoolBuilderSetFlags(&mb,NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED);
 if(!strcmp(mode,"cpu-noaccess"))nvnMemoryPoolBuilderSetFlags(&mb,NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS|NVN_MEMORY_POOL_FLAGS_GPU_CACHED);
 if(!strcmp(mode,"noncoherent")){
  nvnMemoryPoolBuilderSetFlags(&mb,NVN_MEMORY_POOL_FLAGS_CPU_CACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED);
  nvnMemoryPoolBuilderSetStorage(&mb,memory+16,page*4-32);
 }
 CHECK(nvnMemoryPoolInitialize(&pool,&mb));uint64_t address=nvnMemoryPoolGetBufferAddress(&pool);
 NVNqueueBuilder qb;NVNqueue queue;nvnQueueBuilderSetDefaults(&qb);
 nvnQueueBuilderSetDevice(&qb,&d);CHECK(nvnQueueInitialize(&queue,&qb));
 NVNcommandBuffer command;CHECK(nvnCommandBufferInitialize(&command,&d));
 unsigned char untouched[32];memset(untouched,0xa5,sizeof untouched);
 if(!strcmp(mode,"unavailable")){
  nvnCommandBufferBeginRecording(&command);
  REJECT(nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,address),"stage sampling is unavailable");
  REJECT(nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP,address+16),"stage sampling is unavailable");
  for(size_t i=0;i<page*4;i++)CHECK(memory[i]==0xa5);
  nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_UNAVAILABLE_SAMPLING_REJECTED_WITH_CANARY_OK");return 0;
 }
 if(!strcmp(mode,"cpu-noaccess")||!strcmp(mode,"noncoherent")){
  nvnCommandBufferBeginRecording(&command);
  REJECT(nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,address),"coherent CPU-accessible");
  CHECK(!memcmp(memory,untouched,32));
  nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_UNPUBLISHABLE_POOL_REJECTED_OK");return 0;
 }
 if(!strcmp(mode,"bounds")){
  nvnCommandBufferBeginRecording(&command);
  REJECT(nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,address+1),"alignment");
  REJECT(nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,address+page*4-8),"range/device");
  CHECK(!memcmp(memory+page*4-32,untouched,32));
  nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,address+page*4-16);
  NVNcommandHandle last=nvnCommandBufferEndRecording(&command);
  nvnQueueSubmitCommands(&queue,1,&last);nvnQueueFinish(&queue);
  NVNcounterData result;memcpy(&result,memory+page*4-16,16);CHECK(result.timestamp>0);
  for(size_t i=0;i<page*4-16;i++)CHECK(memory[i]==0xa5);
  nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_ALIGNMENT_AND_EXACT_END_16B_OK");return 0;
 }
 if(!strcmp(mode,"second-none")){
  nvnCommandBufferBeginRecording(&command);
  nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,address);
  nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP,address+page);
  NVNcommandHandle pair=nvnCommandBufferEndRecording(&command);
  CHECK(!mprotect(memory+page,page,PROT_NONE));
  REJECT(nvnQueueSubmitCommands(&queue,1,&pair),"destination");
  CHECK(!mprotect(memory+page,page,PROT_READ|PROT_WRITE));
  nvnQueueFinish(&queue);
  CHECK(!memcmp(memory,untouched,32)&&!memcmp(memory+page,untouched,32));
  nvnQueueSubmitCommands(&queue,1,&pair);nvnQueueFinish(&queue);
  NVNcounterData first,second;memcpy(&first,memory,16);memcpy(&second,memory+page,16);
  CHECK(first.timestamp>0&&second.timestamp>=first.timestamp);
  nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_WHOLE_BATCH_PREFLIGHT_NO_EARLY_PUBLICATION_OK");return 0;
 }
 if(!strcmp(mode,"clock-read")){
  NVNcounterData valid={.counter=UINT64_MAX,.timestamp=123};
  CHECK(nvnDeviceGetTimestampInNanoseconds(&d,&valid)==123);
  CHECK(!mprotect(memory+page,page,PROT_NONE));
  REJECT((void)nvnDeviceGetTimestampInNanoseconds(&d,(const NVNcounterData*)(memory+page)),"readable");
  REJECT((void)nvnDeviceGetTimestampInNanoseconds(&d,(const NVNcounterData*)(memory+page-8)),"readable");
  CHECK(!mprotect(memory+page,page,PROT_READ|PROT_WRITE));
  CHECK(!memcmp(memory,untouched,32)&&!memcmp(memory+128,untouched,32));
  CHECK(nvnDeviceGetTimestampInNanoseconds(&d,&valid)==123);
  nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_TIMESTAMP_NONE_AND_CROSS_PAGE_READ_REJECTED_OK");return 0;
 }
 if(!strcmp(mode,"private-live")||!strcmp(mode,"private-pending")){
  NVNtextureBuilder tb;NVNtexture texture;nvnTextureBuilderSetDefaults(&tb);
  nvnTextureBuilderSetDevice(&tb,&d);nvnTextureBuilderSetSize2D(&tb,4,4);
  nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(&tb,&pool,page);
  CHECK(nvnTextureInitialize(&texture,&tb));const NVNtexture *targets[]={&texture};float color[]={1,0,0,1};
  nvnCommandBufferBeginRecording(&command);
  nvnCommandBufferSetRenderTargets(&command,1,targets,NULL,NULL,NULL);
  nvnCommandBufferClearColor(&command,0,color,15);
  if(!strcmp(mode,"private-live")){
   NVNcommandHandle clear=nvnCommandBufferEndRecording(&command);
   nvnQueueSubmitCommands(&queue,1,&clear);nvnQueueFinish(&queue);
   nvnCommandBufferBeginRecording(&command);
  }
  REJECT({nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,address+page);
          NVNcommandHandle bad=nvnCommandBufferEndRecording(&command);nvnQueueSubmitCommands(&queue,1,&bad);},"private texture");
  CHECK(!memcmp(memory+page,untouched,32));
  if(!strcmp(mode,"private-live")){
   unsigned char pixels[64];CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
   CHECK(pixels[0]==255&&pixels[1]==0&&pixels[2]==0&&pixels[3]==255);
  }
  nvnTextureFinalize(&texture);nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_PRIVATE_TEXTURE_ALIAS_REJECTED_OK");return 0;
 }
 nvnCommandBufferBeginRecording(&command);
 nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,address);
 nvnCommandBufferCopyBufferToBuffer(&command,address,address+128,16,0);
 nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP,address+16);
 nvnCommandBufferCopyBufferToBuffer(&command,address+16,address+144,16,0);
 NVNcommandHandle handle=nvnCommandBufferEndRecording(&command);
 CHECK(!memcmp(memory,untouched,32)&&!memcmp(memory+128,untouched,32));
 if(!strcmp(mode,"logical-ro")){
  denied_start=(uintptr_t)memory;denied_end=denied_start+16;
  botw_graphics_set_memory_access_validator(memory_access);
  REJECT(nvnQueueSubmitCommands(&queue,1,&handle),"logically writable");
  CHECK(!memcmp(memory,untouched,32)&&!memcmp(memory+128,untouched,32));
  botw_graphics_set_memory_access_validator(NULL);
  nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_LOGICAL_READONLY_REJECTED_OK");return 0;
 }
 if(!strcmp(mode,"wrong-device")){
  NVNdevice other;CHECK(nvnDeviceInitialize(&other,&db));NVNqueue other_queue;
  nvnQueueBuilderSetDevice(&qb,&other);CHECK(nvnQueueInitialize(&other_queue,&qb));
  REJECT(nvnQueueSubmitCommands(&other_queue,1,&handle),"device mismatch");
  CHECK(!memcmp(memory,untouched,32)&&!memcmp(memory+128,untouched,32));
  nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_CROSS_DEVICE_SUBMIT_REJECTED_OK");return 0;
 }
 if(!strcmp(mode,"stale")){
  nvnMemoryPoolFinalize(&pool);CHECK(nvnMemoryPoolInitialize(&pool,&mb));
  REJECT(nvnQueueSubmitCommands(&queue,1,&handle),"stale");
  CHECK(!memcmp(memory,untouched,32)&&!memcmp(memory+128,untouched,32));
  nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_POOL_ABA_REJECTED_OK");return 0;
 }
 if(!strcmp(mode,"readonly")||!strcmp(mode,"none")){
  CHECK(!mprotect(memory,page,!strcmp(mode,"none")?PROT_NONE:PROT_READ));
  REJECT(nvnQueueSubmitCommands(&queue,1,&handle),"destination");
  CHECK(!mprotect(memory,page,PROT_READ|PROT_WRITE));
  CHECK(!memcmp(memory,untouched,32)&&!memcmp(memory+128,untouched,32));
  nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
  puts("COUNTER_INACCESSIBLE_DESTINATION_REJECTED_OK");return 0;
 }
 uint64_t previous=0;
 for(unsigned pass=0;pass<2;pass++){
  uint64_t before=nvnDeviceGetCurrentTimestampInNanoseconds(&d);
  nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
  uint64_t after=nvnDeviceGetCurrentTimestampInNanoseconds(&d);
  NVNcounterData top,bottom;memcpy(&top,memory,16);memcpy(&bottom,memory+16,16);
  CHECK(top.timestamp>0&&top.timestamp>=before&&bottom.timestamp>=top.timestamp&&after>=bottom.timestamp);
  CHECK(nvnDeviceGetTimestampInNanoseconds(&d,&top)==top.timestamp);
  CHECK(nvnDeviceGetTimestampInNanoseconds(&d,&bottom)==bottom.timestamp);
  CHECK(!memcmp(memory,memory+128,32));
  CHECK(!previous||top.timestamp>previous);previous=bottom.timestamp;
  for(unsigned i=32;i<128;i++)CHECK(memory[i]==0xa5);
 }
 nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);
 puts("COUNTER_ORDERED_COPY_REPLAY_AND_RAW_NS_OK");return 0;
}
'''


# This test-only wrapper changes public capability responses, never timestamps.
NO_SAMPLING_DEVICE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
/* Only the two public capability responses differ; real Metal does all other work. */
@interface CounterUnavailableDevice : NSObject
@property(strong) id<MTLDevice> actual;
@end
@implementation CounterUnavailableDevice
- (NSArray<id<MTLCounterSet>> *)counterSets { return @[]; }
- (BOOL)supportsCounterSampling:(MTLCounterSamplingPoint)point { (void)point; return NO; }
- (id)forwardingTargetForSelector:(SEL)selector { (void)selector; return self.actual; }
@end
static id<MTLDevice> counterUnavailableDevice(void) {
 id<MTLDevice> actual=MTLCreateSystemDefaultDevice(); if(!actual)return nil;
 CounterUnavailableDevice *proxy=[CounterUnavailableDevice new];proxy.actual=actual;
 return (id<MTLDevice>)proxy;
}
#define MTLCreateSystemDefaultDevice counterUnavailableDevice
#include "graphics.m"
#undef MTLCreateSystemDefaultDevice
'''


@unittest.skipUnless(platform.system() == 'Darwin', 'requires Metal')
class CounterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-counter-contract-')
        cls.executable = Path(cls.build.name) / 'counter'
        cls.no_sampling_executable = Path(cls.build.name) / 'no-sampling'
        graphics = ROOT / 'native/botw_graphics'
        for name, contents, extra_sources, executable in (
                ('counter.c', FIXTURE, [str(graphics / 'graphics.m')], cls.executable),
                ('no-sampling.m', NO_SAMPLING_DEVICE + FIXTURE, [], cls.no_sampling_executable)):
            source = Path(cls.build.name) / name
            source.write_text(contents)
            built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fblocks',
                                    '-Wall', '-Wextra', '-Werror', '-I', str(graphics),
                                    *extra_sources, str(source),
                                    '-framework', 'Foundation', '-framework', 'Metal',
                                    '-framework', 'QuartzCore', '-o', str(executable)],
                                   capture_output=True, text=True, timeout=120)
            if built.returncode:
                cls.build.cleanup()
                raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_mode(self, mode, marker, *, executable=None):
        if mode in ('clock-read', 'unavailable'):
            require_metal()
        else:
            require_counter_sampling()
        run = subprocess.run([str(executable or self.executable), mode], capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn(marker, run.stdout)

    def test_missing_hardware_sampling_rejects_reports_without_publication(self):
        capabilities = require_metal()
        if capabilities['timestamp_counter'] and capabilities['stage_boundary']:
            self.skipTest('public Metal query supports sampling; unavailable-device contract tested by public proxy')
        self.run_mode('unavailable', 'COUNTER_UNAVAILABLE_SAMPLING_REJECTED_WITH_CANARY_OK')

    def test_public_capability_absence_rejects_reports_and_keeps_reader_available(self):
        # Real production initialization sees only public capability absence.
        # Other selectors forward to the actual device; no sample is fabricated.
        self.run_mode('unavailable', 'COUNTER_UNAVAILABLE_SAMPLING_REJECTED_WITH_CANARY_OK',
                      executable=self.no_sampling_executable)
        self.run_mode('clock-read', 'COUNTER_TIMESTAMP_NONE_AND_CROSS_PAGE_READ_REJECTED_OK',
                      executable=self.no_sampling_executable)

    def test_report_then_gpu_copy_and_replay_use_fresh_calibrated_samples(self):
        self.run_mode('ordered', 'COUNTER_ORDERED_COPY_REPLAY_AND_RAW_NS_OK')

    def test_recorded_output_rejects_pool_finalize_and_same_address_reinitialization(self):
        self.run_mode('stale', 'COUNTER_POOL_ABA_REJECTED_OK')

    def test_actual_readonly_and_inaccessible_destinations_are_controlled_errors(self):
        require_counter_sampling()
        for mode in ('readonly', 'none'):
            with self.subTest(mode=mode):
                self.run_mode(mode, 'COUNTER_INACCESSIBLE_DESTINATION_REJECTED_OK')

    def test_live_and_current_segment_private_texture_authority_are_preserved(self):
        require_counter_sampling()
        for mode in ('private-live', 'private-pending'):
            with self.subTest(mode=mode):
                self.run_mode(mode, 'COUNTER_PRIVATE_TEXTURE_ALIAS_REJECTED_OK')

    def test_timestamp_reader_rejects_none_and_cross_page_records_without_poisoning_state(self):
        self.run_mode('clock-read', 'COUNTER_TIMESTAMP_NONE_AND_CROSS_PAGE_READ_REJECTED_OK')

    def test_alignment_and_exact_pool_end_preserve_adjacent_bytes(self):
        self.run_mode('bounds', 'COUNTER_ALIGNMENT_AND_EXACT_END_16B_OK')

    def test_cross_device_and_logical_readonly_outputs_are_rejected(self):
        self.run_mode('wrong-device', 'COUNTER_CROSS_DEVICE_SUBMIT_REJECTED_OK')
        self.run_mode('logical-ro', 'COUNTER_LOGICAL_READONLY_REJECTED_OK')

    def test_cpu_inaccessible_and_noncoherent_pools_are_explicitly_rejected(self):
        require_counter_sampling()
        for mode in ('cpu-noaccess', 'noncoherent'):
            with self.subTest(mode=mode):
                self.run_mode(mode, 'COUNTER_UNPUBLISHABLE_POOL_REJECTED_OK')

    def test_second_unwritable_destination_prevents_first_report_publication(self):
        self.run_mode('second-none', 'COUNTER_WHOLE_BATCH_PREFLIGHT_NO_EARLY_PUBLICATION_OK')


if __name__ == '__main__':
    unittest.main()
