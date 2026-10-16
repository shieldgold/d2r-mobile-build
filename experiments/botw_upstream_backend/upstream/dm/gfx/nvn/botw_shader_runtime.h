#pragma once

#import <Metal/Metal.h>
#import "botw_shader_manifest.h"
#include "botw_shader.h"

NS_ASSUME_NONNULL_BEGIN

/* Absolute path of a runnable manifest-build.json. When the variable is
 * absent the legacy shader map loader is used; any explicit value must load
 * or the runtime is rejected without falling back to legacy files. */
#define BOTW_SHADER_MANIFEST_ENV "DM_BOTW_SHADER_MANIFEST"

FOUNDATION_EXPORT NSErrorDomain const BotwShaderRuntimeErrorDomain;

typedef NS_ERROR_ENUM(BotwShaderRuntimeErrorDomain, BotwShaderRuntimeError) {
    BotwShaderRuntimeErrorIdentity = 1,
    BotwShaderRuntimeErrorEntry,
    BotwShaderRuntimeErrorLibrary,
    BotwShaderRuntimeErrorFunction,
};

typedef NS_ENUM(NSInteger, BotwShaderRuntimeMode) {
    BotwShaderRuntimeModeLegacy = 0,
    BotwShaderRuntimeModeManifest,
    BotwShaderRuntimeModeRejected,
};

/* Pure selection used by the process latch and tests. A NULL value selects
 * legacy mode; every other value either loads a manifest or is rejected. */
BotwShaderRuntimeMode botw_shader_runtime_select(const char *_Nullable value,
                                                 BotwShaderManifest *_Nullable *_Nonnull manifest,
                                                 NSString *_Nullable *_Nonnull error);

/* Process-lifetime latch: reads BOTW_SHADER_MANIFEST_ENV exactly once. */
BotwShaderRuntimeMode botw_shader_runtime_mode(void);
BotwShaderManifest *_Nullable botw_shader_runtime_manifest(void);
NSString *_Nullable botw_shader_runtime_error(void);

/* Legacy loaders (shader map TSV, c1_full, warm-up lists and unhashed
 * readable-tile libraries) are allowed only when no manifest was requested. */
int botw_shader_runtime_allows_legacy_loaders(void);

/* Identity recorded with a cached shader; a zeroed identity never matches. */
typedef struct {
    char key[17];
    char sha256[65];
    uint32_t stage;
} botw_shader_identity;

void botw_shader_identity_set(botw_shader_identity *identity, const botw_shader_info *info);
int botw_shader_identity_matches(const botw_shader_identity *identity, const botw_shader_info *info);

/* Resolves an immutable program shader identity against the manifest. */
BotwShaderArtifact *_Nullable botw_shader_runtime_artifact(BotwShaderManifest *manifest,
                                                           const botw_shader_info *info,
                                                           NSError *_Nullable *_Nullable error);

/* Builds the reflection entry point from the artifact's owned metallib bytes.
 * The function must exist and its Metal type must match the artifact stage. */
id<MTLFunction> _Nullable botw_shader_runtime_function(id<MTLDevice> device,
                                                       BotwShaderArtifact *artifact,
                                                       NSError *_Nullable *_Nullable error);

NS_ASSUME_NONNULL_END

// Compare the live NVN device modes with the modes baked into a graphics artifact.
// Compute shaders are independent of these modes. Unknown live enum values fail.
BOOL botw_shader_runtime_device_matches(BotwShaderArtifact *_Nullable artifact, uint32_t origin, uint32_t depth);
