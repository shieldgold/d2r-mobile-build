/* Included by graphics.m under its serialized public ABI wrappers. */
static BOOL queuePartitionValid(size_t bytes){
    return bytes>=BW_QUEUE_ARENA_PAGE&&bytes<=BW_QUEUE_MAX_PARTITION_BYTES&&bytes%BW_QUEUE_ARENA_PAGE==0;
}
static int queueArenaLayout(const QueueBuilder *b,BWQueueArenaLayout *out){
    if(!queueFlagsSupported(b->flags)||!queuePartitionValid(b->command)||!queuePartitionValid(b->control)||b->compute)
        return report("CPU queue arena requires page-sized command/control budgets and zero compute");
    if(b->command>SIZE_MAX-BW_QUEUE_ARENA_PAGE||b->control>SIZE_MAX-BW_QUEUE_ARENA_PAGE-b->command)
        return report("CPU queue arena size overflow");
    *out=(BWQueueArenaLayout){BW_QUEUE_ARENA_PAGE+b->command+b->control,b->command,b->control};return 1;
}
NVN(nvnQueueBuilderSetCommandMemorySize,void,NVNqueueBuilder *raw,size_t bytes){
    if(!queuePartitionValid(bytes))fatal("CPU queue command budget must be page-aligned, 4096..16777216 bytes");
    ((QueueBuilder*)raw)->command=bytes;
}
NVN(nvnQueueBuilderSetControlMemorySize,void,NVNqueueBuilder *raw,size_t bytes){
    if(!queuePartitionValid(bytes))fatal("CPU queue control budget must be page-aligned, 4096..16777216 bytes");
    ((QueueBuilder*)raw)->control=bytes;
}
NVN(nvnQueueBuilderSetComputeMemorySize,void,NVNqueueBuilder *raw,size_t bytes){
    if(bytes)fatal("Compute queue commands are unsupported; compute budget must be zero");
    ((QueueBuilder*)raw)->compute=0;
}
NVN(nvnQueueBuilderGetQueueMemorySize,size_t,const NVNqueueBuilder *raw){
    if(!raw||(memoryAccessValidator&&!memoryAccessValidator(raw,sizeof(QueueBuilder),0))){report("CPU queue builder is not fully readable");return 0;}
    QueueBuilder snapshot;memcpy(&snapshot,raw,sizeof snapshot);const QueueBuilder *b=&snapshot;get(b->device,[BWDevice class]);
    BWQueueArenaLayout layout;return queueArenaLayout(b,&layout)?layout.totalBytes:0;
}
NVN(nvnQueueBuilderSetQueueMemory,void,NVNqueueBuilder *raw,void *memory,size_t bytes){
    if((!memory)!=(!bytes)||(memory&&((uintptr_t)memory%BW_QUEUE_ARENA_PAGE||bytes%BW_QUEUE_ARENA_PAGE||bytes>2u*BW_QUEUE_MAX_PARTITION_BYTES+BW_QUEUE_ARENA_PAGE||bytes>UINTPTR_MAX-(uintptr_t)memory)))
        fatal("CPU queue memory requires a bounded page-aligned address and size");
    QueueBuilder *b=(QueueBuilder*)raw;b->memory=memory;b->memoryBytes=bytes;
}
static BWQueueArenaHeader queueArenaHeader(BWQueue *queue){
    BWQueueArenaLayout layout=queue.arenaLayout;
    return (BWQueueArenaHeader){BW_QUEUE_ARENA_MAGIC,1,layout.totalBytes,layout.commandBytes,layout.controlBytes,
        queue.arenaIdentity,queue.preparedSubmissions,queue.commandUsed,queue.controlUsed};
}
static int configureQueueArena(BWQueue *queue,const QueueBuilder *b,void *raw,const void *builder){
    BWQueueArenaLayout layout;if(!queueArenaLayout(b,&layout))return 0;
    if((!b->memory)!=(!b->memoryBytes)||(b->memory&&((uintptr_t)b->memory%BW_QUEUE_ARENA_PAGE||b->memoryBytes%BW_QUEUE_ARENA_PAGE||b->memoryBytes>2u*BW_QUEUE_MAX_PARTITION_BYTES+BW_QUEUE_ARENA_PAGE||b->memoryBytes>UINTPTR_MAX-(uintptr_t)b->memory)))return report("CPU queue backing address/size is invalid");
    queue.arenaLayout=layout;queue.guestObject=raw;queue.arenaIdentity=++serial;
    if(b->memory){
        if(b->memoryBytes<layout.totalBytes)return report("CPU queue memory is smaller than its declared budgets");
        if(!memoryAccessValidator||!memoryAccessValidator(b->memory,b->memoryBytes,1))return report("CPU queue arena requires full read/write validation");
        if(memoryOverlap(b->memory,b->memoryBytes,raw,sizeof(NVNqueue))||memoryOverlap(b->memory,b->memoryBytes,builder,sizeof(NVNqueueBuilder))||overlapsQueueArena(b->memory,b->memoryBytes)||overlapsCommandMemory(b->memory,b->memoryBytes))return report("CPU queue arena overlaps live queue/object/recording storage");
        for(NSValue *value in objectSpans.allValues){NSRange span=value.rangeValue;if(memoryOverlap(b->memory,b->memoryBytes,(const void*)span.location,span.length))return report("CPU queue arena overlaps live NVN object storage");}
        for(BWPool *pool in pools)if(memoryOverlap(b->memory,b->memoryBytes,pool.memory,pool.size))return report("CPU queue arena overlaps GPU memory pool");
        queue.arena=b->memory;queue.arenaBytes=b->memoryBytes;queue.borrowedArena=YES;
    }else{
        void *allocation=NULL;if(posix_memalign(&allocation,BW_QUEUE_ARENA_PAGE,layout.totalBytes))return report("CPU queue arena allocation failed");
        /* NSMutableData may copy/free bytesNoCopy input. Keep the actual
         * page-aligned allocation directly; BWQueue dealloc frees only this. */
        queue.ownedArena=allocation;
        queue.arena=allocation;queue.arenaBytes=layout.totalBytes;
    }
    /* Everything that can reject the borrowed span has run before this write. */
    BWQueueArenaHeader header=queueArenaHeader(queue);memcpy(queue.arena,&header,sizeof header);return 1;
}
static NSArray<NSDictionary *> *queueSubmissionCommands(BWQueue *queue,int count,const NVNcommandHandle *input){
    BWQueueArenaLayout layout=queue.arenaLayout;
    if(count<0||(count&&!input)||(size_t)count>layout.commandBytes/sizeof(NVNcommandHandle))fatal("CPU queue command handle budget/count");
    size_t bytes=(size_t)count*sizeof(NVNcommandHandle);
    if(bytes&&memoryAccessValidator&&!memoryAccessValidator(input,bytes,0))fatal("CPU queue submit input is not fully readable");
    if(queue.borrowedArena&&(!memoryAccessValidator||!memoryAccessValidator(queue.arena,queue.arenaBytes,1)))fatal("CPU queue arena lost full read/write access");
    BWQueueArenaHeader actual,expected=queueArenaHeader(queue);memcpy(&actual,queue.arena,sizeof actual);
    if(memcmp(&actual,&expected,sizeof actual))fatal("CPU queue arena header was modified");
    /* Snapshot before writing: input may legitimately overlap either arena
     * partition. Retain every host command array before touching the arena. */
    NSData *source=[NSData dataWithBytes:input length:bytes];
    NSMutableArray<NSArray *> *captured=[NSMutableArray arrayWithCapacity:(NSUInteger)count];
    size_t records=0;
    for(int i=0;i<count;i++){
        NVNcommandHandle handle;memcpy(&handle,(const char*)source.bytes+(size_t)i*sizeof handle,sizeof handle);
        NSArray *commands=handles[@(handle)];if(!commands)fatal("invalid/finalized command handle before CPU arena write");
        if(commands.count>layout.controlBytes/sizeof(BWQueueReplayIndex)-records)fatal("CPU queue control replay budget exceeded");
        records+=commands.count;[captured addObject:commands];
    }
    validateScratchSubmission(queue,captured);validateAddressSubmission(queue,captured);
    if(queue.preparedSubmissions==UINT64_MAX)fatal("CPU queue preparation counter overflow");
    char *command=(char*)queue.arena+BW_QUEUE_ARENA_PAGE,*control=command+layout.commandBytes;
    if(bytes)memcpy(command,source.bytes,bytes);
    size_t cursor=0;
    for(NSUInteger h=0;h<captured.count;h++)for(NSUInteger c=0;c<captured[h].count;c++){
        BWQueueReplayIndex record={(uint32_t)h,(uint32_t)c,0};memcpy(control+cursor++*sizeof record,&record,sizeof record);
    }
    NSMutableArray<NSDictionary *> *ordered=[NSMutableArray arrayWithCapacity:records];cursor=0;
    /* Each untrusted record/handle is fetched once, checked against the
     * preflight sequence, then consumed from that local copy. No double fetch. */
    for(NSUInteger h=0;h<captured.count;h++){
        NVNcommandHandle staged,original;memcpy(&staged,command+h*sizeof staged,sizeof staged);memcpy(&original,(const char*)source.bytes+h*sizeof original,sizeof original);
        if(staged!=original)fatal("CPU queue arena handle was modified");
        NSArray *stagedCommands=handles[@(staged)];if(stagedCommands!=captured[h])fatal("CPU queue command lifetime changed");
        for(NSUInteger c=0;c<captured[h].count;c++){
            BWQueueReplayIndex record;memcpy(&record,control+cursor++*sizeof record,sizeof record);
            if(record.handleIndex!=h||record.commandIndex!=c||record.reserved)fatal("CPU queue arena replay index was modified");
            if(captured[record.handleIndex]!=stagedCommands)fatal("CPU queue staged handle/index mismatch");
            [ordered addObject:stagedCommands[record.commandIndex]];
        }
    }
    queue.preparedSubmissions++;queue.commandUsed=bytes;queue.controlUsed=records*sizeof(BWQueueReplayIndex);
    expected=queueArenaHeader(queue);memcpy(queue.arena,&expected,sizeof expected);return ordered;
}
