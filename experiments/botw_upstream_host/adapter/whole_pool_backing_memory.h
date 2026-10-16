/* Backing state belongs to the one existing g_pools entry. This module has no
 * object/address catalog. Normal operands require caller-owned lifetimes. */
#pragma once
#include <stddef.h>
#include <stdint.h>
int botw_whole_pool_read(void *destination,const void *source,size_t size);
int botw_whole_pool_write(void *destination,const void *source,size_t size);
int botw_whole_pool_writable(void *destination,size_t size);
int botw_whole_pool_readable(const void *source,size_t size);
#ifdef __OBJC__
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
@interface BotwWholePoolSegment:NSObject
@property void *base;
@property size_t length;
@property(strong) id<MTLBuffer> buffer;
@end
@interface BotwWholePoolBacking:NSObject
@property const void *owner;
@property void *memory;
@property size_t size,bufferOffset;
@property unsigned flags;
@property uint64_t generation;
@property BOOL live,retiring,coherent,noCopyDeferred;
@property(strong) id<MTLBuffer> buffer;
@property(strong) NSArray<BotwWholePoolSegment*> *segments;
@property(strong) dispatch_group_t group;
@end
int botw_whole_pool_backing_create(BotwWholePoolBacking *backing,id<MTLDevice> device,
    int(*logical_range)(const void*,size_t),char *error,size_t capacity);
int botw_whole_pool_backing_ensure(BotwWholePoolBacking *backing,id<MTLDevice> device,
    char *error,size_t capacity);
void botw_whole_pool_backing_drop(BotwWholePoolBacking *backing);
int botw_whole_pool_backing_ack(BotwWholePoolBacking *backing,char *error,size_t capacity);
int botw_whole_pool_backing_flush(BotwWholePoolBacking *backing,size_t offset,size_t size,int invalidate,
    char *error,size_t capacity);
#endif
