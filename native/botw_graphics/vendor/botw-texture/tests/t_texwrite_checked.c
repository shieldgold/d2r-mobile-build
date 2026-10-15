/* Exact upstream run traversal through a fault-safe host writer. */
#include "../nvn_texwrite.h"
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
typedef struct { unsigned char *bytes,*touched;size_t size,calls,fail; } Writer;
static int write_run(void *opaque,uint64_t offset,const void *source,size_t bytes){
    Writer *w=opaque;assert(offset<=w->size&&bytes<=w->size-offset);w->calls++;
    if(w->calls==w->fail)return -1;
    memcpy(w->bytes+offset,source,bytes);memset(w->touched+offset,1,bytes);return 0;
}
int main(void){
    nvn_texdesc d={4,0x25,0,0,32,16,2,3,0,0};nvn_texlayout layout;nvn_tex_layout(&d,&layout);
    unsigned char *expected=malloc(layout.size),*actual=malloc(layout.size),*touched=calloc(1,layout.size);assert(expected&&actual&&touched);
    unsigned char source[108];for(unsigned i=0;i<sizeof source;i++)source[i]=(unsigned char)i;
    NVNcopyRegion region={3,2,1,9,3,1};nvn_texwrite_plan plan;const char *why=NULL;
    assert(!nvn_texwrite_plan_make(&d,0,0,-1,0,&region,0,0,&plan,&why));
    memset(expected,0xa5,layout.size);memset(actual,0xa5,layout.size);nvn_texwrite_apply(&d,&plan,source,expected);
    Writer writer={actual,touched,layout.size,0,0};assert(!nvn_texwrite_apply_checked(&d,&plan,source,write_run,&writer));assert(writer.calls>3&&!memcmp(actual,expected,layout.size));
    for(size_t fail=1;fail<=3;fail++){
        memset(actual,0xa5,layout.size);memset(touched,0,layout.size);writer.calls=0;writer.fail=fail;
        assert(nvn_texwrite_apply_checked(&d,&plan,source,write_run,&writer)==-1&&writer.calls==fail);
        for(size_t i=0;i<layout.size;i++)if(!touched[i])assert(actual[i]==0xa5);
    }
    assert(nvn_texwrite_apply_checked(&d,&plan,source,NULL,&writer)==-1);
    free(expected);free(actual);free(touched);puts("CHECKED_TEXWRITE_EXACT_RUNS_AND_FAILURE_STOP_OK");return 0;
}
