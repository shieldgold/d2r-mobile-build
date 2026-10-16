/* Owned parser/compiler-port fixtures. No Metal device or GPU is created. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <CommonCrypto/CommonDigest.h>
#import "botw_shader_runtime.h"
#include "whole_shader_publication.h"
#include <stdio.h>
#include <string.h>

static unsigned compile_calls;
static int fail_compile, fail_function, wrong_type;
@interface OwnedFunction : NSObject
@property(nonatomic) MTLFunctionType functionType;
@end
@implementation OwnedFunction @end
@interface OwnedLibrary : NSObject
- (id)newFunctionWithName:(NSString *)name;
@end
@implementation OwnedLibrary
- (id)newFunctionWithName:(NSString *)name {
    if (fail_function || ![name isEqual:@"main0"]) return nil;
    OwnedFunction *function = [OwnedFunction new];
    function.functionType = wrong_type ? MTLFunctionTypeKernel : MTLFunctionTypeVertex;
    return function;
}
@end
@interface OwnedDevice : NSObject
- (id)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options error:(NSError **)error;
- (id)newLibraryWithData:(dispatch_data_t)data error:(NSError **)error;
@end
@implementation OwnedDevice
- (id)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options error:(NSError **)error {
    ++compile_calls;
    if (options.fastMathEnabled || fail_compile || ![source hasPrefix:@"// owned shader\n"]) {
        if(error)*error=[NSError errorWithDomain:@"owned.compiler" code:1 userInfo:nil];
        return nil;
    }
    return [OwnedLibrary new];
}
- (id)newLibraryWithData:(dispatch_data_t)data error:(NSError **)error {
    (void)data; (void)error; return nil;
}
@end
static NSString *digest(NSData *bytes) {
    unsigned char sum[CC_SHA256_DIGEST_LENGTH]; char hex[65];
    CC_SHA256(bytes.bytes,(CC_LONG)bytes.length,sum);
    for(unsigned i=0;i<sizeof sum;++i)snprintf(hex+2*i,3,"%02x",sum[i]);
    return @(hex);
}
static void json(NSString *path,id object) {
    NSData *bytes=[NSJSONSerialization dataWithJSONObject:object options:NSJSONWritingSortedKeys error:NULL];
    if(!bytes||![bytes writeToFile:path atomically:YES])exit(4);
}
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    @autoreleasepool {
        NSString *scenario=@(argv[1]);
        NSString *directory=[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
        BOOL compiled=[scenario isEqual:@"compiled-v1-source-rejected"];
        NSString *output=[directory stringByAppendingPathComponent:compiled?@"botw-b-0123456789ab":@"botw-s-0123456789ab"];
        if(![NSFileManager.defaultManager createDirectoryAtPath:output withIntermediateDirectories:YES attributes:nil error:NULL])return 3;
        char resolved[4096];if(!realpath(output.fileSystemRepresentation,resolved))return 3;output=@(resolved);
        NSString *key=@"0123456789abcdef", *sha=[key stringByPaddingToLength:64 withString:@"0" startingAtIndex:0];
        NSString *base=[key stringByAppendingString:@"_b"];
        NSString *reflectionName=[base stringByAppendingString:@".json"];
        NSString *sourceName=[base stringByAppendingString:@".metal"];
        NSString *reflectionPath=[output stringByAppendingPathComponent:reflectionName];
        NSString *sourcePath=[output stringByAppendingPathComponent:sourceName];
        NSData *source=[@"// owned shader\nvertex float4 main0() { return float4(0); }\n" dataUsingEncoding:NSUTF8StringEncoding];
        if([scenario isEqual:@"embedded-nul"]){const char bytes[]="// owned shader\n\0untrusted";source=[NSData dataWithBytes:bytes length:sizeof bytes-1];}
        if([scenario isEqual:@"invalid-utf8"]){const unsigned char bytes[]={0xff,0xfe};source=[NSData dataWithBytes:bytes length:2];}
        [source writeToFile:sourcePath atomically:YES];
        NSMutableDictionary *reflection=[@{@"schema":@"botw-shader-reflection-v1",@"route":@"B",@"runnable":@NO,
            @"runtime_compile_required":@YES,@"chain":@"0123456789ab",@"key":base,@"dm_ctl":key,@"sha256":sha,
            @"stage":@"vs",@"entry":@"main0",@"facts_sha256":[@"f" stringByPaddingToLength:64 withString:@"f" startingAtIndex:0],
            @"unknowns":@[],@"conflicts":@[],@"notes":@[],@"bindings":@[],
            @"device_state":@{@"window_origin_mode":@"upper_left",@"depth_mode":@"near_is_minus_w"}} mutableCopy];
        if([scenario isEqual:@"reflection-unknown"])reflection[@"unknowns"]=@[@"missing binding"];
        if([scenario isEqual:@"missing-facts"])[reflection removeObjectForKey:@"facts_sha256"];
        if([scenario isEqual:@"wrong-identity"])reflection[@"sha256"]=[@"a" stringByPaddingToLength:64 withString:@"a" startingAtIndex:0];
        json(reflectionPath,reflection);
        NSMutableDictionary *files=[@{@"json":@{@"path":reflectionName,@"sha256":digest([NSData dataWithContentsOfFile:reflectionPath])},
            @"metal_source":@{@"path":sourceName,@"sha256":digest(source)}} mutableCopy];
        if([scenario isEqual:@"traversal"])files[@"metal_source"]=@{@"path":[@"../" stringByAppendingString:sourceName],@"sha256":digest(source)};
        if([scenario isEqual:@"absolute-path"])files[@"metal_source"]=@{@"path":sourcePath,@"sha256":digest(source)};
        if([scenario isEqual:@"hash-mismatch"])[@"different" writeToFile:sourcePath atomically:YES encoding:NSUTF8StringEncoding error:NULL];
        if([scenario isEqual:@"symlink"]){[NSFileManager.defaultManager moveItemAtPath:sourcePath toPath:[sourcePath stringByAppendingString:@".actual"] error:NULL];[NSFileManager.defaultManager createSymbolicLinkAtPath:sourcePath withDestinationPath:[sourcePath stringByAppendingString:@".actual"] error:NULL];}
        NSMutableDictionary *result=[@{@"key":key,@"status":@"source_ready",@"runnable":@NO,@"runtime_compile_required":@YES,
            @"errors":@[],@"unknowns":@[],@"conflicts":@[],
            @"capture":@{@"key":key,@"sha256":sha,@"stage_name":@"vertex",@"stage":@0},@"files":files} mutableCopy];
        if([scenario isEqual:@"result-unknown"])result[@"unknowns"]=@[@"unresolved"];
        NSMutableDictionary *manifest=[@{@"schema":@"botw-shader-source-manifest-v1",@"mode":@"source",@"runnable":@NO,
            @"runtime_compile_required":@YES,@"chain":@"0123456789ab",@"output":@".",@"results":@[result]} mutableCopy];
        if([scenario isEqual:@"pretend-runnable"])manifest[@"runnable"]=@YES;
        if([scenario isEqual:@"legacy-schema"])manifest[@"schema"]=@"botw-shader-build-manifest-v1";
        if([scenario isEqual:@"duplicate"])manifest[@"results"]=@[result,result];
        if(compiled){
            NSString *libraryPath=[output stringByAppendingPathComponent:[base stringByAppendingString:@".metallib"]];
            [source writeToFile:libraryPath atomically:YES];
            reflection[@"runnable"]=@YES;[reflection removeObjectForKey:@"runtime_compile_required"];
            json(reflectionPath,reflection);
            result[@"status"]=@"ready";result[@"runnable"]=@YES;
            result[@"files"]=@{@"json":@{@"path":reflectionPath,@"sha256":digest([NSData dataWithContentsOfFile:reflectionPath])},
                @"metallib":@{@"path":libraryPath,@"sha256":digest(source)},@"msl":@{@"path":sourcePath}};
            manifest[@"schema"]=@"botw-shader-build-manifest-v1";manifest[@"mode"]=@"build";
            manifest[@"output"]=output;manifest[@"runnable"]=@YES;
        }
        NSString *manifestPath=[output stringByAppendingPathComponent:@"manifest-build.json"];
        json(manifestPath,manifest);
        if([scenario hasPrefix:@"publication-"]){
            NSString *root=[directory stringByAppendingPathComponent:@"botw/shaders"];
            [NSFileManager.defaultManager createDirectoryAtPath:[root stringByAppendingPathComponent:@"facts"] withIntermediateDirectories:YES attributes:nil error:NULL];
            NSString *factPath=[[root stringByAppendingPathComponent:@"facts"] stringByAppendingPathComponent:[key stringByAppendingString:@".facts.json"]];
            json(factPath,@{@"schema":@"botw-shader-facts-v1",@"key":key,@"sha256":sha,@"stage":@"vertex",
                @"provenance":@"owned CPU fixture; no game evidence",@"device_state":reflection[@"device_state"]});
            NSString *factHash=digest([NSData dataWithContentsOfFile:factPath]);reflection[@"facts_sha256"]=factHash;
            json(reflectionPath,reflection);
            files[@"json"]=@{@"path":reflectionName,@"sha256":digest([NSData dataWithContentsOfFile:reflectionPath])};
            NSString *adapterHash=[@"a" stringByPaddingToLength:64 withString:@"a" startingAtIndex:0];
            NSString *inputHash=[@"d" stringByPaddingToLength:64 withString:@"d" startingAtIndex:0];
            manifest[@"source_adapter_sha256"]=adapterHash;json(manifestPath,manifest);
            NSString *folder=output.lastPathComponent;
            NSString *relocated=[root stringByAppendingPathComponent:folder];
            [NSFileManager.defaultManager moveItemAtPath:output toPath:relocated error:NULL];
            manifestPath=[relocated stringByAppendingPathComponent:@"manifest-build.json"];
            NSDictionary *admission=@{@"schema":@"botw-shader-release-admission-v1",@"profile":@"botw-switch-base-v0",@"approved":@YES,
                @"upstream_commit":@"21f026e0c7fd9fee575a1bfa8d925553bb4db88e",@"input_manifest_sha256":inputHash,
                @"captures":@{key:@{@"capture_sha256":sha,@"facts_sha256":factHash,@"review_sha256":adapterHash,@"fields":@[@"device_state"]}}};
            NSString *admissionPath=[root stringByAppendingPathComponent:@"release-admission.json"];json(admissionPath,admission);
            NSString *admissionHash=digest([NSData dataWithContentsOfFile:admissionPath]);
            manifest[@"release_admission_sha256"]=admissionHash;json(manifestPath,manifest);
            NSString *publicationPath=[root stringByAppendingPathComponent:@"source-publication.json"];
            json(publicationPath,@{@"schema":@"botw-shader-source-publication-v1",@"profile":@"botw-switch-base-v0",
                @"package":[@"build/" stringByAppendingString:folder],@"manifest_sha256":digest([NSData dataWithContentsOfFile:manifestPath]),
                @"release_admission_sha256":admissionHash,@"source_adapter_sha256":adapterHash,@"captures":@1,
                @"input_manifest_sha256":inputHash,@"device_compilation_tested":@NO,@"guest_or_gpu_executed":@NO});
            NSMutableDictionary *package=[@{@"schema":@"botw-whole-shader-package-v1",@"profile":@"botw-switch-base-v0",
                @"chain":@"0123456789ab",@"directory":folder,@"publication_sha256":digest([NSData dataWithContentsOfFile:publicationPath]),
                @"admission_sha256":admissionHash} mutableCopy];
            if([scenario isEqual:@"publication-profile"])package[@"profile"]=@"botw-1.9.0";
            if([scenario isEqual:@"publication-path"])package[@"directory"]=@"../escape";
            json([root stringByAppendingPathComponent:@"shader-package.json"],package);
            if([scenario isEqual:@"publication-facts"])[@"tampered" writeToFile:factPath atomically:YES encoding:NSUTF8StringEncoding error:NULL];
            if([scenario isEqual:@"publication-source"])[@"tampered" writeToFile:[relocated stringByAppendingPathComponent:sourceName] atomically:YES encoding:NSUTF8StringEncoding error:NULL];
            if([scenario isEqual:@"publication-admission"])[@"{}" writeToFile:admissionPath atomically:YES encoding:NSUTF8StringEncoding error:NULL];
            NSArray *redirects=@[@"publication-botw-symlink",@"publication-root-symlink",@"publication-facts-symlink",@"publication-package-symlink"];
            if([redirects containsObject:scenario]){
                NSString *original=[scenario isEqual:@"publication-botw-symlink"]?[directory stringByAppendingPathComponent:@"botw"]:
                    [scenario isEqual:@"publication-root-symlink"]?root:
                    [scenario isEqual:@"publication-facts-symlink"]?[root stringByAppendingPathComponent:@"facts"]:relocated;
                NSString *outside=[directory stringByAppendingString:@".outside-owned"];
                [NSFileManager.defaultManager moveItemAtPath:original toPath:outside error:NULL];
                [NSFileManager.defaultManager createSymbolicLinkAtPath:original withDestinationPath:outside error:NULL];
            }
            char actual[4096]="unchanged",diagnostic[256];
            size_t capacity=[scenario isEqual:@"publication-capacity"]?2:sizeof actual;
            int status=whole_runtime_shader_publication(directory.fileSystemRepresentation,actual,capacity,diagnostic,sizeof diagnostic);
            BOOL success=[scenario isEqual:@"publication-valid"];
            int rc=(status==0)!=success||compile_calls?98:0;
            if(!success&&(strcmp(actual,"unchanged")||!diagnostic[0]))rc=99;
            if(success&&strcmp(actual,manifestPath.fileSystemRepresentation))rc=100;
            [NSFileManager.defaultManager removeItemAtPath:[directory stringByAppendingString:@".outside-owned"] error:NULL];
            [NSFileManager.defaultManager removeItemAtPath:directory error:NULL];return rc;
        }
        NSError *error=nil;
        BotwShaderManifest *loaded=[BotwShaderManifest manifestAtAbsolutePath:manifestPath error:&error];
        if([scenario hasPrefix:@"audit-"]){
            if(!loaded)return 94;
            if([scenario isEqual:@"audit-tamper"])[@"changed" writeToFile:sourcePath atomically:YES encoding:NSUTF8StringEncoding error:NULL];
            BOOL valid=[loaded validateArtifactsWithoutRetaining:&error];
            int rc=compile_calls||valid==[scenario isEqual:@"audit-tamper"]?95:0;
            if(!valid&&!error.description.length)rc=96;
            if([scenario isEqual:@"audit-does-not-retain"]){
                [@"changed later" writeToFile:sourcePath atomically:YES encoding:NSUTF8StringEncoding error:NULL];
                if([loaded artifactForKey:key captureSHA256:sha stage:BotwShaderStageVertex error:&error])rc=97;
            }
            [NSFileManager.defaultManager removeItemAtPath:directory error:NULL];return rc;
        }
        BotwShaderArtifact *artifact=loaded?[loaded artifactForKey:key captureSHA256:sha stage:BotwShaderStageVertex error:&error]:nil;
        BOOL positive=compiled||[@[@"valid",@"owned-after-mutation",@"compile-error",@"function-missing",@"function-type"] containsObject:scenario];
        if(positive&&!artifact){fprintf(stderr,"source artifact rejected: %s\n",error.description.UTF8String);return 91;}
        if(!positive){int rc=artifact?92:0;[NSFileManager.defaultManager removeItemAtPath:directory error:NULL];return rc;}
        if([scenario isEqual:@"owned-after-mutation"])[@"different bytes" writeToFile:sourcePath atomically:YES encoding:NSUTF8StringEncoding error:NULL];
        fail_compile=[scenario isEqual:@"compile-error"];fail_function=[scenario isEqual:@"function-missing"];wrong_type=[scenario isEqual:@"function-type"];
        id function=botw_shader_runtime_function((id<MTLDevice>)[OwnedDevice new],artifact,&error);
        BOOL expected=[scenario isEqual:@"valid"]||[scenario isEqual:@"owned-after-mutation"];
        int rc=compile_calls!=(compiled?0u:1u)||((function!=nil)!=expected)?93:0;
        [NSFileManager.defaultManager removeItemAtPath:directory error:NULL];return rc;
    }
}
