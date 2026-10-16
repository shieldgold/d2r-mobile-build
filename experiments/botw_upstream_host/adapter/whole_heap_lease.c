/* SPDX-License-Identifier: MIT
 * Normal bounded geometry/preparation only. Object lifetime stays in G/g_map. */
#include "whole_heap_lease_internal.h"
#include <errno.h>
#include <stdio.h>
#include <unistd.h>
int dm_guest_write_prepare_checked(void *,size_t);
int botw_whole_heap_geometry(uint64_t source,size_t size,uint64_t *base,size_t *span,
        size_t *offset,char *error,size_t capacity){
    int page=getpagesize();
    if(!source||!size||((source|size)&0xfffu)||size>UINT64_MAX-source||
       page<4096||((unsigned)page&((unsigned)page-1))||source+size>UINT64_MAX-((unsigned)page-1)){
        if(error&&capacity)snprintf(error,capacity,"normal heap lease: invalid Horizon span or host-page envelope");errno=EINVAL;return -1;
    }
    *base=source&~((uint64_t)page-1);uint64_t end=(source+size+page-1)&~((uint64_t)page-1);
    if(end-*base>SIZE_MAX){errno=EINVAL;return -1;}
    *span=(size_t)(end-*base);*offset=(size_t)(source-*base);return 0;
}
int botw_whole_heap_prepare_write(uint64_t source,size_t size,char *error,size_t capacity){
    if(dm_guest_write_prepare_checked((void*)(uintptr_t)source,size))return 0;
    if(error&&capacity)snprintf(error,capacity,"normal heap lease: actual checked write preparation failed");errno=EIO;return -1;
}
