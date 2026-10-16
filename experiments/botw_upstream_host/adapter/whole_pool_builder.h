/* SPDX-License-Identifier: MIT
 * Fixed base-v0 seven-leaf MemoryPoolBuilder ABI. No pool registry/backing. */
#pragma once
#include <stddef.h>
#include <stdint.h>
typedef struct {
    void *dev, *mem;
    uint64_t size;
    uint32_t flags;
} botw_whole_pool_builder_schema;
_Static_assert(sizeof(void*)==8 && sizeof(size_t)==8 && sizeof(botw_whole_pool_builder_schema)==32,
    "base-v0 pool builder uses the 64-bit host ABI");
_Static_assert(offsetof(botw_whole_pool_builder_schema,dev)==0 &&
    offsetof(botw_whole_pool_builder_schema,mem)==8 &&
    offsetof(botw_whole_pool_builder_schema,size)==16 &&
    offsetof(botw_whole_pool_builder_schema,flags)==24,"base-v0 pool builder fields");

/* 1 means success. Getter failure preserves the supplied output value. Callers
 * own operand lifetimes and exclude concurrent remapping, as required by protected_copy.h.
 * Only the addressed field is copied. SetDefaults does not read any field. */
int botw_whole_pool_builder_defaults(void *,char *,size_t);
int botw_whole_pool_builder_device(void *,void *,char *,size_t);
int botw_whole_pool_builder_storage(void *,void *,size_t,char *,size_t);
int botw_whole_pool_builder_flags(void *,uint32_t,char *,size_t);
int botw_whole_pool_builder_get_memory(const void *,void **,char *,size_t);
int botw_whole_pool_builder_get_size(const void *,size_t *,char *,size_t);
int botw_whole_pool_builder_get_flags(const void *,uint32_t *,char *,size_t);
