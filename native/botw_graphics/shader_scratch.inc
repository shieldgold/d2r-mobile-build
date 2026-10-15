/* Metal-owned per-invocation private storage replaces NVIDIA external scratch.
 * The NVN binding remains a real immutable, bounded resource-state command. */
static void validateScratchRecord(BWScratchRecord *r,BWDevice *device){
    if(!r||r.device!=device||![objects.allValues containsObject:device])fatal("scratch device mismatch or expired device");
    BWPool *pool=r.pool;
    if(!pool){if(r.offset||r.bytes)fatal("scratch range requires a pool");return;}
    if(!pool.live||pool.generation!=r.generation||objects[@(r.generation)]!=pool)fatal("stale scratch pool generation");
    if(pool.device!=device)fatal("scratch device mismatch");
    if(!range(r.offset,r.bytes,pool.size)||(!r.bytes&&r.offset)||r.offset%4096||r.bytes%131072)fatal("scratch range/compatibility alignment");
}
NVN(nvnCommandBufferSetShaderScratchMemory,void,NVNcommandBuffer *raw,const NVNmemoryPool *memory,ptrdiff_t offset,size_t bytes){
    BWCommands *cmd=commandState(raw,NO);if(!cmd.recording)fatal("scratch command outside recording");
    if(offset<0)fatal("scratch range has negative offset");
    BWScratchRecord *r=[BWScratchRecord new];r.device=cmd.device;r.pool=memory?get(memory,[BWPool class]):nil;r.generation=r.pool.generation;r.offset=(size_t)offset;r.bytes=bytes;
    validateScratchRecord(r,cmd.device);record(raw,@{@"op":@"scratch",@"record":r});
}
static void validateScratchSubmission(BWQueue *queue,NSArray<NSArray *> *groups){
    BWScratchRecord *scratch=queue.replay.scratch;BWProgram *program=queue.replay.program;
    for(NSArray *commands in groups)for(NSDictionary *c in commands){NSString *op=c[@"op"];
        if([op isEqual:@"scratch"]){scratch=c[@"record"];validateScratchRecord(scratch,queue.device);}
        else if([op isEqual:@"program"]){program=objects[c[@"id"]];}
        else if(scratch&&[op isEqual:@"draw"]&&[c[@"count"] unsignedLongLongValue]&&[c[@"instances"] unsignedLongLongValue]){
            validateScratchRecord(scratch,queue.device);
            if(![program isKindOfClass:[BWProgram class]])fatal("scratch draw without a program");
            for(BWShader *shader in program.stageShaders.allValues)if(!shader.metalPrivateScratchVerified)fatal("shader lacks independently validated Metal-private scratch lowering proof");
        }
    }
}
static char scratchRetentionKey;
static void retainScratchUntilCompleted(id<MTLCommandBuffer> cb,NSArray *commands,BWScratchRecord *initial,BWReplay *state){
    NSMutableSet *resources=[NSMutableSet new];if(initial.pool)[resources addObject:initial.pool];if(state.scratch.pool)[resources addObject:state.scratch.pool];
    for(NSDictionary *c in commands)if([c[@"op"] isEqual:@"scratch"]){BWScratchRecord *r=c[@"record"];if(r.pool)[resources addObject:r.pool];}
    if(!resources.count)return;
    objc_setAssociatedObject(cb,&scratchRetentionKey,resources.allObjects,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    [cb addCompletedHandler:^(id<MTLCommandBuffer> done){objc_setAssociatedObject(done,&scratchRetentionKey,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}];
}
