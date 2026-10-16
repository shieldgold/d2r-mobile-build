/* SPDX-License-Identifier: MIT */
#import "whole_shader_conditional.h"
#include "whole_shader_bundle_publication.h"
#include "whole_shader_publication.h"
#include "whole_shader_publication_files.h"
#include "base_v0_graphics_format_identity.h"

static NSString *const BindingContractSHA = @"480e8ed95dc002acc574d90294234e11aaf99caf2afa647c8950ccffe9abe8a3";
static NSString *const Scope = @"noattr-vs-output0-fs-nonqueried-v1";
static NSString *const Profile = @"botw-switch-base-v0";
static NSString *const Upstream = @"21f026e0c7fd9fee575a1bfa8d925553bb4db88e";

static BOOL pinned_document(NSString *root,NSString *name,NSString *sha,size_t limit,NSDictionary **out) {
    NSData *bytes=read_regular([root stringByAppendingPathComponent:name],limit);
    if(!bytes||![digest(bytes) isEqual:sha])return NO;
    NSDictionary *value=document(bytes);if(!value)return NO;*out=value;return YES;
}
static BOOL exact_keys(NSDictionary *left,NSDictionary *right) {
    return object(left)&&object(right)&&[[NSSet setWithArray:left.allKeys] isEqual:[NSSet setWithArray:right.allKeys]];
}
static BOOL mode_number(id value) {
    return [value isKindOfClass:NSNumber.class]&&CFGetTypeID((__bridge CFTypeRef)value)!=CFBooleanGetTypeID()&&
        !CFNumberIsFloatType((__bridge CFNumberRef)value)&&([value intValue]==0||[value intValue]==1);
}
static BOOL mode_same(NSDictionary *left,NSDictionary *right) {
    return object(left)&&object(right)&&mode_number(left[@"window_origin_mode"])&&mode_number(left[@"depth_mode"])&&
        mode_number(right[@"window_origin_mode"])&&mode_number(right[@"depth_mode"])&&
        [left[@"window_origin_mode"] isEqual:right[@"window_origin_mode"]]&&[left[@"depth_mode"] isEqual:right[@"depth_mode"]];
}
/* The complete first-scope translator reflection contains ASCII strings and
 * integer/boolean/null JSON only. Match the producer's sorted compact JSON
 * plus newline exactly; unsupported types must never gain a guessed digest. */
static BOOL core_json_value(id value) {
    if(value==NSNull.null)return YES;
    if(string(value))return [value canBeConvertedToEncoding:NSASCIIStringEncoding];
    if([value isKindOfClass:NSNumber.class])return CFGetTypeID((__bridge CFTypeRef)value)==CFBooleanGetTypeID()||
        !CFNumberIsFloatType((__bridge CFNumberRef)value);
    if(array(value)){for(id child in value)if(!core_json_value(child))return NO;return YES;}
    if(object(value)){for(id key in value)if(!string(key)||!core_json_value(key)||!core_json_value(value[key]))return NO;return YES;}
    return NO;
}
static NSData *reflection_core_bytes(NSDictionary *reflection) {
    NSMutableDictionary *core=[reflection mutableCopy];
    core[@"schema"]=@"botw-shader-reflection-v1";
    [core removeObjectsForKeys:@[@"conditional_contract_sha256",@"runnable",@"runtime_compile_required",
        @"runtime_compiler",@"unknowns",@"conflicts"]];
    if(!core_json_value(core))return nil;
    NSData *raw=[NSJSONSerialization dataWithJSONObject:core
        options:NSJSONWritingSortedKeys|NSJSONWritingWithoutEscapingSlashes error:NULL];
    if(!raw)return nil;
    NSMutableData *bytes=[raw mutableCopy];[bytes appendBytes:"\n" length:1];return bytes;
}
static NSData *constant_segment_bytes(id value) {
    if(!string(value)||[value length]>8192||([value length]&1)||([value length]&&!hex(value,[value length])))return nil;
    NSMutableData *bytes=[NSMutableData dataWithLength:[value length]/2];
    const char *s=[value UTF8String];uint8_t *out=bytes.mutableBytes;
    for(NSUInteger i=0;i<bytes.length;i++){
        unsigned a=s[i*2]<='9'?(unsigned)(s[i*2]-'0'):(unsigned)(s[i*2]-'a'+10);
        unsigned b=s[i*2+1]<='9'?(unsigned)(s[i*2+1]-'0'):(unsigned)(s[i*2+1]-'a'+10);
        out[i]=(uint8_t)(a*16+b);
    }
    return bytes;
}

static int conditional_publication(const char *bundle,NSString *root,NSDictionary *package,
        char *output,size_t capacity,char *error,size_t error_capacity) {
    if(![package[@"profile"] isEqual:Profile]||!hex(package[@"chain"],12)||
            !hex(package[@"publication_sha256"],64)||!hex(package[@"admission_sha256"],64)||
            !hex(package[@"subset_sha256"],64)||![package[@"binding_contract_sha256"] isEqual:BindingContractSHA]||
            ![package[@"format_contract_sha256"] isEqual:@BOTW_BASE_FORMAT_CONTRACT_SHA256])
        return denied(error,error_capacity,@"conditional package identity/proof authority invalid");
    NSString *directory=[@"botw-c-" stringByAppendingString:package[@"chain"]];
    if(![package[@"directory"] isEqual:directory]||!contained_directories(bundle,@[@"botw",@"shaders",directory]))
        return denied(error,error_capacity,@"conditional artifact directory must remain inside the signed bundle");
    NSDictionary *publication,*admission,*proof,*bindings,*formats;
    if(!pinned_document(root,@"conditional-publication.json",package[@"publication_sha256"],65536,&publication)||
            !pinned_document(root,@"conditional-admission.json",package[@"admission_sha256"],64*1024*1024,&admission)||
            !pinned_document(root,@"subset-proof.json",package[@"subset_sha256"],128*1024*1024,&proof)||
            !pinned_document(root,@"base-bindings.json",BindingContractSHA,1024*1024,&bindings)||
            !pinned_document(root,@"base-formats.json",@BOTW_BASE_FORMAT_CONTRACT_SHA256,1024*1024,&formats))
        return denied(error,error_capacity,@"conditional publication/admission/static proof bytes differ");
    if(![publication[@"schema"] isEqual:@"botw-shader-conditional-publication-v1"]||
            ![admission[@"schema"] isEqual:@"botw-shader-conditional-admission-v1"]||
            ![proof[@"schema"] isEqual:@"botw-shader-conditional-subset-proof-v1"]||
            ![admission[@"upstream_commit"] isEqual:Upstream]||![proof[@"upstream_commit"] isEqual:Upstream]||
            ![publication[@"package"] isEqual:[@"build/" stringByAppendingString:directory]]||
            ![publication[@"conditional_admission_sha256"] isEqual:package[@"admission_sha256"]]||
            !boolean(publication[@"device_compilation_tested"],NO)||!boolean(publication[@"guest_or_gpu_executed"],NO)||
            !boolean(proof[@"guest_or_gpu_executed"],NO)||!hex(publication[@"manifest_sha256"],64)||
            !hex(publication[@"source_adapter_sha256"],64)||!hex(proof[@"input_manifest_sha256"],64))
        return denied(error,error_capacity,@"conditional source batch is not an explicit CPU-only publication");
    for(NSDictionary *value in @[publication,admission,proof]) {
        if(![value[@"profile"] isEqual:Profile]||![value[@"source_adapter_sha256"] isEqual:publication[@"source_adapter_sha256"]]||
                ![value[@"binding_contract_sha256"] isEqual:BindingContractSHA]||
                ![value[@"format_contract_sha256"] isEqual:@BOTW_BASE_FORMAT_CONTRACT_SHA256])
            return denied(error,error_capacity,@"conditional proof/source identities differ");
    }
    if(![admission[@"scope"] isEqual:Scope]||![proof[@"scope"] isEqual:Scope]||
            ![publication[@"subset_sha256"] isEqual:package[@"subset_sha256"]]||
            ![admission[@"subset_sha256"] isEqual:package[@"subset_sha256"]]||
            !exact_keys(admission[@"captures"],proof[@"accepted"])||![admission[@"captures"] count])
        return denied(error,error_capacity,@"conditional admission does not cover the exact proven subset");
    NSString *path=[[root stringByAppendingPathComponent:directory] stringByAppendingPathComponent:@"manifest-build.json"];
    NSDictionary *manifest;
    if(!pinned_document([root stringByAppendingPathComponent:directory],@"manifest-build.json",publication[@"manifest_sha256"],256*1024*1024,&manifest)||
            ![manifest[@"conditional_admission_sha256"] isEqual:package[@"admission_sha256"]]||
            ![manifest[@"chain"] isEqual:package[@"chain"]]||
            ![manifest[@"source_adapter_sha256"] isEqual:publication[@"source_adapter_sha256"]])
        return denied(error,error_capacity,@"conditional manifest identity/admission differs");
    NSError *failure=nil;
    WholeShaderConditionalManifest *loaded=[WholeShaderConditionalManifest conditionalManifestAtAbsolutePath:path error:&failure];
    if(!loaded||![loaded validateArtifactsWithoutRetaining:&failure])
        return denied(error,error_capacity,failure.localizedDescription?:@"conditional artifact gates rejected the publication");
    NSArray *results=manifest[@"results"];NSDictionary *captures=admission[@"captures"],*accepted=proof[@"accepted"];
    if(!array(results)||!count_matches(publication[@"captures"],captures.count)||
            !count_matches(publication[@"variants"],results.count)||results.count!=captures.count*4)
        return denied(error,error_capacity,@"conditional subset must contain exactly four proven modes per capture");
    NSMutableDictionary<NSString*,NSMutableSet*> *seen=[NSMutableDictionary dictionary];
    for(NSDictionary *result in results) {
        @autoreleasepool {
            NSDictionary *capture=result[@"capture"];NSString *key=capture[@"key"],*variant=result[@"variant_sha256"];
            NSDictionary *approved=captures[key],*evidence=accepted[key],*contract;
            if(!object(approved)||!object(evidence)||!array(approved[@"variants"])||[approved[@"variants"] count]!=4||
                    ![approved[@"variants"] containsObject:variant]||
                    ![approved[@"capture_sha256"] isEqual:capture[@"sha256"]]||
                    ![approved[@"stage_name"] isEqual:capture[@"stage_name"]]||
                    ![evidence[@"capture_sha256"] isEqual:capture[@"sha256"]]||
                    ![evidence[@"stage_name"] isEqual:capture[@"stage_name"]]||
                    ![evidence[@"capture"] isEqual:capture]||
                    !pinned_document([root stringByAppendingPathComponent:directory],
                        [variant stringByAppendingString:@".contract.json"],variant,16*1024*1024,&contract)||
                    ![contract[@"proofs"][@"binding_contract_sha256"] isEqual:BindingContractSHA]||
                    ![contract[@"proofs"][@"subset_sha256"] isEqual:package[@"subset_sha256"]])
                return denied(error,error_capacity,@"conditional capture/contract is outside its static admitted subset");
            NSArray *modes=evidence[@"modes"];NSDictionary *device=contract[@"requirements"][@"device"],*match=nil;
            if(!array(modes)||modes.count!=4)return denied(error,error_capacity,@"missing complete static mode proof");
            for(id mode in modes)if(mode_same(mode,device)) {
                if(match)return denied(error,error_capacity,@"duplicate mode in conditional static proof");match=mode;
            }
            if(!match||!hex(match[@"job_sha256"],64)||!hex(match[@"res_sha256"],64)||
                    !hex(match[@"translated_code_sha256"],64)||
                    ![contract[@"translation"][@"job_sha256"] isEqual:match[@"job_sha256"]]||
                    ![result[@"files"][@"metal_source"][@"sha256"] isEqual:match[@"msl_sha256"]])
                return denied(error,error_capacity,@"conditional source differs from its exact CPU translation proof");
            NSDictionary *reflection;
            if(!pinned_document([root stringByAppendingPathComponent:directory],
                    [variant stringByAppendingString:@".json"],result[@"files"][@"json"][@"sha256"],16*1024*1024,&reflection))
                return denied(error,error_capacity,@"conditional reflection bytes differ from the verified artifact");
            NSData *core=reflection_core_bytes(reflection),*constant=constant_segment_bytes(reflection[@"c1"]);
            if(!core||!constant||!hex(match[@"reflection_core_sha256"],64)||
                    !hex(evidence[@"c1_sha256"],64)||![digest(core) isEqual:match[@"reflection_core_sha256"]]||
                    ![digest(constant) isEqual:evidence[@"c1_sha256"]])
                return denied(error,error_capacity,@"conditional reflection/core or C1 differs from the original CPU proof");
            NSMutableSet *variants=seen[key];if(!variants){variants=[NSMutableSet set];seen[key]=variants;}
            NSString *mode=[NSString stringWithFormat:@"%@/%@",device[@"window_origin_mode"],device[@"depth_mode"]];
            if([variants containsObject:mode])return denied(error,error_capacity,@"multiple conditional variants claim the same proven device mode");
            [variants addObject:mode];
        }
    }
    if(!exact_keys(captures,seen))return denied(error,error_capacity,@"conditional manifest omits an admitted capture");
    for(NSSet *modes in seen.allValues)if(modes.count!=4)return denied(error,error_capacity,@"conditional manifest omits a proven device mode");
    const char *bytes=path.fileSystemRepresentation;size_t length=strlen(bytes);
    if(length>=capacity)return denied(error,error_capacity,@"manifest output capacity is insufficient");
    memcpy(output,bytes,length+1);return 0;
}

int whole_runtime_shader_bundle_publication(const char *bundle,char *manifest,size_t capacity,char *error,size_t error_capacity) {
    if(error&&error_capacity)error[0]=0;
    if(!bundle||!manifest||!capacity||(error_capacity&&!error))return denied(error,error_capacity,@"invalid publication caller buffers");
    @autoreleasepool {
        NSString *base=[NSFileManager.defaultManager stringWithFileSystemRepresentation:bundle length:strlen(bundle)];
        if(!base.isAbsolutePath||!contained_directories(bundle,@[@"botw",@"shaders"]))
            return denied(error,error_capacity,@"publication root must be inside the signed bundle");
        NSString *root=[base stringByAppendingPathComponent:@"botw/shaders"];
        NSDictionary *index=document(read_regular([root stringByAppendingPathComponent:@"shader-package.json"],65536));
        if([index[@"schema"] isEqual:@"botw-whole-shader-package-v1"])
            return whole_runtime_shader_publication(bundle,manifest,capacity,error,error_capacity);
        if([index[@"schema"] isEqual:@"botw-whole-conditional-shader-package-v1"])
            return conditional_publication(bundle,root,index,manifest,capacity,error,error_capacity);
        return denied(error,error_capacity,@"missing explicit shader publication schema");
    }
}
