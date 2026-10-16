/* Owned host mappings only. The real normal operand provider/copy is linked. */
#include "whole_guest_memory.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
typedef struct {unsigned char *base;size_t size;int owner,mode,prepare_calls,fallback_calls,dirty,fail;} fixture;
static int kernel(void *opaque,uint64_t address,size_t size,int writable){
    fixture *f=opaque;(void)writable;
    if(f->mode)return f->mode;
    if(f->owner!=1)return 0;
    uintptr_t base=(uintptr_t)f->base;
    if(address>=base&&address-base<f->size)return size<=f->size-(address-base)?1:-1;
    return 0;
}
static int module(void *opaque,uint64_t address,size_t size,int writable){fixture *f=opaque;(void)address;(void)size;(void)writable;f->fallback_calls++;return f->owner==2;}
static int thread(void *opaque,uint64_t address,size_t size,int writable){fixture *f=opaque;(void)address;(void)size;(void)writable;f->fallback_calls++;return f->owner==3;}
static int prepare(void *opaque,uint64_t address,size_t size,char *error,size_t capacity){
    fixture *f=opaque;(void)address;(void)size;(void)error;(void)capacity;f->prepare_calls++;
    if(f->fail)return -1;
    return f->dirty?mprotect(f->base,f->size,PROT_READ|PROT_WRITE):0;
}
#define CHECK(c) do{if(!(c)){fprintf(stderr,"owned normal operand condition failed at %d\n",__LINE__);return 91;}}while(0)
int main(int argc,char **argv){
    if(argc!=2)return 2;
    size_t page=(size_t)getpagesize();unsigned char *p=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);if(p==MAP_FAILED)return 3;
    memset(p,0x31,page*2);fixture f={.base=p,.size=page,.owner=1};
    botw_whole_guest_memory c={BOTW_WHOLE_GUEST_MEMORY_ABI,sizeof c,&f,kernel,module,thread,prepare};
    char error[128];uint32_t value=0x12345678,got=0;uint64_t address=(uintptr_t)p;
    if(!strcmp(argv[1],"copy")){CHECK(!botw_whole_guest_memory_read(&c,address,&got,4,error,sizeof error)&&got==0x31313131);CHECK(!botw_whole_guest_memory_write(&c,address,&value,4,error,sizeof error));memcpy(&got,p,4);CHECK(got==value);}
    else if(!strcmp(argv[1],"denial")){f.mode=-1;f.owner=2;CHECK(!botw_whole_guest_memory_access(&c,address,4,1));CHECK(!f.prepare_calls&&!f.fallback_calls);}
    else if(!strcmp(argv[1],"unknown")){f.owner=0;CHECK(!botw_whole_guest_memory_access(&c,address,4,0));CHECK(!f.prepare_calls);}
    else if(!strcmp(argv[1],"bounds")){CHECK(!botw_whole_guest_memory_access(&c,address+page-1,2,1));CHECK(!botw_whole_guest_memory_access(&c,UINT64_MAX-1,8,1));CHECK(!botw_whole_guest_memory_access(&c,address,0,1));CHECK(!f.prepare_calls);}
    else if(!strcmp(argv[1],"none")){CHECK(!mprotect(p,page,PROT_NONE));CHECK(botw_whole_guest_memory_read(&c,address,&got,4,error,sizeof error));}
    else if(!strcmp(argv[1],"readonly")){CHECK(!mprotect(p,page,PROT_READ));CHECK(botw_whole_guest_memory_write(&c,address,&value,4,error,sizeof error));CHECK(p[0]==0x31);}
    else if(!strcmp(argv[1],"dirty")){CHECK(!mprotect(p,page,PROT_READ));f.dirty=1;CHECK(!botw_whole_guest_memory_write(&c,address,&value,4,error,sizeof error));CHECK(f.prepare_calls>0);memcpy(&got,p,4);CHECK(got==value);}
    else if(!strcmp(argv[1],"prepare-fail")){f.fail=1;CHECK(botw_whole_guest_memory_write(&c,address,&value,4,error,sizeof error));CHECK(p[0]==0x31&&f.prepare_calls==1);}
    else if(!strcmp(argv[1],"fallback")){f.owner=2;CHECK(botw_whole_guest_memory_access(&c,address,4,0));f.owner=3;CHECK(botw_whole_guest_memory_access(&c,address,4,0));}
    else if(!strcmp(argv[1],"config")){c.prepare_write=NULL;CHECK(!botw_whole_guest_memory_valid(&c));CHECK(botw_whole_guest_memory_read(&c,address,&got,4,error,sizeof error));}
    else if(!strcmp(argv[1],"invalid-class")){f.mode=2;CHECK(!botw_whole_guest_memory_access(&c,address,4,1));CHECK(!f.prepare_calls&&!f.fallback_calls);}
    else if(!strcmp(argv[1],"multileaf")){f.size=page*2;CHECK(!mprotect(p+page,page,PROT_READ));unsigned char *out=malloc(page*2);CHECK(out);CHECK(!botw_whole_guest_memory_read(&c,address,out,page*2,error,sizeof error));CHECK(!botw_whole_guest_memory_access(&c,address,page*2,1));free(out);}
    else return 4;
    if(munmap(p,page*2))return 5;
    return 0;
}
