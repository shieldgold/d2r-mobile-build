#import "whole_shader_conditional.h"
#import <CommonCrypto/CommonDigest.h>
#import <TargetConditionals.h>
#import "nvn_refl.h"
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static pthread_mutex_t modes = PTHREAD_MUTEX_INITIALIZER;
void botw_conditional_modes_lock(void) { pthread_mutex_lock(&modes); }
void botw_conditional_modes_unlock(void) { pthread_mutex_unlock(&modes); }
int botw_conditional_state_equal(const botw_conditional_draw_state *a,const botw_conditional_draw_state *b) {
    return a && b && a->device_known==b->device_known && a->window_origin_mode==b->window_origin_mode &&
        a->depth_mode==b->depth_mode && a->color0_known==b->color0_known && a->color0_present==b->color0_present &&
        a->color0_format==b->color0_format && a->descriptor_epoch==b->descriptor_epoch;
}
static BOOL reject(NSError **error,NSString *why) {
    if(error)*error=[NSError errorWithDomain:BotwShaderManifestErrorDomain code:BotwShaderManifestErrorInvalidArtifact userInfo:@{NSLocalizedDescriptionKey:why}];return NO;
}
static BOOL dict(id x){return [x isKindOfClass:NSDictionary.class];}
static BOOL arr(id x){return [x isKindOfClass:NSArray.class];}
static BOOL str(id x){return [x isKindOfClass:NSString.class];}
static BOOL boolean(id x,BOOL b){return x && CFGetTypeID((__bridge CFTypeRef)x)==CFBooleanGetTypeID() && [x boolValue]==b;}
static BOOL empty(id x){return arr(x)&&[x count]==0;}
static BOOL integer(id x,NSUInteger hi){return [x isKindOfClass:NSNumber.class] &&
         CFGetTypeID((__bridge CFTypeRef)x)!=CFBooleanGetTypeID() &&
         [x doubleValue]>=0 &&
         [x doubleValue]<=(double)hi &&
         [x doubleValue]==(double)[x unsignedIntegerValue];}
static BOOL hex(id x, NSUInteger n) {
    if (!str(x) || [x length] != n) return NO;
    const char *p = [x UTF8String];
    if (!p || strlen(p) != n) return NO;
    for (NSUInteger i = 0; i < n; i++) {
        if (!((p[i] >= '0' && p[i] <= '9') || (p[i] >= 'a' && p[i] <= 'f'))) return NO;
    }
    return YES;
}
static NSString *digest(NSData *d){unsigned char sum[CC_SHA256_DIGEST_LENGTH];char h[65];CC_SHA256(d.bytes,(CC_LONG)d.length,sum);for(unsigned i=0;i<sizeof sum;i++)snprintf(h+2*i,3,"%02x",sum[i]);return @(h);}
static NSArray<NSString *> *absolute_components(NSString *path) {
    if(!str(path)||!path.isAbsolutePath){errno=EINVAL;return nil;}
    const char *bytes=path.UTF8String;
    if(!bytes||strlen(bytes)!=[path lengthOfBytesUsingEncoding:NSUTF8StringEncoding]){errno=EINVAL;return nil;}
    /* Do not standardize the request: that would hide dot/parent components. */
    NSArray<NSString *> *parts=[path componentsSeparatedByString:@"/"];
    for(NSUInteger i=1;i<parts.count;i++) {
        NSString *part=parts[i];
        if(!part.length||[part isEqual:@"."]||[part isEqual:@".."]) {errno=EINVAL;return nil;}
    }
    return parts;
}
static int directory_fd_at_trusted_root(NSString *path,NSString *trustedRoot) {
    NSArray<NSString *> *parts=absolute_components(path);
    if(!parts)return -1;
    NSUInteger first=1;
    const char *anchor="/";
    if(trustedRoot) {
        NSArray<NSString *> *rootParts=absolute_components(trustedRoot);
        if(!rootParts)return -1;
        /* The trailing separator enforces strict containment, including the
         * root itself and sibling names sharing the root's string prefix. */
        if(![path hasPrefix:[trustedRoot stringByAppendingString:@"/"]]) {errno=EPERM;return -1;}
        first=rootParts.count;anchor=trustedRoot.fileSystemRepresentation;
    }
    int fd=open(anchor,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    for(NSUInteger i=first;fd>=0&&i<parts.count;i++) {
        int next=openat(fd,parts[i].fileSystemRepresentation,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        int failure=errno;close(fd);fd=next;if(fd<0)errno=failure;
    }
    return fd;
}
/* iOS grants access to the signed bundle, not directory enumeration of every
 * sandbox ancestor. Only this OS-owned root is canonicalized. Artifact paths
 * are never resolved, and every component below the anchor remains NOFOLLOW.
 * Desktop/untrusted loading retains its complete walk from '/'. */
static int directory_fd(NSString *path) {
#if TARGET_OS_IOS && !TARGET_OS_SIMULATOR && !TARGET_OS_MACCATALYST
    static NSString *root;static dispatch_once_t once;
    dispatch_once(&once,^{root=[NSBundle.mainBundle.bundlePath.stringByResolvingSymlinksInPath copy];});
    if(!root.length){errno=EINVAL;return -1;}
    return directory_fd_at_trusted_root(path,root);
#else
    return directory_fd_at_trusted_root(path,nil);
#endif
}
static NSData *read_file(NSString *dir,NSString *name,size_t limit,NSError **error) {
    int parent=directory_fd(dir);if(parent<0){reject(error,@"conditional directory is not a symlink-free absolute path");return nil;}
    int fd=openat(parent,name.fileSystemRepresentation,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);close(parent);
    struct stat st;if(fd<0){reject(error,@"conditional artifact cannot be opened");return nil;}
    if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_size<=0||(uint64_t)st.st_size>limit){close(fd);reject(error,@"conditional artifact is not a bounded regular file");return nil;}
    NSMutableData *d=[NSMutableData dataWithLength:(NSUInteger)st.st_size];size_t off=0;
    while(off<d.length){ssize_t n=read(fd,(uint8_t *)d.mutableBytes+off,d.length-off);if(n<0&&errno==EINTR)continue;if(n<=0)break;off+=(size_t)n;}
    close(fd);if(off!=d.length){reject(error,@"conditional artifact short read");return nil;}return [d copy];
}
static NSDictionary *json(NSData *d,NSError **e){if(!d)return nil;id v=[NSJSONSerialization JSONObjectWithData:d options:0 error:e];if(!dict(v)){reject(e,@"conditional JSON root must be an object");return nil;}return v;}
static BOOL capture_valid(NSDictionary *c) {
    if (!dict(c) || !hex(c[@"key"],16) || !hex(c[@"sha256"],64) ||
        ![c[@"key"] isEqual:[c[@"sha256"] substringToIndex:16]]) return NO;
    BOOL vertex = [c[@"stage_name"] isEqual:@"vertex"];
    if (!vertex && ![c[@"stage_name"] isEqual:@"fragment"]) return NO;
    return !c[@"stage"] || (integer(c[@"stage"],1) && [c[@"stage"] unsignedIntValue] == (vertex ? 0u : 1u));
}
static BOOL capture_same(NSDictionary *a,NSDictionary *b){return capture_valid(a)&&
        capture_valid(b)&&
        [a[@"key"] isEqual:b[@"key"]]&&
        [a[@"sha256"] isEqual:b[@"sha256"]]&&
        [a[@"stage_name"] isEqual:b[@"stage_name"]];}
static BOOL deferred(id x){return boolean(x[@"runnable"],NO)&&boolean(x[@"runtime_compile_required"],YES);}

@interface WholeShaderConditionalArtifact ()
@property(nonatomic,copy) NSString *variantSHA256,*cacheIdentity;
@property(nonatomic,copy) NSDictionary *contract;
@property(nonatomic,copy) NSString *ownedKey,*ownedSHA;
@property(nonatomic) BotwShaderStage ownedStage;
@property(nonatomic,copy) NSDictionary *ownedReflection;
@property(nonatomic,copy) NSData *ownedSource;
@end
@implementation WholeShaderConditionalArtifact
- (NSString *)key{return _ownedKey;}
- (NSString *)captureSHA256{return _ownedSHA;}
- (BotwShaderStage)stage{return _ownedStage;}
- (NSDictionary *)reflection{return _ownedReflection;}
- (NSData *)metalSource{return _ownedSource;}
- (NSData *)metallib{return [NSData data];}
@end
@interface WholeShaderConditionalManifest ()
@property(nonatomic,copy) NSString *ownedChain,*ownedDirectory,*adapterSHA;
@property(nonatomic,copy) NSArray<NSDictionary *> *results;
@property(nonatomic,copy) NSDictionary<NSString *,NSArray<NSDictionary *> *> *byCapture;
@property(nonatomic,strong) NSMutableDictionary<NSString *,WholeShaderConditionalArtifact *> *loaded;
@end

static BOOL contract_valid(NSDictionary *c,NSDictionary *result,NSString *chain,NSString *adapter,NSError **e) {
    NSDictionary *q=c[@"requirements"],*device=dict(q)?q[@"device"]:nil,*proof=c[@"proofs"],*translation=c[@"translation"];
    BOOL vs=[result[@"capture"][@"stage_name"] isEqual:@"vertex"];
    if(![c[@"schema"] isEqual:@"botw-shader-conditional-translation-contract-v1"]||
        ![c[@"profile"] isEqual:@"botw-switch-base-v0"]||
        ![c[@"chain"] isEqual:chain]||
        !capture_same(c[@"capture"],result[@"capture"])||
        ![c[@"scope"] isEqual:@"noattr-vs-output0-fs-nonqueried-v1"]||
        !dict(q)||
        q.count!=6||
        !dict(device)||
        device.count!=2||
        !integer(device[@"window_origin_mode"],1)||
        !integer(device[@"depth_mode"],1)||
        !empty(q[@"attributes"])||
        !empty(q[@"texture_queries"])||
        !empty(q[@"images"])||
        !empty(q[@"storage_buffers"])||
        !dict(proof)||
        !hex(proof[@"binding_contract_sha256"],64)||
        !hex(proof[@"subset_sha256"],64)||
        !dict(translation)||
        ![translation[@"source_adapter_sha256"] isEqual:adapter]||
        !hex(translation[@"job_sha256"],64))return reject(e,@"conditional contract identity, proof binding or first-scope requirements invalid");
    if(vs)return q[@"fragment_output0"]==NSNull.null||reject(e,@"vertex candidate cannot require a fragment output");
    NSDictionary *f=q[@"fragment_output0"];
    /* This explicit first-scope list is independently admitted by publication;
     * this loader permits no caller-supplied enlargement of the numeric class. */
    if(!dict(f)||
        f.count!=2||
        ![f[@"type"] isEqual:@"float"]||
        ![f[@"nvn_formats"] isEqual:@[@37,@41,@46,@154]])return reject(e,@"fragment candidate lacks exact admitted output0 float format table");
    return YES;
}
static BOOL reflection_valid(NSDictionary *r,NSDictionary *c,NSString *variant,NSError **e) {
    NSDictionary *cap=c[@"capture"],*device=c[@"requirements"][@"device"],*ds=r[@"device_state"];
    BOOL vs=[cap[@"stage_name"] isEqual:@"vertex"];
    if(![r[@"schema"] isEqual:@"botw-shader-conditional-reflection-v1"]||
        ![r[@"conditional_contract_sha256"] isEqual:variant]||
        r[@"facts_sha256"]||
        ![r[@"route"] isEqual:@"B"]||
        !deferred(r)||
        ![r[@"chain"] isEqual:c[@"chain"]]||
        ![r[@"key"] isEqual:[cap[@"key"] stringByAppendingString:@"_b"]]||
        ![r[@"dm_ctl"] isEqual:cap[@"key"]]||
        ![r[@"sha256"] isEqual:cap[@"sha256"]]||
        ![r[@"stage"] isEqual:(vs?@"vs":@"ps")]||
        !str(r[@"entry"])||
        ![r[@"entry"] length]||
        !empty(r[@"unknowns"])||
        !empty(r[@"conflicts"])||
        !empty(r[@"notes"])||
        !dict(ds)||
        ds.count!=2||
        ![ds[@"window_origin_mode"] isEqual:([device[@"window_origin_mode"] unsignedIntValue]?@"upper_left":@"lower_left")]||
        ![ds[@"depth_mode"] isEqual:([device[@"depth_mode"] unsignedIntValue]?@"near_is_zero":@"near_is_minus_w")]||
        !empty(r[@"vertex_inputs"])||
        !empty(r[@"drv_unfillable"])||
        !arr(r[@"bindings"])||
        !dict(r[@"drv_reads"])||
        !str(r[@"c1"])||
        [r[@"c1"] length]>8192||
        ([r[@"c1"] length]&&
         !hex(r[@"c1"],[r[@"c1"] length])))return reject(e,@"conditional reflection identity or closed-scope fields invalid");
    NSDictionary *dr=r[@"drv_reads"];
    for (id bank in dr) {
        NSDictionary *reads=dr[bank];
        if (![bank isEqual:@"2"] || !dict(reads) || reads.count!=2 || !arr(reads[@"vec4_offsets"]) || !boolean(reads[@"dynamic"],NO))
            return reject(e,@"first-scope driver reads require static normal c2 vec4 offsets");
        for (id off in reads[@"vec4_offsets"])
            if (!integer(off,0x110) || [off unsignedIntValue]<0x20 || ([off unsignedIntValue]-0x20)%16)
                return reject(e,@"unproven driver offset in conditional reflection");
    }
    NSMutableSet *ids=[NSMutableSet set];
    for(id b in r[@"bindings"]){
        if(!dict(b)||
        !str(b[@"kind"])||
        !str(b[@"msl_type"])||
        !str(b[@"msl_name"])||
        !integer(b[@"arg_id"],NVN_REFL_MAXB-1)||
        [ids containsObject:b[@"arg_id"]])return reject(e,@"conditional binding lacks a unique bounded argument id/type");
        [ids addObject:b[@"arg_id"]];NSString *k=b[@"kind"];
        if([k isEqual:@"cbuf"]){if(!integer(b[@"reg"],13)||
        ![b[@"msl_type"] hasPrefix:@"constant "])return reject(e,@"conditional uniform is outside base c3..16 normal binding slots");}
        else if([@[@"tex",@"tbuf",@"samp",@"samp_tex"] containsObject:k]){if(!integer(b[@"reg"],31))return reject(e,@"conditional texture handle exceeds proved c2 table");}
        else if([k isEqual:@"drvcb"]){if(!integer(b[@"bank"],2)||([b[@"bank"] unsignedIntValue]!=0&&[b[@"bank"] unsignedIntValue]!=2))return reject(e,@"invalid driver bank");}
        else if(![k isEqual:@"support"]&&![k isEqual:@"c1"])return reject(e,@"conditional resource kind outside closed first scope");
    }
    NSDictionary *ra=r[@"raster"];
    if(!dict(ra)||!arr(ra[@"colors"])||(!vs&&![ra[@"colors"] isEqual:@[@0]]))return reject(e,@"conditional raster output is not exactly output0");
    if(r[@"fetch"] || (r[@"threadgroup_size"] && r[@"threadgroup_size"]!=NSNull.null))return reject(e,@"conditional first scope has no fetch or compute metadata");
    for(NSString *field in @[@"discard",@"sample_mask",@"pos_attr"]){
        id value=ra[field];if(value &&
         (![value isKindOfClass:NSNumber.class] ||
         [value doubleValue]!=(double)[value intValue]))return reject(e,@"conditional raster scalar must be an integer/boolean");
    }
    if(ra[@"pos_w"]&&!str(ra[@"pos_w"]))return reject(e,@"conditional position-w metadata must be a string");
    for(id color in ra[@"colors"])if(!integer(color,7))return reject(e,@"conditional raster color must be a bounded integer");
    for(NSString *field in @[@"uv_affine",@"uv_from_attr"]){id value=ra[field];if(value&&
        (!dict(value)||
        [value count]))return reject(e,@"no-attribute conditional scope has no attribute-derived UV metadata");}
    /* The normal renderer parser is also exercised; no parallel binding layout. */
    nvn_refl parsed;char why[256];if(nvn_refl_from_json(r,&parsed,why,sizeof why))return reject(e,[NSString stringWithFormat:@"normal reflection parser rejected conditional artifact: %s",why]);
    return YES;
}
@implementation WholeShaderConditionalManifest
- (NSString *)chain{return _ownedChain;}
- (NSString *)outputDirectory{return _ownedDirectory;}
- (NSUInteger)count{return _results.count;}
+ (instancetype)conditionalManifestAtAbsolutePath:(NSString *)path error:(NSError **)e {
    if(e)*e=nil;
    if(!str(path)||
        !path.isAbsolutePath||
        ![path.lastPathComponent isEqual:@"manifest-build.json"]){reject(e,@"conditional manifest needs an absolute manifest-build.json path");return nil;}
    NSString *dir=path.stringByDeletingLastPathComponent;
    NSDictionary *m=json(read_file(dir,path.lastPathComponent,256*1024*1024,e),e);if(!m)return nil;
    if(![m[@"schema"] isEqual:@"botw-shader-conditional-source-manifest-v1"]||
        ![m[@"profile"] isEqual:@"botw-switch-base-v0"]||
        ![m[@"mode"] isEqual:@"conditional_source"]||
        ![m[@"output"] isEqual:@"."]||
        !deferred(m)||
        !hex(m[@"chain"],12)||
        ![dir.lastPathComponent isEqual:[@"botw-c-" stringByAppendingString:m[@"chain"]]]||
        !hex(m[@"source_adapter_sha256"],64)||
        !hex(m[@"conditional_admission_sha256"],64)||
        !arr(m[@"results"])||
        ![m[@"results"] count]){reject(e,@"conditional manifest identity/publication binding invalid");return nil;}
    NSMutableSet *seen=[NSMutableSet set];
    NSMutableDictionary<NSString *,NSMutableArray *> *byCapture=[NSMutableDictionary dictionary];
    for(id r in m[@"results"]){if(!dict(r)||
        !hex(r[@"variant_sha256"],64)||
        [seen containsObject:r[@"variant_sha256"]]||
        ![r[@"status"] isEqual:@"conditional_source_ready"]||
        !deferred(r)||
        !capture_valid(r[@"capture"])||
        !empty(r[@"errors"])||
        !empty(r[@"unknowns"])||
        !empty(r[@"conflicts"])||
        !dict(r[@"files"])||
        [r[@"files"] count]!=3){reject(e,@"conditional result identity/gates invalid");return nil;}
        [seen addObject:r[@"variant_sha256"]];
        NSString *identity=[NSString stringWithFormat:@"%@/%@",r[@"capture"][@"sha256"],r[@"capture"][@"stage_name"]];
        if(!byCapture[identity])byCapture[identity]=[NSMutableArray array];
        [byCapture[identity] addObject:r];
        NSDictionary *suffix=@{@"contract":@".contract.json",@"json":@".json",@"metal_source":@".metal"};
        for(NSString *kind in suffix){id f=r[@"files"][kind];if(!dict(f)||
        [f count]!=2||
        ![f[@"path"] isEqual:[r[@"variant_sha256"] stringByAppendingString:suffix[kind]]]||
        !hex(f[@"sha256"],64)||
        ([kind isEqual:@"contract"]&&
        ![f[@"sha256"] isEqual:r[@"variant_sha256"]])){reject(e,@"conditional artifact must name exact digest-owned siblings");return nil;}}
    }
    WholeShaderConditionalManifest *out=[self new];out.ownedChain=m[@"chain"];out.ownedDirectory=dir;out.adapterSHA=m[@"source_adapter_sha256"];out.results=m[@"results"];out.byCapture=byCapture;out.loaded=[NSMutableDictionary dictionary];return out;
}
- (WholeShaderConditionalArtifact *)loadResult:(NSDictionary *)result error:(NSError **)e {
    NSDictionary *files=result[@"files"];NSMutableDictionary *bytes=[NSMutableDictionary dictionary];
    for(NSString *kind in @[@"contract",@"json",@"metal_source"]){NSDictionary *f=files[kind];NSData *d=read_file(self.ownedDirectory,f[@"path"],16*1024*1024,e);if(!d)return nil;if(![digest(d) isEqual:f[@"sha256"]]){reject(e,@"conditional artifact digest changed");return nil;}bytes[kind]=d;}
    NSDictionary *c=json(bytes[@"contract"],e),*r=json(bytes[@"json"],e);if(!c||!r)return nil;
    if(!contract_valid(c,result,self.chain,self.adapterSHA,e)||!reflection_valid(r,c,result[@"variant_sha256"],e))return nil;
    NSData *source=bytes[@"metal_source"];NSString *text=[[NSString alloc]initWithData:source encoding:NSUTF8StringEncoding];if(!text.length||
        memchr(source.bytes,0,source.length)){reject(e,@"conditional source is not bounded NUL-free UTF8");return nil;}
    WholeShaderConditionalArtifact *a=[WholeShaderConditionalArtifact new];a.variantSHA256=result[@"variant_sha256"];a.contract=c;a.ownedKey=result[@"capture"][@"key"];a.ownedSHA=result[@"capture"][@"sha256"];a.ownedStage=[result[@"capture"][@"stage_name"] isEqual:@"vertex"]?BotwShaderStageVertex:BotwShaderStageFragment;a.ownedReflection=r;a.ownedSource=source;
    a.cacheIdentity=[NSString stringWithFormat:@"conditional/%@/%@/%ld/%@",self.chain,a.captureSHA256,(long)a.stage,a.variantSHA256];return a;
}
- (BOOL)validateArtifactsWithoutRetaining:(NSError **)e {if(e)*e=nil;for(NSDictionary *r in self.results){NSError *failure=nil;BOOL ok;@autoreleasepool{ok=[self loadResult:r error:&failure]!=nil;}if(!ok){if(e)*e=failure;return NO;}}return YES;}
- (BotwShaderArtifact *)artifactForKey:(NSString *)k captureSHA256:(NSString *)h stage:(BotwShaderStage)s error:(NSError **)e {(void)k;(void)h;(void)s;reject(e,@"conditional artifact selection requires the actual draw metadata snapshot");return nil;}
- (WholeShaderConditionalArtifact *)artifactForInfo:(const botw_shader_info *)info state:(const botw_conditional_draw_state *)s error:(NSError **)e {
    if(e)*e=nil;
    if(!info||
        !s||
        !memchr(info->key,0,sizeof info->key)||
        !memchr(info->sha256,0,sizeof info->sha256)||
        !hex(@(info->key),16)||
        !hex(@(info->sha256),64)||
        (info->stage!=0&&
        info->stage!=1)||
        s->device_known!=1||
        s->window_origin_mode>1||
        s->depth_mode>1||
        (info->stage==1&&
        (s->color0_known!=1||
        s->color0_present!=1))){reject(e,@"conditional draw has unknown identity, device modes or effective output0 attachment");return nil;}
    @synchronized(self){WholeShaderConditionalArtifact *selected=nil;
        for(NSDictionary *r in self.byCapture[[NSString stringWithFormat:@"%s/%@",info->sha256,info->stage?@"fragment":@"vertex"]]){NSDictionary *cap=r[@"capture"];if(![cap[@"key"] isEqual:@(info->key)]||
        ![cap[@"sha256"] isEqual:@(info->sha256)]||
        ![(info->stage?@"fragment":@"vertex") isEqual:cap[@"stage_name"]])continue;
            WholeShaderConditionalArtifact *a=self.loaded[r[@"variant_sha256"]];if(!a){a=[self loadResult:r error:e];if(!a)return nil;self.loaded[a.variantSHA256]=a;}
            NSDictionary *q=a.contract[@"requirements"],*d=q[@"device"];
            if([d[@"window_origin_mode"] unsignedIntValue]!=s->window_origin_mode||[d[@"depth_mode"] unsignedIntValue]!=s->depth_mode)continue;
            if(info->stage==1&&![q[@"fragment_output0"][@"nvn_formats"] containsObject:@(s->color0_format)])continue;
            if(selected){reject(e,@"multiple conditional variants exactly match this draw");return nil;}selected=a;
        }
        if(!selected)reject(e,@"no conditional variant exactly matches this draw");return selected;
    }
}
@end
