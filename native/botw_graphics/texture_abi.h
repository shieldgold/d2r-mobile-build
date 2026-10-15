/* Base-v0 SDK ABI, independently checked by the local native oracle.
 * The upstream 1.9 packed objects are intentionally not used here. */
#pragma once
#include <stddef.h>
#include <stdint.h>
typedef struct {
    NVNdevice *device;
    NVNmemoryPool *pool;
    int64_t offset;
    uint32_t flags,target;
    int32_t width,height,depth,levels;
    uint32_t format;
    int32_t samples;
    uint32_t swizzle[4],depthMode,padding4c;
    const void *packagedData;
    uint16_t packaged,padding5a;
    uint32_t stride,glName;
} TextureBuilder;
typedef struct {
    uint8_t flags,baseLevel,numLevels,padding;
    uint16_t minLayer,numLayers;
    uint32_t format,swizzle[4],depthMode,target;
} TextureView;
enum { BWTextureBuilderBytes=0x64, BWTextureViewBytes=0x24 };
_Static_assert(offsetof(TextureBuilder,pool)==8 && offsetof(TextureBuilder,flags)==0x18 &&
    offsetof(TextureBuilder,swizzle)==0x38 && offsetof(TextureBuilder,packaged)==0x58 &&
    offsetof(TextureBuilder,stride)==0x5c && offsetof(TextureBuilder,glName)==0x60,"base texture builder ABI");
_Static_assert(sizeof(TextureView)==0x24 && offsetof(TextureView,swizzle)==0xc,"base texture view ABI");
