/* Native timer raw values are calibrated CPU-domain nanoseconds. They are not
 * either fixed upstream's game-specific NVIDIA frequency. */
#ifndef BOTW_COUNTER_CLOCK_H
#define BOTW_COUNTER_CLOCK_H
#include <stdint.h>
static inline int botw_counter_calibrate(uint64_t sample,uint64_t cpu0,uint64_t gpu0,
                                         uint64_t cpu1,uint64_t gpu1,uint64_t *result){
    if(!result||!cpu0||!gpu0||cpu1==UINT64_MAX||gpu1==UINT64_MAX||
       cpu1<=cpu0||gpu1<=gpu0||sample==UINT64_MAX||
       sample<gpu0||sample>gpu1)return 0;
    unsigned __int128 scaled=(unsigned __int128)(sample-gpu0)*(cpu1-cpu0)/(gpu1-gpu0);
    if(scaled>UINT64_MAX-cpu0)return 0;
    *result=cpu0+(uint64_t)scaled;return 1;
}
#endif
