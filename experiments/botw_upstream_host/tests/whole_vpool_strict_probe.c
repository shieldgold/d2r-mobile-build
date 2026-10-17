/* Complete original interval/lease module, owned real VM only; no guest/Metal. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <errno.h>

static uint64_t owned_floor;
static size_t owned_reserved;
static unsigned owned_mmap_calls;
static void *owned_mmap(void *hint,size_t size,int protection,int flags,int fd,off_t offset) {
    owned_mmap_calls++;
    if(protection!=PROT_NONE || flags!=(MAP_PRIVATE|MAP_ANON) || fd!=-1 || offset)abort();
    if(owned_floor)hint=(void *)(uintptr_t)owned_floor;
    void *p=mmap(hint,size,protection,flags,fd,offset);
    if(p!=MAP_FAILED && (uintptr_t)p<owned_floor){if(munmap(p,size))abort();errno=ENOMEM;return MAP_FAILED;}
    if(p!=MAP_FAILED)owned_reserved+=size;
    return p;
}
static int owned_munmap(void *p,size_t size) {
    int result=munmap(p,size);
    if(!result){if(size>owned_reserved)abort();owned_reserved-=size;}
    return result;
}
#define mmap owned_mmap
#define munmap owned_munmap
#include "nvn_vpool.c"
#undef mmap
#undef munmap

static int failed;
#define CHECK(test) do {if(!(test)){fprintf(stderr,"owned strict check failed at line %d\n",__LINE__);failed=1;}} while(0)
static int strict(const void *owner,uint64_t size,uint64_t *base) {
#ifdef WHOLE_STRICT_BASELINE
    return nvn_vpool_virt_register(owner,size,base,NULL);
#else
    return nvn_vpool_virt_register_strict(owner,size,base);
#endif
}
static void strict_high(void) {
    char owner=0;uint64_t base=123;uint64_t generation=nvn_vpool_generation();
    nvn_vpool_stats_t before={0},after={0};nvn_vpool_stats(&before);
    owned_floor=UINT64_C(1)<<44;
    int status=strict(&owner,NVN_VPOOL_PAGE,&base);
    CHECK(status==NVN_VPOOL_E_NOMEM);CHECK(base==0);CHECK(owned_mmap_calls>=6);
    nvn_vpool_stats(&after);CHECK(after.virt_live==before.virt_live && after.tombstones==before.tombstones);
    CHECK(after.reserved_bytes==before.reserved_bytes && after.above_va_limit==before.above_va_limit);
    CHECK(nvn_vpool_generation()==generation);CHECK(owned_reserved==0);
    if(status==NVN_VPOOL_OK)CHECK(nvn_vpool_virt_finalize(&owner)==NVN_VPOOL_OK);
}
static void legacy_high(void) {
    char owner=0;uint64_t base=0;int above=0;owned_floor=UINT64_C(1)<<44;
    CHECK(nvn_vpool_virt_register(&owner,NVN_VPOOL_PAGE,&base,&above)==NVN_VPOOL_OK);
    CHECK(above==1 && base>=owned_floor && owned_reserved==NVN_VPOOL_PAGE);
    nvn_vpool_lease lease={0};CHECK(nvn_vpool_lease_acquire(&lease,base,1)==NVN_VPOOL_UNMAPPED);
    CHECK(nvn_vpool_virt_finalize(&owner)==NVN_VPOOL_OK);
    CHECK(nvn_vpool_lease_acquire(&lease,base,1)==NVN_VPOOL_STALE);
}
static void ordinary(void) {
    char owner=0;uint64_t base=0;CHECK(strict(&owner,NVN_VPOOL_PAGE,&base)==NVN_VPOOL_OK);
    CHECK(base && base%NVN_VPOOL_PAGE==0 && base<NVN_VPOOL_VA_LIMIT && NVN_VPOOL_PAGE<=NVN_VPOOL_VA_LIMIT-base);
    CHECK(owned_reserved==NVN_VPOOL_PAGE);
    nvn_vpool_lease lease={0};CHECK(nvn_vpool_lease_acquire(&lease,base,1)==NVN_VPOOL_UNMAPPED);
    CHECK(nvn_vpool_virt_finalize(&owner)==NVN_VPOOL_OK);
    CHECK(nvn_vpool_lease_acquire(&lease,base,1)==NVN_VPOOL_STALE);
    CHECK(owned_reserved==NVN_VPOOL_PAGE); /* existing permanent tombstone owns its reservation */
}
static void invalid(void) {
    char owner=0;uint64_t base=123;
    CHECK(strict(NULL,NVN_VPOOL_PAGE,&base)==NVN_VPOOL_INVALID && base==0);
    CHECK(strict(&owner,0,&base)==NVN_VPOOL_INVALID);
    CHECK(strict(&owner,NVN_VPOOL_PAGE+1,&base)==NVN_VPOOL_INVALID);
    CHECK(strict(&owner,UINT64_MAX,&base)==NVN_VPOOL_INVALID);
    CHECK(strict(&owner,NVN_VPOOL_PAGE,NULL)==NVN_VPOOL_INVALID);
    CHECK(owned_mmap_calls==0 && owned_reserved==0 && nvn_vpool_generation()==0);
}
static void nested(void) {
    char owner=0,other=0,physical=0;void *mem=NULL;uint64_t base=0;
    CHECK(posix_memalign(&mem,NVN_VPOOL_PHYS_ALIGN,NVN_VPOOL_PAGE)==0);
    CHECK(nvn_vpool_phys_register(&physical,mem,NVN_VPOOL_PAGE)==NVN_VPOOL_OK);
    CHECK(strict(&owner,NVN_VPOOL_PAGE,&base)==NVN_VPOOL_OK);
    NVNmappingRequest request={.physicalPool=(const NVNmemoryPool *)&physical,.size=NVN_VPOOL_PAGE,.storageClass=0x100};
    CHECK(nvn_vpool_map(&owner,&request,1,NULL,0)==NVN_VPOOL_OK);
    nvn_vpool_lease lease={0};CHECK(nvn_vpool_lease_acquire(&lease,base,1)==NVN_VPOOL_OK);
    unsigned calls=owned_mmap_calls;uint64_t output=123;
    CHECK(strict(&other,NVN_VPOOL_PAGE,&output)==NVN_VPOOL_E_LOCK_ORDER && output==0);
    CHECK(owned_mmap_calls==calls);nvn_vpool_lease_release(&lease);
    CHECK(nvn_vpool_virt_finalize(&owner)==NVN_VPOOL_OK);
    CHECK(nvn_vpool_phys_finalize(&physical,NULL)==NVN_VPOOL_OK);free(mem);
}
static void duplicate(void) {
    char owner=0;uint64_t base=0,output=123;CHECK(strict(&owner,NVN_VPOOL_PAGE,&base)==NVN_VPOOL_OK);
    size_t retained=owned_reserved;uint64_t generation=nvn_vpool_generation();
    CHECK(strict(&owner,NVN_VPOOL_PAGE,&output)==NVN_VPOOL_E_DUPLICATE && output==0);
    CHECK(owned_reserved==retained && nvn_vpool_generation()==generation);
    CHECK(nvn_vpool_virt_finalize(&owner)==NVN_VPOOL_OK);
}
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    if(!strcmp(argv[1],"strict-high"))strict_high();else if(!strcmp(argv[1],"legacy-high"))legacy_high();
    else if(!strcmp(argv[1],"ordinary"))ordinary();else if(!strcmp(argv[1],"invalid"))invalid();
    else if(!strcmp(argv[1],"nested"))nested();else if(!strcmp(argv[1],"duplicate"))duplicate();else return 2;
    if(failed)return 91;puts("WHOLE_VPOOL_STRICT_OWNED_CPU_OK");return 0;
}
