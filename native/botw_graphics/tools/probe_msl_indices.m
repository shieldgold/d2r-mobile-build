/* Local-only offline validation tool. No game shader or constants are bundled. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static int boundedCount(const char *text,unsigned *value){
    char *end=NULL;errno=0;unsigned long parsed=strtoul(text,&end,10);
    if(errno||!text[0]||*end||parsed<1||parsed>64)return 0;
    *value=(unsigned)parsed;return 1;
}
int main(int argc,char **argv){@autoreleasepool{
    unsigned vertices,width;
    if(argc!=4||!boundedCount(argv[2],&vertices)||!boundedCount(argv[3],&width)){
        fprintf(stderr,"usage: probe_msl_indices source.metal vertices[1..64] expressions[1..64]\n");return 2;
    }
    struct stat info;
    if(stat(argv[1],&info)||!S_ISREG(info.st_mode)||info.st_size<0||info.st_size>4*1024*1024){fprintf(stderr,"Invalid bounded local MSL file\n");return 2;}
    NSError *error=nil;NSString *source=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&error];
    if(!source){fprintf(stderr,"Cannot read local MSL source\n");return 2;}
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();if(!device)return 77;
    id<MTLLibrary> library=[device newLibraryWithSource:source options:nil error:&error];
    id<MTLFunction> function=[library newFunctionWithName:@"botw_index_probe"];
    id<MTLComputePipelineState> pipeline=function?[device newComputePipelineStateWithFunction:function error:&error]:nil;
    if(!pipeline){fprintf(stderr,"Metal probe compilation failed: %s\n",error.localizedDescription.UTF8String?:"entry missing");return 3;}
    size_t bytes=(size_t)vertices*width*sizeof(uint32_t);
    id<MTLBuffer> output=[device newBufferWithLength:bytes options:MTLResourceStorageModeShared];
    id<MTLCommandQueue> queue=[device newCommandQueue];id<MTLCommandBuffer> command=[queue commandBuffer];
    if(!output||!queue||!command){fprintf(stderr,"Metal probe allocation failed\n");return 3;}
    memset(output.contents,0xff,bytes);
    id<MTLComputeCommandEncoder> encoder=[command computeCommandEncoder];
    if(!encoder){fprintf(stderr,"Metal probe encoder allocation failed\n");return 3;}
    [encoder setComputePipelineState:pipeline];[encoder setBuffer:output offset:0 atIndex:0];
    [encoder dispatchThreads:MTLSizeMake(vertices,1,1) threadsPerThreadgroup:MTLSizeMake(MIN(vertices,pipeline.maxTotalThreadsPerThreadgroup),1,1)];
    [encoder endEncoding];[command commit];[command waitUntilCompleted];
    if(command.status!=MTLCommandBufferStatusCompleted){fprintf(stderr,"Metal probe execution failed: %s\n",command.error.localizedDescription.UTF8String?:"unknown");return 4;}
    const uint32_t *values=output.contents;NSMutableArray *rows=[NSMutableArray new];
    for(unsigned v=0;v<vertices;v++){NSMutableArray *row=[NSMutableArray new];for(unsigned i=0;i<width;i++)[row addObject:@(values[v*width+i])];[rows addObject:row];}
    NSData *json=[NSJSONSerialization dataWithJSONObject:@{@"relative_vertex_count":@(vertices),@"expression_count":@(width),@"indices":rows} options:0 error:&error];
    if(!json)return 5;fwrite(json.bytes,1,json.length,stdout);fputc('\n',stdout);return 0;
}}
