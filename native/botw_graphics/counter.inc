/* Report Counter is a synchronous native-port boundary: preceding GPU work
 * completes, a real GPU marker is sampled, then checked publication precedes
 * subsequent GPU consumers. This deliberately trades throughput for ordering.
 * Timestamp-only counter.counter is zero; counter.timestamp is calibrated ns.
 * Other statistics, ResetCounter and ReportValue remain unsupported. */
#include "counter_clock.h"
static void counterConfigureDevice(BWDevice *device){
    if(@available(macOS 11.0,iOS 14.0,*)){
        for(id<MTLCounterSet> set in gpu.counterSets)if([set.name isEqualToString:MTLCommonCounterSetTimestamp]){
            for(id<MTLCounter> counter in set.counters)if([counter.name isEqualToString:MTLCommonCounterTimestamp]){device.timestampSet=set;break;}
        }
        device.timestampStages=device.timestampSet&&[gpu supportsCounterSampling:MTLCounterSamplingPointAtStageBoundary];
    }
    fprintf(stderr,"[botw graphics] gpu_timestamp_counter=%s stage_boundary=%s raw_domain=calibrated_ns\n",
            device.timestampSet?"available":"unavailable",device.timestampStages?"supported":"unsupported");
}
static void validateCounterAddress(BWAddressRecord *r,BWDevice *device){
    validateBufferAddress(r,device);
    if(!device.timestampStages)fatal("GPU timestamp stage sampling is unavailable on this device");
    if(r.bytes!=sizeof(NVNcounterData)||((uintptr_t)r.pool.memory+r.offset)%16)fatal("counter destination size/alignment");
    if(!r.pool.coherent||(r.pool.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS))fatal("counter destination requires coherent CPU-accessible pool");
    if(memoryAccessValidator&&!memoryAccessValidator((char*)r.pool.memory+r.offset,r.bytes,1))fatal("counter destination is not logically writable");
}
NVN(nvnCommandBufferReportCounter,void,NVNcommandBuffer *raw,NVNcounterType type,NVNbufferAddress address){
    if(type!=NVN_COUNTER_TYPE_TIMESTAMP&&type!=NVN_COUNTER_TYPE_TIMESTAMP_TOP)fatal("counter type requires an unimplemented real GPU statistic");
    BWCommands *cmd=commandState(raw,NO);if(!cmd.recording)fatal("counter command outside recording");
    BWAddressRecord *r=captureBufferAddress(raw,address,sizeof(NVNcounterData),"counter destination range/device");
    validateCounterAddress(r,cmd.device);record(raw,@{@"op":@"counter",@"type":@(type),@"destination":r});
}
NVN(nvnDeviceGetTimestampInNanoseconds,uint64_t,const NVNdevice *raw,const NVNcounterData *counter){
    get(raw,[BWDevice class]);NVNcounterData snapshot;
    textureReadBytes(counter,&snapshot,sizeof snapshot,"counter timestamp record is not fully readable");
    if(snapshot.timestamp==MTLCounterErrorValue)fatal("counter timestamp is an invalid GPU sample");
    return snapshot.timestamp;
}
NVN(nvnDeviceGetCurrentTimestampInNanoseconds,uint64_t,const NVNdevice *raw){
    BWDevice *device=get(raw,[BWDevice class]);if(!device.timestampStages)fatal("GPU timestamp stage sampling is unavailable on this device");
    MTLTimestamp cpu=0,native=0;[gpu sampleTimestamps:&cpu gpuTimestamp:&native];
    if(!cpu||!native||cpu==MTLCounterErrorValue||native==MTLCounterErrorValue)fatal("GPU timestamp calibration query failed");return cpu;
}
static void counterCommitSegment(BWQueue *queue,id<MTLCommandBuffer> cb,NSArray *commands,
                                  BWScratchRecord *initialScratch,NSArray *initialAddresses,BWReplay *state){
    retainScratchUntilCompleted(cb,commands,initialScratch,state);
    retainAddressesUntilCompleted(cb,commands,initialAddresses,state);
    commitQueueBuffer(queue,cb);finishBuffer(cb);
}
static id<MTLCommandBuffer> counterReplay(BWQueue *queue,id<MTLCommandBuffer> preceding,
    NSDictionary *command,NSArray *commands,BWScratchRecord *initialScratch,NSArray *initialAddresses,BWReplay *state){
    BWAddressRecord *r=command[@"destination"];validateCounterAddress(r,queue.device);
    void *destination=(char*)r.pool.memory+r.offset;
    /* Check pending private writes before committing the segment that owns
     * them. Record this write in the same authority ledger as buffer copies. */
    textureBufferCopy(preceding,destination,destination,sizeof(NVNcounterData));
    texturePrepareWrite(destination,sizeof(NVNcounterData));
    dispatch_group_t publication=allGPU;dispatch_group_enter(publication);
    @try{
        counterCommitSegment(queue,preceding,commands,initialScratch,initialAddresses,state);
        MTLCounterSampleBufferDescriptor *descriptor=[MTLCounterSampleBufferDescriptor new];
        descriptor.counterSet=queue.device.timestampSet;descriptor.sampleCount=2;descriptor.storageMode=MTLStorageModeShared;
        NSError *error=nil;id<MTLCounterSampleBuffer> samples=[gpu newCounterSampleBufferWithDescriptor:descriptor error:&error];
        if(!samples)fatal("GPU timestamp sample-buffer allocation failed");
        id<MTLBuffer> scratch=[gpu newBufferWithLength:4 options:MTLResourceStorageModeShared];
        id<MTLCommandBuffer> marker=[queue.native commandBuffer];
        if(!scratch||!marker)fatal("GPU timestamp marker allocation failed");
        MTLTimestamp cpu0=0,gpu0=0,cpu1=0,gpu1=0;[gpu sampleTimestamps:&cpu0 gpuTimestamp:&gpu0];
        MTLBlitPassDescriptor *pass=[MTLBlitPassDescriptor blitPassDescriptor];
        pass.sampleBufferAttachments[0].sampleBuffer=samples;
        pass.sampleBufferAttachments[0].startOfEncoderSampleIndex=0;pass.sampleBufferAttachments[0].endOfEncoderSampleIndex=1;
        id<MTLBlitCommandEncoder> encoder=[marker blitCommandEncoderWithDescriptor:pass];if(!encoder)fatal("GPU timestamp marker encoder allocation failed");
        [encoder fillBuffer:scratch range:NSMakeRange(0,4) value:0];[encoder endEncoding];
        counterCommitSegment(queue,marker,commands,initialScratch,initialAddresses,state);
        if(marker.status!=MTLCommandBufferStatusCompleted)fatal("GPU timestamp marker did not complete successfully");
        [gpu sampleTimestamps:&cpu1 gpuTimestamp:&gpu1];
        NSData *resolved=[samples resolveCounterRange:NSMakeRange(0,2)];uint64_t ticks[2];
        if(resolved.length!=sizeof ticks)fatal("GPU timestamp resolve returned an invalid byte count");memcpy(ticks,resolved.bytes,sizeof ticks);
        if(ticks[0]==MTLCounterErrorValue||ticks[1]==MTLCounterErrorValue||ticks[1]<ticks[0])fatal("GPU timestamp sample is invalid or unordered");
        NVNcounterData result={0};uint64_t tick=[command[@"type"] intValue]==NVN_COUNTER_TYPE_TIMESTAMP_TOP?ticks[0]:ticks[1];
        if(!botw_counter_calibrate(tick,cpu0,gpu0,cpu1,gpu1,&result.timestamp))fatal("GPU timestamp calibration span is invalid");
        validateCounterAddress(r,queue.device);texturePrepareWrite(destination,sizeof result);
        textureWriteBytes(destination,&result,sizeof result,"counter destination checked publication failed");
        if(queue.device.timestampReports<8){queue.device.timestampReports++;
            fprintf(stderr,"[botw graphics] gpu_counter_report_completed=true type=%d gpu_ticks=%llu calibrated_ns=%llu raw_domain=calibrated_ns\n",
                    [command[@"type"] intValue],(unsigned long long)tick,(unsigned long long)result.timestamp);}
    }@finally{dispatch_group_leave(publication);}
    id<MTLCommandBuffer> next=[queue.native commandBuffer];if(!next)fatal("Metal command allocation after counter");return next;
}
