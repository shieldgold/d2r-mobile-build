"""Host-only graphics lock observation; fake Metal objects, no GPU/device calls."""
from pathlib import Path
import json
import os
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CPU_SOURCE = r'''
#import <Metal/Metal.h>
#include <dlfcn.h>
#include <stdatomic.h>
#include <time.h>
#include <pthread.h>
#include <unistd.h>
#include <errno.h>
static atomic_int clockFault,threadFault,clockCalls;
static int observedClock(clockid_t clock,struct timespec *value);
static int observedThread(pthread_t thread,uint64_t *value);
@interface ObservationCPUDevice : NSObject @end
@implementation ObservationCPUDevice
- (id<MTLCommandQueue>)newCommandQueue { return (id<MTLCommandQueue>)[NSObject new]; }
@end
static id<MTLDevice> observationCPUDevice(void) { return (id<MTLDevice>)[ObservationCPUDevice new]; }
#define MTLCreateSystemDefaultDevice observationCPUDevice
#define clock_gettime observedClock
#define pthread_threadid_np observedThread
#include "graphics.m"
#undef MTLCreateSystemDefaultDevice
#undef clock_gettime
#undef pthread_threadid_np
static int observedClock(clockid_t clock,struct timespec *value){
 atomic_fetch_add(&clockCalls,1);int mode=atomic_load(&clockFault);
 if(mode==1)return EIO;
 int result=clock_gettime(clock,value);if(result)return result;
 if(mode==2)value->tv_nsec=1000000000;
 if(mode==3)atomic_fetch_add(&graphicsObservation.sequence,2);
 if(mode==4){value->tv_sec=0;value->tv_nsec=0;}
 return 0;
}
static int observedThread(pthread_t thread,uint64_t *value){
 int mode=atomic_load(&threadFault);if(mode==1)return EIO;
 if(mode==2){*value=0;return 0;}return pthread_threadid_np(thread,value);
}
typedef struct {
 uint64_t lifecycle,generation,owner_thread_id,acquired_ns,sampled_ns;
 char outer_entry[96],active_entry[96];
} ObservationMirror;
typedef int (*EnableObservation)(void);
typedef int (*ReadObservation)(ObservationMirror *);
static EnableObservation enableObservation;
static ReadObservation readObservation;
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"LOCK_OBSERVATION_CPU_CHECK line%d %s\n",__LINE__,#x); return 91; } } while(0)
static NVNdevice rawDevice;
static NVNbufferBuilder rawBuilder;
static atomic_int gateKind,gateStage,releaseOne,releaseTwo,releaseThree;
static atomic_int ownerStarted,ownerDone,waiterStarted,waiterDone,queryValue,accessCalls;
static BOOL callbackValid=YES,callbackThrows;
static uint64_t callbackPriorGeneration,callbackInnerGeneration;
static atomic_uint_fast64_t ownerID;
static _Thread_local unsigned workerRole;
static unsigned errorCalls;
static BOOL errorUnlocked=YES;
static int waitFor(atomic_int *value,int wanted){for(unsigned i=0;i<3000;i++){if(atomic_load(value)==wanted)return 1;usleep(1000);}return 0;}
static void pauseFor(atomic_int *value){while(!atomic_load(value))usleep(1000);}
static BOOL nameIs(const char *name,const char *wanted){return !strcmp(name,wanted)||(!strncmp(name,"w_",2)&&!strcmp(name+2,wanted));}
static int allZero(const ObservationMirror *value){const unsigned char *p=(const unsigned char*)value;for(size_t i=0;i<sizeof *value;i++)if(p[i])return 0;return 1;}
static int noHeldFields(const ObservationMirror *value){return !value->owner_thread_id&&!value->acquired_ns&&!value->outer_entry[0]&&!value->active_entry[0];}
static int observedAccess(const void *address,size_t bytes,int writable){
 (void)bytes;(void)writable;atomic_fetch_add(&accessCalls,1);int kind=atomic_load(&gateKind);
 if(workerRole==3&&address==&rawDevice){ObservationMirror value;if(readObservation(&value)!=2||!nameIs(value.outer_entry,"nvnDeviceGetInteger")||!nameIs(value.active_entry,"nvnDeviceGetInteger")||value.generation<=callbackPriorGeneration)callbackValid=NO;callbackInnerGeneration=value.generation;}
 if(workerRole==1&&address==&rawDevice){
  if(kind==1){atomic_store(&gateStage,1);pauseFor(&releaseOne);}
  if(kind==2){size_t size=w_nvnBufferBuilderGetSize(&rawBuilder);if(size!=42)return 0;atomic_store(&gateStage,3);pauseFor(&releaseThree);}
 }
 if(workerRole==1&&kind==2&&address==&rawBuilder){atomic_store(&gateStage,2);pauseFor(&releaseTwo);}
 return 1;
}
@interface ObservationLayer : NSObject
@property(nonatomic,strong) id<MTLDevice> device;
@end
@implementation ObservationLayer
- (CGSize)drawableSize {if(workerRole==1&&atomic_load(&gateKind)==3){atomic_store(&gateStage,1);pauseFor(&releaseOne);}return CGSizeMake(4,4);}
@end
@interface ObservationStateLock : NSLock @end
@implementation ObservationStateLock
- (void)lock {if(workerRole==1&&atomic_load(&gateKind)==4){atomic_store(&gateStage,1);pauseFor(&releaseOne);}[super lock];}
@end
static void neverCalledMemoryCallback(NVNcommandBuffer *raw,NVNcommandBufferMemoryEvent event,size_t bytes,void *data){(void)raw;(void)event;(void)bytes;(void)data;}
static int observedMemoryInvoker(PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC callback,NVNcommandBuffer *raw,NVNcommandBufferMemoryEvent event,size_t bytes,void *data,char *error,size_t capacity){
 (void)callback;(void)raw;(void)event;(void)bytes;(void)data;(void)error;(void)capacity;ObservationMirror value;
 if(graphicsDepth||readObservation(&value)!=1||!noHeldFields(&value))callbackValid=NO;
 if(pthread_mutex_trylock(&graphicsLock))callbackValid=NO;else pthread_mutex_unlock(&graphicsLock);
 workerRole=3;int result=0;w_nvnDeviceGetInteger(&rawDevice,NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE,&result);workerRole=0;
 if(result!=65536||readObservation(&value)!=1||!noHeldFields(&value))callbackValid=NO;
 if(callbackThrows)@throw [NSException exceptionWithName:@"OwnedObservationCallback" reason:@"fixture exception" userInfo:nil];return 1;
}
static void observedError(const char *message){
 (void)message;errorCalls++;ObservationMirror observed;
 if(pthread_mutex_trylock(&graphicsLock))errorUnlocked=NO;else pthread_mutex_unlock(&graphicsLock);
 if(readObservation&&readObservation(&observed)!=1)errorUnlocked=NO;
}
static int setup(void){
 CHECK(botw_graphics_init(NULL,observedError,NULL));
 BWDevice *device=[[BWDevice alloc]initWithTextureKindPolicy:BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH];put(&rawDevice,device);
 struct bwu_builder builder={0};builder.size=42;memcpy(&rawBuilder,&builder,sizeof builder);memoryAccessValidator=observedAccess;return 0;
}
static int teardown(void){memoryAccessValidator=NULL;char error[256];CHECK(botw_graphics_shutdown_checked(error,sizeof error));return 0;}
static void *getterWorker(void *argument){@autoreleasepool{
 workerRole=(unsigned)(uintptr_t)argument;uint64_t tid=0;pthread_threadid_np(NULL,&tid);
 if(workerRole==1){atomic_store(&ownerID,tid);atomic_store(&ownerStarted,1);}else atomic_store(&waiterStarted,1);
 int output=0;int kind=atomic_load(&gateKind);
 if(kind==3)output=botw_graphics_platform_initialize();
 else if(kind==4){char error[256];output=botw_graphics_shutdown_checked(error,sizeof error);}
 else w_nvnDeviceGetInteger(&rawDevice,NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE,&output);
 atomic_store(&queryValue,output);
 atomic_store(workerRole==1?&ownerDone:&waiterDone,1);return NULL;
}}
static int heldOwner(ObservationMirror *out){
 CHECK(readObservation(out)==2);CHECK(out->owner_thread_id==atomic_load(&ownerID)&&out->owner_thread_id&&out->acquired_ns&&out->sampled_ns>=out->acquired_ns);
 CHECK(nameIs(out->outer_entry,"nvnDeviceGetInteger"));CHECK(nameIs(out->active_entry,"nvnDeviceGetInteger"));return 0;
}
int main(int argc,char **argv) { @autoreleasepool {
 alarm(12);CHECK(argc==2);const char *mode=argv[1];
 enableObservation=(EnableObservation)dlsym(RTLD_DEFAULT,"botw_graphics_enable_lock_observation");
 readObservation=(ReadObservation)dlsym(RTLD_DEFAULT,"botw_graphics_read_lock_observation");CHECK(enableObservation&&readObservation);
 ObservationMirror before,after;memset(&before,0xa5,sizeof before);
 if(!strcmp(mode,"api")){CHECK(readObservation(&before)==0&&allZero(&before));}
 else if(!strcmp(mode,"disabled")){
  atomic_store(&clockFault,1);atomic_store(&threadFault,1);CHECK(readObservation(&before)==0&&allZero(&before)&&!atomic_load(&clockCalls));CHECK(!setup());CHECK(!enableObservation());CHECK(readObservation(&before)==0&&allZero(&before));CHECK(!teardown());CHECK(!enableObservation());CHECK(readObservation(&before)==0&&allZero(&before)&&!atomic_load(&clockCalls));
 }else if(!strcmp(mode,"idle")){
  CHECK(enableObservation()&&enableObservation());CHECK(readObservation(&before)==1&&noHeldFields(&before)&&!before.lifecycle&&!before.generation&&before.sampled_ns);
  CHECK(!setup());CHECK(!enableObservation());CHECK(readObservation(&after)==1&&after.lifecycle>before.lifecycle&&after.generation>before.generation&&noHeldFields(&after));CHECK(!teardown());
 }else if(!strcmp(mode,"owner-waiter")||!strcmp(mode,"nested")){
  CHECK(enableObservation()&&!setup());atomic_store(&gateKind,!strcmp(mode,"nested")?2:1);pthread_t owner,waiter;CHECK(!pthread_create(&owner,NULL,getterWorker,(void*)1));CHECK(waitFor(&gateStage,!strcmp(mode,"nested")?2:1));
  CHECK(readObservation(&before)==2&&before.owner_thread_id==atomic_load(&ownerID));CHECK(nameIs(before.outer_entry,"nvnDeviceGetInteger"));CHECK(nameIs(before.active_entry,!strcmp(mode,"nested")?"nvnBufferBuilderGetSize":"nvnDeviceGetInteger"));
  CHECK(!pthread_create(&waiter,NULL,getterWorker,(void*)2));CHECK(waitFor(&waiterStarted,1));CHECK(!atomic_load(&waiterDone));
  int accesses=atomic_load(&accessCalls);CHECK(readObservation(&after)==2&&after.generation==before.generation&&after.owner_thread_id==before.owner_thread_id&&after.acquired_ns==before.acquired_ns&&atomic_load(&accessCalls)==accesses);
  CHECK(pthread_mutex_trylock(&graphicsLock)==EBUSY);
  if(!strcmp(mode,"nested")){atomic_store(&releaseTwo,1);CHECK(waitFor(&gateStage,3));CHECK(!heldOwner(&after));CHECK(after.generation==before.generation&&after.acquired_ns==before.acquired_ns);atomic_store(&releaseThree,1);}else atomic_store(&releaseOne,1);
  CHECK(!pthread_join(owner,NULL)&&!pthread_join(waiter,NULL));CHECK(atomic_load(&ownerDone)&&atomic_load(&waiterDone)&&atomic_load(&queryValue)==65536);CHECK(readObservation(&after)==1&&after.generation>=before.generation+1&&noHeldFields(&after));CHECK(!teardown());
 }else if(!strcmp(mode,"repeat")){
  CHECK(enableObservation()&&!setup());for(unsigned i=0;i<3;i++){CHECK(readObservation(&before)==1);int output=0;w_nvnDeviceGetInteger(&rawDevice,NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE,&output);CHECK(output==65536);CHECK(readObservation(&after)==1&&after.generation>before.generation&&after.lifecycle==before.lifecycle&&noHeldFields(&after));}CHECK(!teardown());
 }else if(!strcmp(mode,"error")){
  CHECK(enableObservation()&&!setup());CHECK(!botw_graphics_init(NULL,observedError,NULL));CHECK(errorCalls==1&&errorUnlocked);int output=0;w_nvnDeviceGetInteger(&rawDevice,NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE,&output);CHECK(output==65536&&!graphicsDepth);CHECK(readObservation(&after)==1&&noHeldFields(&after));CHECK(!teardown());
 }else if(!strcmp(mode,"lifecycle")){
  CHECK(enableObservation()&&!setup());CHECK(readObservation(&before)==1&&before.lifecycle);CHECK(!teardown());CHECK(readObservation(&after)==1&&after.lifecycle==before.lifecycle&&after.generation>before.generation);uint64_t generation=after.generation;CHECK(!setup());CHECK(readObservation(&after)==1&&after.lifecycle>before.lifecycle&&after.generation>generation);CHECK(!teardown());
 }else if(!strcmp(mode,"callback")||!strcmp(mode,"callback-throw")){
  CHECK(enableObservation()&&!setup());NVNcommandBuffer raw={0};BWCommands *commands=[BWCommands new];commands.recording=YES;commands.generation=1;commands.callback=neverCalledMemoryCallback;put(&raw,commands);commandMemoryInvoker=observedMemoryInvoker;callbackThrows=!strcmp(mode,"callback-throw");
  const char *outer=graphicsBeginEntry("owned_outer");const char *inner=graphicsBeginEntry("owned_callback_outer");CHECK(readObservation(&before)==2&&!strcmp(before.outer_entry,"owned_outer")&&!strcmp(before.active_entry,"owned_callback_outer"));callbackPriorGeneration=before.generation;
  BOOL threw=NO;@try{invokeRecordingCallback(&raw,commands,NVN_COMMAND_BUFFER_MEMORY_EVENT_OUT_OF_COMMAND_MEMORY,64);}@catch(NSException *exception){threw=[exception.name isEqualToString:@"OwnedObservationCallback"];}
  CHECK(threw==callbackThrows&&callbackValid&&callbackInnerGeneration>before.generation);CHECK(readObservation(&after)==2&&after.generation>callbackInnerGeneration&&after.acquired_ns>=before.acquired_ns&&after.owner_thread_id==before.owner_thread_id&&!strcmp(after.outer_entry,"owned_outer")&&!strcmp(after.active_entry,"owned_callback_outer")&&graphicsDepth==2&&!commands.callbackActive);
  graphicsEndEntry(inner);CHECK(readObservation(&after)==2&&!strcmp(after.active_entry,"owned_outer"));graphicsEndEntry(outer);CHECK(readObservation(&after)==1&&noHeldFields(&after));CHECK(!teardown());
 }else if(!strcmp(mode,"platform")||!strcmp(mode,"checked")){
  CHECK(enableObservation()&&!setup());BOOL checked=!strcmp(mode,"checked");
  if(checked)presentationSnapshot().lock=[ObservationStateLock new];else{ObservationLayer *layer=[ObservationLayer new];layer.device=gpu;displayLayer=(CAMetalLayer*)(id)layer;}
  atomic_store(&gateKind,checked?4:3);pthread_t owner;CHECK(!pthread_create(&owner,NULL,getterWorker,(void*)1));CHECK(waitFor(&gateStage,1));int accesses=atomic_load(&accessCalls);CHECK(readObservation(&before)==2&&before.owner_thread_id==atomic_load(&ownerID)&&!strcmp(before.outer_entry,checked?"botw_graphics_shutdown_checked":"botw_graphics_platform_initialize")&&!strcmp(before.active_entry,before.outer_entry)&&atomic_load(&accessCalls)==accesses);atomic_store(&releaseOne,1);CHECK(!pthread_join(owner,NULL)&&atomic_load(&ownerDone)&&atomic_load(&queryValue)==1);CHECK(readObservation(&after)==1&&noHeldFields(&after));if(!checked)CHECK(!teardown());
 }else if(!strcmp(mode,"unstable-odd")||!strcmp(mode,"unstable-changing")){
  CHECK(enableObservation()&&!setup());int calls=atomic_load(&clockCalls);uint64_t sequence=atomic_load(&graphicsObservation.sequence);
  if(!strcmp(mode,"unstable-odd"))atomic_store(&graphicsObservation.sequence,sequence+1);else atomic_store(&clockFault,3);
  memset(&after,0xa5,sizeof after);CHECK(readObservation(&after)==3&&allZero(&after));CHECK(atomic_load(&clockCalls)-calls==(!strcmp(mode,"unstable-odd")?0:2));
  atomic_store(&clockFault,0);if(!strcmp(mode,"unstable-odd"))atomic_store(&graphicsObservation.sequence,sequence);CHECK(readObservation(&after)==1&&!atomic_load(&graphicsObservation.unavailable));CHECK(!teardown());
 }else if(!strcmp(mode,"saturated-sequence")||!strcmp(mode,"saturated-generation")||!strcmp(mode,"saturated-lifecycle")){
  CHECK(enableObservation()&&!setup());if(!strcmp(mode,"saturated-sequence"))atomic_store(&graphicsObservation.sequence,UINT64_MAX-1);else if(!strcmp(mode,"saturated-generation"))atomic_store(&graphicsObservation.generation,UINT64_MAX);else{CHECK(!teardown());atomic_store(&graphicsObservation.lifecycle,UINT64_MAX);CHECK(!setup());}
  int output=0;w_nvnDeviceGetInteger(&rawDevice,NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE,&output);memset(&after,0xa5,sizeof after);CHECK(output==65536&&readObservation(&after)==4&&allZero(&after)&&!errorCalls);CHECK(!teardown());
 }else if(!strcmp(mode,"clock-failure")||!strcmp(mode,"clock-invalid")||!strcmp(mode,"clock-backwards")||!strcmp(mode,"thread-failure")||!strcmp(mode,"thread-zero")){
  CHECK(enableObservation()&&!setup());int output=0;
  if(!strcmp(mode,"thread-failure")||!strcmp(mode,"thread-zero")){atomic_store(&threadFault,!strcmp(mode,"thread-zero")?2:1);w_nvnDeviceGetInteger(&rawDevice,NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE,&output);CHECK(output==65536);}
  else if(!strcmp(mode,"clock-backwards")){const char *previous=graphicsBeginEntry("owned_backwards");atomic_store(&clockFault,4);CHECK(readObservation(&after)==4&&allZero(&after));graphicsEndEntry(previous);}
  else{atomic_store(&clockFault,!strcmp(mode,"clock-invalid")?2:1);CHECK(readObservation(&after)==4&&allZero(&after));}
  atomic_store(&threadFault,0);atomic_store(&clockFault,0);CHECK(readObservation(&after)==4&&allZero(&after)&&!errorCalls);w_nvnDeviceGetInteger(&rawDevice,NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE,&output);CHECK(output==65536);CHECK(!teardown());
 }else CHECK(0);
 CHECK(!errorCalls||(!strcmp(mode,"error")&&errorCalls==1));printf("LOCK_OBSERVATION_CPU_OK mode=%s no_real_gpu=true\n",mode);return 0;
}}
'''

@unittest.skipUnless(platform.system() == "Darwin" and shutil.which("xcrun"), "Darwin CPU-only Objective-C harness")
class GraphicsLockObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="botw-graphics-lock-")
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        source = folder / "observation.m"
        source.write_text(CPU_SOURCE)
        cls.executable = folder / "observation"
        cls.compile_command = ["xcrun", "clang", "-std=c11", "-fobjc-arc", "-fobjc-arc-exceptions", "-fblocks",
            "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-O1", "-g", "-fsanitize=address,undefined",
            "-Wl,-export_dynamic", "-I", str(ROOT / "native/botw_graphics"), str(source),
            "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore", "-o", str(cls.executable)]
        cls.build_result = subprocess.run(cls.compile_command, capture_output=True, text=True, timeout=120)
        cls.audit = Path(os.environ["BOTW_GRAPHICS_LOCK_AUDIT_DIR"]) if os.environ.get("BOTW_GRAPHICS_LOCK_AUDIT_DIR") else None
        if cls.audit:
            cls.audit.mkdir(parents=True, exist_ok=True)
            (cls.audit / "fixture.m").write_text(CPU_SOURCE)
            (cls.audit / "compile.json").write_text(json.dumps({"command": cls.compile_command, "returncode": cls.build_result.returncode,
                "stdout": cls.build_result.stdout, "stderr": cls.build_result.stderr}, indent=2) + "\n")
        if cls.build_result.returncode:
            raise AssertionError(cls.build_result.stdout + cls.build_result.stderr)

    def mode(self, name):
        run = subprocess.run([str(self.executable), name], capture_output=True, text=True, timeout=15)
        if self.audit:
            (self.audit / (name + ".json")).write_text(json.dumps({"returncode": run.returncode,
                "stdout": run.stdout, "stderr": run.stderr}, indent=2) + "\n")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("LOCK_OBSERVATION_CPU_OK", run.stdout)
        self.assertNotIn("ERROR: AddressSanitizer", run.stderr)
        self.assertNotIn("runtime error:", run.stderr)

    def test_public_observation_symbols_are_available_without_starting_graphics(self):
        self.mode("api")

    def test_disabled_is_default_and_first_entry_freezes_late_enable(self):
        self.mode("disabled")

    def test_prestart_enable_reports_idle_and_initialized_lifecycle(self):
        self.mode("idle")

    def test_public_getter_owner_and_waiter_do_not_block_lock_free_observer(self):
        self.mode("owner-waiter")

    def test_nested_public_getter_restores_outer_active_without_restarting_hold(self):
        self.mode("nested")

    def test_same_public_entry_increments_each_outer_hold_generation(self):
        self.mode("repeat")

    def test_error_callback_observes_released_lock_and_original_error_behavior(self):
        self.mode("error")

    def test_successful_init_advances_lifecycle_and_shutdown_keeps_process_generations(self):
        self.mode("lifecycle")

    def test_command_memory_callback_releases_and_reacquires_a_fresh_hold(self):
        self.mode("callback")

    def test_command_memory_callback_exception_restores_lock_and_nested_names(self):
        self.mode("callback-throw")

    def test_platform_and_checked_shutdown_have_explicit_entry_names(self):
        for name in ("platform", "checked"):
            with self.subTest(name=name):
                self.mode(name)

    def test_reader_has_two_bounded_attempts_and_does_not_latch_contention_as_unavailable(self):
        for name in ("unstable-odd", "unstable-changing"):
            with self.subTest(name=name):
                self.mode(name)

    def test_sequence_generation_and_lifecycle_saturation_preserve_business_calls(self):
        for name in ("saturated-sequence", "saturated-generation", "saturated-lifecycle"):
            with self.subTest(name=name):
                self.mode(name)

    def test_clock_failure_invalid_time_and_backwards_time_fail_closed_without_error_callbacks(self):
        for name in ("clock-failure", "clock-invalid", "clock-backwards"):
            with self.subTest(name=name):
                self.mode(name)

    def test_thread_id_failure_and_zero_fail_closed_without_changing_queries(self):
        for name in ("thread-failure", "thread-zero"):
            with self.subTest(name=name):
                self.mode(name)
