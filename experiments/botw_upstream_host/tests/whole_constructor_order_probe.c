/* Actual original loader function, owned C callbacks only. No Guest execution. */
#include "dm.h"
#include <limits.h>
module_t g_mod[MAX_MOD];int g_nmod;
static int trace[32],used,bad,dependency;
static unsigned arrays;
static void record(int module){
    if(used>=32){bad=1;return;}trace[used++]=module;
    if(module==5)dependency=1;
    if(module==0&&!dependency)bad=1;
    /* Represents this owned module's own _init invoking its own array once. */
    arrays++;
}
static void owned0(void){record(0);}static void owned1(void){record(1);}
static void owned2(void){record(2);}static void owned3(void){record(3);}
static void owned4(void){record(4);}static void owned5(void){record(5);}
#include "actual_run_module_inits.inc"
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CONSTRUCTOR_OWNED:%d %s\n",__LINE__,#x);return 91;}}while(0)
int main(int argc,char **argv){
    CHECK(argc==2);
    void (*callbacks[])(void)={owned0,owned1,owned2,owned3,owned4,owned5};
    const char *names[]={"main","subsdk0","subsdk1","subsdk2","subsdk3","sdk"};
    for(int i=0;i<6;i++){
        uintptr_t function=(uintptr_t)callbacks[i],page=function&~(uintptr_t)0xfff;
        CHECK(function>page);
        g_mod[i]=(module_t){.base=(uint8_t*)page,.size=0x1000,.init=function-page,.init_arraysz=8};
        snprintf(g_mod[i].name,sizeof g_mod[i].name,"%s",names[i]);
    }
    g_nmod=6;
    if(!strcmp(argv[1],"empty"))g_nmod=0;
    else if(!strcmp(argv[1],"single")){g_nmod=1;dependency=1;}
    else if(!strcmp(argv[1],"holes")){g_mod[0].init=0;g_mod[1].init=0;g_mod[3].init=0;g_mod[5].init=0;}
    else if(!strcmp(argv[1],"none")){for(int i=0;i<6;i++)g_mod[i].init=0;}
    else CHECK(!strcmp(argv[1],"six")||!strcmp(argv[1],"dependency")||!strcmp(argv[1],"metadata"));
    module_t before[MAX_MOD];memcpy(before,g_mod,sizeof before);
    run_module_inits();
    if(!strcmp(argv[1],"empty")||!strcmp(argv[1],"none"))CHECK(!used&&!arrays&&!bad);
    else if(!strcmp(argv[1],"single"))CHECK(used==1&&trace[0]==0&&arrays==1&&!bad);
    else if(!strcmp(argv[1],"holes"))CHECK(used==2&&trace[0]==4&&trace[1]==2&&arrays==2&&!bad);
    else{
        CHECK(used==6&&arrays==6);
        for(int i=0;i<6;i++)CHECK(trace[i]==5-i);
        CHECK(!bad&&dependency==1);
    }
    CHECK(!memcmp(before,g_mod,sizeof before));return 0;
}
