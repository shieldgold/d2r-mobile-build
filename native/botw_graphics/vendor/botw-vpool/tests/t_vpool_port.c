/* Host-port contracts; all memory and owners belong to this synthetic fixture. */
#include "../nvn_vpool.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define PG ((uint64_t)NVN_VPOOL_PAGE)
static int failures, checks;
#define CHECK(c, text) do { checks++; if(!(c)){fprintf(stderr,"FAIL %s:%d: %s\n",__FILE__,__LINE__,text);failures++;}}while(0)
static char po, qo, vo, wo;
static void *backing(size_t bytes) {
    void *p=NULL; if(posix_memalign(&p,4096,bytes)){perror("backing");exit(2);}return p;
}
static NVNmappingRequest req(const void *p,uint64_t poff,uint64_t voff,uint64_t bytes,int cls){
    return (NVNmappingRequest){(const NVNmemoryPool*)p,poff,voff,bytes,cls,0};
}
static int map1(NVNmappingRequest r){return nvn_vpool_map(&vo,&r,1,NULL,0);}
static void unchanged(nvn_vpool_test_stats before,nvn_vpool_stats_t registry,uint64_t generation){
    nvn_vpool_test_stats after;nvn_vpool_test_get_stats(&after);
    nvn_vpool_stats_t state;nvn_vpool_stats(&state);
    CHECK(after.active_allocations==before.active_allocations,"failure leaves active allocations unchanged");
    CHECK(after.physical_capacity==before.physical_capacity&&after.virtual_capacity==before.virtual_capacity,"failure leaves registry capacity unchanged");
    CHECK(after.active_reservations==before.active_reservations&&after.reserved_bytes==before.reserved_bytes,"failure leaves real reservations unchanged");
    CHECK(state.phys_live==registry.phys_live&&state.virt_live==registry.virt_live&&state.tombstones==registry.tombstones&&state.spans==registry.spans&&state.above_va_limit==registry.above_va_limit,"failure leaves registered objects/spans unchanged");
    CHECK(nvn_vpool_generation()==generation,"failure leaves generation unchanged");
}
static void test_kinds(void){
    unsigned char *p=backing(4*PG);memset(p,0x11,PG);memset(p+PG,0x22,PG);memset(p+2*PG,0x33,2*PG);
    CHECK(nvn_vpool_phys_register(&po,p,4*PG)==NVN_VPOOL_OK,"physical register");uint64_t base=0;
    CHECK(nvn_vpool_virt_register(&vo,4*PG,&base,NULL)==NVN_VPOOL_OK,"virtual register");
    CHECK(map1(req(&po,0,0,2*PG,0x1fe))==NVN_VPOOL_OK,"class 1FE ordinary color maps");
    nvn_vpool_lease l;
    CHECK(nvn_vpool_lease_acquire(&l,base,2*PG)==NVN_VPOOL_OK,"color lease");
    CHECK(l.contiguous&&l.raw_kind==0xfe&&l.cpu==p,"contiguous color preserves FE");
    nvn_vpool_span s;CHECK(nvn_vpool_lease_span(&l,0,&s)==NVN_VPOOL_OK&&s.raw_kind==0xfe,"span exposes FE");nvn_vpool_lease_release(&l);
    NVNmappingRequest r[]={req(&po,0,0,PG,0x100),req(&po,PG,PG,PG,0x1fe)};
    CHECK(nvn_vpool_map(&vo,r,2,NULL,0)==NVN_VPOOL_OK,"mixed kinds map in one batch");
    CHECK(nvn_vpool_lease_acquire(&l,base,2*PG)==NVN_VPOOL_OK,"mixed-kind lease");
    CHECK(!l.contiguous&&l.nspans==2,"kind boundary neither coalesces nor claims GPU contiguity");
    CHECK(nvn_vpool_lease_span(&l,0,&s)==NVN_VPOOL_OK&&s.raw_kind==0,"pitch raw kind zero");
    CHECK(nvn_vpool_lease_span(&l,1,&s)==NVN_VPOOL_OK&&s.raw_kind==0xfe,"second span retains FE");
    unsigned char bytes[2];CHECK(nvn_vpool_lease_read(&l,PG-1,bytes,2)==NVN_VPOOL_OK&&bytes[0]==0x11&&bytes[1]==0x22,"mixed-kind CPU gather is fresh");nvn_vpool_lease_release(&l);
    r[0]=req(&po,0,0,PG,0x1fe);r[1]=req(&po,PG,PG,PG,0x1fe);
    CHECK(nvn_vpool_map(&vo,r,2,NULL,0)==NVN_VPOOL_OK,"same-kind contiguous batch");
    CHECK(nvn_vpool_lease_acquire(&l,base,2*PG)==NVN_VPOOL_OK&&l.contiguous&&l.nspans==1&&l.raw_kind==0xfe,"same generation FE coalesces");uint64_t id=l.phys_id;nvn_vpool_lease_release(&l);
    CHECK(map1(req(&po,0,0,PG,0x1fe))==NVN_VPOOL_OK,"remap only first FE page");
    CHECK(nvn_vpool_lease_acquire(&l,base,2*PG)==NVN_VPOOL_OK&&l.contiguous&&l.nspans==2&&l.raw_kind==0xfe,"different generations remain contiguous when kind and offsets agree");nvn_vpool_lease_release(&l);
    int bad[]={-1,1,0xfe,0x101,0x102,0x1ff,0x200,0x7fffffff};
    for(unsigned i=0;i<sizeof bad/sizeof *bad;i++){
        uint64_t gen=nvn_vpool_generation();r[0]=req(&po,2*PG,0,PG,0x100);r[1]=req(&po,PG,PG,PG,bad[i]);
        CHECK(nvn_vpool_map(&vo,r,2,NULL,0)==NVN_VPOOL_E_CLASS&&nvn_vpool_generation()==gen,"unknown later class preserves whole batch");
        CHECK(nvn_vpool_lease_acquire(&l,base,2*PG)==NVN_VPOOL_OK&&l.raw_kind==0xfe&&l.phys_id==id&&l.cpu==p,"failed kind batch retains previous range and ID");nvn_vpool_lease_release(&l);
    }
    CHECK(map1(req(&po,0,0,2*PG,0))==NVN_VPOOL_OK,"legacy class0 remains accepted");
    CHECK(nvn_vpool_lease_acquire(&l,base,2*PG)==NVN_VPOOL_OK&&l.contiguous&&l.raw_kind==0,"legacy class0 is raw0");nvn_vpool_lease_release(&l);
    CHECK(map1(req(NULL,123,0,PG,999))==NVN_VPOOL_OK,"legacy unmap ignores offset/class; production adapter is stricter");
    CHECK(nvn_vpool_lease_acquire(&l,base,1)==NVN_VPOOL_UNMAPPED,"unmap makes hole");
    nvn_vpool_virt_finalize(&vo);nvn_vpool_phys_finalize(&po,NULL);free(p);
}
static void test_register_allocation(int virtual_pool,long at){
    void *p=backing(PG);nvn_vpool_test_stats before;nvn_vpool_test_get_stats(&before);nvn_vpool_stats_t state;nvn_vpool_stats(&state);uint64_t gen=nvn_vpool_generation(),base=123;
    nvn_vpool_test_fail_allocation_after(at);
    int st=virtual_pool?nvn_vpool_virt_register(&vo,PG,&base,NULL):nvn_vpool_phys_register(&po,p,PG);
    CHECK(st==NVN_VPOOL_E_NOMEM,"registration allocator failure is reported");
    if(virtual_pool)CHECK(base==0,"failed virtual registration publishes no address");
    unchanged(before,state,gen);nvn_vpool_test_fail_allocation_after(-1);
    CHECK(nvn_vpool_phys_register(&po,p,PG)==NVN_VPOOL_OK,"same physical owner remains reusable after allocation failure");nvn_vpool_phys_finalize(&po,NULL);
    CHECK(nvn_vpool_virt_register(&vo,PG,&base,NULL)==NVN_VPOOL_OK,"same virtual owner remains reusable after allocation failure");nvn_vpool_virt_finalize(&vo);free(p);
}
static void test_register_growth(int virtual_pool,long at){
    static char owners[17];void *p=backing(PG);uint64_t base=0;
    for(int i=0;i<16;i++)CHECK((virtual_pool?nvn_vpool_virt_register(&owners[i],PG,&base,NULL):nvn_vpool_phys_register(&owners[i],p,PG))==NVN_VPOOL_OK,"fill original registry capacity");
    nvn_vpool_test_stats before;nvn_vpool_test_get_stats(&before);nvn_vpool_stats_t state;nvn_vpool_stats(&state);uint64_t gen=nvn_vpool_generation();
    CHECK((virtual_pool?before.virtual_capacity:before.physical_capacity)==16,"fixture reaches registry growth boundary");
    nvn_vpool_test_fail_allocation_after(at);CHECK((virtual_pool?nvn_vpool_virt_register(&owners[16],PG,&base,NULL):nvn_vpool_phys_register(&owners[16],p,PG))==NVN_VPOOL_E_NOMEM,"registry growth allocation fails");unchanged(before,state,gen);nvn_vpool_test_fail_allocation_after(-1);
    for(int i=0;i<16;i++)CHECK((virtual_pool?nvn_vpool_virt_finalize(&owners[i]):nvn_vpool_phys_finalize(&owners[i],NULL))==NVN_VPOOL_OK,"retire every original owner after failed growth");free(p);
}
static void test_map_allocation(long at){
    unsigned char *p=backing(2*PG),*q=backing(2*PG);memset(p,0x41,2*PG);memset(q,0x51,2*PG);uint64_t base;
    nvn_vpool_phys_register(&po,p,2*PG);nvn_vpool_phys_register(&qo,q,2*PG);nvn_vpool_virt_register(&vo,2*PG,&base,NULL);CHECK(map1(req(&po,0,0,2*PG,0x100))==NVN_VPOOL_OK,"initial map");
    nvn_vpool_test_stats before;nvn_vpool_test_get_stats(&before);nvn_vpool_stats_t state;nvn_vpool_stats(&state);uint64_t gen=nvn_vpool_generation();
    nvn_vpool_test_fail_allocation_after(at);CHECK(map1(req(&qo,0,0,2*PG,0x1fe))==NVN_VPOOL_E_NOMEM,"span scratch allocation failure");unchanged(before,state,gen);nvn_vpool_test_fail_allocation_after(-1);
    nvn_vpool_lease l;unsigned char byte=0;CHECK(nvn_vpool_lease_acquire(&l,base,2*PG)==NVN_VPOOL_OK&&l.cpu==p&&l.raw_kind==0&&l.gen==gen,"old mapped bytes/kind/generation intact");CHECK(nvn_vpool_lease_read(&l,PG,&byte,1)==NVN_VPOOL_OK&&byte==0x41,"old actual backing bytes intact");nvn_vpool_lease_release(&l);
    CHECK(map1(req(&qo,0,0,2*PG,0x1fe))==NVN_VPOOL_OK,"map succeeds after allocator reset");nvn_vpool_virt_finalize(&vo);nvn_vpool_phys_finalize(&po,NULL);nvn_vpool_phys_finalize(&qo,NULL);free(p);free(q);
}
static void test_reservation_failure(long at){
    nvn_vpool_test_stats before;nvn_vpool_test_get_stats(&before);nvn_vpool_stats_t state;nvn_vpool_stats(&state);uint64_t gen=nvn_vpool_generation(),base=123;
    nvn_vpool_test_reservation_floor(UINT64_C(1)<<44);nvn_vpool_test_fail_reservation_after(at);
    CHECK(nvn_vpool_virt_register(&vo,PG,&base,NULL)==NVN_VPOOL_E_NOMEM&&base==0,"all reserve attempts fail without publishing a token");unchanged(before,state,gen);
    nvn_vpool_test_stats after;nvn_vpool_test_get_stats(&after);CHECK(after.reservation_calls>before.reservation_calls,"real reservation path attempted");
    nvn_vpool_test_fail_reservation_after(-1);nvn_vpool_test_reservation_floor(0);
    CHECK(nvn_vpool_virt_register_strict(&vo,PG,&base)==NVN_VPOOL_OK,"failed reserve leaves owner reusable");nvn_vpool_virt_finalize(&vo);
}
static void test_strict(void){
    nvn_vpool_test_reservation_floor(UINT64_C(1)<<44);uint64_t base=0;int above=0;
    CHECK(nvn_vpool_virt_register(&vo,PG,&base,&above)==NVN_VPOOL_OK&&above&&base>=NVN_VPOOL_VA_LIMIT,"legacy fallback returns actual above-limit mapping untruncated");
    nvn_vpool_lease l;CHECK(nvn_vpool_lease_acquire(&l,base,1)==NVN_VPOOL_UNMAPPED,"complete high token is classified as virtual");
    CHECK(nvn_vpool_lease_acquire(&l,base&((UINT64_C(1)<<40)-1),1)==NVN_VPOOL_NOT_VIRTUAL,"truncated token is not an alias");
    CHECK(nvn_vpool_virt_finalize(&vo)==NVN_VPOOL_OK,"legacy high token finalize");CHECK(nvn_vpool_lease_acquire(&l,base,1)==NVN_VPOOL_STALE,"high token tombstone retained");
    nvn_vpool_test_stats before;nvn_vpool_test_get_stats(&before);nvn_vpool_stats_t state;nvn_vpool_stats(&state);uint64_t gen=nvn_vpool_generation();base=123;
    CHECK(nvn_vpool_virt_register_strict(&wo,PG,&base)==NVN_VPOOL_E_NOMEM&&base==0,"strict rejects and releases above-limit reservation");unchanged(before,state,gen);
    nvn_vpool_test_reservation_floor(0);CHECK(nvn_vpool_virt_register_strict(&wo,PG,&base)==NVN_VPOOL_OK&&base&&base%PG==0&&base+PG<=NVN_VPOOL_VA_LIMIT,"strict accepted range is real aligned reservation below preferred bound");
    before=(nvn_vpool_test_stats){0};nvn_vpool_test_get_stats(&before);nvn_vpool_stats(&state);gen=nvn_vpool_generation();uint64_t duplicate=123;
    CHECK(nvn_vpool_virt_register_strict(&wo,PG,&duplicate)==NVN_VPOOL_E_DUPLICATE&&duplicate==0,"strict duplicate rejects");unchanged(before,state,gen);
    CHECK(nvn_vpool_lease_acquire(&l,base,1)==NVN_VPOOL_UNMAPPED,"new strict pool starts unmapped");nvn_vpool_virt_finalize(&wo);
}
static void test_invalid(void){
    uint64_t base=123;CHECK(nvn_vpool_virt_register_strict(NULL,PG,&base)==NVN_VPOOL_INVALID&&base==0,"strict null owner");
    CHECK(nvn_vpool_virt_register_strict(&vo,0,&base)==NVN_VPOOL_INVALID,"strict zero size");CHECK(nvn_vpool_virt_register_strict(&vo,PG+1,&base)==NVN_VPOOL_INVALID,"strict misalignment");CHECK(nvn_vpool_virt_register_strict(&vo,UINT64_MAX-PG+1,&base)==NVN_VPOOL_INVALID,"strict huge overflow size");CHECK(nvn_vpool_virt_register_strict(&vo,PG,NULL)==NVN_VPOOL_INVALID,"strict null output");
    unsigned char *p=backing(PG);nvn_vpool_phys_register(&po,p,PG);nvn_vpool_virt_register(&vo,PG,&base,NULL);CHECK(map1(req(&po,0,0,PG,0x1fe))==NVN_VPOOL_OK,"lease lock setup");nvn_vpool_lease l;CHECK(nvn_vpool_lease_acquire(&l,base,1)==NVN_VPOOL_OK,"hold lease");
    nvn_vpool_test_stats before;nvn_vpool_test_get_stats(&before);nvn_vpool_stats_t state;nvn_vpool_stats(&state);uint64_t gen=nvn_vpool_generation(),other=123;
    CHECK(nvn_vpool_virt_register_strict(&wo,PG,&other)==NVN_VPOOL_E_LOCK_ORDER&&other==0,"strict writer under own lease refuses before reservation");unchanged(before,state,gen);
    nvn_vpool_test_stats after;nvn_vpool_test_get_stats(&after);CHECK(after.allocation_calls==before.allocation_calls&&after.reservation_calls==before.reservation_calls,"nested strict writer does not even attempt allocation or reserve");
    nvn_vpool_lease_release(&l);nvn_vpool_virt_finalize(&vo);nvn_vpool_phys_finalize(&po,NULL);free(p);
}
int main(int argc,char **argv){
    alarm(30);if(argc<2)return 2;long at=argc>2?strtol(argv[2],NULL,10):0;
    if(!strcmp(argv[1],"kind"))test_kinds();else if(!strcmp(argv[1],"alloc-physical"))test_register_allocation(0,at);else if(!strcmp(argv[1],"alloc-virtual"))test_register_allocation(1,at);else if(!strcmp(argv[1],"grow-physical"))test_register_growth(0,at);else if(!strcmp(argv[1],"grow-virtual"))test_register_growth(1,at);else if(!strcmp(argv[1],"alloc-map"))test_map_allocation(at);else if(!strcmp(argv[1],"reserve"))test_reservation_failure(at);else if(!strcmp(argv[1],"strict"))test_strict();else if(!strcmp(argv[1],"invalid"))test_invalid();else return 2;
    printf("VPOOL_PORT_%s: %d checks, %d failed\n",argv[1],checks,failures);return failures!=0;
}
