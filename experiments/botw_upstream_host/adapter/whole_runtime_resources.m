/* SPDX-License-Identifier: MIT */
#include "whole_runtime_resources.h"
#include "whole_runtime_resource_checks.h"
#include "whole_runtime_resource_manifest.h"
#include "../../../native/botw_resources/romfs.h"
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <mach-o/loader.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int reject(char *error,size_t capacity,int code,const char *phase) {
    if(error&&capacity)snprintf(error,capacity,"runtime resources: %s (%s)",phase,strerror(code));
    return code;
}
static int span(uint64_t offset,uint64_t size,uint64_t bound) {
    return offset<=bound&&size<=bound-offset;
}
static int exact_read(int fd,void *out,size_t length,uint64_t offset) {
    unsigned char *p=out;
    while(length) {
        ssize_t got=pread(fd,p,length,(off_t)offset);
        if(got<0&&errno==EINTR)continue;
        if(got<=0)return -1;
        p+=got;length-=(size_t)got;offset+=(size_t)got;
    }
    return 0;
}
static int unchanged(const struct stat *before,const struct stat *after) {
    return before->st_dev==after->st_dev&&before->st_ino==after->st_ino&&
        before->st_size==after->st_size&&before->st_mtimespec.tv_sec==after->st_mtimespec.tv_sec&&
        before->st_mtimespec.tv_nsec==after->st_mtimespec.tv_nsec&&
        before->st_ctimespec.tv_sec==after->st_ctimespec.tv_sec&&
        before->st_ctimespec.tv_nsec==after->st_ctimespec.tv_nsec;
}
static int hash_span(int fd,uint64_t offset,uint64_t bytes,CC_SHA256_CTX *hash) {
    unsigned char buffer[32768];
    while(bytes) {
        size_t n=bytes<sizeof buffer?(size_t)bytes:sizeof buffer;
        if(exact_read(fd,buffer,n,offset))return -1;
        CC_SHA256_Update(hash,buffer,(CC_LONG)n);offset+=n;bytes-=n;
    }
    return 0;
}
static int hash_matches(CC_SHA256_CTX *hash,const char *expected) {
    unsigned char bytes[CC_SHA256_DIGEST_LENGTH];char hex[65];
    if(!expected||strlen(expected)!=64)return 0;
    CC_SHA256_Final(bytes,hash);
    for(unsigned i=0;i<sizeof bytes;i++)snprintf(hex+i*2,3,"%02x",bytes[i]);
    return !strcmp(hex,expected);
}
int whole_runtime_verify_files(int directory,const whole_runtime_file_identity *files,
    size_t count,char *error,size_t capacity) {
    if((capacity&&!error)||directory<0||!files||!count)return reject(error,capacity,EINVAL,"file authority");
    for(size_t i=0;i<count;i++) {
        const whole_runtime_file_identity *f=files+i;struct stat before,after;
        if(!f->name||!f->name[0]||strchr(f->name,'/')||!strcmp(f->name,".")||!strcmp(f->name,".."))
            return reject(error,capacity,EINVAL,"file component");
        int fd=openat(directory,f->name,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
        if(fd<0)return reject(error,capacity,errno,f->name);
        int code=0;
        if(fstat(fd,&before)||!S_ISREG(before.st_mode)||before.st_size<0||
            (uint64_t)before.st_size!=f->bytes)code=EINVAL;
        CC_SHA256_CTX hash;CC_SHA256_Init(&hash);
        if(!code&&(hash_span(fd,0,f->bytes,&hash)||!hash_matches(&hash,f->sha256)))code=EILSEQ;
        if(!code&&(fstat(fd,&after)||!unchanged(&before,&after)))code=EBUSY;
        close(fd);if(code)return reject(error,capacity,code,f->name);
    }
    if(error&&capacity)error[0]=0;return 0;
}

typedef struct {uint64_t offset,size;unsigned found;} part;
static int fixed_name(const char value[16],const char *expected) {
    size_t n=strlen(expected);return n<16&&!memcmp(value,expected,n)&&value[n]==0;
}
int whole_runtime_verify_guest(int fd,const base_v0_force_module *modules,size_t count,
    char *error,size_t capacity) {
    if((capacity&&!error)||fd<0||!modules||!count||count>6)return reject(error,capacity,EINVAL,"Guest authority");
    struct stat before,after;struct mach_header_64 header;
    if(fstat(fd,&before)||!S_ISREG(before.st_mode)||before.st_size<(off_t)sizeof header||
        exact_read(fd,&header,sizeof header,0))return reject(error,capacity,EINVAL,"Guest file/header");
    if(header.magic!=MH_MAGIC_64||header.cputype!=CPU_TYPE_ARM64||header.filetype!=MH_DYLIB||
        !header.ncmds||header.ncmds>4096||header.sizeofcmds>1024*1024||
        !span(sizeof header,header.sizeofcmds,(uint64_t)before.st_size))
        return reject(error,capacity,EINVAL,"Guest must be a bounded native ARM64 dylib");
    unsigned char *commands=malloc(header.sizeofcmds);if(!commands)return reject(error,capacity,ENOMEM,"Guest commands");
    int code=0;const char *phase="Guest commands";part parts[6][3]={0};unsigned target=0;
    if(exact_read(fd,commands,header.sizeofcmds,sizeof header)){code=EIO;goto done;}
    size_t offset=0;
    for(uint32_t i=0;i<header.ncmds;i++) {
        if(!span(offset,sizeof(struct load_command),header.sizeofcmds)){code=EINVAL;goto done;}
        const struct load_command *lc=(const void*)(commands+offset);
        if(lc->cmdsize<sizeof *lc||(lc->cmdsize&7)||!span(offset,lc->cmdsize,header.sizeofcmds)){code=EINVAL;goto done;}
        if(lc->cmd==LC_BUILD_VERSION) {
            if(lc->cmdsize<sizeof(struct build_version_command)){code=EINVAL;goto done;}
            const struct build_version_command *v=(const void*)lc;
            if(v->platform!=PLATFORM_IOS||target++){code=EINVAL;phase="Guest target must be iPhoneOS";goto done;}
        } else if(lc->cmd==LC_VERSION_MIN_IPHONEOS) {
            if(lc->cmdsize<sizeof(struct version_min_command)||target++){code=EINVAL;goto done;}
        } else if(lc->cmd==LC_VERSION_MIN_MACOSX||lc->cmd==LC_VERSION_MIN_TVOS||lc->cmd==LC_VERSION_MIN_WATCHOS) {
            code=EINVAL;phase="Guest target must be iPhoneOS";goto done;
        } else if(lc->cmd==LC_SEGMENT_64) {
            if(lc->cmdsize<sizeof(struct segment_command_64)){code=EINVAL;goto done;}
            const struct segment_command_64 *sg=(const void*)lc;
            if(sg->nsects>(lc->cmdsize-sizeof *sg)/sizeof(struct section_64)||
                !span(sg->fileoff,sg->filesize,(uint64_t)before.st_size)){code=EINVAL;goto done;}
            const struct section_64 *sections=(const void*)(sg+1);
            for(uint32_t j=0;j<sg->nsects;j++)for(size_t k=0;k<count;k++) {
                char names[3][16],segments[3][16];
                if(!modules[k].name||strlen(modules[k].name)>7){code=EINVAL;goto done;}
                snprintf(names[0],16,"__dm%sx0",modules[k].name);strcpy(names[1],"__img");
                snprintf(names[2],16,"__dm%sx1",modules[k].name);
                strcpy(segments[0],"__TEXT");snprintf(segments[1],16,"__DM%sW",modules[k].name);strcpy(segments[2],"__TEXT");
                for(unsigned p=0;p<3;p++)if(fixed_name(sg->segname,segments[p])&&fixed_name(sections[j].sectname,names[p])) {
                    const struct section_64 *sc=sections+j;
                    int prot=p==1?(VM_PROT_READ|VM_PROT_WRITE):(VM_PROT_READ|VM_PROT_EXECUTE);
                    if(parts[k][p].found++||!fixed_name(sc->segname,segments[p])||sg->initprot!=prot||
                        (sc->flags&SECTION_TYPE)==S_ZEROFILL||!sc->size||
                        (sc->offset&16383)||(sc->addr&16383)||(sc->size&16383)||
                        !span(sc->offset,sc->size,(uint64_t)before.st_size)||sc->offset<sg->fileoff||
                        !span(sc->offset-sg->fileoff,sc->size,sg->filesize)||sc->addr<sg->vmaddr||
                        !span(sc->addr-sg->vmaddr,sc->size,sg->vmsize)||
                        sc->addr-sg->vmaddr!=sc->offset-sg->fileoff) {
                        code=EINVAL;phase="Guest signed section layout/protection";goto done;
                    }
                    parts[k][p].offset=sc->offset;parts[k][p].size=sc->size;
                }
            }
        }
        offset+=lc->cmdsize;
    }
    if(offset!=header.sizeofcmds||target!=1){code=EINVAL;phase="Guest iPhoneOS target/command closure";goto done;}
    for(size_t k=0;k<count;k++) {
        const base_v0_force_module *m=modules+k;
        /* Exactly the existing packager/force contract: signed RX rounds
         * TEXT_END to the host 16-KiB page, independent of NSO's RO_OFF. */
        uint64_t text=(m->layout.text_end+16383u)&~UINT64_C(16383);
        if(m->layout.text_end>UINT64_MAX-16383u||m->layout.patch_off<text||m->padded_bytes<m->layout.patch_off||
            parts[k][0].size!=text||parts[k][1].size!=m->layout.patch_off-text||
            parts[k][2].size!=m->padded_bytes-m->layout.patch_off) {
            code=EINVAL;phase="Guest section sizes differ from fixed v4 module";goto done;
        }
        CC_SHA256_CTX hash;CC_SHA256_Init(&hash);
        for(unsigned p=0;p<3;p++) {
            if(parts[k][p].found!=1||hash_span(fd,parts[k][p].offset,parts[k][p].size,&hash)) {
                code=EIO;phase="Guest section payload";goto done;
            }
            for(size_t a=0;a<=k;a++)for(unsigned b=0;b<3;b++) {
                if(a==k&&b>=p)continue;
                const part *other=&parts[a][b];
                if(parts[k][p].offset<other->offset+other->size&&other->offset<parts[k][p].offset+parts[k][p].size) {
                    code=EINVAL;phase="Guest overlapping signed sections";goto done;
                }
            }
        }
        if(!hash_matches(&hash,m->padded_sha256)){code=EILSEQ;phase="Guest fixed v4 payload SHA256";goto done;}
    }
    if(fstat(fd,&after)||!unchanged(&before,&after)){code=EBUSY;phase="Guest changed during verification";}
done:
    free(commands);if(code)return reject(error,capacity,code,phase);
    if(error&&capacity)error[0]=0;return 0;
}

static int directory_at(int parent,const char *name) {
    return openat(parent,name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
}
int whole_runtime_resources_verify(const char *bundle,whole_runtime_resource_paths *out,char *error,size_t capacity) {
    if(!bundle||bundle[0]!='/'||!out||(capacity&&!error))return reject(error,capacity,EINVAL,"absolute bundle/output");
    whole_runtime_resource_paths paths;
    int n=snprintf(paths.metadata,sizeof paths.metadata,"%s/botw",bundle);
    int m=snprintf(paths.guest,sizeof paths.guest,"%s/Frameworks/Guest.framework/Guest",bundle);
    if(n<0||(size_t)n>=sizeof paths.metadata||m<0||(size_t)m>=sizeof paths.guest)
        return reject(error,capacity,ENAMETOOLONG,"bundle paths");
    int root=open(bundle,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if(root<0)return reject(error,capacity,errno,"open bundle");
    int metadata=-1,frameworks=-1,framework=-1,guest=-1,code=0;
    if((metadata=directory_at(root,"botw"))<0) {code=reject(error,capacity,errno,"open pinned metadata");goto done;}
    code=whole_runtime_verify_files(metadata,whole_runtime_pinned_metadata,
        sizeof whole_runtime_pinned_metadata/sizeof *whole_runtime_pinned_metadata,error,capacity);
    if(code)goto done;
    if((frameworks=directory_at(root,"Frameworks"))<0||(framework=directory_at(frameworks,"Guest.framework"))<0||
        (guest=openat(framework,"Guest",O_RDONLY|O_NOFOLLOW|O_CLOEXEC))<0) {
        code=reject(error,capacity,errno,"open signed Guest.framework");goto done;
    }
    size_t count=0;const base_v0_force_module *modules=base_v0_force_manifest(&count);
    if(count!=6){code=reject(error,capacity,EINVAL,"fixed force manifest must contain six modules");goto done;}
    code=whole_runtime_verify_guest(guest,modules,count,error,capacity);
    if(!code)*out=paths;
done:
    if(guest>=0)close(guest);if(framework>=0)close(framework);if(frameworks>=0)close(frameworks);
    if(metadata>=0)close(metadata);close(root);return code;
}
int whole_runtime_romfs_verify(const char *documents,char *path,size_t path_capacity,char *error,size_t capacity) {
    if(!documents||documents[0]!='/'||!path||!path_capacity||(capacity&&!error))return reject(error,capacity,EINVAL,"RomFS arguments");
    char candidate[PATH_MAX];int length=snprintf(candidate,sizeof candidate,"%s/botw-romfs.bin",documents);
    if(length<0||(size_t)length>=sizeof candidate||(size_t)length>=path_capacity)
        return reject(error,capacity,ENAMETOOLONG,"RomFS path");
    struct stat st;
    if(lstat(candidate,&st)||!S_ISREG(st.st_mode)||st.st_size!=14420017152LL)
        return reject(error,capacity,EINVAL,"expected fixed base RomFS.bin length/regular file");
    botw_romfs_error detail={0};botw_romfs *romfs=botw_romfs_open(candidate,&detail);
    if(!romfs)return reject(error,capacity,EIO,detail.message);
    botw_romfs_entry entry={0};char version[5];size_t actual=0;
    int code=0;
    if(botw_romfs_image_size(romfs)!=14420017152ULL||botw_romfs_stat(romfs,"/System/Version.txt",&entry,&detail)||
        entry.is_directory||entry.size!=5||botw_romfs_pread(romfs,"/System/Version.txt",0,version,sizeof version,&actual,&detail)||
        actual!=sizeof version||memcmp(version,"1.0.0",5))code=EINVAL;
    botw_romfs_close(romfs);
    struct stat after;
    if(lstat(candidate,&after)||!unchanged(&st,&after))code=EBUSY;
    if(code)return reject(error,capacity,code,"RomFS /System/Version.txt must be exactly 1.0.0");
    memcpy(path,candidate,(size_t)length+1);if(error&&capacity)error[0]=0;return 0;
}
