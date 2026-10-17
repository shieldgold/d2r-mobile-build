/* Actual host HID force table + complete native DebugPad producer; owned CPU. */
#include "actual_hid.c"
#include "input.h"
#include <assert.h>
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>
#include <setjmp.h>
#include <sys/mman.h>
#include <sys/wait.h>
module_t g_mod[MAX_MOD];int g_nmod;
uint64_t dm_guest_frame(void){return 0;}
int dm_opmode(void){return 1;}
void hle_bind_system_event(void *e,int c){(void)e;(void)c;}
static unsigned signals;static jmp_buf finish_tick;static uint64_t clock_ns=1000000000;
void hle_signal_system_event(void *e){(void)e;if(++signals==3)longjmp(finish_tick,1);}
uint64_t now_ns(void){return clock_ns;}
static int owned_sleep(const struct timespec *t,struct timespec *r){(void)r;clock_ns+=(uint64_t)t->tv_sec*1000000000ull+(uint64_t)t->tv_nsec;return 0;}
#define nanosleep owned_sleep
/* ACTUAL_VSYNC_THREAD */
#undef nanosleep
static int denied;
int dm_guest_memory_access(uint64_t a,size_t n,int w){return a&&n==32&&w==1&&denied!=1;}
int dm_guest_write_prepare_checked(void *a,size_t n){return a&&n==32&&denied!=2;}
static void *lookup(const char *name){unsigned long n=0;const hle_force_ent *p=(const void*)getsectiondata(&_mh_execute_header,"__DATA","swforce",&n);void *found=NULL;unsigned count=0;for(unsigned long i=0;i<n/sizeof *p;i++)if(!strcmp(p[i].name,name)){found=p[i].fn;count++;}return count==1?found:NULL;}
typedef void(*init_t)(void);typedef void(*get_t)(botw_debug_pad_state*);
static init_t initialize;static get_t get_state;
static void require_state(botw_debug_pad_state *s){assert(s->sampling&&s->attributes==0&&s->buttons==0&&!s->rx&&!s->ry&&!s->lx&&!s->ly);}
static void *parallel(void *p){(void)p;for(unsigned i=0;i<200;i++){initialize();botw_debug_pad_state s;get_state(&s);require_state(&s);}return NULL;}
int main(int argc,char **argv){assert(argc==2);initialize=lookup("_ZN2nn3hid18InitializeDebugPadEv");get_state=lookup("_ZN2nn3hid16GetDebugPadStateEPNS0_13DebugPadStateE");if(!initialize||!get_state)return 91;
 _Static_assert(sizeof(botw_debug_pad_state)==32,"native actual public DebugPad type");
 if(!strcmp(argv[1],"registry"))return 0;
 if(!strcmp(argv[1],"preinit")){botw_debug_pad_state state;memset(&state,0xa5,sizeof state);get_state(&state);return 92;}
 if(!strcmp(argv[1],"null")){initialize();get_state(NULL);return 92;}
 if(!strcmp(argv[1],"roundtrip")){
  struct {uint64_t before;botw_debug_pad_state state;uint64_t after;}v;memset(&v,0xa5,sizeof v);initialize();get_state(&v.state);require_state(&v.state);assert(v.before==0xa5a5a5a5a5a5a5a5ull&&v.after==0xa5a5a5a5a5a5a5a5ull);botw_debug_pad_state first=v.state;initialize();get_state(&v.state);assert(!memcmp(&first,&v.state,sizeof first));return 0;
 }
 if(!strcmp(argv[1],"clock")){
  if(!setjmp(finish_tick))vsync_thread(NULL);assert(signals==3);initialize();botw_debug_pad_state a,b;get_state(&a);require_state(&a);signals=0;if(!setjmp(finish_tick))vsync_thread(NULL);get_state(&b);require_state(&b);assert(b.sampling==a.sampling+3);botw_debug_pad_state c;get_state(&c);assert(!memcmp(&b,&c,sizeof c));return 0;
 }
 if(!strcmp(argv[1],"deny")){
  unsigned char *memory=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_ANON|MAP_SHARED,-1,0);assert(memory!=MAP_FAILED);memset(memory,0xa5,64);initialize();for(int failure=1;failure<=2;failure++){pid_t child=fork();assert(child>=0);if(!child){denied=failure;get_state((void*)(memory+8));_exit(92);}int status=0;assert(waitpid(child,&status,0)==child&&WIFEXITED(status)&&WEXITSTATUS(status)==70);for(unsigned i=0;i<64;i++)assert(memory[i]==0xa5);}assert(!munmap(memory,4096));return 0;
 }
 if(!strcmp(argv[1],"npad")){
  dm_input_set(DM_BTN_A|DM_BTN_PLUS,123,-456,789,-987);dm_pad_set(DM_BTN_B,0,0,0,0);uint32_t id=0;NpadState a,b;h_hid_StateFullKey(&a,&id);assert((a.buttons&(DM_BTN_A|DM_BTN_B|DM_BTN_PLUS))==(DM_BTN_A|DM_BTN_B|DM_BTN_PLUS)&&a.lx==123&&a.ly==-456&&a.rx==789&&a.ry==-987&&(a.attributes&1));initialize();botw_debug_pad_state d;get_state(&d);require_state(&d);h_hid_StateFullKey(&b,&id);assert(b.sampling==a.sampling+1&&b.buttons==a.buttons&&b.lx==a.lx&&b.ly==a.ly&&b.rx==a.rx&&b.ry==a.ry&&b.attributes==a.attributes);return 0;
 }
 if(!strcmp(argv[1],"concurrent")){pthread_t t[4];for(unsigned i=0;i<4;i++)assert(!pthread_create(t+i,NULL,parallel,NULL));for(unsigned i=0;i<4;i++)assert(!pthread_join(t[i],NULL));return 0;}
 return 90;
}
