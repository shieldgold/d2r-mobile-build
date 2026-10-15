/* Private shader assets are supplied locally by the user. No game content or
 * translated shaders are distributed with this bounded, read-only loader. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <CommonCrypto/CommonDigest.h>
#include "graphics.h"
#include "shader_bundle.h"
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int bundleError(char *error,size_t capacity,const char *format,...){
    if(error&&capacity){va_list args;va_start(args,format);vsnprintf(error,capacity,format,args);va_end(args);}return 0;
}
static BOOL integer(id value,uint64_t limit,uint64_t *out){
    if(![value isKindOfClass:[NSNumber class]] || CFGetTypeID((__bridge CFTypeRef)value)==CFBooleanGetTypeID())return NO;
    double number=[value doubleValue];if(!isfinite(number)||number<0||number>(double)limit||floor(number)!=number)return NO;
    *out=(uint64_t)number;return YES;
}
static BOOL safeName(id value){
    if(![value isKindOfClass:[NSString class]])return NO;
    const char *name=[value UTF8String];if(!name)return NO;size_t count=strlen(name);
    if(!count||count>128||count!=[value lengthOfBytesUsingEncoding:NSUTF8StringEncoding]||!strcmp(name,".")||!strcmp(name,".."))return NO;
    for(size_t i=0;i<count;i++)if(!((name[i]>='a'&&name[i]<='z')||(name[i]>='A'&&name[i]<='Z')||(name[i]>='0'&&name[i]<='9')||name[i]=='.'||name[i]=='_'||name[i]=='-'))return NO;
    return YES;
}
static BOOL hashName(id value){
    if(![value isKindOfClass:[NSString class]]||[value length]!=64)return NO;
    const char *s=[value UTF8String];if(!s||strlen(s)!=64)return NO;
    for(unsigned i=0;i<64;i++)if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return NO;return YES;
}
static NSString *sha256(NSData *data){
    unsigned char bytes[CC_SHA256_DIGEST_LENGTH];CC_SHA256(data.bytes,(CC_LONG)data.length,bytes);
    char hex[65];for(unsigned i=0;i<sizeof bytes;i++)snprintf(hex+i*2,3,"%02x",bytes[i]);return @(hex);
}
static NSData *readAsset(int directory,NSString *name,size_t limit,size_t *total,char *error,size_t capacity){
    if(!safeName(name)){bundleError(error,capacity,"Asset path must be a bounded relative filename");return nil;}
    int fd=openat(directory,name.UTF8String,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(fd<0){bundleError(error,capacity,"Cannot open relative asset (symlinks rejected): %s",name.UTF8String);return nil;}
    struct stat info;
    if(fstat(fd,&info)||!S_ISREG(info.st_mode)||info.st_size<0||(uint64_t)info.st_size>limit||(uint64_t)info.st_size>64*1024*1024-*total){close(fd);bundleError(error,capacity,"Asset size/type exceeds bundle bounds: %s",name.UTF8String);return nil;}
    size_t size=(size_t)info.st_size;NSMutableData *data=[NSMutableData dataWithLength:size];
    size_t position=0;
    while(position<size){ssize_t n=read(fd,(char*)data.mutableBytes+position,size-position);if(n<0&&errno==EINTR)continue;if(n<=0){close(fd);bundleError(error,capacity,"Asset read was truncated: %s",name.UTF8String);return nil;}position+=(size_t)n;}
    char extra;ssize_t trailing=read(fd,&extra,1);close(fd);
    if(trailing!=0){bundleError(error,capacity,"Asset changed size during read: %s",name.UTF8String);return nil;}
    *total+=size;return data;
}
static NSData *verifiedAsset(int directory,id spec,size_t limit,size_t *total,char *error,size_t capacity){
    if(![spec isKindOfClass:[NSDictionary class]]){bundleError(error,capacity,"Missing asset descriptor");return nil;}
    uint64_t size;if(!integer(spec[@"bytes"],limit,&size)||!hashName(spec[@"sha256"])){bundleError(error,capacity,"Invalid asset size/SHA256 descriptor");return nil;}
    NSData *data=readAsset(directory,spec[@"path"],limit,total,error,capacity);if(!data)return nil;
    if(data.length!=size){bundleError(error,capacity,"Asset declared size mismatch");return nil;}
    if(![sha256(data) isEqual:spec[@"sha256"]]){bundleError(error,capacity,"Asset SHA256 mismatch: %s",[spec[@"path"] UTF8String]);return nil;}return data;
}
@interface BWBundleShader : NSObject
@property(strong) NSData *control,*code,*bindings;
@property(strong) NSString *source,*entry,*identity,*reason;
@property NVNshaderStage stage;
@property BOOL unsupported;
@end
@implementation BWBundleShader
@end
static BOOL reflectedSlot(NSDictionary *reflection,NSString *kind,unsigned slot){
    id entries=reflection[kind];if(![entries isKindOfClass:[NSArray class]])return NO;
    for(id item in entries){uint64_t value;if([item isKindOfClass:[NSDictionary class]]&&integer(item[@"slot"],127,&value)&&value==slot)return YES;}return NO;
}
static NSData *bindings(id list,id reflection,char *error,size_t capacity){
    if(![list isKindOfClass:[NSArray class]]||[list count]>64||![reflection isKindOfClass:[NSDictionary class]]){bundleError(error,capacity,"Invalid binding metadata");return nil;}
    NSMutableData *result=[NSMutableData new];NSMutableSet *used=[NSMutableSet new];
    for(id row in list){if(![row isKindOfClass:[NSDictionary class]]){bundleError(error,capacity,"Invalid binding entry");return nil;}
        NSString *kind=row[@"kind"];int type=[kind isEqual:@"uniform"]?BOTW_GRAPHICS_UNIFORM:[kind isEqual:@"texture"]?BOTW_GRAPHICS_TEXTURE:[kind isEqual:@"sampler"]?BOTW_GRAPHICS_SAMPLER:-1;
        uint64_t nvn,metal;if(type<0||!integer(row[@"nvn_slot"],127,&nvn)||!integer(row[@"metal_slot"],type==BOTW_GRAPHICS_TEXTURE?127:15,&metal)){bundleError(error,capacity,"Unknown or out-of-bounds binding kind/slot");return nil;}
        NSString *key=[NSString stringWithFormat:@"%d:%llu",type,(unsigned long long)metal];
        if([used containsObject:key]||!reflectedSlot(reflection,type==BOTW_GRAPHICS_UNIFORM?@"uniforms":@"samplers",(unsigned)nvn)){bundleError(error,capacity,"Duplicate Metal slot or NVN binding absent from reflection");return nil;}
        [used addObject:key];botw_graphics_shader_binding entry={(enum botw_graphics_binding_kind)type,(unsigned)nvn,(unsigned)metal};[result appendBytes:&entry length:sizeof entry];
    }
    return result;
}
static NSArray<NSTextCheckingResult*> *matches(NSString *pattern,NSString *source){
    NSRegularExpression *expression=[NSRegularExpression regularExpressionWithPattern:pattern options:0 error:nil];
    return [expression matchesInString:source options:0 range:NSMakeRange(0,source.length)];
}
static NSString *part(NSString *source,NSTextCheckingResult *match,NSUInteger index){return [source substringWithRange:[match rangeAtIndex:index]];}
static BOOL constantSpecializations(BWBundleShader *shader,id list,char *error,size_t capacity){
    if(!list)return YES; /* Older schema-1 bundles did not specialize c1. */
    if(![list isKindOfClass:[NSArray class]]||[list count]>16384)return bundleError(error,capacity,"Invalid c1 specialization list");
    if(![list count])return YES;
    if(shader.control.length<0x708)return bundleError(error,capacity,"Missing original c1 control bounds");
    uint32_t start,end;memcpy(&start,(const char*)shader.control.bytes+0x700,4);memcpy(&end,(const char*)shader.control.bytes+0x704,4);
    if(start<0x80||start%4||end!=shader.code.length||end<start||end-start>65536)return bundleError(error,capacity,"Invalid original c1 code bounds");
    NSMutableSet *seen=[NSMutableSet new];
    for(id row in list){uint64_t bank,offset,count;
        if(![row isKindOfClass:[NSDictionary class]]||!integer(row[@"bank"],1,&bank)||bank!=1||!integer(row[@"offset"],65532,&offset)||offset%4||offset+4>end-start||!integer(row[@"count"],65536,&count)||!count)return bundleError(error,capacity,"Invalid c1 specialization offset/count");
        NSString *kind=row[@"type"],*bits=row[@"bits"];
        if((![kind isEqual:@"float"]&&![kind isEqual:@"uint"])||![bits isKindOfClass:[NSString class]]||matches(@"^[0-9a-f]{8}$",bits).count!=1)return bundleError(error,capacity,"Invalid c1 specialization type/bits");
        uint32_t original;memcpy(&original,(const char*)shader.code.bytes+start+offset,4);NSString *expected=[NSString stringWithFormat:@"%08x",original];
        NSString *key=[NSString stringWithFormat:@"%llu:%@",(unsigned long long)offset,kind];
        if(![expected isEqual:bits]||[seen containsObject:key])return bundleError(error,capacity,"Original c1 bits mismatch or duplicate specialization");[seen addObject:key];
        NSString *literal=[NSString stringWithFormat:@"0x%@u",bits];if([kind isEqual:@"float"])literal=[NSString stringWithFormat:@"as_type<float>(%@)",literal];
        if(matches([NSRegularExpression escapedPatternForString:literal],shader.source).count<count)return bundleError(error,capacity,"MSL c1 specialization expression/count mismatch");
    }
    return YES;
}
/* The bundle accepts the fixed offline compiler's explicit resource grammar.
 * Re-derive NVN slots from the actual compiled source, rather than trusting a
 * manifest to remap one reflected slot onto another. Unknown grammar fails. */
static BOOL sourceMetadata(BWBundleShader *shader,NSDictionary *entry,char *error,size_t capacity){
    NSString *pattern=[NSString stringWithFormat:@"\\b(vertex|fragment)\\s+\\w+\\s+%@\\s*\\(",[NSRegularExpression escapedPatternForString:shader.entry]];
    NSArray *functions=matches(pattern,shader.source);
    if(functions.count!=1)return bundleError(error,capacity,"Ambiguous Metal entry signature");
    NSTextCheckingResult *function=functions[0];NSRange body=[shader.source rangeOfString:@"{" options:0 range:NSMakeRange(function.range.location,shader.source.length-function.range.location)];
    if(body.location==NSNotFound)return bundleError(error,capacity,"Missing Metal entry body");
    NSString *signature=[shader.source substringWithRange:NSMakeRange(function.range.location,body.location-function.range.location)];
    NSMutableDictionary *expected=[NSMutableDictionary new],*actual=[NSMutableDictionary new],*types=[NSMutableDictionary new];
    const botw_graphics_shader_binding *map=shader.bindings.bytes;
    for(size_t i=0;i<shader.bindings.length/sizeof *map;i++)expected[[NSString stringWithFormat:@"%d:%u",map[i].kind,map[i].metal_slot]]=@(map[i].nvn_slot);
    for(NSTextCheckingResult *row in matches(@"\\b(constant|device)\\s+\\w+\\s*[&*]\\s+(\\w+)\\s+\\[\\[buffer\\((\\d+)\\)\\]\\]",signature)){
        NSString *variable=part(signature,row,2);NSArray *banks=matches(@"^c(\\d+)(?:_\\d+)?$",variable);
        if(![part(signature,row,1) isEqual:@"constant"]||banks.count!=1)return bundleError(error,capacity,"Unknown private/driver buffer in Metal source");
        NSUInteger bank=[part(variable,banks[0],1) integerValue],metal=[part(signature,row,3) integerValue];
        if(bank<3||bank>16||metal>15)return bundleError(error,capacity,"Metal uniform bank/index outside supported bounds");
        NSString *key=[NSString stringWithFormat:@"%d:%lu",BOTW_GRAPHICS_UNIFORM,(unsigned long)metal];
        if(actual[key])return bundleError(error,capacity,"Duplicate Metal buffer declaration");actual[key]=@(bank-3);
    }
    for(NSTextCheckingResult *row in matches(@"\\b(texture\\w+|sampler)(?:<[^>]+>)?\\s+((?:fs|vs)_tex0_([0-9a-f]{1,8})(?:Smplr)?)\\s+\\[\\[(texture|sampler)\\((\\d+)\\)\\]\\]",signature)){
        NSString *type=part(signature,row,1),*kind=part(signature,row,4);BOOL sampler=[type isEqual:@"sampler"];
        if((sampler&&![kind isEqual:@"sampler"])||(!sampler&&(![kind isEqual:@"texture"]||(![type isEqual:@"texture2d"]&&![type isEqual:@"texture2d_array"]))))return bundleError(error,capacity,"Unsupported Metal texture type");
        unsigned long offset=strtoul(part(signature,row,3).UTF8String,NULL,16);NSUInteger metal=[part(signature,row,5) integerValue];
        if(offset<0x20||(offset-0x20)%8||(offset-0x20)/8>127||metal>(sampler?15:127))return bundleError(error,capacity,"Unsupported Metal texture slot");
        NSString *key=[NSString stringWithFormat:@"%d:%lu",sampler?BOTW_GRAPHICS_SAMPLER:BOTW_GRAPHICS_TEXTURE,(unsigned long)metal];
        if(actual[key])return bundleError(error,capacity,"Duplicate Metal texture declaration");actual[key]=@((offset-0x20)/8);
        if(!sampler)types[key]=[type substringFromIndex:7];
    }
    NSUInteger declarations=matches(@"\\[\\[(?:buffer|texture|sampler)\\(\\d+\\)\\]\\]",signature).count;
    if(declarations!=actual.count||![actual isEqual:expected])return bundleError(error,capacity,"Metal source resource binding map mismatch");
    id textureTypes=entry[@"texture_types"];NSMutableSet *seen=[NSMutableSet new];
    if(![textureTypes isKindOfClass:[NSArray class]]||[textureTypes count]!=types.count)return bundleError(error,capacity,"Invalid texture type requirements");
    for(id row in textureTypes){uint64_t nvn,metal;
        if(![row isKindOfClass:[NSDictionary class]]||!integer(row[@"nvn_slot"],127,&nvn)||!integer(row[@"metal_slot"],127,&metal))return bundleError(error,capacity,"Invalid texture type slot");
        NSString *key=[NSString stringWithFormat:@"%d:%llu",BOTW_GRAPHICS_TEXTURE,(unsigned long long)metal];
        if([seen containsObject:key]||![types[key] isEqual:row[@"type"]]||![actual[key] isEqual:@(nvn)])return bundleError(error,capacity,"Metal texture type requirement mismatch");[seen addObject:key];
    }
    id inputs=entry[@"vertex_inputs"];NSMutableSet *declaredInputs=[NSMutableSet new],*actualInputs=[NSMutableSet new];
    if(![inputs isKindOfClass:[NSArray class]]||[inputs count]>16)return bundleError(error,capacity,"Invalid vertex input requirements");
    for(id item in inputs){uint64_t slot;if(!integer(item,15,&slot)||[declaredInputs containsObject:@(slot)]||!reflectedSlot(entry[@"reflection"],@"inputs",(unsigned)slot))return bundleError(error,capacity,"Invalid/unreflected vertex input slot");[declaredInputs addObject:@(slot)];}
    for(NSTextCheckingResult *row in matches(@"\\[\\[attribute\\((\\d+)\\)\\]\\]",shader.source))[actualInputs addObject:@([part(shader.source,row,1) integerValue])];
    if(![declaredInputs isEqual:actualInputs]||(shader.stage!=NVN_SHADER_STAGE_VERTEX&&actualInputs.count))return bundleError(error,capacity,"Metal vertex input requirement mismatch");
    return YES;
}
int botw_graphics_load_shader_bundle(const char *path,botw_graphics_shader_bundle_result *result,char *error,size_t capacity){@autoreleasepool{
    if(result)memset(result,0,sizeof *result);if(error&&capacity)error[0]=0;
    if(!path||!path[0]||!result)return bundleError(error,capacity,"Missing shader bundle path/result");
    int directory=open(path,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    if(directory<0)return bundleError(error,capacity,"Cannot open shader bundle directory (symlinks rejected)");
    size_t total=0;NSData *manifestBytes=readAsset(directory,@"manifest.json",4*1024*1024,&total,error,capacity);
    if(!manifestBytes){close(directory);return 0;}
    NSError *jsonError=nil;id manifest=[NSJSONSerialization JSONObjectWithData:manifestBytes options:0 error:&jsonError];
    uint64_t schema;
    if(![manifest isKindOfClass:[NSDictionary class]]||!integer(manifest[@"schema"],1,&schema)||schema!=1||![manifest[@"entries"] isKindOfClass:[NSArray class]]||[manifest[@"entries"] count]>4096){close(directory);return bundleError(error,capacity,"Invalid shader bundle schema/entry bounds");}
    NSMutableArray<BWBundleShader*> *prepared=[NSMutableArray new];NSMutableSet *identities=[NSMutableSet new];size_t unsupported=0;
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();if(!device){close(directory);return bundleError(error,capacity,"No Metal device for shader preflight");}
    for(id entry in manifest[@"entries"]){
        if(![entry isKindOfClass:[NSDictionary class]]||!hashName(entry[@"control_sha256"])||!hashName(entry[@"code_sha256"])){close(directory);return bundleError(error,capacity,"Invalid original shader identity");}
        NSString *stage=entry[@"stage"];NVNshaderStage nvnStage;
        if([stage isEqual:@"vertex"])nvnStage=NVN_SHADER_STAGE_VERTEX;else if([stage isEqual:@"fragment"])nvnStage=NVN_SHADER_STAGE_FRAGMENT;else{close(directory);return bundleError(error,capacity,"Unsupported shader stage");}
        NSString *identity=[NSString stringWithFormat:@"%@:%@:%@",stage,entry[@"control_sha256"],entry[@"code_sha256"]];
        if([identities containsObject:identity]){close(directory);return bundleError(error,capacity,"Duplicate shader identity");}[identities addObject:identity];
        BWBundleShader *shader=[BWBundleShader new];shader.stage=nvnStage;shader.identity=identity;
        shader.control=verifiedAsset(directory,entry[@"control"],1024*1024,&total,error,capacity);shader.code=shader.control?verifiedAsset(directory,entry[@"code"],16*1024*1024,&total,error,capacity):nil;
        NSData *source=shader.code?verifiedAsset(directory,entry[@"msl"],4*1024*1024,&total,error,capacity):nil;
        if(!source){close(directory);return 0;}
        uint32_t cm=0,dm=0;if(shader.control.length>=4)memcpy(&cm,shader.control.bytes,4);if(shader.code.length>=4)memcpy(&dm,shader.code.bytes,4);
        if(cm!=0x98761234||dm!=0x12345678||shader.code.length<0x80||![sha256(shader.control) isEqual:entry[@"control_sha256"]]||![sha256(shader.code) isEqual:entry[@"code_sha256"]]){close(directory);return bundleError(error,capacity,"Original NVN data identity/header mismatch");}
        if([entry[@"status"] isEqual:@"unsupported"]){
            if(![entry[@"reason"] isKindOfClass:[NSString class]]||![entry[@"reason"] length]||[entry[@"reason"] length]>1024){close(directory);return bundleError(error,capacity,"Unsupported shader requires a bounded reason");}
            shader.unsupported=YES;shader.reason=entry[@"reason"];unsupported++;[prepared addObject:shader];continue;
        }
        if(![entry[@"status"] isEqual:@"ready"]||!safeName(entry[@"entry"])){close(directory);return bundleError(error,capacity,"Invalid supported shader status/entry");}
        shader.entry=entry[@"entry"];shader.bindings=bindings(entry[@"bindings"],entry[@"reflection"],error,capacity);shader.source=[[NSString alloc] initWithData:source encoding:NSUTF8StringEncoding];
        if(!shader.bindings){close(directory);return 0;}
        if(!shader.source||strlen(shader.source.UTF8String)!=source.length){close(directory);return bundleError(error,capacity,"Invalid UTF-8/NUL shader source");}
        if(!constantSpecializations(shader,entry[@"constant_specializations"],error,capacity)){close(directory);return 0;}
        if(!sourceMetadata(shader,entry,error,capacity)){close(directory);return 0;}
        NSError *metalError=nil;id<MTLLibrary> library=[device newLibraryWithSource:shader.source options:nil error:&metalError];id<MTLFunction> function=[library newFunctionWithName:shader.entry];
        if(!function||function.functionType!=(nvnStage==NVN_SHADER_STAGE_VERTEX?MTLFunctionTypeVertex:MTLFunctionTypeFragment)){close(directory);return bundleError(error,capacity,"Metal entry/stage preflight failed: %s",metalError.localizedDescription.UTF8String?:"entry missing");}
        [prepared addObject:shader];
    }
    id records=manifest[@"stage_records"];
    if(![records isKindOfClass:[NSArray class]]||[records count]>65536){close(directory);return bundleError(error,capacity,"Invalid stage record bounds");}
    NSMutableIndexSet *referenced=[NSMutableIndexSet new];
    for(id row in records){uint64_t index,variation;
        if(![row isKindOfClass:[NSDictionary class]]||!integer(row[@"entry"],prepared.count?prepared.count-1:0,&index)||index>=prepared.count||!integer(row[@"variation"],65535,&variation)||!safeName(row[@"container"])){close(directory);return bundleError(error,capacity,"Invalid shader stage record");}
        BWBundleShader *shader=prepared[(NSUInteger)index];NSString *stage=shader.stage==NVN_SHADER_STAGE_VERTEX?@"vertex":@"fragment";
        if(![row[@"stage"] isEqual:stage]){close(directory);return bundleError(error,capacity,"Shader stage record identity mismatch");}[referenced addIndex:(NSUInteger)index];
    }
    if(referenced.count!=prepared.count){close(directory);return bundleError(error,capacity,"Unreferenced shader stage record identity");}
    close(directory);
    size_t registered=0;
    for(BWBundleShader *shader in prepared){
        if(shader.unsupported){fprintf(stderr,"[botw shaders] unsupported identity=%s reason=%s\n",shader.identity.UTF8String,shader.reason.UTF8String);continue;}
        if(!botw_graphics_register_shader_with_bindings(shader.control.bytes,shader.control.length,shader.code.bytes,shader.code.length,shader.stage,shader.source.UTF8String,shader.entry.UTF8String,shader.bindings.bytes,shader.bindings.length/sizeof(botw_graphics_shader_binding)))return bundleError(error,capacity,"Shader registration failed: %s",shader.identity.UTF8String);
        registered++;
    }
    result->declared_entries=prepared.count;result->registered_entries=registered;result->unsupported_entries=unsupported;
    fprintf(stderr,"[botw shaders] bundle registered=%zu unsupported=%zu unique=%zu\n",registered,unsupported,prepared.count);return 1;
}}
