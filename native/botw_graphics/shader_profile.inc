/* Included by shader_bundle.m. Conditional profiles are recomputed from the
 * actual MSL and original c1 bytes; manifest claims never authorize a range. */
static BOOL storageDescriptorSlot(NSDictionary *entry,NVNshaderStage stage,NSUInteger index,unsigned *out,char *error,size_t capacity){
    id profile=entry[@"draw_profile"];uint64_t schema,slot,vertices,instances;
    if(stage!=NVN_SHADER_STAGE_VERTEX||![profile isKindOfClass:[NSDictionary class]]||!integer(profile[@"schema"],1,&schema)||schema!=1||!integer(profile[@"storage_slot"],15,&slot)||!integer(profile[@"relative_vertex_inputs"],64,&vertices)||vertices!=64||!integer(profile[@"relative_instance_inputs"],64,&instances)||instances!=64)return bundleError(error,capacity,"Missing finite readonly storage profile");
    id descriptors=profile[@"storage_descriptors"];if(![descriptors isKindOfClass:[NSArray class]]||![descriptors count]||[descriptors count]>16)return bundleError(error,capacity,"Invalid compiler storage descriptors");
    NSMutableSet *seen=[NSMutableSet new];BOOL found=NO;
    for(id row in descriptors){uint64_t ordinal,bank,offset,count;
        if(![row isKindOfClass:[NSDictionary class]]||!integer(row[@"descriptor_index"],15,&ordinal)||!integer(row[@"cbuf_index"],0,&bank)||!integer(row[@"cbuf_offset"],0x200,&offset)||!integer(row[@"count"],1,&count)||count!=1||![row[@"is_written"] isKindOfClass:[NSNumber class]]||CFGetTypeID((__bridge CFTypeRef)row[@"is_written"])!=CFBooleanGetTypeID()||[row[@"is_written"] boolValue]||[seen containsObject:@(ordinal)]||offset!=0x110+slot*16||!reflectedSlot(entry[@"reflection"],@"storage",(unsigned)slot))return bundleError(error,capacity,"Storage descriptor table/reflection mismatch");
        [seen addObject:@(ordinal)];if(ordinal==index)found=YES;
    }
    if(!found)return bundleError(error,capacity,"Missing compiler storage descriptor index");*out=(unsigned)slot;return YES;
}
static NSArray<NSDictionary*> *arrayReads(NSString *source,NSString *pattern){
    NSMutableArray *result=[NSMutableArray new];
    for(NSTextCheckingResult *match in matches(pattern,source)){
        NSUInteger start=NSMaxRange(match.range),end=start;unsigned level=1;
        while(end<source.length&&level){unichar c=[source characterAtIndex:end++];if(c=='[')level++;if(c==']')level--;if(level>128)return nil;}
        if(level)return nil;
        [result addObject:@{@"match":match,@"index":[source substringWithRange:NSMakeRange(start,end-start-1)],@"start":@(match.range.location),@"end":@(end)}];
    }
    return result;
}
@interface BWIndexClosure : NSObject
@property(strong) NSString *source,*failure,*input;
@property(strong) NSArray<NSNumber*> *words;
@property(strong) NSMutableSet *completed,*visiting;
@property(strong) NSMutableArray<NSString*> *statements;
- (NSString*)expression:(NSString*)value;
- (BOOL)variable:(NSString*)name;
@end
@implementation BWIndexClosure
- (BOOL)variable:(NSString*)name{
    if([self.completed containsObject:name])return YES;
    if([self.visiting containsObject:name]||self.visiting.count+self.completed.count>=256){self.failure=@"Cyclic or excessive finite profile dependency";return NO;}
    NSString *escaped=[NSRegularExpression escapedPatternForString:name];
    NSString *writes=[NSString stringWithFormat:@"\\b%@\\s*\\)*\\s*(?:=(?!=)|[+*/%%&|^-]=|<<=|>>=|\\+\\+|--)|(?:\\+\\+|--)\\s*\\(*\\s*\\b%@\\b",escaped,escaped];
    NSArray *definitions=matches([NSString stringWithFormat:@"\\b(uint|int|float)\\s+%@\\s*=\\s*([^;]+);",escaped],self.source);
    if(matches(writes,self.source).count!=1||definitions.count!=1){self.failure=[@"Ambiguous finite profile assignment: " stringByAppendingString:name];return NO;}
    NSString *prefix=[self.source substringToIndex:((NSTextCheckingResult*)definitions[0]).range.location];
    if(matches(@"\\{",prefix).count!=matches(@"\\}",prefix).count+1){self.failure=[@"Conditional finite profile assignment: " stringByAppendingString:name];return NO;}
    [self.visiting addObject:name];NSString *value=[self expression:part(self.source,definitions[0],2)];if(!value)return NO;
    [self.statements addObject:[NSString stringWithFormat:@"    %@ %@ = %@;\n",part(self.source,definitions[0],1),name,value]];
    [self.visiting removeObject:name];[self.completed addObject:name];return YES;
}
- (NSString*)expression:(NSString*)input{
    NSMutableString *value=[input mutableCopy];
    NSArray *reads=arrayReads(value,@"\\bbotw_c1_words\\[");if(!reads){self.failure=@"Unbalanced c1 probe expression";return nil;}
    for(NSDictionary *read in reads.reverseObjectEnumerator){NSString *index=read[@"index"];
        if(matches(@"^\\d+u?$",index).count!=1){self.failure=@"Dynamic memory in finite profile dependency";return nil;}
        unsigned long long element=strtoull(index.UTF8String,NULL,10);if(element>=self.words.count){self.failure=@"Finite profile constant exceeds original c1";return nil;}
        NSRange range=NSMakeRange([read[@"start"] unsignedIntegerValue],[read[@"end"] unsignedIntegerValue]-[read[@"start"] unsignedIntegerValue]);
        [value replaceCharactersInRange:range withString:[NSString stringWithFormat:@"0x%08xu",[self.words[(NSUInteger)element] unsignedIntValue]]];
    }
    NSString *basis=[self.input isEqual:@"vertex"]?@"\\bgl_VertexIndex\\s*-\\s*gl_BaseVertex\\b":@"\\bgl_InstanceIndex\\s*-\\s*gl_BaseInstance\\b";
    NSRegularExpression *relative=[NSRegularExpression regularExpressionWithPattern:[NSString stringWithFormat:@"\\(\\s*%@\\s*\\)",basis] options:0 error:nil];
    [relative replaceMatchesInString:value options:0 range:NSMakeRange(0,value.length) withTemplate:@"(botw_relative_input)"];
    if(matches([NSString stringWithFormat:@"^\\s*%@\\s*$",basis],value).count)[value setString:@"botw_relative_input"];
    if(matches(@"^[\\w\\s.+*/%<>=!&|^~?:(),-]+$",value).count!=1||[value containsString:@"//"]||[value containsString:@"/*"]||[value containsString:@"*/"]||matches(@"(?<![=!<>])=(?!=)|\\+\\+|--",value).count){self.failure=@"Unknown finite profile expression grammar";return nil;}
    NSSet *allowed=[NSSet setWithArray:@[@"uint",@"int",@"float",@"as_type",@"extract_bits",@"abs",@"fast",@"clamp",@"trunc",@"max",@"min",@"isnan",@"botw_relative_input"]];
    for(NSTextCheckingResult *identifier in matches(@"\\b[A-Za-z_]\\w*\\b",value)){
        NSString *name=[value substringWithRange:identifier.range];
        if(matches(@"^_\\d+$",name).count){if(![self variable:name])return nil;}
        else if(![allowed containsObject:name]){self.failure=[@"Unresolved external finite profile input: " stringByAppendingString:name];return nil;}
    }
    return value;
}
@end
static NSData *evaluateIndices(NSString *source,NSArray<NSNumber*> *words,NSArray<NSString*> *expressions,NSString *input,id<MTLDevice> device,char *error,size_t capacity){
    if(!expressions.count||expressions.count>64){bundleError(error,capacity,"Invalid finite profile expression count");return nil;}
    BWIndexClosure *closure=[BWIndexClosure new];closure.source=source;closure.words=words;closure.input=input;closure.completed=[NSMutableSet new];closure.visiting=[NSMutableSet new];closure.statements=[NSMutableArray new];
    NSMutableArray *targets=[NSMutableArray new];for(NSString *expression in expressions){NSString *target=[closure expression:expression];if(!target){bundleError(error,capacity,"%s",closure.failure.UTF8String);return nil;}[targets addObject:target];}
    NSMutableString *probe=[NSMutableString stringWithString:@"#include <metal_stdlib>\nusing namespace metal;\nkernel void botw_index_probe(device uint *out [[buffer(0)]],uint botw_relative_input [[thread_position_in_grid]]) {\n    if(botw_relative_input>=64u)return;\n"];
    for(NSString *statement in closure.statements)[probe appendString:statement];
    for(NSUInteger i=0;i<targets.count;i++)[probe appendFormat:@"    out[botw_relative_input*%luu+%luu]=uint(%@);\n",(unsigned long)targets.count,(unsigned long)i,targets[i]];
    [probe appendString:@"}\n"];NSError *metalError=nil;
    id<MTLLibrary> library=[device newLibraryWithSource:probe options:nil error:&metalError];id<MTLFunction> function=[library newFunctionWithName:@"botw_index_probe"];
    id<MTLComputePipelineState> pipeline=function?[device newComputePipelineStateWithFunction:function error:&metalError]:nil;
    if(!pipeline){bundleError(error,capacity,"Finite profile Metal compilation failed: %s",metalError.localizedDescription.UTF8String?:"entry missing");return nil;}
    size_t bytes=64*targets.count*sizeof(uint32_t);id<MTLBuffer> output=[device newBufferWithLength:bytes options:MTLResourceStorageModeShared];
    id<MTLCommandQueue> queue=[device newCommandQueue];id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLComputeCommandEncoder> encoder=[command computeCommandEncoder];
    if(!output||!queue||!command||!encoder){bundleError(error,capacity,"Finite profile Metal allocation failed");return nil;}
    memset(output.contents,0xff,bytes);[encoder setComputePipelineState:pipeline];[encoder setBuffer:output offset:0 atIndex:0];
    [encoder dispatchThreads:MTLSizeMake(64,1,1) threadsPerThreadgroup:MTLSizeMake(MIN((NSUInteger)64,pipeline.maxTotalThreadsPerThreadgroup),1,1)];[encoder endEncoding];[command commit];[command waitUntilCompleted];
    if(command.status!=MTLCommandBufferStatusCompleted){bundleError(error,capacity,"Finite profile Metal execution failed");return nil;}
    return [NSData dataWithBytes:output.contents length:bytes];
}
static BOOL finiteDrawProfile(BWBundleShader *shader,NSDictionary *entry,id<MTLDevice> device,char *error,size_t capacity){
    if(!entry[@"draw_profile"]){
        if(matches(@"\\bbotw_c1_words\\b",shader.source).count||matches(@"\\bdevice\\b",shader.source).count)return bundleError(error,capacity,"Unprofiled private/storage resource");return YES;
    }
    if(shader.stage!=NVN_SHADER_STAGE_VERTEX||shader.control.length<0x708)return bundleError(error,capacity,"Finite profile requires original vertex c1 bounds");
    NSRegularExpression *parameters=[NSRegularExpression regularExpressionWithPattern:@"\\bconstant\\s+\\w+\\s*&\\s*c(?:[3-9]|1[0-6])(?:_\\d+)?\\s+\\[\\[buffer\\(\\d+\\)\\]\\]" options:0 error:nil];
    NSString *withoutParameters=[parameters stringByReplacingMatchesInString:shader.source options:0 range:NSMakeRange(0,shader.source.length) withTemplate:@""];
    if([withoutParameters containsString:@"&"])return bundleError(error,capacity,"Unproven finite source reference or address escape");
    NSRegularExpression *usingMetal=[NSRegularExpression regularExpressionWithPattern:@"\\busing\\s+namespace\\s+metal\\s*;" options:0 error:nil];
    NSString *withoutUsing=[usingMetal stringByReplacingMatchesInString:shader.source options:0 range:NSMakeRange(0,shader.source.length) withTemplate:@""];
    if(matches(@"\\b(?:thread|threadgroup|typedef|auto|decltype|template|namespace|using)\\b",withoutUsing).count)return bundleError(error,capacity,"Unproven finite source alias grammar");
    for(NSString *line in [shader.source componentsSeparatedByString:@"\n"]){NSString *trimmed=[line stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
        if([trimmed hasPrefix:@"#"]&&matches(@"^#include\\s+<(?:metal_stdlib|simd/simd\\.h)>\\s*$",trimmed).count!=1)return bundleError(error,capacity,"Unknown finite source preprocessor directive");}
    NSMutableSet *pure=[NSMutableSet setWithArray:@[@"as_type",@"extract_bits",@"abs",@"fma",@"clamp",@"trunc",@"max",@"min",@"isnan",@"if",@"buffer",@"attribute",@"user",shader.entry]];
    for(NSString *scalar in @[@"uint",@"int",@"float",@"bool"])for(NSString *width in @[@"",@"2",@"3",@"4"])[pure addObject:[scalar stringByAppendingString:width]];
    NSUInteger entryCalls=0;
    for(NSTextCheckingResult *call in matches(@"\\b([A-Za-z_]\\w*)\\s*(?:<[^<>]+>)?\\s*\\(",shader.source)){NSString *name=part(shader.source,call,1);if([name isEqual:shader.entry])entryCalls++;if(![pure containsObject:name])return bundleError(error,capacity,"Unknown or mutable finite source function");}
    if(entryCalls!=1)return bundleError(error,capacity,"Ambiguous or recursive finite source entry");
    NSArray *definitions=matches(@"(?:^|[;{}])\\s*(?:(?:inline|static|constexpr|vertex|fragment|kernel)\\s+)*\\w+\\s+(\\w+)\\s*\\([^;{}]*\\)\\s*\\{",shader.source);NSUInteger entries=0;
    for(NSTextCheckingResult *definition in definitions){NSString *name=part(shader.source,definition,1);if([@[@"if",@"for",@"while",@"switch"] containsObject:name])continue;
        if(![name isEqual:shader.entry])return bundleError(error,capacity,"Unproven finite source helper definition");entries++;}
    if(entries!=1)return bundleError(error,capacity,"Ambiguous finite source function definition");
    uint32_t start,end;memcpy(&start,(const char*)shader.control.bytes+0x700,4);memcpy(&end,(const char*)shader.control.bytes+0x704,4);
    if(start<0x80||start%4||end!=shader.code.length||end<=start||(end-start)%4||end-start>65536)return bundleError(error,capacity,"Invalid original finite c1 bounds");
    NSArray *arrays=matches(@"\\bconstant\\s+uint\\s+botw_c1_words\\[(\\d+)\\]\\s*=\\s*\\{([^}]+)\\};",shader.source);
    if(arrays.count!=1||[part(shader.source,arrays[0],1) integerValue]!=(end-start)/4)return bundleError(error,capacity,"Missing exact original c1 word array");
    NSArray *literals=[part(shader.source,arrays[0],2) componentsSeparatedByString:@","];NSMutableArray<NSNumber*> *words=[NSMutableArray new];
    if(literals.count!=(end-start)/4)return bundleError(error,capacity,"Finite c1 array length mismatch");
    for(NSUInteger i=0;i<literals.count;i++){NSString *literal=[literals[i] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];uint32_t original;
        memcpy(&original,(const char*)shader.code.bytes+start+i*4,4);
        if(![literal isEqual:[NSString stringWithFormat:@"0x%08xu",original]])return bundleError(error,capacity,"Original finite c1 bits mismatch");[words addObject:@(original)];
    }
    NSArray *c1Reads=arrayReads(shader.source,@"\\bbotw_c1_words\\[");NSMutableArray *c1Expressions=[NSMutableArray new];
    if(!c1Reads||matches(@"\\bbotw_c1_words\\b",shader.source).count!=c1Reads.count)return bundleError(error,capacity,"Unknown finite c1 array escape");
    NSArray *allArrays=arrayReads(shader.source,@"\\b(\\w+)\\s*\\[(?!\\[)");
    NSArray *layouts=matches(@"\\bstruct\\s+\\w+\\s*\\{\\s*(?:uint[234]?|float[234]?)\\s+(?:data|_m0)\\[\\d+\\];\\s*\\};",shader.source);
    if(!allArrays)return bundleError(error,capacity,"Unbalanced finite source array");
    for(NSDictionary *read in allArrays){NSTextCheckingResult *match=read[@"match"];NSString *name=part(shader.source,match,1);NSUInteger position=[read[@"start"] unsignedIntegerValue];BOOL recognized=NO;
        for(NSTextCheckingResult *layout in layouts)if(NSLocationInRange(position,layout.range))recognized=YES;
        if([name isEqual:@"botw_c1_words"]||matches(@"^ssbo\\d+(?:_\\d+)?$",name).count)recognized=YES;
        if([name isEqual:@"data"]&&position){NSString *prefix=[shader.source substringToIndex:position];
            if(matches(@"\\bc(?:[3-9]|1[0-6])(?:_\\d+)?\\.$",prefix).count)recognized=YES;}
        if(!recognized)return bundleError(error,capacity,"Unproven extra array in finite shader source");
    }
    /* The declaration is also a bracket match; never treat its extent as a read. */
    for(NSDictionary *read in c1Reads){NSUInteger position=[read[@"start"] unsignedIntegerValue];if(NSLocationInRange(position,[arrays[0] range]))continue;
        NSString *expression=read[@"index"];if(![c1Expressions containsObject:expression])[c1Expressions addObject:expression];
    }
    NSData *vertexData=evaluateIndices(shader.source,words,c1Expressions,@"vertex",device,error,capacity);if(!vertexData)return NO;
    const uint32_t *vertexIndices=vertexData.bytes;unsigned vertices=0;
    for(unsigned v=0;v<64;v++){BOOL safe=YES;for(NSUInteger i=0;i<c1Expressions.count;i++)if(vertexIndices[v*c1Expressions.count+i]>=words.count)safe=NO;if(!safe)break;vertices++;}
    if(!vertices)return bundleError(error,capacity,"No safe finite relative vertex prefix");
    NSMutableArray<NSString*> *storageExpressions=[NSMutableArray new];NSMutableArray<NSDictionary*> *accesses=[NSMutableArray new];unsigned storageSlot=16;
    for(NSTextCheckingResult *parameter in matches(@"\\bdevice\\s+const\\s+(uint(?:2|4)?)\\s*\\*\\s*(ssbo(\\d+)(?:_\\d+)?)\\s+\\[\\[buffer\\((\\d+)\\)\\]\\]",shader.source)){
        NSString *variable=part(shader.source,parameter,2);unsigned slot;
        if(!storageDescriptorSlot(entry,shader.stage,[part(shader.source,parameter,3) integerValue],&slot,error,capacity))return NO;
        if(storageSlot!=16&&storageSlot!=slot)return bundleError(error,capacity,"Finite profile has multiple NVN storage slots");storageSlot=slot;
        NSString *scalar=part(shader.source,parameter,1);size_t stride=[scalar isEqual:@"uint"]?4:[scalar isEqual:@"uint2"]?8:16;
        NSArray *reads=arrayReads(shader.source,[NSString stringWithFormat:@"\\b%@\\[",[NSRegularExpression escapedPatternForString:variable]]);
        if(!reads||!reads.count||matches([NSString stringWithFormat:@"\\b%@\\b",[NSRegularExpression escapedPatternForString:variable]],shader.source).count!=reads.count+1)return bundleError(error,capacity,"Unknown finite storage pointer escape");
        for(NSDictionary *read in reads){NSUInteger position=[read[@"start"] unsignedIntegerValue];while(position&&[[NSCharacterSet whitespaceAndNewlineCharacterSet] characterIsMember:[shader.source characterAtIndex:position-1]])position--;
            if(position&&[shader.source characterAtIndex:position-1]=='&')return bundleError(error,capacity,"Finite storage address escape");
            NSString *expression=read[@"index"];NSUInteger index=[storageExpressions indexOfObject:expression];if(index==NSNotFound){index=storageExpressions.count;[storageExpressions addObject:expression];}
            [accesses addObject:@{@"index":@(index),@"stride":@(stride)}];
        }
    }
    if(storageSlot==16)return bundleError(error,capacity,"Finite profile has no actual storage pointers");
    NSData *instanceData=evaluateIndices(shader.source,words,storageExpressions,@"instance",device,error,capacity);if(!instanceData)return NO;
    const uint32_t *indices=instanceData.bytes;size_t minima[65]={0};unsigned instances=0;
    for(unsigned n=0;n<64;n++){uint64_t minimum=minima[n];
        for(NSDictionary *access in accesses){uint64_t index=indices[n*storageExpressions.count+[access[@"index"] unsignedIntegerValue]];uint64_t bytes=(index+1)*[access[@"stride"] unsignedIntegerValue];minimum=MAX(minimum,bytes);}
        if(!minimum||minimum>device.maxBufferLength)break;minima[n+1]=(size_t)minimum;instances++;
    }
    if(!instances)return bundleError(error,capacity,"No safe finite relative instance storage prefix");
    shader.maxVertices=vertices;shader.maxInstances=instances;shader.storageSlot=storageSlot;shader.storageMinimum=[NSData dataWithBytes:minima length:(instances+1)*sizeof(size_t)];
    fprintf(stderr,"[botw shaders] finite identity=%s relative_vertices=%u relative_instances=%u storage_slot=%u bytes_at_max=%zu\n",shader.identity.UTF8String,vertices,instances,storageSlot,minima[instances]);return YES;
}
