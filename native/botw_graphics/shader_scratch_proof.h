/* Bounded proof for the initial graphics-only Metal-owned scratch profile.
 * SPH fields match the pinned Ryujinx ShaderHeader/Translator implementation.
 * Nonzero local storage needs a separate complete range/initialization proof. */
#ifndef BOTW_SHADER_SCRATCH_PROOF_H
#define BOTW_SHADER_SCRATCH_PROOF_H
#include "nvn.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static inline uint32_t bwScratchWord(const void *p,size_t n){uint32_t v;memcpy(&v,(const char*)p+n*4,4);return v;}
static inline int bwScratchProof(const void *code,size_t codeBytes,NVNshaderStage stage,const void *spv,size_t bytes,char *error,size_t cap){
#define BW_SCRATCH_REJECT(message) do{if(error&&cap)snprintf(error,cap,"%s",message);return 0;}while(0)
    if(!code||codeBytes<0x80||(stage!=NVN_SHADER_STAGE_VERTEX&&stage!=NVN_SHADER_STAGE_FRAGMENT))BW_SCRATCH_REJECT("scratch proof requires bounded graphics NVN SPH");
    unsigned type=(bwScratchWord(code,12)>>10)&15;
    if(type!=(stage==NVN_SHADER_STAGE_VERTEX?1u:5u))BW_SCRATCH_REJECT("scratch proof NVN SPH stage mismatch");
    uint32_t low=bwScratchWord(code,13)&0xffffff,high=bwScratchWord(code,14)&0xffffff,crs=bwScratchWord(code,15)&0xffffff;
    if(!spv||bytes<20||bytes>16u*1024*1024||bytes%4||bwScratchWord(spv,0)!=0x07230203||(bwScratchWord(spv,1)!=0x00010300&&bwScratchWord(spv,1)!=0x00010400)||!bwScratchWord(spv,3)||bwScratchWord(spv,3)>1048576||bwScratchWord(spv,4))BW_SCRATCH_REJECT("scratch proof requires bounded SPIR-V version 1.3 or 1.4");
    size_t words=bytes/4;uint32_t bound=bwScratchWord(spv,3),entry=0;unsigned entries=0,memory=0,shader=0,function=0,privateVariables=0,inFunction=0,previous=0,functions=0,ends=0;
    for(size_t p=5;p<words;){uint32_t first=bwScratchWord(spv,p),count=first>>16,op=first&65535;
        if(!count||count>words-p||(op>400&&op!=5380))BW_SCRATCH_REJECT("scratch proof malformed or unsupported SPIR-V instruction");
        /* SPV_EXT_demote_to_helper_invocation has no operands or storage. The
         * pinned original MSL compiler lowers it to discard_fragment(). */
        if(op==5380&&(count!=1||!inFunction||stage!=NVN_SHADER_STAGE_FRAGMENT))BW_SCRATCH_REJECT("scratch proof demote requires a fragment function");
        if(op==14){if(count!=3||bwScratchWord(spv,p+1)||bwScratchWord(spv,p+2)!=1)BW_SCRATCH_REJECT("scratch proof requires Logical GLSL450 memory model");memory++;}
        if(op==17&&count==2&&bwScratchWord(spv,p+1)==1)shader++;
        if(op==15){if(count<4||bwScratchWord(spv,p+1)!=(stage==NVN_SHADER_STAGE_VERTEX?0u:4u)||!memchr((const char*)spv+(p+3)*4,0,(count-3)*4))BW_SCRATCH_REJECT("scratch proof SPIR-V entry/stage mismatch");entry=bwScratchWord(spv,p+2);if(!entry||entry>=bound)BW_SCRATCH_REJECT("scratch proof entry id is out of bounds");entries++;}
        if(op==54){if(count!=5||inFunction)BW_SCRATCH_REJECT("scratch proof malformed function");if(bwScratchWord(spv,p+2)==entry)function++;inFunction=1;functions++;}
        if(op==56){if(count!=1||!inFunction||(previous<249||previous>255))BW_SCRATCH_REJECT("scratch proof incomplete function");inFunction=0;ends++;}
        if(op==59){if(count<4)BW_SCRATCH_REJECT("scratch proof malformed variable");uint32_t storage=bwScratchWord(spv,p+3);if(storage==6)privateVariables++;if(storage==4||storage>12)BW_SCRATCH_REJECT("scratch proof unsupported graphics storage class");}
        previous=op;p+=count;
    }
    if(entries!=1||memory!=1||!shader||function!=1||inFunction||functions!=ends)BW_SCRATCH_REJECT("scratch proof incomplete graphics module");
    if(!low&&!high&&!(crs/32)&&privateVariables)BW_SCRATCH_REJECT("scratch proof rejects zero-declared Private storage; implicit 128-byte local-memory fallback is not admitted");
    if(low||high||crs||privateVariables)BW_SCRATCH_REJECT("scratch proof nonzero local-memory range/initialization is unverified");
    if(error&&cap){error[0]=0;}
    return 1;
#undef BW_SCRATCH_REJECT
}
#endif
