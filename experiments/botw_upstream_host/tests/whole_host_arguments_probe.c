/* SPDX-License-Identifier: MIT
 * The unmodified/patched original guest_main body runs owned native callbacks.
 * This never maps a module, initializes a real SDK or executes Guest code. */
#include "dm.h"
#include "dm_title.h"
#include <assert.h>
typedef void (*fn0)(void);
typedef void (*fn2)(uint64_t,uint64_t);
typedef void (*fnp)(void *);
static char trace[64];static size_t position;
static int observed_argc,argc_writes,argv_writes;
static char **observed_argv;
static char *g_argv[64];static int g_argc=1;
static void step(char c){assert(position+1<sizeof trace);trace[position++]=c;trace[position]=0;}
static void check_arguments(void) {
    assert(argc_writes==1&&argv_writes==1);
    assert(observed_argc==g_argc&&observed_argv==g_argv);
    assert(!strcmp(observed_argv[0],"BotW"));
    if(g_argc==3){assert(!strcmp(observed_argv[1],"--owned"));assert(!strcmp(observed_argv[2],"two words"));}
}
void guest_thread_init(void){step('t');}
static void early(void){step('e');}
uint32_t svc_thread_main_init(void){step('h');return 0x41;}
static void libc0(void){step('0');assert(!argc_writes&&!argv_writes);}
static void nnos(uint64_t handle,uint64_t area){step('n');assert(handle==0x41&&!area);assert(!argc_writes&&!argv_writes);}
static void set_argc(void *value){step('c');observed_argc=(int)(uintptr_t)value;argc_writes++;}
static void set_argv(void *value){step('v');observed_argv=value;argv_writes++;}
static void libc1(void){step('1');check_arguments();}
static void sdk(void){step('s');check_arguments();}
static void startup(void){step('u');check_arguments();}
static void libc2(void){step('2');check_arguments();}
void run_module_inits(void){step('i');check_arguments();}
static void before(void){step('b');check_arguments();}
static void nnmain(void){step('m');check_arguments();}
void dm_prof_mark(const char *segment){assert(segment&&segment[0]);}
const dm_title_desc *dm_title(void){static const dm_title_desc title={.guest_early=early,.before_nnmain=before};return &title;}
void *guest_sym(const char *name) {
    if(!strcmp(name,"_init_libc0"))return libc0;
    if(!strcmp(name,"nnosInitialize"))return nnos;
    if(!strcmp(name,"_init_libc1"))return libc1;
    if(!strcmp(name,"nninitInitializeSdkModule"))return sdk;
    if(!strcmp(name,"nninitStartup"))return startup;
    if(!strcmp(name,"_init_libc2"))return libc2;
    if(!strcmp(name,"_ZN2nn2os11SetHostArgcEi"))return set_argc;
    if(!strcmp(name,"_ZN2nn2os11SetHostArgvEPPc"))return set_argv;
    if(!strcmp(name,"nnMain"))return nnmain;
    assert(!"unexpected original guest_main symbol");return NULL;
}
#include "actual_guest_main.inc"
int main(int argc,char **argv) {
    assert(argc==2);g_argv[0]="BotW";
    if(!strcmp(argv[1],"arguments")){g_argc=3;g_argv[1]="--owned";g_argv[2]="two words";}
    else assert(!strcmp(argv[1],"default"));
    guest_main();assert(!strcmp(trace,"teh0ncv1su2ibm"));return 0;
}
