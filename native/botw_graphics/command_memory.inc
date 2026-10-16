/* Included under the typed, serialized public ABI wrappers. */
static NSArray<BWCommandMemory *> *commandMemorySpans(BWCommands *cmd){
    NSMutableSet *spans=[NSMutableSet new];if(cmd.commandMemory)[spans addObject:cmd.commandMemory];if(cmd.controlMemory)[spans addObject:cmd.controlMemory];
    for(BWRecordedCommand *r in cmd.records){[spans addObject:r.command];[spans addObject:r.control];}
    for(BWRecordedLink *r in cmd.links){[spans addObject:r.previous];[spans addObject:r.next];}return spans.allObjects;
}
static BOOL overlapsCommandMemory(const void *memory,size_t bytes){
    for(id value in objects.allValues)if([value isKindOfClass:[BWCommands class]])
        for(BWCommandMemory *span in commandMemorySpans(value))if(memoryOverlap(memory,bytes,span.memory,span.size))return YES;
    return NO;
}
static BWCommands *commandState(const NVNcommandBuffer *raw,BOOL refill){
    BWCommands *cmd=get(raw,[BWCommands class]);
    if(cmd.callbackActive&&(!refill||!pthread_equal(cmd.callbackOwner,pthread_self())))fatal("command buffer is busy in a memory callback");return cmd;
}
static void recordingMemoryAccess(BWCommandMemory *span){
    if(!span||!span.memory||span.used>span.size)fatal("invalid CPU recording memory state");
    if(span.pool&&(!poolHostLive(span.pool)||span.pool.retiring))fatal("stale CPU command memory pool");
    if(!span.owned&&(!memoryAccessValidator||!memoryAccessValidator(span.memory,span.size,1)))fatal("CPU recording memory requires full read/write access");
}
static BOOL spanReferenced(BWCommands *cmd,BWCommandMemory *span){
    for(BWRecordedCommand *r in cmd.records)if(r.command==span||r.control==span)return YES;
    for(BWRecordedLink *r in cmd.links)if(r.previous==span||r.next==span)return YES;return NO;
}
static void validateRecordingSpan(BWCommands *cmd,BWCommandMemory *span,BOOL control){
    size_t minimum=control?BW_MIN_CONTROL_MEMORY:BW_MIN_COMMAND_MEMORY,alignment=control?BW_CONTROL_MEMORY_ALIGNMENT:BW_COMMAND_MEMORY_ALIGNMENT;
    if(!span.memory||span.size<minimum||span.size>BW_MAX_RECORDING_MEMORY||(uintptr_t)span.memory%alignment||span.size%alignment||span.size>UINTPTR_MAX-(uintptr_t)span.memory)fatal("CPU recording memory size/alignment/range");
    recordingMemoryAccess(span);
    if(overlapsQueueArena(span.memory,span.size))fatal("CPU recording memory overlaps queue arena");
    for(NSValue *value in objectSpans.allValues){NSRange r=value.rangeValue;if(memoryOverlap(span.memory,span.size,(const void*)r.location,r.length))fatal("CPU recording memory overlaps live NVN object storage");}
    size_t retained=span.size,count=1;
    BWCommandMemory *replace=control?cmd.controlMemory:cmd.commandMemory;
    for(id value in objects.allValues)if([value isKindOfClass:[BWCommands class]]){
        for(BWCommandMemory *other in commandMemorySpans(value)){
            if(value==cmd&&other==replace&&!spanReferenced(cmd,other))continue;
            if(memoryOverlap(span.memory,span.size,other.memory,other.size))fatal("CPU recording memory overlaps a live recording span");
            if(value==cmd){if(retained>2u*BW_MAX_RECORDING_MEMORY-other.size)fatal("CPU recording retained memory budget exceeded");retained+=other.size;count++;}
        }
    }
    if(count>BW_MAX_RECORDING_SPANS)fatal("CPU recording span count exceeded");
    if(control)for(BWPool *pool in pools)if(pool.kind!=BW_POOL_VIRTUAL&&pool.memory&&memoryOverlap(span.memory,span.size,pool.memory,pool.size))fatal("CPU control memory overlaps GPU memory pool");
}
static void adoptRecordingSpan(BWCommands *cmd,BWCommandMemory *span,BOOL control){
    validateRecordingSpan(cmd,span,control);BWCommandMemory *old=cmd.controlMemory;
    if(control&&cmd.recording&&old&&cmd.records.count){
        recordingMemoryAccess(old);if(!range(old.used,sizeof(BWRecordingLink),old.size))fatal("CPU control link reserve was exhausted");
        BWRecordedLink *link=[BWRecordedLink new];link.previous=old;link.next=span;link.offset=old.used;
        BWRecordingLink pod={BW_RECORDING_LINK_MAGIC,(uintptr_t)span.memory};memcpy((char*)old.memory+old.used,&pod,sizeof pod);old.used+=sizeof pod;[cmd.links addObject:link];
    }
    if(control)cmd.controlMemory=span;else cmd.commandMemory=span;
}
static void invokeRecordingCallback(NVNcommandBuffer *raw,BWCommands *cmd,NVNcommandBufferMemoryEvent event,size_t minimum){
    botw_graphics_command_memory_invoker invoke=commandMemoryInvoker;PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC callback=cmd.callback;
    if(!callback)fatal("CPU recording memory exhausted without a memory callback");if(!invoke)fatal("CPU recording memory callback requires a protected host invoker");
    uint64_t generation=cmd.generation;void *data=cmd.callbackData;char message[512]={0};int success=0;
    unsigned depth=graphicsDepth;BOOL oldError=pendingError,oldFatal=pendingFatal;const char *entry=currentEntry;
    const char *observationOuter=graphicsObservationOuter,*observationActive=graphicsObservationActive;
    cmd.callbackActive=YES;cmd.callbackOwner=pthread_self();graphicsDepth=0;pendingError=NO;pendingFatal=NO;currentEntry=NULL;
    graphicsObservationReleased();graphicsObservationOuter=graphicsObservationActive=NULL;pthread_mutex_unlock(&graphicsLock);
    /* The host adapter contains every guest escape. @finally additionally
     * restores the lock if a native adapter throws an Objective-C exception. */
    @try{success=invoke(callback,raw,event,minimum,data,message,sizeof message);}
    @finally{pthread_mutex_lock(&graphicsLock);graphicsDepth=depth;pendingError=oldError;pendingFatal=oldFatal;currentEntry=entry;cmd.callbackActive=NO;graphicsObservationOuter=observationOuter;graphicsObservationActive=observationActive;graphicsObservationAcquired();}
    message[sizeof message-1]=0;
    if(!success){char detail[640];snprintf(detail,sizeof detail,"protected command memory callback failed: %s",message[0]?message:"host did not provide a diagnostic");fatal(detail);}
    if(commandState(raw,NO)!=cmd||!cmd.recording||cmd.generation!=generation)fatal("command buffer identity/generation changed during memory callback");
}
static BWCommandMemory *requireRecordingMemory(NVNcommandBuffer *raw,BWCommands *cmd,BOOL control,size_t bytes){
    size_t reserve=control?BW_CONTROL_LINK_RESERVE:0;BWCommandMemory *span=control?cmd.controlMemory:cmd.commandMemory;
    if(span&&span.used<=span.size&&bytes<=span.size-span.used&&reserve<=span.size-span.used-bytes){recordingMemoryAccess(span);return span;}
    if(!cmd.externalMemory&&!cmd.callback){
        span=[BWCommandMemory new];void *memory=NULL;if(posix_memalign(&memory,8,BW_DEFAULT_RECORDING_MEMORY))fatal("CPU recording host memory allocation failed");
        span.memory=memory;span.owned=memory;span.size=BW_DEFAULT_RECORDING_MEMORY;adoptRecordingSpan(cmd,span,control);return span;
    }
    size_t minimum=control?MAX(bytes+reserve,(size_t)BW_MIN_CONTROL_MEMORY):MAX(bytes,(size_t)BW_MIN_COMMAND_MEMORY);
    invokeRecordingCallback(raw,cmd,control?NVN_COMMAND_BUFFER_MEMORY_EVENT_OUT_OF_CONTROL_MEMORY:NVN_COMMAND_BUFFER_MEMORY_EVENT_OUT_OF_COMMAND_MEMORY,minimum);
    span=control?cmd.controlMemory:cmd.commandMemory;
    if(!span||span.used>span.size||bytes>span.size-span.used||reserve>span.size-span.used-bytes)fatal("memory callback did not provide enough CPU recording memory");
    recordingMemoryAccess(span);return span;
}
NVN(nvnCommandBufferInitialize,NVNboolean,NVNcommandBuffer *raw,NVNdevice *device){BWCommands *cmd=[BWCommands new];cmd.device=get(device,[BWDevice class]);put(raw,cmd);return 1;}
NVN(nvnCommandBufferFinalize,void,NVNcommandBuffer *raw){BWCommands *cmd=commandState(raw,NO);if(cmd.recording)fatal("finalize recording command buffer");removeObject(raw);}
NVN(nvnCommandBufferSetMemoryCallback,void,NVNcommandBuffer *raw,PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC callback){commandState(raw,NO).callback=callback;}
NVN(nvnCommandBufferSetMemoryCallbackData,void,NVNcommandBuffer *raw,void *data){commandState(raw,NO).callbackData=data;}
NVN(nvnCommandBufferGetMemoryCallback,PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC,const NVNcommandBuffer *raw){return commandState(raw,YES).callback;}
NVN(nvnCommandBufferGetMemoryCallbackData,void*,const NVNcommandBuffer *raw){return commandState(raw,YES).callbackData;}
NVN(nvnCommandBufferAddCommandMemory,void,NVNcommandBuffer *raw,const NVNmemoryPool *memory,ptrdiff_t offset,size_t bytes){
    BWCommands *cmd=commandState(raw,YES);BWPool *pool=get(memory,[BWPool class]);
    if(pool.kind!=BW_POOL_ORDINARY)fatal("CPU command memory requires ordinary pool");
    if(offset<0||!range((size_t)offset,bytes,pool.size))fatal("CPU command memory pool bounds");
    const char *failure=NULL;if(!poolEnsureOrdinaryBacking(pool,&failure))fatal(failure);
    if(!pool.buffer)fatal("CPU command memory pool has no materialized Metal backing");
    BWCommandMemory *span=[BWCommandMemory new];span.memory=(char*)pool.memory+offset;span.size=bytes;span.pool=pool;adoptRecordingSpan(cmd,span,NO);cmd.externalMemory=YES;
}
NVN(nvnCommandBufferAddControlMemory,void,NVNcommandBuffer *raw,void *memory,size_t bytes){
    BWCommands *cmd=commandState(raw,YES);BWCommandMemory *span=[BWCommandMemory new];span.memory=memory;span.size=bytes;adoptRecordingSpan(cmd,span,YES);cmd.externalMemory=YES;
}
NVN(nvnCommandBufferGetCommandMemorySize,size_t,const NVNcommandBuffer *raw){return commandState(raw,YES).commandMemory.size;}
NVN(nvnCommandBufferGetCommandMemoryUsed,size_t,const NVNcommandBuffer *raw){return commandState(raw,YES).commandMemory.used;}
NVN(nvnCommandBufferGetCommandMemoryFree,size_t,const NVNcommandBuffer *raw){BWCommandMemory *s=commandState(raw,YES).commandMemory;return s.size-s.used;}
NVN(nvnCommandBufferGetControlMemorySize,size_t,const NVNcommandBuffer *raw){return commandState(raw,YES).controlMemory.size;}
NVN(nvnCommandBufferGetControlMemoryUsed,size_t,const NVNcommandBuffer *raw){return commandState(raw,YES).controlMemory.used;}
NVN(nvnCommandBufferGetControlMemoryFree,size_t,const NVNcommandBuffer *raw){BWCommandMemory *s=commandState(raw,YES).controlMemory;return s.size-s.used;}
NVN(nvnCommandBufferBeginRecording,void,NVNcommandBuffer *raw){
    BWCommands *cmd=commandState(raw,NO);if(cmd.recording)fatal("nested recording");
    cmd.commands=[NSMutableArray new];cmd.records=[NSMutableArray new];cmd.links=[NSMutableArray new];cmd.generation=++serial;cmd.recording=YES;
}
static void record(NVNcommandBuffer *raw,NSDictionary *command){
    BWCommands *cmd=commandState(raw,NO);if(!cmd.recording)fatal("command outside recording");
    if(cmd.commands.count>=BW_MAX_RECORDING_RECORDS)fatal("CPU recording command count exceeded");
    (void)requireRecordingMemory(raw,cmd,NO,sizeof(BWRecordingToken));(void)requireRecordingMemory(raw,cmd,YES,sizeof(BWRecordingToken));
    BWCommandMemory *commandSpan=cmd.commandMemory,*controlSpan=cmd.controlMemory;
    recordingMemoryAccess(commandSpan);recordingMemoryAccess(controlSpan);
    if(!range(commandSpan.used,sizeof(BWRecordingToken),commandSpan.size)||!range(controlSpan.used,sizeof(BWRecordingToken)+BW_CONTROL_LINK_RESERVE,controlSpan.size))fatal("CPU recording capacity changed during refill");
    BWRecordingToken token={cmd.generation,(uint32_t)cmd.commands.count,0};BWRecordedCommand *r=[BWRecordedCommand new];r.command=commandSpan;r.control=controlSpan;r.commandOffset=commandSpan.used;r.controlOffset=controlSpan.used;
    memcpy((char*)commandSpan.memory+commandSpan.used,&token,sizeof token);memcpy((char*)controlSpan.memory+controlSpan.used,&token,sizeof token);
    commandSpan.used+=sizeof token;controlSpan.used+=sizeof token;[cmd.commands addObject:command];[cmd.records addObject:r];
}
static NSData *recordingSnapshot(BWCommandMemory *span,NSMutableDictionary<NSValue *,NSData *> *snapshots){return snapshots[[NSValue valueWithPointer:(__bridge const void*)span]];}
NVN(nvnCommandBufferEndRecording,NVNcommandHandle,NVNcommandBuffer *raw){
    BWCommands *cmd=commandState(raw,NO);if(!cmd.recording)fatal("end without recording");
    BWCommandMemory *control=requireRecordingMemory(raw,cmd,YES,sizeof(uint64_t));
    NSArray *spans=commandMemorySpans(cmd);for(BWCommandMemory *span in spans)recordingMemoryAccess(span);
    NSMutableDictionary<NSValue *,NSData *> *snapshots=[NSMutableDictionary new];
    for(BWCommandMemory *span in spans)snapshots[[NSValue valueWithPointer:(__bridge const void*)span]]=[NSData dataWithBytes:span.memory length:span.used];
    NSMutableArray *commands=[NSMutableArray arrayWithCapacity:cmd.commands.count];
    for(NSUInteger i=0;i<cmd.records.count;i++){
        BWRecordedCommand *r=cmd.records[i];BWRecordingToken a,b,expected={cmd.generation,(uint32_t)i,0};
        NSData *command=recordingSnapshot(r.command,snapshots),*indices=recordingSnapshot(r.control,snapshots);
        if(!range(r.commandOffset,sizeof a,command.length)||!range(r.controlOffset,sizeof b,indices.length))fatal("CPU recording token bounds");
        memcpy(&a,(const char*)command.bytes+r.commandOffset,sizeof a);memcpy(&b,(const char*)indices.bytes+r.controlOffset,sizeof b);
        if(memcmp(&a,&expected,sizeof a)||memcmp(&b,&expected,sizeof b)||a.index!=b.index)fatal("CPU recording command/control token was modified");
        [commands addObject:cmd.commands[b.index]];
    }
    for(BWRecordedLink *r in cmd.links){BWRecordingLink actual,expected={BW_RECORDING_LINK_MAGIC,(uintptr_t)r.next.memory};NSData *snapshot=recordingSnapshot(r.previous,snapshots);
        if(!range(r.offset,sizeof actual,snapshot.length))fatal("CPU control link bounds");memcpy(&actual,(const char*)snapshot.bytes+r.offset,sizeof actual);if(memcmp(&actual,&expected,sizeof actual))fatal("CPU control link was modified");}
    /* Reject modified records before writing the end marker or committing
     * current-span usage. Replay consumes only the validated local snapshots. */
    uint64_t end=BW_RECORDING_END_MAGIC^cmd.generation,actual;size_t endOffset=control.used;
    memcpy((char*)control.memory+endOffset,&end,sizeof end);memcpy(&actual,(char*)control.memory+endOffset,sizeof actual);if(actual!=end)fatal("CPU recording end token was modified");control.used+=sizeof end;
    cmd.recording=NO;uint64_t handle=++serial;handles[@(handle)]=commands;cmd.commands=nil;cmd.records=nil;cmd.links=nil;return handle;
}
