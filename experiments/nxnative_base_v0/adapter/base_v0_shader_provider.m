#import "base_v0_shader_provider.h"
#include "whole_shader_bundle_publication.h"
#include "whole_shader_publication_files.h"

/* New wrapper inputs have the same bounded regular-file contract. Open them
 * nonblocking so even an invalid FIFO cannot stall validation. */
static NSData *bridge_bytes(NSString *path,size_t limit) {
    int fd=open(path.fileSystemRepresentation,O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);
    if(fd<0)return nil;struct stat before,after;
    if(fstat(fd,&before)||!S_ISREG(before.st_mode)||before.st_size<=0||(uint64_t)before.st_size>limit){close(fd);return nil;}
    NSMutableData *bytes=[NSMutableData dataWithLength:(NSUInteger)before.st_size];size_t used=0;
    while(used<bytes.length){ssize_t n=read(fd,(uint8_t *)bytes.mutableBytes+used,bytes.length-used);if(n<0&&errno==EINTR)continue;if(n<=0)break;used+=(size_t)n;}
    int changed=fstat(fd,&after)||before.st_dev!=after.st_dev||before.st_ino!=after.st_ino||before.st_size!=after.st_size||
        before.st_mtimespec.tv_sec!=after.st_mtimespec.tv_sec||before.st_mtimespec.tv_nsec!=after.st_mtimespec.tv_nsec||
        before.st_ctimespec.tv_sec!=after.st_ctimespec.tv_sec||before.st_ctimespec.tv_nsec!=after.st_ctimespec.tv_nsec;
    close(fd);return !changed&&used==bytes.length?[bytes copy]:nil;
}

static BOOL bridge_mode(id n){return [n isKindOfClass:NSNumber.class] &&
    CFGetTypeID((__bridge CFTypeRef)n)!=CFBooleanGetTypeID() && !CFNumberIsFloatType((__bridge CFNumberRef)n) &&
    ([n isEqual:@0]||[n isEqual:@1]);}

@interface NxBaseShaderProvider ()
@property(nonatomic) WholeShaderConditionalManifest *manifest;
@property(nonatomic,copy) NSDictionary<NSString*,NSDictionary*> *identities;
@end
static id invalid_package(NSError **error) {
    if(error)*error=[NSError errorWithDomain:BotwShaderRuntimeErrorDomain code:BotwShaderRuntimeErrorIdentity userInfo:@{NSLocalizedDescriptionKey:@"base-v0 signed source package rejected"}];return nil;
}
@implementation NxBaseShaderProvider
/* The bundle entry above is the sole production caller: it first verifies the
 * complete original publication and owns this bridge snapshot. This helper is
 * also exercised by owned CPU fixtures with original manifest objects. */
+ (instancetype)checkedManifest:(WholeShaderConditionalManifest *)manifest bridge:(NSDictionary *)bridge
                     admission:(NSDictionary *)admission error:(NSError **)error {
#define REQUIRE(v) do {if(!(v)){if(error)*error=[NSError errorWithDomain:BotwShaderRuntimeErrorDomain code:BotwShaderRuntimeErrorIdentity userInfo:@{NSLocalizedDescriptionKey:@"base-v0 shader bridge identity rejected"}];return nil;}}while(0)
    REQUIRE([manifest isKindOfClass:WholeShaderConditionalManifest.class] &&
        [bridge[@"schema"] isEqual:@"nxnative-base-v0-shader-key-bridge-v1"] &&
        [bridge[@"profile"] isEqual:@"botw-switch-base-v0"] && [bridge[@"game_version"] isEqual:@"1.0.0"] &&
        array(bridge[@"rows"]) && object(admission[@"captures"]));
    NSMutableDictionary *identities=[NSMutableDictionary dictionary],*captures=[NSMutableDictionary dictionary];
    NSMutableSet *allNative=[NSMutableSet set],*allShort=[NSMutableSet set];
    for(id row in bridge[@"rows"]) {
        REQUIRE(object(row) && hex(row[@"native_sha256"],64) && hex(row[@"native_key"],16) &&
            [row[@"native_key"] isEqual:[row[@"native_sha256"] substringToIndex:16]] &&
            ![allNative containsObject:row[@"native_sha256"]] && ![allShort containsObject:row[@"native_key"]]);
        [allNative addObject:row[@"native_sha256"]];[allShort addObject:row[@"native_key"]];
        REQUIRE(hex(row[@"capture_key"],16) && hex(row[@"capture_sha256"],64) &&
            [row[@"capture_key"] isEqual:[row[@"capture_sha256"] substringToIndex:16]]);
        NSArray *fields=@[@"version",@"stage",@"control_size",@"code_size",@"code_offset",@"c1_offset",@"c1_size",@"native_hashed_control_size"];
        for(NSString *field in fields) {id n=row[field];REQUIRE([n isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)n)!=CFBooleanGetTypeID() &&
            !CFNumberIsFloatType((__bridge CFNumberRef)n) && [n longLongValue]>=0 && [n unsignedLongLongValue]<=UINT32_MAX);}
        REQUIRE(([row[@"version"] unsignedIntValue]==8 || [row[@"version"] unsignedIntValue]==9) && [row[@"stage"] unsignedIntValue]<=2 &&
            [row[@"stage_name"] isEqual:(@[@"vertex",@"fragment",@"geometry"][[row[@"stage"] unsignedIntValue]])] &&
            [row[@"control_size"] unsignedIntValue]==[row[@"native_hashed_control_size"] unsignedIntValue] &&
            [row[@"control_size"] unsignedIntValue]>=0x760 && [row[@"control_size"] unsignedIntValue]<=0x880 &&
            [row[@"c1_offset"] unsignedLongLongValue]+[row[@"c1_size"] unsignedLongLongValue]==[row[@"code_size"] unsignedLongLongValue]);
        identities[row[@"native_sha256"]]=row;
        if([row[@"status"] isEqual:@"only_identity"]) { REQUIRE(array(row[@"conditional_modes"]) && ![row[@"conditional_modes"] count]);continue; }
        REQUIRE([row[@"status"] isEqual:@"conditional_reference_only"] && [row[@"stage"] unsignedIntValue]<=1 && !captures[row[@"capture_key"]]);
        NSDictionary *approved=admission[@"captures"][row[@"capture_key"]];
        REQUIRE(object(approved) && [approved[@"capture_sha256"] isEqual:row[@"capture_sha256"]] &&
            [approved[@"stage_name"] isEqual:row[@"stage_name"]] && array(approved[@"variants"]) && [approved[@"variants"] count]==4);
        NSArray *modes=row[@"conditional_modes"];REQUIRE(array(modes)&&modes.count==4);
        NSMutableSet *seen=[NSMutableSet set],*variants=[NSMutableSet set];
        for(id mode in modes) {
            REQUIRE(object(mode)&&hex(mode[@"variant_sha256"],64)&&[mode[@"contract_sha256"] isEqual:mode[@"variant_sha256"]] &&
                bridge_mode(mode[@"window_origin_mode"]) && bridge_mode(mode[@"depth_mode"]));
            NSString *pair=[NSString stringWithFormat:@"%@/%@",mode[@"window_origin_mode"],mode[@"depth_mode"]];
            REQUIRE(![seen containsObject:pair]);[seen addObject:pair];[variants addObject:mode[@"variant_sha256"]];
        }
        REQUIRE([variants isEqual:[NSSet setWithArray:approved[@"variants"]]]);
        captures[row[@"capture_key"]]=row;
    }
    REQUIRE([[NSSet setWithArray:captures.allKeys] isEqual:[NSSet setWithArray:[admission[@"captures"] allKeys]]] && identities.count);
    NxBaseShaderProvider *provider=[self new];provider.manifest=manifest;provider.identities=identities;return provider;
#undef REQUIRE
}
+ (instancetype)providerAtBundle:(NSString *)bundle error:(NSError **)error {
    if(error)*error=nil;
    char path[4096],message[256];
    if(whole_runtime_shader_bundle_publication(bundle.fileSystemRepresentation,path,sizeof path,message,sizeof message))return invalid_package(error);
    NSString *root=[bundle stringByAppendingPathComponent:@"botw/shaders"];
    NSData *indexBytes=bridge_bytes([root stringByAppendingPathComponent:@"shader-package.json"],65536);
    NSDictionary *index=document(indexBytes),*wrapper=document(bridge_bytes([root stringByAppendingPathComponent:@"native-source-package.json"],65536));
    NSData *bridgeBytes=bridge_bytes([root stringByAppendingPathComponent:@"native-key-bridge.json"],32*1024*1024);
    if(![index[@"schema"] isEqual:@"botw-whole-conditional-shader-package-v1"] ||
        ![wrapper[@"schema"] isEqual:@"nxnative-base-v0-source-package-v1"] || ![wrapper[@"profile"] isEqual:@"botw-switch-base-v0"] ||
        ![wrapper[@"game_version"] isEqual:@"1.0.0"] || !bridgeBytes ||
        ![wrapper[@"bridge_sha256"] isEqual:digest(bridgeBytes)] ||
        ![wrapper[@"conditional_publication_sha256"] isEqual:index[@"publication_sha256"]] ||
        ![wrapper[@"conditional_index_sha256"] isEqual:digest(indexBytes)])return invalid_package(error);
    WholeShaderConditionalManifest *manifest=[WholeShaderConditionalManifest conditionalManifestAtAbsolutePath:@(path) error:error];
    if(!manifest)return nil;
    return [self checkedManifest:manifest bridge:document(bridgeBytes)
        admission:document(read_regular([root stringByAppendingPathComponent:@"conditional-admission.json"],64*1024*1024)) error:error];

}
- (BOOL)infoForNativeDigest:(const uint8_t *)sum controlSize:(uint32_t)control codeSize:(uint32_t)code output:(botw_shader_info *)output {
    if(!sum||!output)return NO;
    char text[65];for(unsigned i=0;i<32;i++)snprintf(text+2*i,3,"%02x",sum[i]);
    NSDictionary *row=self.identities[@(text)];
    if(!row||[row[@"native_hashed_control_size"] unsignedIntValue]!=control||[row[@"code_size"] unsignedIntValue]!=code)return NO;
    botw_shader_info info={0};strlcpy(info.key,[row[@"capture_key"] UTF8String],sizeof info.key);strlcpy(info.sha256,[row[@"capture_sha256"] UTF8String],sizeof info.sha256);
    info.version=[row[@"version"] unsignedIntValue];info.stage=[row[@"stage"] unsignedIntValue];info.control_size=[row[@"control_size"] unsignedIntValue];
    info.code_size=code;info.code_offset=[row[@"code_offset"] unsignedIntValue];info.c1_offset=[row[@"c1_offset"] unsignedIntValue];info.c1_size=[row[@"c1_size"] unsignedIntValue];*output=info;return YES;
}
@end
int nx_base_shader_enabled(void){return 1;}
NxBaseShaderProvider *nx_base_shader_provider(void) {
    static NxBaseShaderProvider *provider;static dispatch_once_t once;
    dispatch_once(&once,^{@autoreleasepool {provider=[NxBaseShaderProvider providerAtBundle:NSBundle.mainBundle.bundlePath.stringByResolvingSymlinksInPath error:NULL];}});
    return provider;
}
int nx_base_shader_info(const uint8_t sum[32],uint32_t control,uint32_t code,botw_shader_info *out) {
    return [nx_base_shader_provider() infoForNativeDigest:sum controlSize:control codeSize:code output:out];
}
