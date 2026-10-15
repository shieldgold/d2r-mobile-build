/* Self-owned CPU mappings exercise the real graphics copy entrypoints. No
 * Metal device, guest image, post-recovery diagnostic or GPU work is used. */
#include "../graphics.m"
#include <sys/mman.h>
#define GC_CHECK(x) do{if(!(x)){fprintf(stderr,"GRAPHICS_COPY_CHECK line%d: %s [%s]\n",__LINE__,#x,lastError);return 91;}}while(0)
typedef struct {unsigned leaves;vm_object_id_t object;} GCCoverage;
static size_t gcPage,gcBytes,gcAllocation;
static unsigned char *gcSource,*gcDestination;
static unsigned gcReads,gcWrites;static int gcDenyRead,gcDenyWrite;
static int gcAccess(const void *address,size_t bytes,int write){(void)address;(void)bytes;if(write){gcWrites++;return !gcDenyWrite;}gcReads++;return !gcDenyRead;}
static GCCoverage gcCoverage(void *memory,size_t bytes){
    GCCoverage result={0};uintptr_t cursor=(uintptr_t)memory,end=cursor+bytes;
    while(cursor<end){natural_t depth=0;vm_address_t region=cursor;vm_size_t span=0;vm_region_submap_info_data_64_t info={0};
        for(;;){mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;
            if(vm_region_recurse_64(mach_task_self(),&region,&span,&depth,(vm_region_recurse_info_t)&info,&count)!=KERN_SUCCESS||count<VM_REGION_SUBMAP_INFO_V2_COUNT_64||region>cursor||!span||cursor-region>=span)abort();
            if(info.is_submap){depth++;region=cursor;continue;}break;}
        if(!result.leaves)result.object=info.object_id_full;result.leaves++;
        size_t step=MIN((size_t)(end-cursor),(size_t)(span-(cursor-region)));cursor+=step;
    }return result;
}
static int gcCopy(const char *operation,void *destination,const void *source,size_t bytes){
    int result=0;@try{
        if(!strcmp(operation,"read"))textureReadBytes(source,destination,bytes,"synthetic graphics protected read");
        else if(!strcmp(operation,"write"))textureWriteBytes(destination,source,bytes,"synthetic graphics protected write");
        else result=textureWriteRun(destination,0,source,bytes);
    }@catch(BWGraphicsFailure *failure){(void)failure;result=-1;}
    pendingError=NO;pendingFatal=NO;return result;
}
static int gcBulk(const char *operation){
    GCCoverage source=gcCoverage(gcSource,gcAllocation),destination=gcCoverage(gcDestination,gcAllocation);GC_CHECK(source.leaves==1&&destination.leaves==1);
    size_t offset=gcPage/2;GC_CHECK(!gcCopy(operation,gcDestination+offset,gcSource+offset,gcBytes));
    GC_CHECK(!memcmp(gcSource+offset,gcDestination+offset,gcBytes));GC_CHECK(gcDestination[offset-1]==0xa5&&gcDestination[offset+gcBytes]==0xa5);
    GCCoverage afterSource=gcCoverage(gcSource,gcAllocation),afterDestination=gcCoverage(gcDestination,gcAllocation);
    fprintf(stderr,"GRAPHICS_COPY_VM operation=%s source_leaves=%u->%u destination_leaves=%u->%u\n",operation,source.leaves,afterSource.leaves,destination.leaves,afterDestination.leaves);
    GC_CHECK(afterSource.leaves==1&&afterDestination.leaves==1&&source.object==afterSource.object&&destination.object==afterDestination.object);return 0;
}
static int gcFaults(void){
    const char *operations[]={"read","write","run"};
    for(unsigned i=0;i<3;i++){
        memset(gcDestination,0xa5,2*gcPage);GC_CHECK(!mprotect(gcSource+gcPage,gcPage,PROT_NONE));
        GC_CHECK(gcCopy(operations[i],gcDestination,gcSource,2*gcPage)==-1);GC_CHECK(!mprotect(gcSource+gcPage,gcPage,PROT_READ|PROT_WRITE));
        for(size_t n=0;n<2*gcPage;n++)GC_CHECK(gcDestination[n]==0xa5);
        memset(gcDestination,0xa5,2*gcPage);GC_CHECK(!mprotect(gcDestination+gcPage,gcPage,PROT_NONE));
        GC_CHECK(gcCopy(operations[i],gcDestination,gcSource,2*gcPage)==-1);
        for(size_t n=0;n<gcPage;n++)GC_CHECK(gcDestination[n]==0xa5);GC_CHECK(!mprotect(gcDestination+gcPage,gcPage,PROT_READ|PROT_WRITE));
        GC_CHECK(!mprotect(gcDestination+gcPage,gcPage,PROT_READ));GC_CHECK(gcCopy(operations[i],gcDestination,gcSource,2*gcPage)==-1);
        for(size_t n=0;n<gcPage;n++)GC_CHECK(gcDestination[n]==0xa5);GC_CHECK(!mprotect(gcDestination+gcPage,gcPage,PROT_READ|PROT_WRITE));
    }return 0;
}
static int gcOverlap(void){
    const char *operations[]={"read","write","run"};unsigned char *expected=malloc(gcAllocation);GC_CHECK(expected);
    for(unsigned i=0;i<3;i++)for(unsigned direction=0;direction<2;direction++){
        for(size_t n=0;n<gcAllocation;n++)gcDestination[n]=(unsigned char)(n*17+(n>>8));
        memcpy(expected,gcDestination,gcAllocation);size_t from=direction?128:0,to=direction?0:128;memmove(expected+to,expected+from,gcBytes);
        GC_CHECK(!gcCopy(operations[i],gcDestination+to,gcDestination+from,gcBytes));GC_CHECK(!memcmp(expected,gcDestination,gcAllocation));
    }
    free(expected);return 0;
}
static int gcLogicalPrepare(void){
    memoryAccessValidator=gcAccess;gcDenyRead=1;GC_CHECK(gcCopy("read",gcDestination,gcSource,64)==-1);GC_CHECK(gcReads==1&&!gcWrites&&strstr(lastError,"synthetic graphics protected read"));
    for(unsigned i=0;i<64;i++)GC_CHECK(gcDestination[i]==0xa5);
    gcDenyRead=0;texturePrepareWrite(gcDestination,64);GC_CHECK(gcWrites==1);GC_CHECK(!gcCopy("write",gcDestination,gcSource,64));GC_CHECK(gcWrites==1&&gcReads==1&&!memcmp(gcDestination,gcSource,64));
    gcDenyWrite=1;BOOL rejected=NO;@try{texturePrepareWrite(gcDestination,64);}@catch(BWGraphicsFailure *failure){(void)failure;rejected=YES;}pendingError=NO;pendingFatal=NO;GC_CHECK(rejected&&gcWrites==2);memoryAccessValidator=NULL;return 0;
}
static int gcBounds(void){
    GC_CHECK(!gcCopy("write",NULL,NULL,0));GC_CHECK(!gcCopy("run",NULL,NULL,0));GC_CHECK(!gcCopy("read",gcDestination,gcSource,0));GC_CHECK(gcCopy("read",NULL,NULL,0)==-1);
    GC_CHECK(gcCopy("read",gcDestination,(void*)(UINTPTR_MAX-7),16)==-1);GC_CHECK(gcCopy("write",gcDestination,(void*)(UINTPTR_MAX-7),16)==-1);
    GC_CHECK(textureWriteRun((void*)(UINTPTR_MAX-7),16,gcSource,16)==-1);GC_CHECK(!gpu);return 0;
}
int main(int argc,char **argv){@autoreleasepool{
    alarm(25);GC_CHECK(argc==2);gcPage=(size_t)getpagesize();gcBytes=4u*1024*1024;gcAllocation=gcBytes+2*gcPage;
    gcSource=mmap(NULL,gcAllocation,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);gcDestination=mmap(NULL,gcAllocation,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);GC_CHECK(gcSource!=MAP_FAILED&&gcDestination!=MAP_FAILED);
    for(size_t i=0;i<gcAllocation;i++)gcSource[i]=(unsigned char)(i*13+(i>>8)+7);memset(gcDestination,0xa5,gcAllocation);
    int result;if(!strncmp(argv[1],"bulk-",5))result=gcBulk(argv[1]+5);else if(!strcmp(argv[1],"faults"))result=gcFaults();else if(!strcmp(argv[1],"overlap"))result=gcOverlap();else if(!strcmp(argv[1],"logical-prepare"))result=gcLogicalPrepare();else if(!strcmp(argv[1],"bounds"))result=gcBounds();else return 2;
    if(!result){GC_CHECK(!gpu&&!munmap(gcSource,gcAllocation)&&!munmap(gcDestination,gcAllocation));puts("GRAPHICS_PROTECTED_COPY_CPU_OK");}return result;
}}
