/* Owned source/metadata fixture. Never creates a Metal device, guest or GPU. */
/* Include the implementation to exercise its private filesystem helper with
 * owned directory authority; production exposes no root-override API. */
#import "../adapter/whole_shader_conditional.m"
#import "botw_shader_runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/stat.h>
#include <unistd.h>
@interface ConditionalFunctionPort : NSObject
@property(nonatomic) MTLFunctionType functionType;
@end
@implementation ConditionalFunctionPort @end
@interface ConditionalLibraryPort : NSObject
@property(nonatomic) MTLFunctionType type;
- (id)newFunctionWithName:(NSString *)name;
@end
@implementation ConditionalLibraryPort
- (id)newFunctionWithName:(NSString *)name { if(![name isEqual:@"main0"])return nil;ConditionalFunctionPort *f=[ConditionalFunctionPort new];f.functionType=self.type;return f; }
@end
@interface ConditionalCompilerPort : NSObject
@property(nonatomic) MTLFunctionType type;
@property(nonatomic) unsigned calls;
- (id)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options error:(NSError **)error;
- (id)newLibraryWithData:(dispatch_data_t)data error:(NSError **)error;
@end
@implementation ConditionalCompilerPort
- (id)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options error:(NSError **)error {
    (void)error;if(![source hasPrefix:@"// owned source;"]||options.fastMathEnabled)return nil;self.calls++;
    ConditionalLibraryPort *p=[ConditionalLibraryPort new];p.type=self.type;return p;
}
- (id)newLibraryWithData:(dispatch_data_t)d error:(NSError **)e {(void)d;(void)e;abort();}
@end
int main(int argc,char **argv) {
    if(argc==4&&!strcmp(argv[1],"directory")) {
        @autoreleasepool {
            int fd=directory_fd_at_trusted_root(@(argv[2]),strcmp(argv[3],"-")?@(argv[3]):nil);
            if(fd<0){fprintf(stderr,"directory errno=%d\n",errno);return 30;}
            struct stat st;int rc=fstat(fd,&st);close(fd);
            return rc||!S_ISDIR(st.st_mode)?31:0;
        }
    }
    if(argc!=10)return 2;
    @autoreleasepool {
        NSError *error=nil;
        WholeShaderConditionalManifest *m=(WholeShaderConditionalManifest *)[BotwShaderManifest manifestAtAbsolutePath:@(argv[1]) error:&error];
        if(!m){fprintf(stderr,"load: %s\n",error.localizedDescription.UTF8String);return 10;}
        if(!strcmp(argv[2],"audit"))return [m validateArtifactsWithoutRetaining:&error]?0:11;
        botw_shader_info info={0};snprintf(info.key,sizeof info.key,"%.16s",argv[2]);snprintf(info.sha256,sizeof info.sha256,"%s",argv[2]);info.stage=(uint32_t)atoi(argv[3]);
        botw_conditional_draw_state s={(uint32_t)atoi(argv[4]),(uint32_t)atoi(argv[5]),(uint32_t)atoi(argv[6]),(uint32_t)atoi(argv[7]),1,(uint32_t)atoi(argv[8]),(uint64_t)strtoull(argv[9],0,10)};
        WholeShaderConditionalArtifact *a=[m artifactForInfo:&info state:&s error:&error];
        if(!a){fprintf(stderr,"select: %s\n",error.localizedDescription.UTF8String);return 12;}
        printf("%s\n",a.cacheIdentity.UTF8String);
        if(!a.metalSource.length||a.metallib.length||![a.reflection[@"conditional_contract_sha256"] isEqual:a.variantSHA256])return 13;
        botw_conditional_draw_state t=s;
        if(!botw_conditional_state_equal(&s,&t))return 14;
        t.descriptor_epoch++;if(botw_conditional_state_equal(&s,&t))return 15;
        t=s;t.window_origin_mode^=1;if(botw_conditional_state_equal(&s,&t))return 16;
        t=s;t.depth_mode^=1;if(botw_conditional_state_equal(&s,&t))return 17;
        /* A second normal draw after either mode changes must reselect. */
        t=s;t.window_origin_mode^=1;
        WholeShaderConditionalArtifact *b=[m artifactForInfo:&info state:&t error:&error];
        if(b&&[b.cacheIdentity isEqual:a.cacheIdentity])return 18;
        t=s;t.depth_mode^=1;b=[m artifactForInfo:&info state:&t error:&error];
        if(b&&[b.cacheIdentity isEqual:a.cacheIdentity])return 19;
        ConditionalCompilerPort *compiler=[ConditionalCompilerPort new];
        compiler.type=info.stage?MTLFunctionTypeFragment:MTLFunctionTypeVertex;
        if(!botw_shader_runtime_function((id<MTLDevice>)compiler,a,&error)||compiler.calls!=1)return 20;
        return 0;
    }
}
