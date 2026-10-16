"""Test prerequisites queried directly through public Metal, without the bridge."""
from functools import lru_cache
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CAPABILITY_PROBE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
int main(void) { @autoreleasepool {
 id<MTLDevice> device=MTLCreateSystemDefaultDevice();
 NSMutableDictionary *result=[@{@"metal_device":device ? @YES : @NO,
   @"device":device.name?:@"", @"api_available":@NO,
   @"timestamp_counter":@NO, @"stage_boundary":@NO, @"apple7_family":@NO,
   @"counter_sets":@{}} mutableCopy];
 if(device) {
  if(@available(macOS 11.0, iOS 14.0, *)) {
   result[@"api_available"]=@YES;
   result[@"apple7_family"]=@([device supportsFamily:MTLGPUFamilyApple7]);
   NSMutableDictionary *sets=[NSMutableDictionary new];
   BOOL timestamp=NO;
   for(id<MTLCounterSet> set in device.counterSets) {
    NSMutableArray *names=[NSMutableArray new];
    for(id<MTLCounter> counter in set.counters) {
     [names addObject:counter.name];
     if([set.name isEqualToString:MTLCommonCounterSetTimestamp] &&
        [counter.name isEqualToString:MTLCommonCounterTimestamp])timestamp=YES;
    }
    sets[set.name]=names;
   }
   result[@"counter_sets"]=sets;
   result[@"timestamp_counter"]=@(timestamp);
   result[@"stage_boundary"]=@([device supportsCounterSampling:MTLCounterSamplingPointAtStageBoundary]);
  }
 }
 NSError *error=nil;
 NSData *data=[NSJSONSerialization dataWithJSONObject:result options:0 error:&error];
 if(!data){fprintf(stderr,"Metal capability JSON failed: %s\n",error.description.UTF8String);return 1;}
 fwrite(data.bytes,1,data.length,stdout);putchar('\n');return 0;
} }
'''


@lru_cache(maxsize=1)
def counter_capabilities():
    """Failures to compile/query are test errors, never absence of hardware."""
    with tempfile.TemporaryDirectory(prefix='botw-metal-counter-capability-') as directory:
        source = Path(directory) / 'capability.m'
        executable = Path(directory) / 'capability'
        source.write_text(CAPABILITY_PROBE)
        built = subprocess.run(
            ['xcrun', 'clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
             str(source), '-framework', 'Foundation', '-framework', 'Metal',
             '-o', str(executable)], capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)
        run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
        if run.returncode:
            raise AssertionError(run.stdout + run.stderr)
        result = json.loads(run.stdout)
    for key in ('metal_device', 'api_available', 'timestamp_counter', 'stage_boundary', 'apple7_family'):
        if type(result.get(key)) is not bool:
            raise AssertionError(f'invalid public Metal capability {key}: {result!r}')
    if not isinstance(result.get('counter_sets'), dict):
        raise AssertionError(f'invalid public Metal counter sets: {result!r}')
    print('METAL_COUNTER_TEST_CAPABILITIES ' + json.dumps(result, sort_keys=True),
          file=sys.stderr, flush=True)
    return result


def require_metal():
    capabilities = counter_capabilities()
    if not capabilities['metal_device']:
        raise unittest.SkipTest('public Metal query found no device')
    return capabilities


def require_counter_sampling():
    capabilities = require_metal()
    if not (capabilities['timestamp_counter'] and capabilities['stage_boundary']):
        raise unittest.SkipTest('public Metal query lacks timestamp counter or stage-boundary sampling')
    return capabilities


def require_apple7():
    capabilities = require_metal()
    if not capabilities['apple7_family']:
        raise unittest.SkipTest('public Metal query lacks Apple7 family support required by IMAGE fixtures')
    return capabilities
