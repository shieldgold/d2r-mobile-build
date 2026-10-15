/* Synthetic-only bridge: observes real permission faults in a child process. */
#pragma once
#include <signal.h>
#include <sys/ucontext.h>
#include <unistd.h>
static int (*textureTestFault)(void*,int,void*);
static int (*textureTestPrepare)(void*,size_t,char*,size_t);
static volatile sig_atomic_t textureTestFaults;
static void textureTestSignal(int number,siginfo_t *info,void *context){
    (void)number;
#if defined(__aarch64__)
    ucontext_t *u=context;unsigned esr=u->uc_mcontext->__es.__esr,ec=esr>>26;
    int write=(ec==0x24||ec==0x25)&&(esr&0x40);
#elif defined(__x86_64__)
    ucontext_t *u=context;int write=!!(u->uc_mcontext->__es.__err&2);
#else
#error Unsupported synthetic signal fixture
#endif
    ++textureTestFaults;if(!textureTestFault(info->si_addr,write,context))_exit(88);
}
static int textureTestRegistration(int (*fault)(void*,int,void*),int (*prepare)(void*,size_t,char*,size_t)){
    textureTestFault=fault;textureTestPrepare=prepare;struct sigaction action={0};action.sa_sigaction=textureTestSignal;action.sa_flags=SA_SIGINFO;
    return sigaction(SIGBUS,&action,NULL)||sigaction(SIGSEGV,&action,NULL)?-1:0;
}
