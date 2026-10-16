/* Host-only storage transfer, entered only by the separately audited runtime
 * copy site. A registry value/resource is independent of its primary C0 slot.
 * This module never copies or reads that guest slot and never relaxes guards. */
#define BW_TEXTURE_MOVE_CAPACITY 64
#define BW_TEXTURE_MOVE_MAGIC UINT64_C(0x4257544d4f564531)
static NSMutableDictionary<NSNumber*,NSDictionary*> *textureMoves;
static uint64_t textureMoveLifecycle,textureMoveSequence;
static BOOL textureMoveReady;
static void textureTransferBeginLifecycle(void){
    textureMoves=[NSMutableDictionary new];textureMoveReady=textureMoveLifecycle!=UINT64_MAX;
    if(textureMoveReady)textureMoveLifecycle++;
}
static void textureTransferRequireQuiescent(void){if(textureMoves.count)fatal("texture storage transfer is pending; graphics resources retained");}
static void textureTransferEndLifecycle(void){textureMoves=nil;textureMoveReady=NO;}
static int textureMoveReject(char *error,size_t capacity,const char *message){if(error&&capacity)snprintf(error,capacity,"%s",message);return 0;}
static BOOL textureMoveDestination(const void *source,void *destination,size_t bytes,char *error,size_t capacity){
    if(!source||!destination||bytes!=sizeof(NVNtexture)||bytes>UINTPTR_MAX-(uintptr_t)source||bytes>UINTPTR_MAX-(uintptr_t)destination)
        return textureMoveReject(error,capacity,"texture storage transfer size/address is invalid");
    if(memoryOverlap(source,bytes,destination,bytes))return textureMoveReject(error,capacity,"texture storage transfer source/destination overlap");
    validateObjectStorage(destination,bytes);
    for(NSNumber *key in objectSpans){NSRange span=objectSpans[key].rangeValue;
        if(memoryOverlap(destination,bytes,(void*)span.location,span.length)){
            /* The conflicting range and value already belong to the host.
             * Do not inspect destination bytes to classify or retire them. */
            id existing=objects[key];BWTexture *texture=[existing isKindOfClass:[BWTexture class]]?existing:nil;
            BOOL exact=(uintptr_t)destination==span.location;uintptr_t offset=exact?0:(uintptr_t)destination>span.location?(uintptr_t)destination-span.location:span.location-(uintptr_t)destination;
            const char *primary=texture?(!texture.base&&texture.identity==key.unsignedLongLongValue?"true":"false"):"not_texture";
            char detail[384];snprintf(detail,sizeof detail,"texture storage transfer destination overlaps live NVN storage existing_class=%.48s span_bytes=%zu exact_base=%s exact_span=%s existing_primary=%s existing_move_epoch=%llu relative_offset=%c%zu",existing?class_getName([existing class]):"<none>",(size_t)span.length,exact?"true":"false",exact&&span.length==bytes?"true":"false",primary,(unsigned long long)texture.storageMoveEpoch,(uintptr_t)destination<span.location?'-':'+',(size_t)offset);
            return textureMoveReject(error,capacity,detail);
        }
    }
    return YES;
}
static int textureMovePreflight(const void *source,void *destination,size_t bytes,botw_graphics_texture_move_token *output,char *error,size_t capacity){
    if(!output)return textureMoveReject(error,capacity,"texture storage transfer token output is missing");
    if(!gpu||!textureMoveReady||!textureMoves)return textureMoveReject(error,capacity,"texture storage transfer lifecycle is unavailable");
    if(textureMoves.count>=BW_TEXTURE_MOVE_CAPACITY||textureMoveSequence==UINT64_MAX)return textureMoveReject(error,capacity,"texture storage transfer token capacity exhausted");
    if(!textureMoveDestination(source,destination,bytes,error,capacity))return 0;
    NSNumber *identity=nil;BWTexture *texture=nil;
    for(NSNumber *key in objectSpans){NSRange span=objectSpans[key].rangeValue;id object=objects[key];
        if(span.location!=(uintptr_t)source||span.length!=bytes||![object isKindOfClass:[BWTexture class]])continue;
        if(texture)return textureMoveReject(error,capacity,"texture storage transfer source is ambiguous");
        texture=object;identity=key;
    }
    if(!texture||texture.base||texture.identity!=identity.unsignedLongLongValue)return textureMoveReject(error,capacity,"texture storage transfer source is not an exact owned BWTexture primary span");
    textureLive(texture);
    if(texture.storageMoveEpoch==UINT64_MAX)return textureMoveReject(error,capacity,"texture storage transfer source epoch exhausted");
    uint64_t sequence=textureMoveSequence+1;
    botw_graphics_texture_move_token token={{sequence,textureMoveLifecycle,texture.identity,(uintptr_t)source,(uintptr_t)destination,bytes,texture.storageMoveEpoch,BW_TEXTURE_MOVE_MAGIC}};
    NSDictionary *record=@{@"token":[NSData dataWithBytes:&token length:sizeof token],@"texture":texture,@"owner":[NSValue valueWithPointer:pthread_self()]};
    @try{textureMoves[@(sequence)]=record;textureMoveSequence=sequence;*output=token;}
    @catch(NSException *exception){[textureMoves removeObjectForKey:@(sequence)];@throw exception;}
    return 1;
}
static NSDictionary *textureMoveRecord(const botw_graphics_texture_move_token *token,char *error,size_t capacity){
    if(!token||token->opaque[7]!=BW_TEXTURE_MOVE_MAGIC){textureMoveReject(error,capacity,"texture storage transfer token is invalid");return nil;}
    NSDictionary *record=textureMoves[@(token->opaque[0])];NSData *snapshot=record[@"token"];
    if(!record||snapshot.length!=sizeof *token||memcmp(snapshot.bytes,token,sizeof *token)||
       !pthread_equal((pthread_t)[record[@"owner"] pointerValue],pthread_self())){
        textureMoveReject(error,capacity,"texture storage transfer token is unknown, consumed or belongs to another thread");return nil;
    }
    return record;
}
static int textureMoveCommit(const botw_graphics_texture_move_token *token,char *error,size_t capacity){
    NSDictionary *record=textureMoveRecord(token,error,capacity);if(!record)return 0;
    /* Consume even on a later failure. Strong local record pins the resource
     * until this checked call exits; original copy bytes are not rolled back. */
    [textureMoves removeObjectForKey:@(token->opaque[0])];
    BWTexture *texture=record[@"texture"];NSNumber *key=@(token->opaque[2]);NSValue *value=objectSpans[key];NSRange span=value.rangeValue;
    if(!textureMoveReady||textureMoveLifecycle!=token->opaque[1]||objects[key]!=texture||!value||
       span.location!=token->opaque[3]||span.length!=token->opaque[5]||texture.storageMoveEpoch!=token->opaque[6])
        return textureMoveReject(error,capacity,"texture storage transfer lifecycle/source/span epoch changed");
    textureLive(texture);
    if(!textureMoveDestination((void*)(uintptr_t)token->opaque[3],(void*)(uintptr_t)token->opaque[4],token->opaque[5],error,capacity))return 0;
    NSValue *replacement=[NSValue valueWithRange:NSMakeRange(token->opaque[4],token->opaque[5])];
    objectSpans[key]=replacement;texture.storageMoveEpoch++;return 1;
}
static int textureMoveAbort(const botw_graphics_texture_move_token *token,char *error,size_t capacity){
    if(!textureMoveRecord(token,error,capacity))return 0;[textureMoves removeObjectForKey:@(token->opaque[0])];return 1;
}
/* Return-only host boundary. Never clear an outer NVN transaction's flags or
 * invoke its callback, and never leave the serialization lock across memcpy. */
#define BW_TEXTURE_MOVE_CHECKED(Name,Parameters,Operation) \
 int Name Parameters { \
    if(graphicsDepth)return textureMoveReject(error,capacity,"texture storage transfer requires an outermost host call"); \
    graphicsBegin();const char *previous=currentEntry;currentEntry=#Name;int result=0;if(error&&capacity)error[0]=0; \
    @autoreleasepool{@try{result=(Operation);} \
      @catch(BWGraphicsFailure *failure){(void)failure;textureMoveReject(error,capacity,lastError);} \
      @catch(NSException *exception){textureMoveReject(error,capacity,exception.reason.UTF8String?:"native texture storage transfer exception");}} \
    pendingError=NO;pendingFatal=NO;currentEntry=previous;graphicsEnd();return result; \
 }
BW_TEXTURE_MOVE_CHECKED(botw_graphics_texture_move_preflight,(const void *source,void *destination,size_t bytes,botw_graphics_texture_move_token *token,char *error,size_t capacity),textureMovePreflight(source,destination,bytes,token,error,capacity))
BW_TEXTURE_MOVE_CHECKED(botw_graphics_texture_move_commit,(const botw_graphics_texture_move_token *token,char *error,size_t capacity),textureMoveCommit(token,error,capacity))
BW_TEXTURE_MOVE_CHECKED(botw_graphics_texture_move_abort,(const botw_graphics_texture_move_token *token,char *error,size_t capacity),textureMoveAbort(token,error,capacity))
#undef BW_TEXTURE_MOVE_CHECKED
