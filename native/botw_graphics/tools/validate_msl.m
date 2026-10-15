/* Local verification tool. Emits metadata only; shader input remains local. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(int argc,const char **argv){@autoreleasepool{
    if(argc!=2){fprintf(stderr,"usage: validate_msl <local converted shader directory>\n");return 2;}
    id<MTLDevice> gpu=MTLCreateSystemDefaultDevice();if(!gpu){fputs("No Metal device\n",stderr);return 77;}
    NSString *directory=@(argv[1]);NSError *error=nil;
    NSArray *files=[[NSFileManager defaultManager] contentsOfDirectoryAtPath:directory error:&error];
    if(!files){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 1;}
    NSMutableArray *results=[NSMutableArray new];int failed=0;
    for(NSString *name in [files sortedArrayUsingSelector:@selector(compare:)]){
        if(![name.pathExtension isEqual:@"metal"])continue;
        NSString *path=[directory stringByAppendingPathComponent:name];
        NSString *source=[NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:&error];
        if(!source){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 1;}
        MTLCompileOptions *options=[MTLCompileOptions new];options.fastMathEnabled=NO;
        id<MTLLibrary> library=[gpu newLibraryWithSource:source options:options error:&error];
        NSMutableDictionary *row=[@{@"file":name,@"compiled":@(library!=nil),@"nvn_bindings_validated":@NO} mutableCopy];
        if(library){row[@"functions"]=library.functionNames;}else{row[@"error"]=error.localizedDescription?:@"Metal compilation failed";failed++;}
        [results addObject:row];
    }
    NSData *json=[NSJSONSerialization dataWithJSONObject:@{@"schema":@1,@"results":results} options:NSJSONWritingPrettyPrinted error:&error];
    if(!json){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 1;}
    fwrite(json.bytes,1,json.length,stdout);fputc('\n',stdout);return failed?1:0;
}}
