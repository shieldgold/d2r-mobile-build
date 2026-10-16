/* Included once, in original hle_thread.c after its checked kernel bridge.
 * G -> heap resize -> map is the only nested order. No provider/audio/event
 * callback runs with G held. Actual objects, not recyclable handles, own refs. */
struct botw_whole_transfer_lease {botw_whole_transfer_object *memory;};
static int whole_transfer_unref_locked(botw_whole_transfer_object *memory,char *error,size_t capacity){
    if(memory->refs>1){memory->refs--;return 0;}
    return botw_whole_transfer_dispose(memory,error,capacity);
}
int botw_whole_transfer_acquire(uint32_t handle,size_t requested,unsigned permission,
        botw_whole_transfer_lease **out,botw_whole_transfer_view *view,char *error,size_t capacity){
    if(!out||*out||!view||!requested||(permission!=1&&permission!=3))
        return whole_kernel_problem(error,capacity,"transfer acquire arguments are invalid");
    botw_whole_transfer_lease *lease=calloc(1,sizeof *lease);
    if(!lease)return whole_kernel_problem(error,capacity,"transfer lease allocation failed");
    pthread_mutex_lock(&G);
    botw_whole_transfer_object *memory=h_peek(handle,O_TRANSFER);
    if(!memory||memory->blocked||requested>memory->size||memory->refs==UINT_MAX){
        pthread_mutex_unlock(&G);free(lease);
        return whole_kernel_problem(error,capacity,"transfer acquire requires a live backing and bounded size");
    }
    memory->refs++;lease->memory=memory;
    *view=(botw_whole_transfer_view){memory->backing,memory->source,memory->size,requested,memory->permission,memory->backing_id,memory->backing_offset};
    *out=lease;pthread_mutex_unlock(&G);return 0;
}
int botw_whole_transfer_release(botw_whole_transfer_lease **owner,char *error,size_t capacity){
    if(!owner||!*owner)return whole_kernel_problem(error,capacity,"transfer lease owner is absent");
    botw_whole_transfer_lease *lease=*owner;
    pthread_mutex_lock(&G);
    int result=whole_transfer_unref_locked(lease->memory,error,capacity);
    if(!result){free(lease);*owner=NULL;}
    pthread_mutex_unlock(&G);return result;
}
int botw_whole_transfer_duplicate(uint32_t handle,uint32_t *out,char *error,size_t capacity){
    if(!out)return whole_kernel_problem(error,capacity,"transfer duplicate output is absent");
    pthread_mutex_lock(&G);botw_whole_transfer_object *memory=h_peek(handle,O_TRANSFER);
    if(!memory||memory->blocked||memory->refs==UINT_MAX||!whole_kernel_free_slots()){
        pthread_mutex_unlock(&G);return whole_kernel_problem(error,capacity,"transfer duplicate capacity or handle is invalid");
    }
    uint32_t duplicate=h_new(O_TRANSFER,memory);memory->refs++;*out=duplicate;
    pthread_mutex_unlock(&G);return 0;
}
static int whole_transfer_close_locked(uint32_t handle,char *error,size_t capacity){
    botw_whole_transfer_object *memory=h_peek(handle,O_TRANSFER);
    if(!memory)return whole_kernel_problem(error,capacity,"transfer close requires a live typed handle");
    if(whole_transfer_unref_locked(memory,error,capacity))return -1;
    unsigned slot=handle-0x1000;g_h[slot].type=O_FREE;g_h[slot].obj=NULL;return 0;
}
int botw_whole_transfer_close(uint32_t handle,char *error,size_t capacity){
    pthread_mutex_lock(&G);int result=whole_transfer_close_locked(handle,error,capacity);
    pthread_mutex_unlock(&G);return result;
}
int botw_whole_transfer_reap_pending(char *error,size_t capacity){
    pthread_mutex_lock(&G);int result=botw_whole_transfer_pending_cleanup(error,capacity);
    pthread_mutex_unlock(&G);return result;
}
int botw_whole_transfer_dispatch(int number,uint64_t *regs,char *error,size_t capacity){
    if(number!=0x15&&number!=0x16)return 0;
    if(!regs)return whole_kernel_problem(error,capacity,"transfer SVC frame is absent");
    pthread_mutex_lock(&G);
    if(number==0x16){
        if(!h_peek((uint32_t)regs[0],O_TRANSFER)){pthread_mutex_unlock(&G);return 0;}
        int result=whole_transfer_close_locked((uint32_t)regs[0],error,capacity);
        if(!result)regs[0]=0;
        pthread_mutex_unlock(&G);return result?-1:1;
    }
    uint64_t source=regs[1],length=regs[2];uint32_t permission=(uint32_t)regs[3],result=0;
    if(source&0xfffu)result=0xcc01;
    else if(!length||length>SIZE_MAX||(length&0xfffu))result=0xca01;
    else if(source>UINT64_MAX-length)result=0xd401;
    else if(permission!=0&&permission!=1&&permission!=3)result=0xd801;
    /* A live original wait borrows its word until wake/timeout unlinks it. */
    if(!result)for(thr_t *wait=g_wait;wait;wait=wait->next){
        if(wait->wkind<1||wait->wkind>3)continue;
        uint64_t words[2]={wait->waddr,wait->wkind==2?wait->wkey:0};
        for(unsigned i=0;i<2;i++)if(words[i]&&words[i]<=UINT64_MAX-4&&
            source<words[i]+4&&words[i]<source+length)result=0xd401;
    }
    botw_whole_transfer_object *memory=NULL;int status=0;
    if(!result)status=botw_whole_transfer_prepare(source,(size_t)length,permission,&memory,&result,error,capacity);
    if(!status&&memory){
        if(!whole_kernel_free_slots()){
            result=R_OUT_OF_RES;
            status=botw_whole_transfer_dispose(memory,error,capacity);
        }else{
            uint32_t handle=h_new(O_TRANSFER,memory);memory->published=1;regs[1]=handle;
        }
    }
    if(!status)regs[0]=result;
    pthread_mutex_unlock(&G);return status?-1:1;
}
