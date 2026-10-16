#pragma once

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

FOUNDATION_EXPORT NSErrorDomain const BotwShaderManifestErrorDomain;

typedef NS_ERROR_ENUM(BotwShaderManifestErrorDomain, BotwShaderManifestError) {
    BotwShaderManifestErrorInvalidPath = 1,
    BotwShaderManifestErrorRead,
    BotwShaderManifestErrorInvalidManifest,
    BotwShaderManifestErrorInvalidArtifact,
    BotwShaderManifestErrorProgramMismatch,
};

typedef NS_ENUM(NSInteger, BotwShaderStage) {
    BotwShaderStageVertex = 0,
    BotwShaderStageFragment = 1,
    BotwShaderStageCompute = 5,
};

@interface BotwShaderArtifact : NSObject
@property(nonatomic, readonly, copy) NSString *key;
@property(nonatomic, readonly, copy) NSString *captureSHA256;
@property(nonatomic, readonly) BotwShaderStage stage;
@property(nonatomic, readonly, copy) NSDictionary *reflection;
@property(nonatomic, readonly, copy) NSData *metallib;
@end

@interface BotwShaderManifest : NSObject
@property(nonatomic, readonly, copy) NSString *chain;
@property(nonatomic, readonly, copy) NSString *outputDirectory;
@property(nonatomic, readonly) NSUInteger count;

/* Validates the manifest and every result's identity, gates and artifact
 * paths and digests. The published files themselves are read on first use. */
+ (nullable instancetype)manifestAtAbsolutePath:(NSString *)path
                                           error:(NSError * _Nullable * _Nullable)error;

/* Returns the artifact for an immutable program identity. On first use its
 * reflection and metallib are read, checked against the manifest digests and
 * validated, and the bytes are retained; a failure is permanent for the key. */
- (nullable BotwShaderArtifact *)artifactForKey:(NSString *)key
                                  captureSHA256:(NSString *)captureSHA256
                                           stage:(BotwShaderStage)stage
                                           error:(NSError * _Nullable * _Nullable)error;
@end

NS_ASSUME_NONNULL_END
