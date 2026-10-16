/* Complete original BOTW pad producer + checked write registry on owned CPU
 * buffers. No guest instructions, UIKit, GameController hardware or GPU runs. */
#include "dm.h"
#include <assert.h>
#include <setjmp.h>
#include <stdatomic.h>
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>
static sigjmp_buf failure;
static _Atomic int armed, failed;
static _Noreturn void owned_failure(const char *format,...) {
    (void)format;
    atomic_fetch_add(&failed,1);
    if(atomic_load(&armed))siglongjmp(failure,1);
    abort();
}
#undef die
#define die(...) owned_failure(__VA_ARGS__)
#include "normal_registry.inc"
#include "dm/host/botw_hid.c"
void botw_keys_start(void) {} /* No scripted input producer is installed. */
uint64_t now_ns(void) { return UINT64_C(1000000000); }
static const char *const names[] = {
    "_ZN2nn3hid12GetNpadStateEPNS0_16NpadFullKeyStateERKj",
    "_ZN2nn3hid12GetNpadStateEPNS0_17NpadHandheldStateERKj",
    "_ZN2nn3hid12GetNpadStateEPNS0_16NpadJoyDualStateERKj",
    "_ZN2nn3hid12GetNpadStateEPNS0_16NpadJoyLeftStateERKj",
    "_ZN2nn3hid12GetNpadStateEPNS0_17NpadJoyRightStateERKj"
};
static const char why[]="base-v0 single Npad state uses the complete native pad producer";
typedef void (*single_fn)(NpadState *,const uint32_t *);
static const hle_force_ent *find(const char *name) {
    unsigned long size=0;
    const hle_force_ent *entries=(const void*)getsectiondata(&_mh_execute_header,"__DATA","swforce",&size);
    assert(entries && !(size%sizeof *entries));
    for(size_t i=0;i<size/sizeof *entries;i++)if(!strcmp(entries[i].name,name))return entries+i;
    return NULL;
}
static void get(unsigned kind,NpadState *out,const uint32_t *id) {
    assert(kind<5);
#ifdef BOTW_WHOLE_HID_SINGLE_BASELINE
    /* The actual original plural is the tempting direct adapter. Its real
     * empty-output writes are the behavioral RED; this is not a fake success. */
    int (*const plural[])(NpadState *,int,const uint32_t *)={botw_hid_get_states_fullkey,
        botw_hid_get_states_handheld,botw_hid_get_states_joydual,
        botw_hid_get_states_joyleft,botw_hid_get_states_joyright};
    (void)plural[kind](out,1,id);
#else
    const hle_force_ent *entry=find(names[kind]);assert(entry);
    ((single_fn)entry->fn)(out,id);
#endif
}
static unsigned char *destination;
static unsigned destination_preps,stack_preps;
static int reject_destination;
static int prepare(void *p,size_t n) {
    if(p==destination) { assert(n==40);destination_preps++;return !reject_destination; }
    /* The original plural really prepares its owned normal C stack temp. */
    assert(n==40);stack_preps++;return 1;
}
static void setup(void) {
    botw_hid_initialize_npad();botw_input_focus(1);
    assert(dm_guest_write_prep_register_checked(prepare));
}
static void configure(int handheld) {
    uint32_t ids[]={0,1,2,3,4,5,6,7,0x10,0x20};
    botw_hid_set_supported_styles(handheld?STYLE_HANDHELD:STYLE_FULLKEY|STYLE_HANDHELD|STYLE_JOYDUAL);
    botw_hid_set_supported_ids(ids,10);
}
typedef struct { uint64_t before[2];NpadState state;uint64_t after[2]; } guarded;
static void clear_guard(guarded *g) { memset(g,0xa5,sizeof *g);destination=(unsigned char*)&g->state; }
static void guard_ok(const guarded *g) {
    for(unsigned i=0;i<2;i++)assert(g->before[i]==UINT64_C(0xa5a5a5a5a5a5a5a5)&&g->after[i]==UINT64_C(0xa5a5a5a5a5a5a5a5));
}
static void unchanged(const guarded *g) {
    for(size_t i=0;i<sizeof *g;i++)assert(((const unsigned char*)g)[i]==0xa5);
}
static void registry(void) {
    for(unsigned i=0;i<5;i++) { const hle_force_ent *entry=find(names[i]);assert(entry);
        assert(entry->fn&&entry->titles==DM_TM_BOTW&&!strcmp(entry->why,why)); }
}
static void active(void) {
    setup();guarded g;uint32_t id=0;
    botw_pad_state pad={BOTW_BTN_A|BOTW_BTN_B|BOTW_BTN_PLUS,12345,-23456,-30000,29000};
    configure(0);botw_hid_pad_set(1,&pad);clear_guard(&g);get(0,&g.state,&id);guard_ok(&g);
    assert(g.state.buttons==pad.buttons&&g.state.lx==pad.lx&&g.state.ly==pad.ly&&g.state.rx==pad.rx&&g.state.ry==pad.ry);
    assert(g.state.attributes==ATTR_CONNECTED&&!g.state.reserved&&g.state.sampling>0);
    int64_t previous=g.state.sampling;get(0,&g.state,&id);assert(g.state.sampling>previous);
    configure(1);id=0x20;clear_guard(&g);get(1,&g.state,&id);guard_ok(&g);
    assert(g.state.buttons==pad.buttons&&g.state.attributes==ATTR_CONNECTED);
    assert(destination_preps==3&&stack_preps==3);
}
static void empty(void) {
    setup();guarded g;
    /* Successful initialization owns all ten legal IDs independently of
     * styles/IDs being declared; they are empty, not a missing slot. */
    const uint32_t ids[]={0,1,2,3,4,5,6,7,0x20,0x10};
    for(unsigned kind=0;kind<5;kind++)for(unsigned i=0;i<10;i++) {
        clear_guard(&g);get(kind,&g.state,ids+i);unchanged(&g);
    }
    configure(0);
    for(unsigned kind=0;kind<5;kind++)for(unsigned i=0;i<10;i++) {
        if(!kind&&!i)continue;
        clear_guard(&g);get(kind,&g.state,ids+i);unchanged(&g);
    }
    assert(!destination_preps&&stack_preps==99);
}
static void failure_case(const char *mode) {
    guarded g;clear_guard(&g);uint32_t id=0;
    if(strcmp(mode,"uninitialized")){setup();configure(0);}
    if(!strcmp(mode,"bad-id"))id=8;
    if(!strcmp(mode,"prepare-failed"))reject_destination=1;
    atomic_store(&armed,1);
    if(!sigsetjmp(failure,1)) {
        get(0,!strcmp(mode,"null-out")?NULL:&g.state,!strcmp(mode,"null-id")?NULL:&id);
        assert(!"typed failure was silently accepted");
    }
    atomic_store(&armed,0);assert(atomic_load(&failed)==1);unchanged(&g);
    if(!strcmp(mode,"prepare-failed"))assert(destination_preps==1&&stack_preps==1);
}
static void focus(void) {
    setup();configure(0);guarded g;uint32_t id=0;
    botw_pad_state touch={.buttons=BOTW_BTN_A},controller={.buttons=BOTW_BTN_PLUS,.rx=30000};
    botw_hid_pad_set(1,&touch);botw_hid_pad_set(2,&controller);clear_guard(&g);get(0,&g.state,&id);
    assert(g.state.buttons==(BOTW_BTN_A|BOTW_BTN_PLUS)&&g.state.rx==30000);
    botw_input_focus(0);get(0,&g.state,&id);assert(!g.state.buttons&&!g.state.lx&&!g.state.ly&&!g.state.rx&&!g.state.ry);
    botw_input_focus(1);get(0,&g.state,&id);assert(!g.state.buttons&&!g.state.rx);
    botw_hid_pad_set(2,&controller);get(0,&g.state,&id);assert(g.state.buttons==BOTW_BTN_PLUS&&g.state.rx==30000);
    botw_hid_pad_drop(2);get(0,&g.state,&id);assert(!g.state.buttons&&!g.state.rx);guard_ok(&g);
}
static _Atomic int stop;
static void *writer(void *unused) {
    (void)unused;botw_pad_state a={BOTW_BTN_A,30000,-29000,28000,-27000},b={BOTW_BTN_B,-31000,30000,-29000,28000};
    while(!atomic_load(&stop)){botw_hid_pad_set(1,&a);botw_hid_pad_set(1,&b);}return NULL;
}
static void coherent(void) {
    setup();configure(0);guarded g;uint32_t id=0;pthread_t thread;
    botw_pad_state first={BOTW_BTN_A,30000,-29000,28000,-27000};botw_hid_pad_set(1,&first);
    assert(!pthread_create(&thread,NULL,writer,NULL));clear_guard(&g);
    for(unsigned i=0;i<4000;i++) {
        get(0,&g.state,&id);
        assert((g.state.buttons==BOTW_BTN_A&&g.state.lx==30000&&g.state.ly==-29000&&g.state.rx==28000&&g.state.ry==-27000)||
            (g.state.buttons==BOTW_BTN_B&&g.state.lx==-31000&&g.state.ly==30000&&g.state.rx==-29000&&g.state.ry==28000));
    }
    atomic_store(&stop,1);assert(!pthread_join(thread,NULL));guard_ok(&g);
}
int main(int argc,char **argv) {
    assert(argc==2);
    if(!strcmp(argv[1],"registry"))registry();
    else if(!strcmp(argv[1],"active"))active();
    else if(!strcmp(argv[1],"empty"))empty();
    else if(!strcmp(argv[1],"focus"))focus();
    else if(!strcmp(argv[1],"coherent"))coherent();
    else failure_case(argv[1]);
    return 0;
}
