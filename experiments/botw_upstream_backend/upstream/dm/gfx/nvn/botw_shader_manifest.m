#import "botw_shader_manifest.h"

#import <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

NSErrorDomain const BotwShaderManifestErrorDomain = @"dev.botw-mobile.shader-manifest";

static NSString *const kManifestSchema = @"botw-shader-build-manifest-v1";
static NSString *const kReflectionSchema = @"botw-shader-reflection-v1";
/* A full-library manifest holds about 20,000 results of roughly 1.5 KiB. */
enum { ManifestLimit = 256 * 1024 * 1024, ReflectionLimit = 16 * 1024 * 1024 };
static const uint64_t MetallibLimit = 512ull * 1024 * 1024;

@interface BotwShaderArtifact ()
@property(nonatomic, readwrite, copy) NSString *key;
@property(nonatomic, readwrite, copy) NSString *captureSHA256;
@property(nonatomic, readwrite) BotwShaderStage stage;
@property(nonatomic, readwrite, copy) NSDictionary *reflection;
@property(nonatomic, readwrite, copy) NSData *metallib;
@end
@implementation BotwShaderArtifact @end

/* A manifest result whose published files have not been read yet. Its paths
 * and digests are fixed when the manifest is loaded; the files are read,
 * digest-checked and validated on first lookup. */
@interface BotwShaderEntry : NSObject
@property(nonatomic, copy) NSString *key, *captureSHA256, *reflectionPath, *reflectionSHA256, *metallibPath, *metallibSHA256;
@property(nonatomic) BotwShaderStage stage;
@property(nonatomic, strong) BotwShaderArtifact *artifact;
@property(nonatomic, strong) NSError *failure;
@end
@implementation BotwShaderEntry @end

@interface BotwShaderManifest ()
@property(nonatomic, readwrite, copy) NSString *chain;
@property(nonatomic, readwrite, copy) NSString *outputDirectory;
@property(nonatomic, readwrite, copy) NSDictionary<NSString *, BotwShaderEntry *> *entries;
@end

static BOOL fail(NSError **error, BotwShaderManifestError code, NSString *format, ...) NS_FORMAT_FUNCTION(3, 4);
static BOOL fail(NSError **error, BotwShaderManifestError code, NSString *format, ...) {
    if (error) {
        va_list ap;
        va_start(ap, format);
        NSString *message = [[NSString alloc] initWithFormat:format arguments:ap];
        va_end(ap);
        *error = [NSError errorWithDomain:BotwShaderManifestErrorDomain code:code
                                 userInfo:@{NSLocalizedDescriptionKey: message}];
    }
    return NO;
}

static BOOL is_dictionary(id value) { return [value isKindOfClass:NSDictionary.class]; }
static BOOL is_array(id value) { return [value isKindOfClass:NSArray.class]; }
static BOOL is_string(id value) { return [value isKindOfClass:NSString.class]; }
static BOOL is_boolean(id value) { return value && CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID(); }
static BOOL empty_array(id value) { return is_array(value) && [value count] == 0; }

static BOOL lowercase_hex(NSString *value, NSUInteger length) {
    if (!is_string(value) || value.length != length) return NO;
    const char *s = value.UTF8String;
    if (!s || strlen(s) != length) return NO;
    for (NSUInteger i = 0; i < length; ++i)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return NO;
    return YES;
}

static NSData *read_regular(NSString *path, uint64_t limit, NSError **error) {
    int fd = open(path.fileSystemRepresentation, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) { fail(error, BotwShaderManifestErrorRead, @"cannot open %@", path.lastPathComponent); return nil; }
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size <= 0 || (uint64_t)st.st_size > limit) {
        close(fd); fail(error, BotwShaderManifestErrorRead, @"%@ is not a bounded regular file", path.lastPathComponent); return nil;
    }
    size_t size = (size_t)st.st_size;
    void *bytes = malloc(size);
    if (!bytes) { close(fd); fail(error, BotwShaderManifestErrorRead, @"cannot allocate %zu bytes for %@", size, path.lastPathComponent); return nil; }
    size_t offset = 0;
    while (offset < size) {
        ssize_t count = read(fd, (uint8_t *)bytes + offset, size - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        offset += (size_t)count;
    }
    close(fd);
    if (offset != size) { free(bytes); fail(error, BotwShaderManifestErrorRead, @"short read from %@", path.lastPathComponent); return nil; }
    return [[NSData alloc] initWithBytesNoCopy:bytes length:size freeWhenDone:YES];
}

static NSDictionary *json_dictionary(NSData *data, NSString *name, NSError **error) {
    NSError *json_error = nil;
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:&json_error];
    if (!is_dictionary(value)) {
        fail(error, BotwShaderManifestErrorInvalidArtifact, @"%@ is not a JSON object: %@", name,
             json_error.localizedDescription ?: @"invalid root type");
        return nil;
    }
    return value;
}

static NSString *sha256(NSData *data) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    char hex[CC_SHA256_DIGEST_LENGTH * 2 + 1];
    for (unsigned i = 0; i < CC_SHA256_DIGEST_LENGTH; ++i) snprintf(hex + i * 2, 3, "%02x", digest[i]);
    return @(hex);
}

static BOOL stage_value(NSDictionary *capture, BotwShaderStage *stage) {
    NSString *name = capture[@"stage_name"];
    NSNumber *number = capture[@"stage"];
    if (!is_string(name) || ![number isKindOfClass:NSNumber.class] || is_boolean(number)) return NO;
    NSInteger n = number.integerValue;
    if (number.doubleValue != (double)n) return NO;
    if (([name isEqual:@"vertex"] && n == BotwShaderStageVertex) ||
        ([name isEqual:@"fragment"] && n == BotwShaderStageFragment) ||
        ([name isEqual:@"compute"] && n == BotwShaderStageCompute)) {
        *stage = (BotwShaderStage)n;
        return YES;
    }
    return NO;
}

static NSString *reflection_stage(BotwShaderStage stage) {
    switch (stage) {
        case BotwShaderStageVertex: return @"vs";
        case BotwShaderStageFragment: return @"ps";
        case BotwShaderStageCompute: return @"cs";
    }
    return nil;
}

static BOOL exact_file(NSDictionary *files, NSString *kind, NSString *path, NSString **digest) {
    NSDictionary *metadata = files[kind];
    if (!is_dictionary(metadata) || metadata.count != 2 || ![metadata[@"path"] isEqual:path] ||
        !lowercase_hex(metadata[@"sha256"], 64)) return NO;
    *digest = metadata[@"sha256"];
    return YES;
}

static BOOL validate_reflection(NSDictionary *reflection, NSString *key, NSString *captureSHA,
                                BotwShaderStage stage, NSString *chain, NSError **error) {
    NSString *runtime_key = [key stringByAppendingString:@"_b"];
    if (![reflection[@"schema"] isEqual:kReflectionSchema] || ![reflection[@"route"] isEqual:@"B"] ||
        !is_boolean(reflection[@"runnable"]) || ![reflection[@"runnable"] boolValue] ||
        ![reflection[@"chain"] isEqual:chain] || ![reflection[@"key"] isEqual:runtime_key] ||
        ![reflection[@"dm_ctl"] isEqual:key] || ![reflection[@"sha256"] isEqual:captureSHA] ||
        ![reflection[@"stage"] isEqual:reflection_stage(stage)] ||
        !empty_array(reflection[@"unknowns"]) || !empty_array(reflection[@"conflicts"]) ||
        !empty_array(reflection[@"notes"]))
        return fail(error, BotwShaderManifestErrorInvalidArtifact,
                    @"reflection identity or runnable gates do not match %@", key);
    id state = reflection[@"device_state"];
    if (stage == BotwShaderStageCompute) {
        if (state && state != NSNull.null)
            return fail(error, BotwShaderManifestErrorInvalidArtifact, @"compute shader %@ has graphics device state", key);
    } else if (!is_dictionary(state) || [state count] != 2 ||
               ![@[@"upper_left", @"lower_left"] containsObject:state[@"window_origin_mode"] ?: NSNull.null] ||
               ![@[@"near_is_zero", @"near_is_minus_w"] containsObject:state[@"depth_mode"] ?: NSNull.null]) {
        return fail(error, BotwShaderManifestErrorInvalidArtifact, @"graphics shader %@ has missing or invalid device state", key);
    }
    return YES;
}

@implementation BotwShaderManifest

- (NSUInteger)count { return self.entries.count; }

+ (instancetype)manifestAtAbsolutePath:(NSString *)path error:(NSError **)error {
    if (error) *error = nil;
    if (!is_string(path) || !path.isAbsolutePath) {
        fail(error, BotwShaderManifestErrorInvalidPath, @"shader manifest path must be absolute"); return nil;
    }
    struct stat input_stat;
    if (lstat(path.fileSystemRepresentation, &input_stat) || !S_ISREG(input_stat.st_mode)) {
        fail(error, BotwShaderManifestErrorInvalidPath, @"shader manifest path must name a regular file"); return nil;
    }
    char resolved[PATH_MAX];
    if (!realpath(path.fileSystemRepresentation, resolved)) {
        fail(error, BotwShaderManifestErrorInvalidPath, @"cannot resolve shader manifest path"); return nil;
    }
    NSString *manifest_path = @(resolved);
    NSString *output = manifest_path.stringByDeletingLastPathComponent;
    if (![manifest_path.lastPathComponent isEqual:@"manifest-build.json"]) {
        fail(error, BotwShaderManifestErrorInvalidPath, @"shader manifest must be named manifest-build.json"); return nil;
    }

    NSData *manifest_data = read_regular(manifest_path, ManifestLimit, error);
    if (!manifest_data) return nil;
    NSDictionary *manifest = json_dictionary(manifest_data, @"manifest-build.json", error);
    if (!manifest) return nil;
    NSString *chain = manifest[@"chain"];
    if (![manifest[@"schema"] isEqual:kManifestSchema]) {
        fail(error, BotwShaderManifestErrorInvalidManifest, @"manifest schema is invalid"); return nil;
    }
    if (![manifest[@"mode"] isEqual:@"build"] || !is_boolean(manifest[@"runnable"]) || ![manifest[@"runnable"] boolValue]) {
        fail(error, BotwShaderManifestErrorInvalidManifest, @"manifest is not a runnable build"); return nil;
    }
    if (!lowercase_hex(chain, 12)) {
        fail(error, BotwShaderManifestErrorInvalidManifest, @"manifest chain is invalid"); return nil;
    }
    if (![manifest[@"output"] isEqual:output] ||
        ![output.lastPathComponent isEqual:[@"botw-b-" stringByAppendingString:chain]]) {
        fail(error, BotwShaderManifestErrorInvalidManifest,
             @"manifest output directory %@ does not match package %@", manifest[@"output"], output); return nil;
    }
    NSArray *results = manifest[@"results"];
    if (!is_array(results) || !results.count) {
        fail(error, BotwShaderManifestErrorInvalidManifest, @"manifest has no selected shader results"); return nil;
    }

    NSMutableDictionary<NSString *, BotwShaderEntry *> *entries = [NSMutableDictionary dictionary];
    for (id item in results) {
        if (!is_dictionary(item)) { fail(error, BotwShaderManifestErrorInvalidManifest, @"manifest result is not an object"); return nil; }
        NSDictionary *result = item;
        NSString *key = result[@"key"];
        NSDictionary *capture = result[@"capture"];
        NSDictionary *files = result[@"files"];
        BotwShaderStage stage;
        NSString *capture_key = is_dictionary(capture) ? capture[@"key"] : nil;
        NSString *capture_sha = is_dictionary(capture) ? capture[@"sha256"] : nil;
        if (!lowercase_hex(key, 16) || entries[key] || ![result[@"status"] isEqual:@"ready"] ||
            !is_boolean(result[@"runnable"]) || ![result[@"runnable"] boolValue] ||
            !empty_array(result[@"errors"]) || !empty_array(result[@"unknowns"]) || !empty_array(result[@"conflicts"]) ||
            !is_dictionary(capture) || ![capture_key isEqual:key] || !lowercase_hex(capture_sha, 64) ||
            ![[capture_sha substringToIndex:16] isEqual:key] || !stage_value(capture, &stage) || !is_dictionary(files)) {
            fail(error, BotwShaderManifestErrorInvalidManifest, @"manifest result identity or runnable gates are invalid");
            return nil;
        }
        NSString *base = [key stringByAppendingString:@"_b"];
        NSString *reflection_path = [output stringByAppendingPathComponent:[base stringByAppendingString:@".json"]];
        NSString *metallib_path = [output stringByAppendingPathComponent:[base stringByAppendingString:@".metallib"]];
        NSString *msl_path = [output stringByAppendingPathComponent:[base stringByAppendingString:@".metal"]];
        NSDictionary *msl = files[@"msl"];
        NSString *reflection_hash, *metallib_hash;
        if (files.count != 3 || !is_dictionary(msl) || msl.count != 1 || ![msl[@"path"] isEqual:msl_path] ||
            !exact_file(files, @"json", reflection_path, &reflection_hash) ||
            !exact_file(files, @"metallib", metallib_path, &metallib_hash)) {
            fail(error, BotwShaderManifestErrorInvalidManifest, @"manifest artifact names or hashes are invalid for %@", key);
            return nil;
        }
        BotwShaderEntry *entry = [BotwShaderEntry new];
        entry.key = key; entry.captureSHA256 = capture_sha; entry.stage = stage;
        entry.reflectionPath = reflection_path; entry.reflectionSHA256 = reflection_hash;
        entry.metallibPath = metallib_path; entry.metallibSHA256 = metallib_hash;
        entries[key] = entry;
    }
    BotwShaderManifest *loaded = [BotwShaderManifest new];
    loaded.chain = chain; loaded.outputDirectory = output; loaded.entries = entries;
    return loaded;
}

/* Reads one entry's published files. The bytes are kept, so later changes to
 * the files never affect an artifact that has been validated. */
- (BotwShaderArtifact *)loadEntry:(BotwShaderEntry *)entry error:(NSError **)error {
    NSData *reflection_data = read_regular(entry.reflectionPath, ReflectionLimit, error);
    NSData *metallib_data = reflection_data ? read_regular(entry.metallibPath, MetallibLimit, error) : nil;
    if (!reflection_data || !metallib_data) return nil;
    if (![sha256(reflection_data) isEqual:entry.reflectionSHA256] || ![sha256(metallib_data) isEqual:entry.metallibSHA256]) {
        fail(error, BotwShaderManifestErrorInvalidArtifact, @"published artifact digest mismatch for %@", entry.key);
        return nil;
    }
    NSDictionary *reflection = json_dictionary(reflection_data, entry.reflectionPath.lastPathComponent, error);
    if (!reflection || !validate_reflection(reflection, entry.key, entry.captureSHA256, entry.stage, self.chain, error))
        return nil;
    BotwShaderArtifact *artifact = [BotwShaderArtifact new];
    artifact.key = entry.key; artifact.captureSHA256 = entry.captureSHA256; artifact.stage = entry.stage;
    artifact.reflection = reflection; artifact.metallib = metallib_data;
    return artifact;
}

- (BotwShaderArtifact *)artifactForKey:(NSString *)key captureSHA256:(NSString *)captureSHA256
                                  stage:(BotwShaderStage)stage error:(NSError **)error {
    if (error) *error = nil;
    BotwShaderEntry *entry = self.entries[key];
    if (!entry || ![entry.captureSHA256 isEqual:captureSHA256] || entry.stage != stage) {
        fail(error, BotwShaderManifestErrorProgramMismatch,
             @"immutable program shader identity does not match manifest key %@", key);
        return nil;
    }
    @synchronized (entry) {
        if (!entry.artifact && !entry.failure) {
            NSError *load_error = nil;
            entry.artifact = [self loadEntry:entry error:&load_error];
            if (!entry.artifact)
                entry.failure = load_error ?: [NSError errorWithDomain:BotwShaderManifestErrorDomain
                                                                  code:BotwShaderManifestErrorInvalidArtifact
                                                              userInfo:@{NSLocalizedDescriptionKey: @"artifact load failed"}];
        }
        /* A failed artifact keeps failing; it is never retried or replaced. */
        if (entry.failure && error) *error = entry.failure;
        return entry.artifact;
    }
}

@end
