/* The original gfx gate owns this state; no second graphics registry exists.
 * The mutex protects admission only. No callback or queue drain runs under it. */
#pragma once
#include <pthread.h>
typedef struct {
    pthread_mutex_t mutex;
    pthread_cond_t changed;
    unsigned readers, waiting;
    int writer, suspended;
} botw_whole_pool_gate;
#define BOTW_WHOLE_POOL_GATE_INITIALIZER {PTHREAD_MUTEX_INITIALIZER,PTHREAD_COND_INITIALIZER,0,0,0,0}
typedef struct {botw_whole_pool_gate *gate;int kind;} botw_whole_pool_guard;
int botw_whole_pool_gate_read(botw_whole_pool_gate *gate);
int botw_whole_pool_gate_write(botw_whole_pool_gate *gate);
int botw_whole_pool_gate_suspend(botw_whole_pool_gate *gate,int on);
void botw_whole_pool_gate_leave(botw_whole_pool_gate *gate,int kind);
void botw_whole_pool_guard_leave(botw_whole_pool_guard *guard);
