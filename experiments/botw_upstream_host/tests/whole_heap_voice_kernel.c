/* Include the frozen owned normal/transfer fixture with only its main renamed
 * mechanically in the private test directory. No second kernel/catalog. */
#include "owned_normal_fixture.inc"
#include "whole_heap_voice_kernel.h"
int owned_heap_voice_initialize(void){return initialize();}
uint64_t owned_heap_voice_source(void){return (uintptr_t)(g_heap+2*MIB+4096);}
uint64_t owned_heap_voice_process(void){return (uint64_t)getpid();}
int owned_heap_voice_transfer(size_t size,botw_whole_transfer_lease **owner,botw_whole_transfer_view *view){
    size_t page=(size_t)getpagesize();
    CHECK(size&&size<=32*MIB&&owner&&!*owner&&view);
    size_t envelope=(size+page-1)&~(page-1);uint8_t *source=g_heap+32*MIB;
    memset(source,0,envelope);uint32_t handle=0;
    CHECK(!create(source,envelope,0,&handle));
    CHECK(!botw_whole_transfer_acquire(handle,size,3,owner,view,E));
    /* A numeric Guest-side close cannot invalidate this true native lease. */
    CHECK(!botw_whole_transfer_close(handle,E)&&!live());return 0;
}
unsigned owned_heap_voice_maps(void){
    pthread_mutex_lock(&g_map_lock);unsigned count=(unsigned)g_nmap;pthread_mutex_unlock(&g_map_lock);return count;
}
int owned_heap_voice_dirty_admission(void){
    void *source=(void*)(uintptr_t)owned_heap_voice_source();
    int admitted=nvn_dirty_add_range_checked(source,4096);
    if(admitted)nvn_dirty_remove_range(source);return admitted;
}
void owned_heap_voice_fail_unmaps(unsigned count){failed_unmaps=(int)count;}
int owned_heap_voice_cleanup(void){
    CHECK(!g_nmap&&!live()&&!dirty_live()&&!(munmap)(g_as,RESERVE));
    void *own=pthread_getspecific(g_keyown);CHECK(!pthread_setspecific(g_keyown,NULL));
    CHECK(!pthread_setspecific(g_keytls,NULL)&&!pthread_setspecific(g_keytlr,NULL));own_free(own);return 0;
}
