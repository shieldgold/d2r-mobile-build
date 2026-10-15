/* Every recorded GPU address captures its live pool generation. Numeric addresses
 * remain NVN-compatible CPU aliases; they never silently resolve to a new pool. */
static BWAddressRecord *captureBufferAddress(NVNcommandBuffer *raw,uint64_t addr,size_t bytes,const char *failure){
    BWCommands *cmd=commandState(raw,NO);size_t offset=0;BWPool *pool=address(addr,bytes,&offset);
    if(!pool||!pool.live||pool.device!=cmd.device||pool.flags&NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS)fatal(failure);
    BWAddressRecord *r=[BWAddressRecord new];r.pool=pool;r.device=cmd.device;r.offset=offset;r.bytes=bytes;r.generation=pool.generation;return r;
}
static BWPoolRange *validateBufferAddress(BWAddressRecord *r,BWDevice *device){
    if(!r||!r.pool.live||r.pool.generation!=r.generation||objects[@(r.generation)]!=r.pool)fatal("stale buffer address pool generation");
    if(r.device!=device||r.pool.device!=device||![objects.allValues containsObject:device])fatal("buffer address device mismatch");
    if(!r.bytes||!range(r.offset,r.bytes,r.pool.size)||r.pool.flags&NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS)fatal("buffer address range/backing is invalid");
    BWPoolRange *mapping=poolResolve(r.pool,r.offset,r.bytes,0,"buffer address current mapping");
    textureCheckBufferAuthority(mapping.canonical,mapping.length);return mapping;
}
static void addressTrackWrite(NSMutableArray<BWPoolRange*> *writes,BWPoolRange *write){
    /* Translations remain bound until their program is retired/rebound. Reject
     * every GPU write to active code, including a batch's final operation. */
    for(id object in objects.allValues)if([object isKindOfClass:[BWProgram class]])for(NSDictionary *source in ((BWProgram*)object).codeSources){BWPoolRange *code=source[@"range"];
        if(memoryOverlap(code.canonical,code.length,write.canonical,write.length))fatal("GPU write aliases active program code; retire program before modifying code");}
    [writes addObject:write];
}
static void addressTrackTargetWrite(BWTexture *selected,NSMutableArray<BWPoolRange*> *writes){
    if(!selected)return;textureEnsureMapping(selected,"code/write intent target preflight");BWTexture *base=selected.base?:selected;
    if(!base.blockLinear)poolPrepareGPUWrite(base.mapping);addressTrackWrite(writes,base.mapping);
}
static void validateAddressSubmission(BWQueue *queue,NSArray<NSArray *> *groups){
    NSMutableDictionary *vertices=[queue.replay.vertices mutableCopy],*uniforms=[queue.replay.uniforms mutableCopy],*storage=[queue.replay.storage mutableCopy];
    BWProgram *program=queue.replay.program;BWTexture *target=queue.replay.target,*depth=queue.replay.depth;NSMutableArray<BWPoolRange*> *pendingWrites=[NSMutableArray new];
    for(NSArray *commands in groups)for(NSDictionary *c in commands){NSString *op=c[@"op"];
        if([op isEqual:@"copy"]){BWPoolRange *src=validateBufferAddress(c[@"source"],queue.device),*dst=validateBufferAddress(c[@"destination"],queue.device);
            if(memoryOverlap(src.canonical,src.length,dst.canonical,dst.length))fatal("GPU copy bounds/overlap");poolPrepareGPUWrite(dst);addressTrackWrite(pendingWrites,dst);}
        else if([op isEqual:@"counter"]){BWAddressRecord *r=c[@"destination"];validateCounterAddress(r,queue.device);BWPoolRange *dst=validateBufferAddress(r,queue.device);poolPrepareGPUWrite(dst);addressTrackWrite(pendingWrites,dst);}
        else if([op isEqual:@"target"]){target=c[@"selected"];depth=c[@"depthSelected"];}
        else if([op isEqual:@"vertex"]){vertices[c[@"slot"]]=c;}
        else if([op isEqual:@"uniform"]){uniforms[resourceKey(c[@"stage"],c[@"slot"])]=c;}
        else if([op isEqual:@"storage"]){storage[resourceKey(c[@"stage"],c[@"slot"])]=c;}
        else if([op isEqual:@"program"]){program=objects[c[@"id"]];}
        else if([op isEqual:@"clear"]||[op isEqual:@"depthClear"]||([op isEqual:@"draw"]&&[c[@"count"] unsignedLongLongValue]&&[c[@"instances"] unsignedLongLongValue])){
            BOOL draw=[op isEqual:@"draw"];if(draw){validateProgramSources(program,queue.device);
                for(NSDictionary *r in vertices.allValues)if(r[@"resource"])validateBufferAddress(r[@"resource"],queue.device);
                for(NSNumber *stage in program.stageShaders){BWShader *shader=program.stageShaders[stage];const botw_graphics_shader_binding *binding=shader.bindings.bytes;
                    for(size_t i=0;i<shader.bindings.length/sizeof *binding;i++){
                        BOOL isStorage=binding[i].kind==BOTW_GRAPHICS_STORAGE||binding[i].kind==BOTW_GRAPHICS_STORAGE_ADDRESS_LOW;
                        if(!isStorage&&binding[i].kind!=BOTW_GRAPHICS_UNIFORM)continue;NSDictionary *r=(isStorage?storage:uniforms)[resourceKey(stage,@(binding[i].nvn_slot))];
                        if(r[@"resource"]){BWPoolRange *mapping=validateBufferAddress(r[@"resource"],queue.device);if(binding[i].kind==BOTW_GRAPHICS_STORAGE){poolPrepareGPUWrite(mapping);addressTrackWrite(pendingWrites,mapping);}}
                    }
                }
            }
            /* Include the current draw's complete write intent before comparing
             * active code, including attachments and same-draw shader stores. */
            addressTrackTargetWrite(target,pendingWrites);addressTrackTargetWrite(depth,pendingWrites);
            if(draw)for(NSDictionary *source in program.codeSources){BWPoolRange *code=source[@"range"];
                for(BWPoolRange *write in pendingWrites)if(memoryOverlap(code.canonical,code.length,write.canonical,write.length))fatal("program code aliases current/earlier submission write intent");}
        }
    }
}

static NSArray *addressReplayResources(BWReplay *state){
    NSMutableArray *resources=[NSMutableArray new];
    for(NSDictionary *r in state.vertices.allValues)if(r[@"resource"])[resources addObject:r[@"resource"]];
    for(NSDictionary *r in state.uniforms.allValues)if(r[@"resource"])[resources addObject:r[@"resource"]];
    for(NSDictionary *r in state.storage.allValues)if(r[@"resource"])[resources addObject:r[@"resource"]];
    return resources;
}
static char addressRetentionKey;
static void retainAddressesUntilCompleted(id<MTLCommandBuffer> cb,NSArray *commands,NSArray *initial,BWReplay *state){
    NSMutableArray *resources=[initial mutableCopy];[resources addObjectsFromArray:addressReplayResources(state)];
    for(NSDictionary *c in commands)for(NSString *key in @[@"resource",@"source",@"destination"])if(c[key])[resources addObject:c[key]];
    if(!resources.count)return;objc_setAssociatedObject(cb,&addressRetentionKey,resources,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    [cb addCompletedHandler:^(id<MTLCommandBuffer> done){objc_setAssociatedObject(done,&addressRetentionKey,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}];
}
