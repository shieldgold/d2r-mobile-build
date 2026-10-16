/* HID ABI and conventional Npad state mapping cross-checked against MIT upstream
 * jhaizhou-ops/d2r-mobile e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8
 * dm/host/hle_hid.c. See THIRD_PARTY_LICENSE.txt. No D2R actions are used. */
#define _POSIX_C_SOURCE 200809L
#include "input.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
    uint64_t sampling, buttons;
    int32_t lx, ly, rx, ry;
    uint32_t attributes, reserved;
} npad_state;
typedef struct { _Atomic uint64_t sampling; npad_state state; } npad_entry;
typedef struct {
    _Atomic uint64_t ticks, capacity, tail, count;
    npad_entry entries[17];
} npad_ring;
typedef struct {
    uint64_t delta_time, sampling;
    float acceleration[3], angular_velocity[3], angle[3], direction[9];
    uint32_t attributes, reserved;
} six_axis_state;
typedef struct { _Atomic uint64_t sampling; six_axis_state state; } six_axis_entry;
typedef struct {
    _Atomic uint64_t ticks, capacity, tail, count;
    six_axis_entry entries[17];
} six_axis_ring;
typedef struct {
    _Atomic uint32_t style, assignment;
    uint32_t colors[8];
    npad_ring rings[7];
    six_axis_ring six_axis[6];
    unsigned char reserved[BOTW_HID_NPAD_STRIDE-0x4188];
} npad_memory;
_Static_assert(sizeof(npad_state)==0x28,"old SDK NpadState");
_Static_assert(sizeof(npad_entry)==0x30,"old SDK atomic Npad sample");
_Static_assert(sizeof(npad_ring)==0x350,"old SDK Npad Lifo");
_Static_assert(offsetof(npad_memory,rings)==0x28,"old SDK FullKey ring");
_Static_assert(sizeof(six_axis_state)==0x60,"old SDK SixAxisState");
_Static_assert(offsetof(six_axis_state,sampling)==8,"old SDK SixAxis sampling");
_Static_assert(offsetof(six_axis_state,acceleration)==0x10,"old SDK SixAxis acceleration");
_Static_assert(offsetof(six_axis_state,angular_velocity)==0x1c,"old SDK SixAxis angular velocity");
_Static_assert(offsetof(six_axis_state,angle)==0x28,"old SDK SixAxis angle");
_Static_assert(offsetof(six_axis_state,direction)==0x34,"old SDK SixAxis direction");
_Static_assert(offsetof(six_axis_state,attributes)==0x58,"old SDK SixAxis attributes");
_Static_assert(sizeof(six_axis_entry)==0x68,"old SDK atomic SixAxis sample");
_Static_assert(sizeof(six_axis_ring)==0x708,"old SDK SixAxis Lifo");
_Static_assert(offsetof(npad_memory,six_axis)==0x1758,"old SDK SixAxis ring family");
_Static_assert(offsetof(npad_memory,reserved)==0x4188,"old SDK SixAxis family end");
_Static_assert(sizeof(npad_memory)==BOTW_HID_NPAD_STRIDE,"old SDK Npad stride");
typedef botw_debug_pad_state debug_pad_state;
typedef struct { _Atomic uint64_t sampling; debug_pad_state state; } debug_pad_entry;
typedef struct {
    _Atomic uint64_t ticks, capacity, tail, count;
    debug_pad_entry entries[17];
} debug_pad_ring;
_Static_assert(sizeof(debug_pad_state)==0x20,"old SDK DebugPadState");
_Static_assert(sizeof(debug_pad_entry)==0x28,"old SDK atomic DebugPad sample");
_Static_assert(sizeof(debug_pad_ring)==0x2c8,"old SDK DebugPad Lifo");
_Static_assert(sizeof(debug_pad_ring)<=BOTW_HID_DEBUG_PAD_SIZE,"DebugPad region bounds");
typedef struct { uint64_t buttons; int32_t axes[4]; int connected; } source_state;
struct botw_input {
    pthread_mutex_t mutex;
    unsigned char *memory, *owned;
    source_state sources[BOTW_INPUT_SOURCE_COUNT];
    uint32_t styles, ids[10], blocked;
    size_t id_count;
    int active;
    uint64_t sampling, debug_pad_sampling;
    int debug_pad_active;
    uint64_t six_axis_sampling[10][6];
    unsigned char six_axis_active[10][6];
};
static int id_index(uint32_t id) { return id<8?(int)id:id==0x20?8:id==0x10?9:-1; }
static npad_memory *npad(botw_input *input,unsigned index) {
    return (npad_memory *)(void *)(input->memory+BOTW_HID_NPAD_OFFSET+index*BOTW_HID_NPAD_STRIDE);
}
static int six_axis_handle(uint32_t handle,int *index,unsigned *ring) {
    if(handle>>24)return -1;
    int id=id_index((handle>>8)&255);if(id<0)return -1;
    unsigned style=handle&255,sensor=(handle>>16)&255,which;
    if(style==3&&sensor==2)which=0;
    else if(style==4&&sensor==2)which=1;
    else if(style==5&&sensor<2)which=2+sensor;
    else if(style==6&&sensor==0)which=4;
    else if(style==7&&sensor==1)which=5;
    else return -1;
    *index=id;*ring=which;return 0;
}
static int32_t clamp(int32_t value) { return value>32767?32767:value< -32768?-32768:value; }
static int magnitude(int32_t value) { return value<0?-value:value; }
static uint64_t directions(const int32_t *axes) {
    uint64_t buttons=0;
    for(unsigned s=0;s<2;s++) {
        unsigned bit=16+s*4;
        if(axes[s*2]<-16384)buttons|=UINT64_C(1)<<bit;
        if(axes[s*2+1]>16384)buttons|=UINT64_C(1)<<(bit+1);
        if(axes[s*2]>16384)buttons|=UINT64_C(1)<<(bit+2);
        if(axes[s*2+1]<-16384)buttons|=UINT64_C(1)<<(bit+3);
    }
    return buttons;
}
static uint32_t select_style(botw_input *input,int index,int physical) {
    if(input->blocked&(1u<<index))return 0;
    if(index==8)return input->styles&BOTW_NPAD_STYLE_HANDHELD;
    if(index==9)return 0; /* DebugPad is not an Npad player controller. */
    if(input->styles&BOTW_NPAD_STYLE_FULLKEY)return BOTW_NPAD_STYLE_FULLKEY;
    if(input->styles&BOTW_NPAD_STYLE_JOYDUAL)return BOTW_NPAD_STYLE_JOYDUAL;
    (void)physical; return 0;
}
static int selected(botw_input *input,uint32_t *style) {
    if(!input->active)return -1;
    int physical=input->sources[BOTW_INPUT_GAMECONTROLLER].connected;
    if(!physical&&!input->sources[BOTW_INPUT_TOUCH].connected)return -1;
    /* Touch controls are a handheld device when the guest supports handheld. */
    if(!physical)for(size_t i=0;i<input->id_count;i++)if(input->ids[i]==0x20) {
        *style=select_style(input,8,0); if(*style)return 8;
    }
    for(size_t i=0;i<input->id_count;i++) {
        int index=id_index(input->ids[i]); *style=select_style(input,index,physical);
        if(*style)return index;
    }
    return -1;
}
static void initialize_memory(botw_input *input) {
    memset(input->memory,0,BOTW_HID_SHARED_MEMORY_SIZE);
    input->debug_pad_active=0;input->debug_pad_sampling=0;
    memset(input->six_axis_sampling,0,sizeof input->six_axis_sampling);
    memset(input->six_axis_active,0,sizeof input->six_axis_active);
    debug_pad_ring *debug=(debug_pad_ring *)(void *)(input->memory+BOTW_HID_DEBUG_PAD_OFFSET);
    atomic_init(&debug->capacity,17);atomic_init(&debug->tail,16);
    for(unsigned index=0;index<10;index++)for(unsigned ring=0;ring<7;ring++) {
        npad_ring *r=&npad(input,index)->rings[ring];
        atomic_init(&r->capacity,17); atomic_init(&r->tail,16);
    }
    for(unsigned index=0;index<10;index++)for(unsigned ring=0;ring<6;ring++) {
        six_axis_ring *r=&npad(input,index)->six_axis[ring];
        atomic_init(&r->ticks,0);atomic_init(&r->capacity,17);
        atomic_init(&r->tail,16);atomic_init(&r->count,0);
    }
}
botw_input *botw_input_create(void) {
    botw_input *input=calloc(1,sizeof *input); if(!input)return NULL;
    input->owned=calloc(1,BOTW_HID_SHARED_MEMORY_SIZE);
    if(!input->owned || pthread_mutex_init(&input->mutex,NULL)) { free(input->owned);free(input);return NULL; }
    input->memory=input->owned; input->styles=3; input->ids[0]=0; input->ids[1]=0x20; input->id_count=2;
    input->active=1; initialize_memory(input); return input;
}
void botw_input_destroy(botw_input *input) {
    if(!input)return;
    pthread_mutex_destroy(&input->mutex);free(input->owned);free(input);
}
void *botw_input_shared_memory(botw_input *input,size_t *size) {
    if(!input)return NULL;
    pthread_mutex_lock(&input->mutex); void *memory=input->memory;
    if(size)*size=BOTW_HID_SHARED_MEMORY_SIZE;
    pthread_mutex_unlock(&input->mutex);return memory;
}
int botw_input_bind_shared_memory(botw_input *input,void *backing,size_t size) {
    if(!input||!backing||size!=BOTW_HID_SHARED_MEMORY_SIZE||((uintptr_t)backing&7))return -1;
    pthread_mutex_lock(&input->mutex);
    input->memory=backing;input->sampling=0;initialize_memory(input);
    pthread_mutex_unlock(&input->mutex);return 0;
}
void botw_input_unbind_shared_memory(botw_input *input) {
    if(!input)return;
    pthread_mutex_lock(&input->mutex);
    input->memory=input->owned;input->sampling=0;initialize_memory(input);
    pthread_mutex_unlock(&input->mutex);
}
int botw_input_set_source(botw_input *input,enum botw_input_source source,uint64_t buttons,
        int32_t lx,int32_t ly,int32_t rx,int32_t ry,int connected) {
    if(!input||source<0||source>=BOTW_INPUT_SOURCE_COUNT||(buttons&~UINT64_C(0xffffff)))return -1;
    pthread_mutex_lock(&input->mutex); source_state *state=&input->sources[source];
    memset(state,0,sizeof *state); state->connected=!!connected;
    if(connected&&input->active) {
        state->buttons=buttons;state->axes[0]=clamp(lx);state->axes[1]=clamp(ly);
        state->axes[2]=clamp(rx);state->axes[3]=clamp(ry);
    }
    pthread_mutex_unlock(&input->mutex);return 0;
}
void botw_input_set_active(botw_input *input,int active) {
    if(!input)return;
    pthread_mutex_lock(&input->mutex); input->active=!!active;
    if(!active)for(unsigned s=0;s<BOTW_INPUT_SOURCE_COUNT;s++) {
        input->sources[s].buttons=0;memset(input->sources[s].axes,0,sizeof input->sources[s].axes);
    }
    pthread_mutex_unlock(&input->mutex);
}
int botw_input_configure(botw_input *input,uint32_t styles,const uint32_t *ids,size_t count) {
    if(!input||(styles&~31u)||count>10||(!ids&&count))return -1;
    uint32_t seen=0;
    for(size_t i=0;i<count;i++) {
        int index=id_index(ids[i]);if(index<0||(seen&(1u<<index)))return -1;seen|=1u<<index;
    }
    pthread_mutex_lock(&input->mutex); input->styles=styles;input->id_count=count;input->blocked=0;
    if(count)memcpy(input->ids,ids,count*sizeof *ids);
    pthread_mutex_unlock(&input->mutex);return 0;
}
uint32_t botw_input_style(botw_input *input,uint32_t id) {
    if(!input||id_index(id)<0)return 0;
    pthread_mutex_lock(&input->mutex);uint32_t style=0;int index=selected(input,&style);
    if(index!=id_index(id))style=0;
    pthread_mutex_unlock(&input->mutex);return style;
}
int botw_input_disconnect(botw_input *input,uint32_t id) {
    int index=id_index(id);if(!input||index<0)return -1;
    pthread_mutex_lock(&input->mutex);input->blocked|=1u<<index;
    pthread_mutex_unlock(&input->mutex);return 0;
}
static void publish(npad_ring *ring,const npad_state *state,uint64_t ticks) {
    uint64_t tail=(atomic_load_explicit(&ring->tail,memory_order_relaxed)+1)%17;
    npad_entry *entry=&ring->entries[tail];
    atomic_store_explicit(&entry->sampling,UINT64_MAX,memory_order_release);
    memcpy(&entry->state,state,sizeof *state);
    atomic_store_explicit(&entry->sampling,state->sampling,memory_order_release);
    atomic_store_explicit(&ring->ticks,ticks,memory_order_relaxed);
    atomic_store_explicit(&ring->tail,tail,memory_order_release);
    uint64_t count=atomic_load_explicit(&ring->count,memory_order_relaxed);
    atomic_store_explicit(&ring->count,count<16?count+1:16,memory_order_release);
}
static void publish_debug_pad(botw_input *input,uint64_t ticks) {
    debug_pad_ring *ring=(debug_pad_ring *)(void *)(input->memory+BOTW_HID_DEBUG_PAD_OFFSET);
    debug_pad_state state={0};state.sampling=++input->debug_pad_sampling;
    uint64_t tail=(atomic_load_explicit(&ring->tail,memory_order_relaxed)+1)%17;
    debug_pad_entry *entry=&ring->entries[tail];
    atomic_store_explicit(&entry->sampling,UINT64_MAX,memory_order_release);
    memcpy(&entry->state,&state,sizeof state);
    atomic_store_explicit(&entry->sampling,state.sampling,memory_order_release);
    atomic_store_explicit(&ring->ticks,ticks,memory_order_relaxed);
    atomic_store_explicit(&ring->tail,tail,memory_order_release);
    uint64_t count=atomic_load_explicit(&ring->count,memory_order_relaxed);
    atomic_store_explicit(&ring->count,count<16?count+1:16,memory_order_release);
}
int botw_input_activate_debug_pad(botw_input *input,uint64_t ticks) {
    if(!input)return -1;
    pthread_mutex_lock(&input->mutex);
    if(!input->debug_pad_active) {
        input->debug_pad_active=1;publish_debug_pad(input,ticks);
    }
    pthread_mutex_unlock(&input->mutex);return 0;
}
int botw_input_sample_debug_pad(botw_input *input,uint64_t ticks) {
    if(!input||!ticks)return -1;
    pthread_mutex_lock(&input->mutex);
    if(!input->debug_pad_active) { pthread_mutex_unlock(&input->mutex);return -1; }
    publish_debug_pad(input,ticks);
    pthread_mutex_unlock(&input->mutex);return 0;
}
int botw_input_read_debug_pad(botw_input *input,botw_debug_pad_state *out) {
    if(!input||!out)return -1;
    pthread_mutex_lock(&input->mutex);
    if(!input->debug_pad_active) { pthread_mutex_unlock(&input->mutex);return -1; }
    debug_pad_ring *ring=(debug_pad_ring *)(void *)(input->memory+BOTW_HID_DEBUG_PAD_OFFSET);
    uint64_t capacity=atomic_load_explicit(&ring->capacity,memory_order_acquire);
    uint64_t tail=atomic_load_explicit(&ring->tail,memory_order_acquire);
    uint64_t count=atomic_load_explicit(&ring->count,memory_order_acquire);
    if(capacity!=17||tail>=17||count>16) { pthread_mutex_unlock(&input->mutex);return -1; }
    if(!count) { pthread_mutex_unlock(&input->mutex);return 0; }
    memcpy(out,&ring->entries[tail].state,sizeof *out);
    pthread_mutex_unlock(&input->mutex);return 1;
}
static void publish_six_axis(botw_input *input,unsigned index,unsigned which,uint64_t ticks) {
    six_axis_ring *ring=&npad(input,index)->six_axis[which];
    /* No CoreMotion/controller-motion backend exists yet. A button controller
     * connection is not evidence of a connected sensor or a measured pose.
     * delta_time's device unit is deliberately not inferred from header ticks. */
    six_axis_state state={0};state.sampling=++input->six_axis_sampling[index][which];
    uint64_t tail=(atomic_load_explicit(&ring->tail,memory_order_relaxed)+1)%17;
    six_axis_entry *entry=&ring->entries[tail];
    atomic_store_explicit(&entry->sampling,UINT64_MAX,memory_order_release);
    memcpy(&entry->state,&state,sizeof state);
    atomic_store_explicit(&entry->sampling,state.sampling,memory_order_release);
    atomic_store_explicit(&ring->ticks,ticks,memory_order_relaxed);
    atomic_store_explicit(&ring->tail,tail,memory_order_release);
    uint64_t count=atomic_load_explicit(&ring->count,memory_order_relaxed);
    atomic_store_explicit(&ring->count,count<16?count+1:16,memory_order_release);
}
int botw_input_start_six_axis(botw_input *input,uint32_t handle,uint64_t ticks) {
    int index;unsigned ring;
    if(!input||!ticks||six_axis_handle(handle,&index,&ring))return -1;
    pthread_mutex_lock(&input->mutex);
    if(!input->six_axis_active[index][ring]) {
        input->six_axis_active[index][ring]=1;publish_six_axis(input,(unsigned)index,ring,ticks);
    }
    pthread_mutex_unlock(&input->mutex);return 0;
}
int botw_input_stop_six_axis(botw_input *input,uint32_t handle) {
    int index;unsigned ring;
    if(!input||six_axis_handle(handle,&index,&ring))return -1;
    pthread_mutex_lock(&input->mutex);input->six_axis_active[index][ring]=0;
    pthread_mutex_unlock(&input->mutex);return 0;
}
void botw_input_sample(botw_input *input,uint64_t ticks) {
    if(!input)return;
    pthread_mutex_lock(&input->mutex);uint32_t style=0;int index=selected(input,&style);
    if(input->debug_pad_active)publish_debug_pad(input,ticks);
    for(unsigned n=0;n<10;n++)for(unsigned r=0;r<6;r++)
        if(input->six_axis_active[n][r])publish_six_axis(input,n,r,ticks);
    npad_state current={0};current.sampling=++input->sampling;
    int32_t axes[4]={0};
    if(index>=0)for(unsigned s=0;s<BOTW_INPUT_SOURCE_COUNT;s++) {
        source_state *source=&input->sources[s];if(!source->connected)continue;
        current.buttons|=source->buttons;
        for(unsigned a=0;a<4;a++)if(magnitude(source->axes[a])>magnitude(axes[a]))axes[a]=source->axes[a];
    }
    current.buttons|=directions(axes);current.lx=axes[0];current.ly=axes[1];
    current.rx=axes[2];current.ry=axes[3];current.attributes=index>=0?BOTW_NPAD_ATTRIBUTE_CONNECTED:0;
    for(unsigned n=0;n<10;n++) {
        npad_memory *memory=npad(input,n);
        atomic_store_explicit(&memory->style,(int)n==index?style:0,memory_order_release);
        atomic_store_explicit(&memory->assignment,0,memory_order_release);
        for(unsigned r=0;r<7;r++) {
            npad_state state={0};state.sampling=current.sampling;
            /* Style-specific rings plus the system/default rings. */
            if((int)n==index&&(r>=5||(style==1&&r==0)||(style==2&&r==1)||(style==4&&r==2)))state=current;
            publish(&memory->rings[r],&state,ticks);
        }
    }
    pthread_mutex_unlock(&input->mutex);
}
uint64_t botw_input_monotonic_ticks(void) {
    struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))return 0;
    return (uint64_t)t.tv_sec*UINT64_C(19200000)+(uint64_t)t.tv_nsec*192/10000;
}
