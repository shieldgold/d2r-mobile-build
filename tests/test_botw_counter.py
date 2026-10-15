"""Synthetic NVN timestamp commands execute on real Metal, without a window."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from botw_metal_counter_capability import require_counter_sampling

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = r'''
#include "counter_clock.h"
#include <assert.h>
int main(void){
 uint64_t result=71;
 assert(botw_counter_calibrate(150,1000,100,3000,200,&result)&&result==2000);
 assert(botw_counter_calibrate(100,1000,100,3000,200,&result)&&result==1000);
 assert(botw_counter_calibrate(200,1000,100,3000,200,&result)&&result==3000);
 /* Multiplication exceeds 64 bits; its exact calibrated result still fits. */
 assert(botw_counter_calibrate(UINT64_MAX-2,1,1,UINT64_MAX-1,UINT64_MAX-1,&result)&&result==UINT64_MAX-2);
 uint64_t invalid[][5]={{150,1000,100,1000,200},{150,1000,100,3000,100},
 {99,1000,100,3000,200},{201,1000,100,3000,200},{UINT64_MAX,1000,100,3000,200},
 {150,1000,100,3000,UINT64_MAX},{150,1000,100,UINT64_MAX,200},{150,0,100,3000,200}};
 for(unsigned i=0;i<sizeof invalid/sizeof invalid[0];i++){
  result=71;assert(!botw_counter_calibrate(invalid[i][0],invalid[i][1],invalid[i][2],invalid[i][3],invalid[i][4],&result));assert(result==71);
 }
 assert(!botw_counter_calibrate(150,1000,100,3000,200,0));return 0;
}
'''


class CounterClockTests(unittest.TestCase):
    def test_calibration_preserves_bounds_and_rejects_error_values_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'clock.c').write_text(CALIBRATION)
            result = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-I', str(ROOT / 'native/botw_graphics'), str(path / 'clock.c'),
                '-o', str(path / 'clock')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(path / 'clock')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


DRIVER = r'''
#include "graphics.h"
#include "nvn_protos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define API(name) NVNT_##name name = (NVNT_##name)botw_nvn_bootstrap_loader(#name)
#define CHECK(x) do {if(!(x)){fprintf(stderr,"CHECK %s: %s\n",#x,botw_graphics_last_error());return 1;}}while(0)
static int memory(const void *p,size_t n,int write){(void)write;return p&&n<=16*1024*1024;}
static int shader_memory(const void *p,size_t n){return p&&n<=16*1024*1024;}
static void error(const char *reason){fprintf(stderr,"UNEXPECTED_COUNTER_ERROR %s\n",reason);exit(91);}
int main(int argc,char **argv){
 if(!botw_graphics_init(NULL,error,shader_memory))return 77;
 botw_graphics_set_memory_access_validator(memory);
 API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceFinalize);
 API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetFlags);
 API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolInitialize);API(nvnMemoryPoolFinalize);API(nvnMemoryPoolGetBufferAddress);
 API(nvnQueueBuilderSetDefaults);API(nvnQueueBuilderSetDevice);API(nvnQueueInitialize);API(nvnQueueFinish);API(nvnQueueFinalize);
 API(nvnCommandBufferInitialize);API(nvnCommandBufferBeginRecording);API(nvnCommandBufferEndRecording);
 API(nvnCommandBufferFinalize);API(nvnQueueSubmitCommands);API(nvnCommandBufferCopyBufferToBuffer);
 API(nvnCommandBufferReportCounter);API(nvnDeviceGetTimestampInNanoseconds);
 NVNdeviceBuilder db;NVNdevice d;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&d,&db));
 size_t page=(size_t)getpagesize();unsigned char *allocation=NULL;CHECK(!posix_memalign((void**)&allocation,page,page*3));
 memset(allocation,0xa5,page*3);unsigned char *bytes=allocation+64;size_t length=page*2-64;
 NVNmemoryPoolBuilder pb;NVNmemoryPool pool;nvnMemoryPoolBuilderSetDefaults(&pb);nvnMemoryPoolBuilderSetDevice(&pb,&d);
 nvnMemoryPoolBuilderSetFlags(&pb,NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_UNCACHED);
 nvnMemoryPoolBuilderSetStorage(&pb,bytes,length);CHECK(nvnMemoryPoolInitialize(&pool,&pb));
 NVNqueueBuilder qb;NVNqueue q;nvnQueueBuilderSetDefaults(&qb);nvnQueueBuilderSetDevice(&qb,&d);CHECK(nvnQueueInitialize(&q,&qb));
 NVNcommandBuffer c;CHECK(nvnCommandBufferInitialize(&c,&d));uint64_t address=nvnMemoryPoolGetBufferAddress(&pool);CHECK(address==(uintptr_t)bytes);
 nvnCommandBufferBeginRecording(&c);
 nvnCommandBufferCopyBufferToBuffer(&c,address+1024,address+768,16,0);
 nvnCommandBufferReportCounter(&c,NVN_COUNTER_TYPE_TIMESTAMP_TOP,address);
 nvnCommandBufferCopyBufferToBuffer(&c,address,address+512,16,0);
 nvnCommandBufferReportCounter(&c,NVN_COUNTER_TYPE_TIMESTAMP,address+16);
 nvnCommandBufferCopyBufferToBuffer(&c,address+16,address+528,16,0);
 NVNcommandHandle handle=nvnCommandBufferEndRecording(&c);
 for(size_t i=0;i<32;i++)CHECK(bytes[i]==0xa5); /* Recording must not publish. */
 nvnQueueSubmitCommands(&q,1,&handle);nvnQueueFinish(&q);
 NVNcounterData top,bottom;memcpy(&top,bytes,16);memcpy(&bottom,bytes+16,16);
 CHECK(top.timestamp&&bottom.timestamp>=top.timestamp&&top.timestamp!=UINT64_MAX);
 CHECK(!top.counter&&!bottom.counter);CHECK(!memcmp(bytes,bytes+512,32)); /* Same-list GPU copies consumed reports. */
 CHECK(nvnDeviceGetTimestampInNanoseconds(&d,(const NVNcounterData*)bytes)==top.timestamp);
 CHECK(nvnDeviceGetTimestampInNanoseconds(&d,(const NVNcounterData*)(bytes+16))==bottom.timestamp);
 for(size_t i=32;i<512;i++)CHECK(bytes[i]==0xa5);
 for(size_t i=0;i<64;i++)CHECK(allocation[i]==0xa5); /* Nonzero Metal backing offset preserved. */
 unsigned repetitions=argc>1&&!strcmp(argv[1],"bounded")?4:argc>1&&!strcmp(argv[1],"replay");
 for(unsigned replay=0;replay<repetitions;replay++){
  uint64_t previous=bottom.timestamp;usleep(2000);nvnQueueSubmitCommands(&q,1,&handle);nvnQueueFinish(&q);
  memcpy(&top,bytes,16);memcpy(&bottom,bytes+16,16);CHECK(top.timestamp>previous&&bottom.timestamp>=top.timestamp);CHECK(!memcmp(bytes,bytes+512,32));
 }
 if(argc>1&&!strcmp(argv[1],"clock")){
  API(nvnDeviceGetCurrentTimestampInNanoseconds);API(nvnDeviceGetInteger);int alignment=0;
  nvnDeviceGetInteger(&d,NVN_DEVICE_INFO_COUNTER_ALIGNMENT,&alignment);CHECK(alignment==16);
  uint64_t now=nvnDeviceGetCurrentTimestampInNanoseconds(&d);CHECK(now>=bottom.timestamp&&now-bottom.timestamp<UINT64_C(10000000000));
 }
 nvnCommandBufferFinalize(&c);nvnQueueFinalize(&q);nvnMemoryPoolFinalize(&pool);nvnDeviceFinalize(&d);botw_graphics_shutdown();free(allocation);
 puts("REAL_GPU_TIMESTAMP_PAIR_ORDER_COPY_AND_CANARIES_OK");return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin', 'requires Metal')
class CounterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)
        (path / 'counter.c').write_text(DRIVER)
        cls.executable = path / 'counter'
        command = ['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fblocks', '-O2',
                   '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'native/botw_graphics'),
                   str(ROOT / 'native/botw_graphics/graphics.m'), str(path / 'counter.c'),
                   '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore',
                   '-o', str(cls.executable)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def probe(self, mode=''):
        require_counter_sampling()
        result = subprocess.run([str(self.executable)] + ([mode] if mode else []),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('REAL_GPU_TIMESTAMP_PAIR_ORDER_COPY_AND_CANARIES_OK', result.stdout)
        return result

    def test_report_pair_executes_real_gpu_before_same_list_copy(self):
        self.probe()

    def test_replaying_the_same_handle_samples_new_gpu_time(self):
        self.probe('replay')

    def test_converter_current_timestamp_and_alignment_share_ns_domain(self):
        self.probe('clock')

    def test_completed_report_diagnostics_are_bounded_and_distinct_from_recording(self):
        result = self.probe('bounded')
        self.assertEqual(result.stderr.count('gpu_counter_report_completed=true'), 8)
        self.assertIn('type=16', result.stderr)
        self.assertIn('type=0', result.stderr)


@unittest.skipUnless(platform.system() == 'Darwin', 'requires Metal')
class CounterRenderTests(unittest.TestCase):
    def test_counter_segments_preserve_real_clear_draw_and_bound_pipeline_state(self):
        require_counter_sampling()
        import test_botw_graphics as baseline
        source = baseline.METAL_SMOKE.replace(
            '    API(nvnDeviceBuilderSetDefaults); API(nvnDeviceInitialize);',
            '    API(nvnCommandBufferReportCounter);\n'
            '    API(nvnDeviceBuilderSetDefaults); API(nvnDeviceInitialize);')
        for operation in ('nvnCommandBufferClearColor(&cb,0,color,15);',
                          'nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);'):
            source = source.replace(operation,
                'nvnCommandBufferReportCounter(&cb,NVN_COUNTER_TYPE_TIMESTAMP_TOP,gpu+3008);'
                + operation +
                'nvnCommandBufferReportCounter(&cb,NVN_COUNTER_TYPE_TIMESTAMP,gpu+3024);')
        # Independent query already established a device; initialization failure must fail.
        source = source.replace('return 77;', 'return 76;')
        baseline.MetalBridgeTests.compile_render_probe(
            self, source, [('', 'GPU_COPY_CLEAR_DRAW_OK', True)])


FAULTS = r'''
/* Failure injection wraps real Metal; it does not simulate successful GPU work. */
static const char *failureMode;
static unsigned char *counterOutput;
static IMP originalResolve;
static IMP originalWait;
static id<MTLCommandQueue> failureQueue;
static MTLCommandBufferStatus incompleteStatus(id object,SEL selector){(void)object;(void)selector;return MTLCommandBufferStatusScheduled;}
static void completedButUnexpectedStatus(id object,SEL selector){
 ((void(*)(id,SEL))originalWait)(object,selector);
 (void)method_setImplementation(class_getInstanceMethod([object class],@selector(status)),(IMP)incompleteStatus);
}
static NSData *brokenResolve(id object,SEL selector,NSRange range){
 if(!strcmp(failureMode,"resolve-delay")){
  if(!dispatch_group_wait(allGPU,DISPATCH_TIME_NOW))exit(94);
  NSData *actual=((NSData *(*)(id,SEL,NSRange))originalResolve)(object,selector,range);
  usleep(150000);return actual;
 }
 if(!strcmp(failureMode,"resolve-short"))return [NSMutableData dataWithLength:8];
 uint64_t ticks[2]={MTLCounterErrorValue,MTLCounterErrorValue};
 if(!strcmp(failureMode,"resolve-unordered")){ticks[0]=2;ticks[1]=1;}
 return [NSData dataWithBytes:ticks length:sizeof ticks];
}
@interface CounterFailureDevice : NSObject
@property(strong) id<MTLDevice> actual;
@end
@implementation CounterFailureDevice
- (id)forwardingTargetForSelector:(SEL)selector{(void)selector;return self.actual;}
- (void)sampleTimestamps:(MTLTimestamp *)cpu gpuTimestamp:(MTLTimestamp *)native{
 if(!strcmp(failureMode,"calibration-zero")){*cpu=0;*native=0;}
 else [self.actual sampleTimestamps:cpu gpuTimestamp:native];
}
- (id<MTLCounterSampleBuffer>)newCounterSampleBufferWithDescriptor:(MTLCounterSampleBufferDescriptor *)descriptor error:(NSError **)error{
 if(!strcmp(failureMode,"sample-allocation"))return nil;
 id<MTLCounterSampleBuffer> samples=[self.actual newCounterSampleBufferWithDescriptor:descriptor error:error];
 if(samples&&!strcmp(failureMode,"unexpected-status")){
  id<MTLCommandBuffer> probe=[failureQueue commandBuffer];
  Method method=class_getInstanceMethod([(id)probe class],@selector(waitUntilCompleted));
  IMP previous=method_setImplementation(method,(IMP)completedButUnexpectedStatus);if(previous!=(IMP)completedButUnexpectedStatus)originalWait=previous;
 }
 if(samples&&(!strncmp(failureMode,"resolve-",8))){
  Method method=class_getInstanceMethod([(id)samples class],@selector(resolveCounterRange:));
  IMP previous=method_setImplementation(method,(IMP)brokenResolve);if(previous!=(IMP)brokenResolve)originalResolve=previous;
 }
 return samples;
}
@end
'''


@unittest.skipUnless(platform.system() == 'Darwin', 'requires Metal')
class CounterFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)
        # Include the unmodified production translation unit to wrap only the
        # underlying device response and inspect the real completion group.
        source = '#include "graphics.m"\n' + FAULTS + DRIVER
        source = source.replace(
            'static void error(const char *reason){fprintf(stderr,"UNEXPECTED_COUNTER_ERROR %s\\n",reason);exit(91);}',
            'static void error(const char *reason){fprintf(stderr,"INJECTED_COUNTER_FAILURE %s\\n",reason);'
            'if(!counterOutput||dispatch_group_wait(allGPU,DISPATCH_TIME_NOW))exit(92);'
            'for(unsigned i=0;i<32;i++)if(counterOutput[i]!=0xa5)exit(93);'
            'puts("COUNTER_FAILURE_NO_PUBLICATION_AND_GROUP_DRAINED");exit(0);}')
        source = source.replace(' nvnQueueSubmitCommands(&q,1,&handle);nvnQueueFinish(&q);',
            ' failureMode=argc>1?argv[1]:"resolve-error";counterOutput=bytes;'
            'failureQueue=((BWQueue*)get(&q,[BWQueue class])).native;'
            'CounterFailureDevice *proxy=[CounterFailureDevice new];proxy.actual=gpu;gpu=(id<MTLDevice>)proxy;'
            'if(!strcmp(failureMode,"capability"))((BWDevice*)get(&d,[BWDevice class])).timestampStages=NO;'
            'nvnQueueSubmitCommands(&q,1,&handle);nvnQueueFinish(&q);', 1)
        source = source.replace(' CHECK(!top.counter&&!bottom.counter);',
            ' if(!strcmp(failureMode,"resolve-delay")){API(nvnDeviceGetCurrentTimestampInNanoseconds);'
            'uint64_t now=nvnDeviceGetCurrentTimestampInNanoseconds(&d);'
            'CHECK(now>=bottom.timestamp&&now-bottom.timestamp>=UINT64_C(100000000));}'
            'CHECK(!top.counter&&!bottom.counter);')
        (path / 'failure.m').write_text(source)
        cls.executable = path / 'failure'
        result = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fblocks', '-O2',
            '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'native/botw_graphics'),
            str(path / 'failure.m'), '-framework', 'Foundation', '-framework', 'Metal',
            '-framework', 'QuartzCore', '-o', str(cls.executable)], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_metal_failures_never_publish_counter_and_drain_publication_group(self):
        require_counter_sampling()
        cases = {'sample-allocation': 'sample-buffer allocation failed',
                 'resolve-short': 'invalid byte count', 'resolve-error': 'sample is invalid',
                 'resolve-unordered': 'sample is invalid', 'calibration-zero': 'calibration span is invalid',
                 'capability': 'stage sampling is unavailable',
                 'unexpected-status': 'marker did not complete successfully'}
        for mode, message in cases.items():
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.executable), mode], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('COUNTER_FAILURE_NO_PUBLICATION_AND_GROUP_DRAINED', result.stdout)
                self.assertIn(message, result.stderr)

    def test_delayed_cpu_resolution_does_not_become_gpu_time_or_finish_early(self):
        require_counter_sampling()
        result = subprocess.run([str(self.executable), 'resolve-delay'], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('REAL_GPU_TIMESTAMP_PAIR_ORDER_COPY_AND_CANARIES_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
