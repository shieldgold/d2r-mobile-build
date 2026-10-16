/* Normal scoped admission. Recursive readers cover command-memory callbacks;
 * upgrading an admitted API to mutation is rejected instead of self-waiting. */
#include "whole_pool_backing_gate.h"
static _Thread_local botw_whole_pool_gate *current_gate;
static _Thread_local botw_whole_pool_gate *writing_gate;
static _Thread_local unsigned depth;
int botw_whole_pool_gate_read(botw_whole_pool_gate *gate){
    if(!gate||writing_gate)return 0;
    if(depth){if(current_gate!=gate)return 0;depth++;return 1;}
    pthread_mutex_lock(&gate->mutex);
    while(gate->writer||gate->waiting||gate->suspended)pthread_cond_wait(&gate->changed,&gate->mutex);
    gate->readers++;current_gate=gate;depth=1;
    pthread_mutex_unlock(&gate->mutex);return 1;
}
int botw_whole_pool_gate_write(botw_whole_pool_gate *gate){
    if(!gate||depth||writing_gate)return 0;
    pthread_mutex_lock(&gate->mutex);gate->waiting++;
    while(gate->writer||gate->readers||gate->suspended)pthread_cond_wait(&gate->changed,&gate->mutex);
    gate->waiting--;gate->writer=1;writing_gate=gate;
    pthread_mutex_unlock(&gate->mutex);return 1;
}
int botw_whole_pool_gate_suspend(botw_whole_pool_gate *gate,int on){
    if(!gate||depth||writing_gate)return 0;
    pthread_mutex_lock(&gate->mutex);gate->suspended=on!=0;
    if(on){
        while(gate->writer||gate->readers)pthread_cond_wait(&gate->changed,&gate->mutex);
        gate->writer=1;writing_gate=gate;
    }else pthread_cond_broadcast(&gate->changed);
    pthread_mutex_unlock(&gate->mutex);return 1;
}
void botw_whole_pool_gate_leave(botw_whole_pool_gate *gate,int kind){
    if(kind==1){if(!depth||current_gate!=gate)return;if(--depth)return;current_gate=NULL;}
    pthread_mutex_lock(&gate->mutex);
    if(kind==1){if(gate->readers)gate->readers--;}
    else {gate->writer=0;writing_gate=NULL;}
    pthread_cond_broadcast(&gate->changed);pthread_mutex_unlock(&gate->mutex);
}
void botw_whole_pool_guard_leave(botw_whole_pool_guard *guard){
    if(guard&&guard->gate)botw_whole_pool_gate_leave(guard->gate,guard->kind);
}
