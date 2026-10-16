#import "botw_shader_runtime.h"

#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

NSErrorDomain const BotwShaderRuntimeErrorDomain = @"dev.botw-mobile.shader-runtime";

static BOOL fail(NSError **error, BotwShaderRuntimeError code, NSString *format, ...) NS_FORMAT_FUNCTION(3, 4);
static BOOL fail(NSError **error, BotwShaderRuntimeError code, NSString *format, ...) {
    if (error) {
        va_list ap;
        va_start(ap, format);
        NSString *message = [[NSString alloc] initWithFormat:format arguments:ap];
        va_end(ap);
        *error = [NSError errorWithDomain:BotwShaderRuntimeErrorDomain code:code
                                 userInfo:@{NSLocalizedDescriptionKey: message}];
    }
    return NO;
}

BotwShaderRuntimeMode botw_shader_runtime_select(const char *value, BotwShaderManifest **manifest, NSString **error) {
    *manifest = nil;
    *error = nil;
    if (!value) return BotwShaderRuntimeModeLegacy;
    if (!*value) {
        *error = @BOTW_SHADER_MANIFEST_ENV " is set but empty";
        return BotwShaderRuntimeModeRejected;
    }
    NSString *path = [NSFileManager.defaultManager stringWithFileSystemRepresentation:value length:strlen(value)];
    NSError *load_error = nil;
    BotwShaderManifest *loaded = path ? [BotwShaderManifest manifestAtAbsolutePath:path error:&load_error] : nil;
    if (!loaded) {
        *error = load_error.localizedDescription ?: @"shader manifest path is not representable";
        return BotwShaderRuntimeModeRejected;
    }
    *manifest = loaded;
    return BotwShaderRuntimeModeManifest;
}

static dispatch_once_t g_once;
static BotwShaderRuntimeMode g_mode;
static BotwShaderManifest *g_manifest;
static NSString *g_error;

static void latch(void) {
    dispatch_once(&g_once, ^{
        BotwShaderManifest *manifest = nil;
        NSString *error = nil;
        g_mode = botw_shader_runtime_select(getenv(BOTW_SHADER_MANIFEST_ENV), &manifest, &error);
        g_manifest = manifest;
        g_error = error;
    });
}

BotwShaderRuntimeMode botw_shader_runtime_mode(void) { latch(); return g_mode; }
BotwShaderManifest *botw_shader_runtime_manifest(void) { latch(); return g_manifest; }
NSString *botw_shader_runtime_error(void) { latch(); return g_error; }
int botw_shader_runtime_allows_legacy_loaders(void) { return botw_shader_runtime_mode() == BotwShaderRuntimeModeLegacy; }

static int bounded_string(const char *s, size_t size) { return memchr(s, 0, size) != NULL && *s; }

void botw_shader_identity_set(botw_shader_identity *identity, const botw_shader_info *info) {
    memset(identity, 0, sizeof *identity);
    if (!bounded_string(info->key, sizeof info->key) || !bounded_string(info->sha256, sizeof info->sha256)) return;
    memcpy(identity->key, info->key, sizeof identity->key);
    memcpy(identity->sha256, info->sha256, sizeof identity->sha256);
    identity->stage = info->stage;
}

int botw_shader_identity_matches(const botw_shader_identity *identity, const botw_shader_info *info) {
    return bounded_string(identity->key, sizeof identity->key) && bounded_string(identity->sha256, sizeof identity->sha256) &&
           bounded_string(info->key, sizeof info->key) && bounded_string(info->sha256, sizeof info->sha256) &&
           !strcmp(identity->key, info->key) && !strcmp(identity->sha256, info->sha256) && identity->stage == info->stage;
}

static int valid_stage(uint32_t stage) {
    return stage == BotwShaderStageVertex || stage == BotwShaderStageFragment || stage == BotwShaderStageCompute;
}

BotwShaderArtifact *botw_shader_runtime_artifact(BotwShaderManifest *manifest, const botw_shader_info *info, NSError **error) {
    if (error) *error = nil;
    if (!bounded_string(info->key, sizeof info->key) || !bounded_string(info->sha256, sizeof info->sha256)) {
        fail(error, BotwShaderRuntimeErrorIdentity, @"program shader identity is missing its key or full SHA-256");
        return nil;
    }
    if (!valid_stage(info->stage)) {
        fail(error, BotwShaderRuntimeErrorIdentity, @"program shader %s has unsupported stage %u", info->key, info->stage);
        return nil;
    }
    NSError *lookup_error = nil;
    BotwShaderArtifact *artifact = [manifest artifactForKey:@(info->key) captureSHA256:@(info->sha256)
                                                      stage:(BotwShaderStage)info->stage error:&lookup_error];
    if (!artifact) {
        fail(error, BotwShaderRuntimeErrorIdentity, @"program shader %s (sha256 %s, stage %u) is not in the manifest: %@",
             info->key, info->sha256, info->stage, lookup_error.localizedDescription);
        return nil;
    }
    return artifact;
}

static MTLFunctionType function_type(BotwShaderStage stage) {
    switch (stage) {
        case BotwShaderStageVertex: return MTLFunctionTypeVertex;
        case BotwShaderStageFragment: return MTLFunctionTypeFragment;
        case BotwShaderStageCompute: return MTLFunctionTypeKernel;
    }
    return MTLFunctionTypeVisible;
}

id<MTLFunction> botw_shader_runtime_function(id<MTLDevice> device, BotwShaderArtifact *artifact, NSError **error) {
    if (error) *error = nil;
    NSString *entry = artifact.reflection[@"entry"];
    if (![entry isKindOfClass:NSString.class] || !entry.length) {
        fail(error, BotwShaderRuntimeErrorEntry, @"reflection for %@ has no entry point", artifact.key);
        return nil;
    }
    NSData *bytes = artifact.metallib;
    // Only compiled libraries are accepted; there is no source compilation fallback.
    if (bytes.length < 4 || memcmp(bytes.bytes, "MTLB", 4)) {
        fail(error, BotwShaderRuntimeErrorLibrary, @"artifact %@ is not a compiled Metal library", artifact.key);
        return nil;
    }
    // The destructor block retains the owned NSData until Metal releases the dispatch data.
    dispatch_data_t data = dispatch_data_create(bytes.bytes, bytes.length, dispatch_get_global_queue(QOS_CLASS_UTILITY, 0),
                                                ^{ (void)bytes; });
    NSError *library_error = nil;
    id<MTLLibrary> library = [device newLibraryWithData:data error:&library_error];
    if (!library) {
        fail(error, BotwShaderRuntimeErrorLibrary, @"artifact %@ library creation failed: %@", artifact.key,
             library_error.localizedDescription ?: @"unknown error");
        return nil;
    }
    id<MTLFunction> function = [library newFunctionWithName:entry];
    if (!function) {
        fail(error, BotwShaderRuntimeErrorFunction, @"artifact %@ has no function named %@", artifact.key, entry);
        return nil;
    }
    if (function.functionType != function_type(artifact.stage)) {
        fail(error, BotwShaderRuntimeErrorFunction, @"artifact %@ function %@ has Metal type %lu, stage %ld requires %lu",
             artifact.key, entry, (unsigned long)function.functionType, (long)artifact.stage,
             (unsigned long)function_type(artifact.stage));
        return nil;
    }
    return function;
}

BOOL botw_shader_runtime_device_matches(BotwShaderArtifact *artifact, uint32_t origin, uint32_t depth) {
    if (!artifact) return NO;
    if (artifact.stage == BotwShaderStageCompute) return YES;
    if (origin > 1 || depth > 1) return NO;
    NSDictionary *state = artifact.reflection[@"device_state"];
    return [state[@"window_origin_mode"] isEqual:(origin ? @"upper_left" : @"lower_left")] &&
           [state[@"depth_mode"] isEqual:(depth ? @"near_is_zero" : @"near_is_minus_w")];
}
