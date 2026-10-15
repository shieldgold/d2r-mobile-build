/* Synthetic NVN queue arena GPU probe. No game bytes or derived shaders. */

#include "graphics.h"
#include "nvn_protos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define API(name) NVNT_##name name = (NVNT_##name)botw_nvn_bootstrap_loader(#name)
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"CHECK failed: %s (%s)\n", #x, botw_graphics_last_error()); return 1; } } while(0)
static const void *ctl_ptrs[2];
static void *queueArena;static size_t queueArenaBytes;
static int queue_access(const void *p,size_t n,int writable){if(!writable)return 1;return (uintptr_t)p>=(uintptr_t)queueArena&&(uintptr_t)p-(uintptr_t)queueArena<=queueArenaBytes&&n<=queueArenaBytes-((uintptr_t)p-(uintptr_t)queueArena); }
static int valid(const void *p, size_t n) { return n <= 64 && (p == ctl_ptrs[0] || p == ctl_ptrs[1]); }
int main(int argc, char **argv) {
    if (!botw_graphics_init(NULL, NULL, valid)) return 77;
    if (argc > 1 && !strcmp(argv[1],"unsupported")) {
        void (*bad)(void) = botw_nvn_bootstrap_loader("nvnCommandBufferDispatchCompute");
        CHECK(bad); bad(); return 2;
    }
    API(nvnDeviceBuilderSetDefaults); API(nvnDeviceInitialize);
    API(nvnMemoryPoolBuilderSetDefaults); API(nvnMemoryPoolBuilderSetDevice);
    API(nvnMemoryPoolBuilderSetStorage); API(nvnMemoryPoolInitialize);
    API(nvnMemoryPoolGetBufferAddress); API(nvnMemoryPoolFinalize);
    API(nvnMemoryPoolInvalidateMappedRange); API(nvnMemoryPoolFlushMappedRange);
    API(nvnDeviceGetInteger);API(nvnQueueBuilderSetComputeMemorySize);API(nvnQueueBuilderSetCommandMemorySize);API(nvnQueueBuilderSetControlMemorySize);API(nvnQueueBuilderGetQueueMemorySize);API(nvnQueueBuilderSetQueueMemory);API(nvnQueueBuilderSetDefaults); API(nvnQueueBuilderSetDevice); API(nvnQueueInitialize);
    API(nvnQueueSubmitCommands); API(nvnQueueFinish); API(nvnQueueFenceSync);
    API(nvnSyncInitialize); API(nvnSyncWait);
    API(nvnCommandBufferInitialize); API(nvnCommandBufferBeginRecording);
    API(nvnCommandBufferEndRecording); API(nvnCommandBufferCopyBufferToBuffer);
    API(nvnCommandBufferSetRenderTargets); API(nvnCommandBufferClearColor);
    API(nvnCommandBufferBindProgram); API(nvnCommandBufferDrawArrays); API(nvnCommandBufferSetScissor);
    API(nvnTextureBuilderSetDefaults); API(nvnTextureBuilderSetDevice);
    API(nvnTextureBuilderSetSize2D); API(nvnTextureBuilderSetFormat);
    API(nvnTextureBuilderSetFlags); API(nvnTextureBuilderSetStorage);
    API(nvnTextureBuilderSetStride); API(nvnTextureInitialize);
    API(nvnProgramInitialize); API(nvnProgramSetShaders);
    NVNdeviceBuilder db; NVNdevice device;
    nvnDeviceBuilderSetDefaults(&db); CHECK(nvnDeviceInitialize(&device,&db));
    size_t page=(size_t)getpagesize(); unsigned char *memory=NULL;
    CHECK(posix_memalign((void**)&memory,page,page*2)==0); memset(memory,0,page*2);
    memcpy(memory,"real gpu copy",14);
    NVNmemoryPoolBuilder mb; NVNmemoryPool pool;
    nvnMemoryPoolBuilderSetDefaults(&mb); nvnMemoryPoolBuilderSetDevice(&mb,&device);
    nvnMemoryPoolBuilderSetStorage(&mb,memory,page*2); CHECK(nvnMemoryPoolInitialize(&pool,&mb));
    NVNqueueBuilder qb; NVNqueue queue; nvnQueueBuilderSetDefaults(&qb);
    nvnQueueBuilderSetDevice(&qb,&device);
    int commandBudget=-1,controlBudget=-1,computeBudget=-1;
    nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_QUEUE_COMMAND_MEMORY_DEFAULT_SIZE,&commandBudget);
    nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_QUEUE_CONTROL_MEMORY_DEFAULT_SIZE,&controlBudget);
    nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_QUEUE_COMPUTE_MEMORY_DEFAULT_SIZE,&computeBudget);
    CHECK(commandBudget==65536&&controlBudget==65536&&computeBudget==0);
    nvnQueueBuilderSetComputeMemorySize(&qb,0);nvnQueueBuilderSetCommandMemorySize(&qb,commandBudget);nvnQueueBuilderSetControlMemorySize(&qb,controlBudget);
    queueArenaBytes=nvnQueueBuilderGetQueueMemorySize(&qb);CHECK(queueArenaBytes==135168);
    CHECK(!posix_memalign(&queueArena,4096,queueArenaBytes));memset(queueArena,0x5a,queueArenaBytes);
    botw_graphics_set_memory_access_validator(queue_access);
    nvnQueueBuilderSetQueueMemory(&qb,queueArena,queueArenaBytes);CHECK(nvnQueueInitialize(&queue,&qb));
    NVNcommandBuffer cb; CHECK(nvnCommandBufferInitialize(&cb,&device));
    memory[0]='R'; /* Uncached/shared writes become GPU-visible without a flush. */
    nvnCommandBufferBeginRecording(&cb);
    uint64_t gpu=nvnMemoryPoolGetBufferAddress(&pool);
    nvnCommandBufferCopyBufferToBuffer(&cb,gpu,gpu+64,14,0);
    NVNcommandHandle handle=nvnCommandBufferEndRecording(&cb);
    CHECK(!memory[64]); nvnQueueSubmitCommands(&queue,1,&handle);
    NVNsync pending; CHECK(nvnSyncInitialize(&pending,&device));
    nvnQueueFenceSync(&queue,&pending,NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE,0);
    NVNsyncWaitResult waited=nvnSyncWait(&pending,10000000000ull);
    CHECK(waited==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED || waited==NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED);
    nvnQueueFinish(&queue);
    nvnMemoryPoolInvalidateMappedRange(&pool,64,14); CHECK(!memcmp(memory+64,"Real gpu copy",14));
    NVNsync sync; CHECK(nvnSyncInitialize(&sync,&device));
    CHECK(nvnSyncWait(&sync,0)==NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED);
    nvnQueueFenceSync(&queue,&sync,NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE,0);
    CHECK(nvnSyncWait(&sync,0)==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED);
    NVNtextureBuilder tb; NVNtexture texture;
    nvnTextureBuilderSetDefaults(&tb); nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetSize2D(&tb,4,4); nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);
    nvnTextureBuilderSetFlags(&tb,NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET);
    nvnTextureBuilderSetStride(&tb,256); nvnTextureBuilderSetStorage(&tb,&pool,4096);
    CHECK(nvnTextureInitialize(&texture,&tb));
    const NVNtexture *targets[]={&texture}; float color[]={0,1,0,1};
    nvnCommandBufferBeginRecording(&cb); nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
    if (argc > 1 && !strcmp(argv[1],"partial-clear")) nvnCommandBufferSetScissor(&cb,1,1,2,2);
    nvnCommandBufferClearColor(&cb,0,color,15); handle=nvnCommandBufferEndRecording(&cb);
    nvnQueueSubmitCommands(&queue,1,&handle); nvnQueueFinish(&queue);
    unsigned char pixels[64]; CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    CHECK(pixels[0]==0 && pixels[1]==255 && pixels[2]==0 && pixels[3]==255);
    unsigned char controls[2][64]={{0}}, codes[2][128]={{0}};
    for (int i=0;i<2;i++) { *(uint32_t*)controls[i]=0x98761234; *(uint32_t*)codes[i]=0x12345678;
        controls[i][8]=(unsigned char)i; codes[i][8]=(unsigned char)i; ctl_ptrs[i]=controls[i];
        memcpy(memory+128+i*128,codes[i],128); }
    const char *msl="#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 vs(uint v [[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)}; return float4(p[v],0,1); }\n"
        "fragment float4 fs() { return float4(1,0,0,1); }\n";
    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,msl,"vs"));
    CHECK(botw_graphics_register_shader(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,msl,"fs"));
    nvnMemoryPoolFlushMappedRange(&pool,128,256);
    NVNprogram program; CHECK(nvnProgramInitialize(&program,&device));
    NVNshaderData sd[]={{gpu+128,controls[0]},{gpu+256,controls[1]}};
    NVNshaderData bad[]={{gpu+128,NULL},{gpu+256,controls[1]}};
    CHECK(!nvnProgramSetShaders(&program,2,bad));
    CHECK(nvnProgramSetShaders(&program,2,sd));
    nvnCommandBufferBeginRecording(&cb); nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
    nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
    nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);
    handle=nvnCommandBufferEndRecording(&cb); nvnQueueSubmitCommands(&queue,1,&handle); nvnQueueFinish(&queue);
    CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    CHECK(pixels[0]==255 && pixels[1]==0 && pixels[2]==0 && pixels[3]==255);
    nvnMemoryPoolFinalize(&pool); botw_graphics_shutdown(); free(queueArena);free(memory);
    puts("GPU_COPY_CLEAR_DRAW_OK"); return 0;
}
