/* In original nvn_dirty.c: reuse its actual g_slot_mu and immutable g_r rows.
 * Signal/epoch readers are unchanged and never enter heap/map/G locks. */
#include "whole_heap_lease_internal.h"
#include <errno.h>
int botw_whole_dirty_untracked_begin(uint64_t source,size_t size){
    if(!source||!size||size>UINT64_MAX-source){errno=EINVAL;return 0;}
    pthread_mutex_lock(&g_slot_mu);int n=atomic_load(&g_nr);
    for(int i=0;i<n;i++){
        range_t *range=atomic_load(&g_r[i]);
        /* Retiring entries still own canonical pages until their real ACK. */
        if(range&&source<range->end&&range->base<source+size){pthread_mutex_unlock(&g_slot_mu);errno=EBUSY;return 0;}
    }
    return 1;
}
void botw_whole_dirty_untracked_end(void){pthread_mutex_unlock(&g_slot_mu);}
