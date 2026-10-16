/* SPDX-License-Identifier: MIT -- owned CPU operands only. */
#include "whole_runtime_resource_checks.h"
#include "whole_runtime_resources.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc,char **argv) {
    char error[256]={0};int result;
    if(argc!=5)return 90;
    if(!strcmp(argv[1],"package")) {
        whole_runtime_resource_paths paths={0};
        result=whole_runtime_resources_verify(argv[2],&paths,error,sizeof error);
        printf("%d %s\n",result,error);return result?1:0;
    }
    int fd=open(argv[2],O_RDONLY|O_CLOEXEC);if(fd<0)return 91;
    if(!strcmp(argv[1],"file")) {
        whole_runtime_file_identity f={"owned.tsv",(size_t)strtoull(argv[4],NULL,10),argv[3]};
        result=whole_runtime_verify_files(fd,&f,1,error,sizeof error);
    } else {
        base_v0_force_module m={0};m.name="owned";m.padded_bytes=49152;
        m.padded_sha256=argv[3];m.layout.text_end=16000;m.layout.ro_off=16384;m.layout.patch_off=32768;
        result=whole_runtime_verify_guest(fd,&m,1,error,sizeof error);
    }
    close(fd);printf("%d %s\n",result,error);return result?1:0;
}
