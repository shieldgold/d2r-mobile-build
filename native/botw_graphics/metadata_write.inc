/* A field write retires a qualified primary storage claim, not its value.
 * The caller supplies host-owned bytes produced by the complete POD module.
 * No old guest header is inspected to infer storage or resource lifetime. */
static void metadataWrite(void *destination,const void *source,size_t bytes){
    if(!destination||bytes>UINTPTR_MAX-(uintptr_t)destination||
       (memoryAccessValidator&&!memoryAccessValidator(destination,bytes,1)))
        fatal("buffer builder memory is not fully accessible");
    validateObjectStorage(destination,bytes);
    for(NSDictionary *record in textureMoves.allValues){
        NSData *snapshot=record[@"token"];if(snapshot.length!=sizeof(botw_graphics_texture_move_token))fatal("metadata write encountered invalid pending transfer metadata");
        const botw_graphics_texture_move_token *token=snapshot.bytes;
        if(memoryOverlap(destination,bytes,(void*)(uintptr_t)token->opaque[3],token->opaque[5])||
           memoryOverlap(destination,bytes,(void*)(uintptr_t)token->opaque[4],token->opaque[5]))
            fatal("metadata write overlaps pending texture storage transfer");
    }
    NSMutableArray<BWTexture*> *retired=[NSMutableArray new];
    NSMutableArray<NSNumber*> *keys=[NSMutableArray new];
    if(!retired||!keys)fatal("metadata write claim snapshot allocation failed");
    for(NSNumber *key in objectSpans){
        NSRange span=objectSpans[key].rangeValue;
        if(!memoryOverlap(destination,bytes,(void*)span.location,span.length))continue;
        id value=objects[key];BWTexture *texture=[value isKindOfClass:[BWTexture class]]?value:nil;
        if(!texture||texture.base||texture.identity!=key.unsignedLongLongValue||
           span.length!=sizeof(NVNtexture)||span.length>UINTPTR_MAX-span.location||!texture.storageMoveEpoch)
            bufferStorageOverlap(destination,bytes,key,span);
        if(texture.storageMoveEpoch==UINT64_MAX)fatal("metadata write texture storage epoch exhausted");
        textureLive(texture);[retired addObject:texture];[keys addObject:key];
    }
    /* Finish every allocation and private dictionary mutation before touching
     * output. Preparation may conservatively dirty pages even on failure. */
    NSMutableDictionary *next=nil;
    if(keys.count){next=[objectSpans mutableCopy];if(!next)fatal("metadata write claim clone allocation failed");
        for(NSNumber *key in keys)[next removeObjectForKey:key];}
    char error[256];
    if(textureTrackingAvailable&&nvn_dirty_prepare(destination,bytes,error,sizeof error))fatal(error);
    if(botw_protected_vm_write(destination,source,bytes)!=KERN_SUCCESS)
        fatal("buffer builder destination memory is not writable");
    /* Graphics serialization excludes registry changes during this fixed write.
     * Copy bytes are not a transaction under concurrent caller memory changes. */
    if(next){objectSpans=next;for(BWTexture *texture in retired)texture.storageMoveEpoch++;}
}
