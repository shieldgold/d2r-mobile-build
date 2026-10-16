/* SPDX-License-Identifier: MIT
 * Normal owned address reservation, without region enumeration or probing. */
#include "whole_address_space.h"
#include <errno.h>
#include <stdint.h>
#include <sys/mman.h>

static int reserve_one(botw_whole_address_space *out,size_t size,int *error){
    void *base=mmap(NULL,size,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);
    if(base==MAP_FAILED){if(error)*error=errno;return -1;}
    *out=(botw_whole_address_space){base,size};
    if(error)*error=0;
    return 0;
}
int botw_whole_address_space_reserve(botw_whole_address_space *out,int physical_ios,int *last_errno){
    if(!out||(physical_ios!=0&&physical_ios!=1)){
        if(last_errno)*last_errno=EINVAL;
        return -1;
    }
    static const size_t sizes[]={UINT64_C(5)<<30,UINT64_C(4)<<30,UINT64_C(3)<<30,UINT64_C(2)<<30};
    size_t count=physical_ios?sizeof sizes/sizeof *sizes:1;
    for(size_t i=0;i<count;i++)if(!reserve_one(out,sizes[i],last_errno))return 0;
    return -1;
}
