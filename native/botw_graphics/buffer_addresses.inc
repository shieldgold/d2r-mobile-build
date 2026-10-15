/* Every recorded GPU address captures its live pool generation. Numeric addresses
 * remain NVN-compatible CPU aliases; they never silently resolve to a new pool. */
static BWAddressRecord *captureBufferAddress(NVNcommandBuffer *raw,uint64_t addr,size_t bytes,const char *failure){
    BWCommands *cmd=commandState(raw,NO);size_t offset=0;BWPool *pool=address(addr,bytes,&offset);
    if(!pool||!pool.live||pool.device!=cmd.device||pool.flags&NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS)fatal(failure);
    BWAddressRecord *r=[BWAddressRecord new];r.pool=pool;r.device=cmd.device;r.offset=offset;r.bytes=bytes;r.generation=pool.generation;return r;
}
static void validateBufferAddress(BWAddressRecord *r,BWDevice *device){
    if(!r||!r.pool.live||r.pool.generation!=r.generation||objects[@(r.generation)]!=r.pool)fatal("stale buffer address pool generation");
    if(r.device!=device||r.pool.device!=device||![objects.allValues containsObject:device])fatal("buffer address device mismatch");
    nvn_texown_hit hit;if(nvn_texown_find((char*)r.pool.memory+r.offset,r.bytes,&hit))fatal("buffer address aliases private texture storage authority");
    if(!r.bytes||!r.pool.buffer||!range(r.offset,r.bytes,r.pool.size)||r.pool.flags&NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS)fatal("buffer address range/backing is invalid");
}
static void validateAddressSubmission(BWQueue *queue,NSArray<NSArray *> *groups){
    NSMutableDictionary *vertices=[queue.replay.vertices mutableCopy],*uniforms=[queue.replay.uniforms mutableCopy],*storage=[queue.replay.storage mutableCopy];
    BWProgram *program=queue.replay.program;
    for(NSArray *commands in groups)for(NSDictionary *c in commands){NSString *op=c[@"op"];
        if([op isEqual:@"copy"]){validateBufferAddress(c[@"source"],queue.device);validateBufferAddress(c[@"destination"],queue.device);}
        else if([op isEqual:@"vertex"]){vertices[c[@"slot"]]=c;}
        else if([op isEqual:@"uniform"]){uniforms[resourceKey(c[@"stage"],c[@"slot"])]=c;}
        else if([op isEqual:@"storage"]){storage[resourceKey(c[@"stage"],c[@"slot"])]=c;}
        else if([op isEqual:@"program"]){program=objects[c[@"id"]];}
        else if([op isEqual:@"draw"]&&[c[@"count"] unsignedLongLongValue]&&[c[@"instances"] unsignedLongLongValue]){
            for(NSDictionary *r in vertices.allValues)if(r[@"resource"])validateBufferAddress(r[@"resource"],queue.device);
            /* Unused NVN slots are not consumed by the encoder. Match the
             * actual registered shader binding map rather than every past bind. */
            for(NSNumber *stage in program.stageShaders){BWShader *shader=program.stageShaders[stage];
                const botw_graphics_shader_binding *binding=shader.bindings.bytes;
                for(size_t i=0;i<shader.bindings.length/sizeof *binding;i++){
                    BOOL isStorage=binding[i].kind==BOTW_GRAPHICS_STORAGE||binding[i].kind==BOTW_GRAPHICS_STORAGE_ADDRESS_LOW;
                    if(!isStorage&&binding[i].kind!=BOTW_GRAPHICS_UNIFORM)continue;
                    NSDictionary *r=(isStorage?storage:uniforms)[resourceKey(stage,@(binding[i].nvn_slot))];
                    if(r[@"resource"])validateBufferAddress(r[@"resource"],queue.device);
                }
            }
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
