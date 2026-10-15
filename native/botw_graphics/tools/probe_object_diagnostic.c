/* Synthetic ABI diagnostics; no game bytes. */
#include "graphics.h"
#include "nvn_protos.h"
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
static sigjmp_buf jump;
static void error(const char *s){fprintf(stderr,"ERROR:%s\n",s);siglongjmp(jump,1);}
static int access_memory(const void *p,size_t n,int writable){(void)n;(void)writable;return (uintptr_t)p!=1;}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK:%s message=%s\n",#x,botw_graphics_last_error());return 1;}}while(0)
#define FAILS(op,entry,expected,actual) do{if(!sigsetjmp(jump,1)){op;CHECK(0);}CHECK(strstr(botw_graphics_last_error(),entry));CHECK(strstr(botw_graphics_last_error(),expected));CHECK(strstr(botw_graphics_last_error(),actual));CHECK(strstr(botw_graphics_last_error(),"raw="));CHECK(strstr(botw_graphics_last_error(),"serial="));}while(0)
int main(void){
 if(!botw_graphics_init(NULL,error,NULL))return 77;botw_graphics_set_memory_access_validator(access_memory);
 NVNT_nvnDeviceFinalize finalize=(NVNT_nvnDeviceFinalize)botw_nvn_bootstrap_loader("nvnDeviceFinalize");
 NVNT_nvnSyncFinalize syncFinalize=(NVNT_nvnSyncFinalize)botw_nvn_bootstrap_loader("nvnSyncFinalize");
 NVNT_nvnDeviceBuilderSetDefaults defaults=(NVNT_nvnDeviceBuilderSetDefaults)botw_nvn_bootstrap_loader("nvnDeviceBuilderSetDefaults");
 NVNT_nvnDeviceInitialize initialize=(NVNT_nvnDeviceInitialize)botw_nvn_bootstrap_loader("nvnDeviceInitialize");
 NVNdevice empty={0};FAILS(finalize(&empty),"entry=nvnDeviceFinalize","expected=BWDevice","actual=<none>");
 NVNdeviceBuilder b;NVNdevice d;defaults(&b);CHECK(initialize(&d,&b));
 FAILS(syncFinalize((NVNsync*)&d),"entry=nvnSyncFinalize","expected=BWSync","actual=BWDevice");
 FAILS(finalize((NVNdevice*)1),"entry=nvnDeviceFinalize","expected=BWDevice","serial=<unreadable>");
 finalize(&d);botw_graphics_shutdown();CHECK(botw_graphics_init(NULL,error,NULL));botw_graphics_shutdown();
 puts("NVN_ENTRY_OBJECT_DIAGNOSTIC_AND_ERROR_ESCAPE_OK");return 0;
}
