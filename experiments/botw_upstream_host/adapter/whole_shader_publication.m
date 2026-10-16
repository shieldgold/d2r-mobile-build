/* SPDX-License-Identifier: MIT */
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#import "botw_shader_manifest.h"
#include "whole_shader_publication.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "whole_shader_publication_files.h"

static NSArray *fact_fields(NSDictionary *facts){
    NSMutableArray *fields=[NSMutableArray array];
    for(NSString *table in @[@"constant_buffers",@"textures",@"images",@"storage_buffers",@"uniform_pointers",@"attributes",@"outputs"]){
        id mapping=facts[table];if(mapping&&!object(mapping))return nil;
        for(id key in mapping){if(!string(key))return nil;[fields addObject:[table stringByAppendingFormat:@".%@",key]];}
    }
    for(NSString *name in @[@"compute",@"device_state"])if(facts[name])[fields addObject:name];
    return [fields sortedArrayUsingSelector:@selector(compare:)];
}
int whole_runtime_shader_publication(const char *bundle,char *manifest_out,size_t manifest_capacity,
        char *error,size_t error_capacity){
    if(error&&error_capacity)error[0]=0;
    if(!bundle||!manifest_out||!manifest_capacity||(error_capacity&&!error))
        return denied(error,error_capacity,@"invalid caller buffers");
    @autoreleasepool {
        NSString *bundle_path=[NSFileManager.defaultManager stringWithFileSystemRepresentation:bundle length:strlen(bundle)];
        if(!bundle_path.isAbsolutePath)return denied(error,error_capacity,@"bundle path must be absolute");
        if(!contained_directories(bundle,@[@"botw",@"shaders"]))
            return denied(error,error_capacity,@"shader root must contain actual bundle directories");
        NSString *root=[bundle_path stringByAppendingPathComponent:@"botw/shaders"];
        NSDictionary *package=document(read_regular([root stringByAppendingPathComponent:@"shader-package.json"],65536));
        if(![package[@"schema"] isEqual:@"botw-whole-shader-package-v1"]||
                ![package[@"profile"] isEqual:@"botw-switch-base-v0"]||
                !hex(package[@"publication_sha256"],64)||!hex(package[@"admission_sha256"],64)||
                !hex(package[@"chain"],12))return denied(error,error_capacity,@"missing explicit base-v0 source package");
        NSString *directory=[@"botw-s-" stringByAppendingString:package[@"chain"]];
        if(![package[@"directory"] isEqual:directory])return denied(error,error_capacity,@"invalid package directory");
        if(!contained_directories(bundle,@[@"botw",@"shaders",@"facts"])||
                !contained_directories(bundle,@[@"botw",@"shaders",directory]))
            return denied(error,error_capacity,@"facts and artifact directories must remain inside the bundle");
        NSData *publication_bytes=read_regular([root stringByAppendingPathComponent:@"source-publication.json"],65536);
        NSData *admission_bytes=read_regular([root stringByAppendingPathComponent:@"release-admission.json"],64*1024*1024);
        if(!publication_bytes||!admission_bytes||![digest(publication_bytes) isEqual:package[@"publication_sha256"]]||
                ![digest(admission_bytes) isEqual:package[@"admission_sha256"]])
            return denied(error,error_capacity,@"publication/admission digest mismatch");
        NSDictionary *publication=document(publication_bytes),*admission=document(admission_bytes);
        NSDictionary *captures=admission[@"captures"];
        if(![publication[@"schema"] isEqual:@"botw-shader-source-publication-v1"]||
                ![publication[@"profile"] isEqual:@"botw-switch-base-v0"]||
                ![publication[@"package"] isEqual:[@"build/" stringByAppendingString:directory]]||
                ![publication[@"release_admission_sha256"] isEqual:package[@"admission_sha256"]]||
                !hex(publication[@"manifest_sha256"],64)||!hex(publication[@"source_adapter_sha256"],64)||
                !boolean(publication[@"device_compilation_tested"],NO)||!boolean(publication[@"guest_or_gpu_executed"],NO)||
                ![admission[@"schema"] isEqual:@"botw-shader-release-admission-v1"]||
                !boolean(admission[@"approved"],YES)||![admission[@"profile"] isEqual:@"botw-switch-base-v0"]||
                ![admission[@"upstream_commit"] isEqual:@"21f026e0c7fd9fee575a1bfa8d925553bb4db88e"]||
                !hex(admission[@"input_manifest_sha256"],64)||
                ![publication[@"input_manifest_sha256"] isEqual:admission[@"input_manifest_sha256"]]||
                !object(captures)||!captures.count||!count_matches(publication[@"captures"],captures.count))
            return denied(error,error_capacity,@"source batch lacks matching admission/identity");
        NSString *path=[[root stringByAppendingPathComponent:directory] stringByAppendingPathComponent:@"manifest-build.json"];
        NSData *manifest_bytes=read_regular(path,256*1024*1024);
        if(!manifest_bytes||![digest(manifest_bytes) isEqual:publication[@"manifest_sha256"]])
            return denied(error,error_capacity,@"manifest digest mismatch");
        NSDictionary *manifest=document(manifest_bytes);
        NSArray *results=manifest[@"results"];
        if(![manifest[@"schema"] isEqual:@"botw-shader-source-manifest-v1"]||
                ![manifest[@"chain"] isEqual:package[@"chain"]]||
                ![manifest[@"release_admission_sha256"] isEqual:package[@"admission_sha256"]]||
                ![manifest[@"source_adapter_sha256"] isEqual:publication[@"source_adapter_sha256"]]||
                !array(results)||results.count!=captures.count)
            return denied(error,error_capacity,@"manifest is not the complete admitted source batch");
        NSMutableSet *seen=[NSMutableSet set];
        for(NSDictionary *result in results){
            @autoreleasepool {
                if(!object(result))return denied(error,error_capacity,@"invalid source result");
                NSString *key=result[@"key"];NSDictionary *capture=result[@"capture"];
                NSDictionary *approved=hex(key,16)?captures[key]:nil;
                if(!object(approved)||[seen containsObject:key]||!object(capture)||
                        !hex(approved[@"capture_sha256"],64)||!hex(approved[@"facts_sha256"],64)||
                        !hex(approved[@"review_sha256"],64)||
                        ![capture[@"sha256"] isEqual:approved[@"capture_sha256"]])
                    return denied(error,error_capacity,@"capture/facts review identity mismatch");
                [seen addObject:key];
                NSData *facts_bytes=read_regular([[root stringByAppendingPathComponent:@"facts"]
                    stringByAppendingPathComponent:[key stringByAppendingString:@".facts.json"]],1024*1024);
                NSDictionary *facts=document(facts_bytes);
                NSString *provenance=facts[@"provenance"];
                NSDictionary *reflection=document(read_regular([[root stringByAppendingPathComponent:directory]
                    stringByAppendingPathComponent:[key stringByAppendingString:@"_b.json"]],16*1024*1024));
                if(!facts_bytes||![digest(facts_bytes) isEqual:approved[@"facts_sha256"]]||
                        ![facts[@"schema"] isEqual:@"botw-shader-facts-v1"]||![facts[@"key"] isEqual:key]||
                        ![facts[@"sha256"] isEqual:approved[@"capture_sha256"]]||
                        ![facts[@"stage"] isEqual:capture[@"stage_name"]]||!string(provenance)||
                        ![provenance stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length||
                        [[provenance stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].lowercaseString hasPrefix:@"botw-derive-"]||
                        ![fact_fields(facts) isEqual:approved[@"fields"]]||
                        ![reflection[@"facts_sha256"] isEqual:approved[@"facts_sha256"]])
                    return denied(error,error_capacity,@"detached established facts differ from admitted reflection");
            }
        }
        NSError *failure=nil;
        BotwShaderManifest *loaded=[BotwShaderManifest manifestAtAbsolutePath:path error:&failure];
        if(!loaded||![loaded validateArtifactsWithoutRetaining:&failure])
            return denied(error,error_capacity,failure.localizedDescription?:@"original artifact gates rejected package");
        const char *value=path.fileSystemRepresentation;size_t length=strlen(value);
        if(length>=manifest_capacity)return denied(error,error_capacity,@"manifest output capacity is insufficient");
        memcpy(manifest_out,value,length+1);return 0;
    }
}
