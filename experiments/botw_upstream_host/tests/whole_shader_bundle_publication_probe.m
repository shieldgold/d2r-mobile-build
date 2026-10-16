/* SPDX-License-Identifier: MIT
 * Owned CPU publication audit only. Never create a Metal device or enter Guest. */
#include "whole_shader_bundle_publication.h"
#include "whole_shader_publication.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc,char **argv) {
    assert(argc>=2&&argc<=4);
    char path[8192]="/owned/unchanged",error[1024]={0};
    size_t capacity=argc==4?(size_t)strtoul(argv[3],NULL,10):sizeof path;
    assert(capacity<=sizeof path);
    int result=argc>=3&&!strcmp(argv[2],"legacy")?
        whole_runtime_shader_publication(argv[1],path,capacity,error,sizeof error):
        whole_runtime_shader_bundle_publication(argv[1],path,capacity,error,sizeof error);
    if(result){assert(!strcmp(path,"/owned/unchanged")&&error[0]);fprintf(stderr,"%s\n",error);return 2;}
    assert(path[0]=='/'&&!error[0]);puts(path);return 0;
}
