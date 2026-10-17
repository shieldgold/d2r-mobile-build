/* SPDX-License-Identifier: MIT
 * Positive normal preload admission. No loader, implementation registry, Guest
 * execution, VM enumeration, addresses in diagnostics or mutable env authority.
 */
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#include "base_v0_loader_guard.h"
#include "game_profile.h"
#include <errno.h>
#include <fcntl.h>
#include <mach-o/loader.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
_Static_assert(DM_GAME_NMOD==6 && DM_GAME_NVN_API_MAJOR==52 && DM_GAME_NVN_API_MINOR==32,
    "normal loader guard requires the dedicated six-module NVN52 profile");
typedef struct {uint64_t text_end,ro_off,data_off,patch_off,key_page_off,key_page_size;unsigned key_idx[8];} nx_guard_layout;
typedef struct {const char *name,*source_sha256,*build_id,*image_sha256,*padded_sha256;size_t image_bytes,padded_bytes;nx_guard_layout layout;} nx_guard_module;
typedef struct {const char *name;size_t bytes;const char *sha256;} nx_guard_file;
/* Complete existing signed-section/file checks, with fixed emitter tightening.
 * Source whole_runtime_resources.m SHA256: e47ae2399de5492ed4da970505b7968db1c85824d20b781ae9b5d7e675ea30d0 */
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
static int nx_guard_verify_files(int directory,const nx_guard_file *files,
    size_t count,char *error,size_t capacity) {
    if((capacity&&!error)||directory<0||!files||!count)return reject(error,capacity,EINVAL,"file authority");
    for(size_t i=0;i<count;i++) {
        const nx_guard_file *f=files+i;struct stat before,after;
        if(!f->name||!f->name[0]||strchr(f->name,'/')||!strcmp(f->name,".")||!strcmp(f->name,".."))
            return reject(error,capacity,EINVAL,"file component");
        int fd=openat(directory,f->name,O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC);
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
static int nx_guard_verify_guest(int fd,const nx_guard_module *modules,size_t count,
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
    int code=0;const char *phase="Guest commands";part parts[6][3]={0};unsigned target=0, section_count=0;
    if(exact_read(fd,commands,header.sizeofcmds,sizeof header)){code=EIO;goto done;}
    size_t offset=0;
    for(uint32_t i=0;i<header.ncmds;i++) {
        if(!span(offset,sizeof(struct load_command),header.sizeofcmds)){code=EINVAL;goto done;}
        const struct load_command *lc=(const void*)(commands+offset);
        if(lc->cmdsize<sizeof *lc||(lc->cmdsize&7)||!span(offset,lc->cmdsize,header.sizeofcmds)){code=EINVAL;goto done;}
        if(lc->cmd!=LC_SEGMENT_64&&lc->cmd!=LC_ID_DYLIB&&lc->cmd!=LC_DYLD_INFO_ONLY&&
            lc->cmd!=LC_LOAD_DYLIB&&lc->cmd!=LC_SYMTAB&&lc->cmd!=LC_DYSYMTAB&&lc->cmd!=LC_UUID&&
            lc->cmd!=LC_BUILD_VERSION&&lc->cmd!=LC_CODE_SIGNATURE&&lc->cmd!=LC_VERSION_MIN_IPHONEOS) {
            code=EINVAL;phase="Guest contains a non-data-only load command";goto done;
        }
        if(lc->cmd==LC_LOAD_DYLIB||lc->cmd==LC_ID_DYLIB){
            if(lc->cmdsize<sizeof(struct dylib_command)){code=EINVAL;goto done;}
            const struct dylib_command *d=(const void*)lc;uint32_t n=d->dylib.name.offset;
            const char *expected=lc->cmd==LC_LOAD_DYLIB?"/usr/lib/libSystem.B.dylib":"@rpath/Guest.framework/Guest";
            if(n<sizeof *d||n>=lc->cmdsize||strnlen((const char*)lc+n,lc->cmdsize-n)>=lc->cmdsize-n||strcmp((const char*)lc+n,expected)){code=EINVAL;goto done;}
        }
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
            if(sg->maxprot!=sg->initprot||sg->nsects>18-section_count){code=EINVAL;goto done;}
            section_count+=sg->nsects;
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
                        (sc->flags&SECTION_TYPE)!=S_REGULAR||!sc->size||
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
    if(offset!=header.sizeofcmds||target!=1||section_count!=18){code=EINVAL;phase="Guest iPhoneOS target/command closure";goto done;}
    for(size_t k=0;k<count;k++) {
        const nx_guard_module *m=modules+k;
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

static const char *const module_names[] = DM_GAME_MODULES;
static const char *const source_hashes[]={"fa2541333fdb5a868a286ea138774d82d13a1b0b97988d7af6c60ed82d2b1c81","58b6791aff6e5cb509c5347c1dc41d01a32c0a298c7f5da0c88ea56fc8a6eb17","a1b399f06db89bac005e8c7d8ce3475f931a9cdff6b7fc3509b9427e0a47be41","e56eec1a58d1fa53779469e6eea36a795c0deac90bbf25b54b97c44ed195bd65","2f344d84badf826937317e033b47f32bb2e1e8b7856553ce7356616268b5e238","0abff9d26d975cb17c24ddfecca488a4900e20ffd7ab9bb642e6df667dde9f31"};
static const char *const source_ids[]={"2094f152836683a3632c6153fd84ab8000000000000000000000000000000000","9f7e69a5c5e8aa0e61199ac33dd0d2d13717c190000000000000000000000000","fc3d9aae8b6769e2d69961c005e239f536805f19000000000000000000000000","2ba5fcc00ba28cdda26c792e934949cbb5cbc513000000000000000000000000","aa72f973a902c24747af4522ee67ea9a7994c3fb000000000000000000000000","99d55fb2a2ce9e14bd5d56428ec0397be46e48eb000000000000000000000000"};

static const char *const field_names[]={"TEXT_END","RO_OFF","DATA_OFF","PATCH_OFF","KEY_PAGE_OFF","KEY_PAGE_SIZE"};
static const char *const key_names[]={"K18","KTLS","KTLR","KGATE","KRES0","KRES1","KASPECT","KASPECTUI"};
static const char *const entry_names[]={"_init_libc0","nnosInitialize","_init_libc1","nninitInitializeSdkModule","nninitStartup","_init_libc2","_ZN2nn2os11SetHostArgcEi","_ZN2nn2os11SetHostArgvEPPc","nnMain"};
static const unsigned gate_counts[]={93,0,0,0,0,137};
enum {EMPTY,PRELOADED,LAYOUTS,IMAGES,REGISTRY,COMPLETE,FAILED};
static unsigned phase,layouts,images,gates;
static NSDictionary *contract,*rules;
static NSMutableDictionary<NSString*,NSMutableDictionary*> *references;
static NSString *metadata_path;
static nx_guard_module modules[6];
static int fail(void){phase=FAILED;return -1;}
static BOOL dictionary(id v){return [v isKindOfClass:NSDictionary.class];}
static BOOL array(id v){return [v isKindOfClass:NSArray.class];}
static BOOL string(id v){return [v isKindOfClass:NSString.class];}
static BOOL boolean(id v,BOOL b){return v&&CFGetTypeID((__bridge CFTypeRef)v)==CFBooleanGetTypeID()&&[v boolValue]==b;}
static BOOL integer(id v,uint64_t *out){
    if(![v isKindOfClass:NSNumber.class]||CFGetTypeID((__bridge CFTypeRef)v)==CFBooleanGetTypeID())return NO;
    const char *s=[v objCType];if(!s||!strchr("cCsSiIlLqQ",s[0])||[v compare:@0]==NSOrderedAscending)return NO;
    *out=[v unsignedLongLongValue];return YES;
}
static BOOL hex64(id v){
    if(!string(v)||[v length]!=64)return NO;const char *s=[v UTF8String];if(!s||strlen(s)!=64)return NO;
    for(unsigned i=0;i<64;i++)if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return NO;return YES;
}
static BOOL number(id v,uint64_t expected){uint64_t n;return integer(v,&n)&&n==expected;}
static int module_index(const char *name){if(name)for(unsigned i=0;i<6;i++)if(!strcmp(name,module_names[i]))return (int)i;return -1;}
static NSData *bounded_at(int parent,const char *name,size_t limit){
    int fd=openat(parent,name,O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC);if(fd<0)return nil;
    struct stat before,after;NSMutableData *data=nil;
    if(fstat(fd,&before)||!S_ISREG(before.st_mode)||before.st_size<0||(uint64_t)before.st_size>limit)goto done;
    data=[NSMutableData dataWithLength:(NSUInteger)before.st_size];if(!data)goto done;
    if(exact_read(fd,data.mutableBytes,data.length,0)||fstat(fd,&after)||!unchanged(&before,&after))data=nil;
done:close(fd);return data;
}
static int directory_at(int parent,const char *name){return openat(parent,name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);}
int nxnative_base_v0_loader_preload(const char *bundle){@autoreleasepool {
    if(phase!=EMPTY||!bundle||bundle[0]!='/')return fail();
    int root=-1,dm=-1,frameworks=-1,framework=-1,guest=-1,result=-1;
    NSDictionary *doc=nil;NSMutableDictionary *by_name=[NSMutableDictionary dictionary];
    root=open(bundle,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);if(root<0)goto done;
    dm=directory_at(root,"dm");if(dm<0)goto done;
    {NSData *raw=bounded_at(dm,"base_v0_loader_contract.json",2*1024*1024);
     if(!raw)goto done;id parsed=[NSJSONSerialization JSONObjectWithData:raw options:0 error:NULL];if(!dictionary(parsed))goto done;doc=parsed;}
    if(![doc[@"schema"] isEqual:@"nxnative-base-v0-loader-guard-contract-v1"]||
       ![doc[@"profile_id"] isEqual:@"botw-switch-base-v0"]||![doc[@"title_id"] isEqual:@DM_GAME_ID]||
       ![doc[@"game_version"] isEqual:@"1.0.0"]||!dictionary(doc[@"nvn_api"])||
       !number(doc[@"nvn_api"][@"major"],52)||!number(doc[@"nvn_api"][@"minor"],32)||
       !number(doc[@"original_heap_bytes"],0xc2600000)||!number(doc[@"original_root_bytes"],0xc0600000)||
       !boolean(doc[@"rtld_loaded"],NO)||!number(doc[@"normal_gate_count"],230)||
       !array(doc[@"module_order"])||[doc[@"module_order"] count]!=6||
       !array(doc[@"modules"])||[doc[@"modules"] count]!=6||
       !dictionary(doc[@"loader_tables"])||[doc[@"loader_tables"] count]!=30)goto done;
    for(unsigned i=0;i<6;i++){
        NSDictionary *m=doc[@"modules"][i];if(!dictionary(m))goto done;
        NSDictionary *layout=m[@"layout"],*source=m[@"source"];
        if(![doc[@"module_order"][i] isEqual:@(module_names[i])]||!dictionary(m)||
           ![m[@"name"] isEqual:@(module_names[i])]||!dictionary(source)||
           ![source[@"sha256"] isEqual:@(source_hashes[i])]||![source[@"build_id"] isEqual:@(source_ids[i])]||
           !boolean(source[@"load"],YES)||!dictionary(layout)||!hex64(m[@"image_sha256"])||!hex64(m[@"padded_sha256"])||
           !number(m[@"normal_gate_count"],gate_counts[i]))goto done;
        uint64_t fields[6],size,padded;
        for(unsigned j=0;j<6;j++)if(!integer(layout[@(field_names[j])],fields+j))goto done;
        if(!integer(m[@"image_bytes"],&size)||!integer(m[@"padded_bytes"],&padded)||
            !size||size>512*1024*1024||padded!=(size+16383u)/16384u*16384u||
            fields[0]>fields[1]||fields[1]>fields[2]||fields[2]>fields[3]||fields[3]>=size||
            fields[4]>size||fields[5]!=16384||fields[5]>size-fields[4]||fields[3]%16384||fields[4]%16384)goto done;
        nx_guard_module candidate={module_names[i],source_hashes[i],source_ids[i],
            [m[@"image_sha256"] UTF8String],[m[@"padded_sha256"] UTF8String],(size_t)size,(size_t)padded,{0}};
        memcpy(&candidate.layout,fields,sizeof fields);
        for(unsigned j=0;j<8;j++){
            uint64_t index;NSString *key=[NSString stringWithFormat:@"KEY_%s_IDX",key_names[j]];
            if(!integer(layout[key],&index)||index>=2048)goto done;
            for(unsigned k=0;k<j;k++)if(candidate.layout.key_idx[k]==index)goto done;
            candidate.layout.key_idx[j]=(unsigned)index;
        }
        modules[i]=candidate;
        for(NSString *kind in @[@"fixups",@"gates",@"cuts",@"params",@"obs"]){
            NSString *name=[NSString stringWithFormat:@"%s.%@.tsv",module_names[i],kind];NSDictionary *f=doc[@"loader_tables"][name];uint64_t bytes;
            if(!dictionary(f)||!integer(f[@"bytes"],&bytes)||bytes>1024*1024||!hex64(f[@"sha256"]))goto done;
            /* Retained directory descriptors plus nonblocking regular reads: empty gate files are valid. */
            nx_guard_file file={name.UTF8String,(size_t)bytes,[f[@"sha256"] UTF8String]};
            if(nx_guard_verify_files(dm,&file,1,NULL,0))goto done;
            NSData *data=bounded_at(dm,name.UTF8String,1024*1024);if(!data||data.length!=bytes)goto done;
            CC_SHA256_CTX hash;CC_SHA256_Init(&hash);CC_SHA256_Update(&hash,data.bytes,(CC_LONG)data.length);
            if(!hash_matches(&hash,[f[@"sha256"] UTF8String]))goto done;
            if([kind isEqual:@"gates"]){
                NSString *text=[[NSString alloc]initWithData:data encoding:NSUTF8StringEncoding];if(!text)goto done;
                unsigned count=0;NSMutableSet *returns=[NSMutableSet set];
                for(NSString *line in [text componentsSeparatedByString:@"\n"]){if(!line.length)continue;
                    unsigned long long ret,orig;int arg,used=0;char gate[16];const char *s=line.UTF8String;
                    if(sscanf(s,"%llx %15s %d %llx %n",&ret,gate,&arg,&orig,&used)!=4||s[used]||
                        ret%4||ret<fields[3]||ret>=size||orig%4||orig>=fields[0]||
                        (strcmp(gate,"svc")&&strcmp(gate,"cntpct")&&strcmp(gate,"cntvct"))||
                        arg<0||arg>(!strcmp(gate,"svc")?127:30)||[returns containsObject:@(ret)])goto done;
                    [returns addObject:@(ret)];count++;
                }
                if(count!=gate_counts[i])goto done;
            }
        }
    }
    if(!dictionary(doc[@"force_registry"])||!array(doc[@"force_rules"])||![doc[@"force_rules"] count]||
        [doc[@"force_rules"] count]>4096||!number(doc[@"force_registry"][@"count"],[doc[@"force_rules"] count]))goto done;
    for(NSDictionary *rule in doc[@"force_rules"]){
        if(!dictionary(rule)||!string(rule[@"name"])||![rule[@"name"] length]||!string(rule[@"why"])||
            ![rule[@"why"] length]||!dictionary(rule[@"references"])||by_name[rule[@"name"]])goto done;
        BOOL selected=[rule[@"references"] count]>0;if(!boolean(rule[@"selected"],selected))goto done;
        for(NSString *module in rule[@"references"]){uint64_t count;
            if(!string(module)||module_index(module.UTF8String)<0||!integer(rule[@"references"][module],&count)||!count||count>1000000)goto done;}
        by_name[rule[@"name"]]=rule;
    }
    if(!dictionary(doc[@"entry_symbols"])||[doc[@"entry_symbols"] count]!=9)goto done;
    for(unsigned i=0;i<9;i++){
        NSDictionary *entry=doc[@"entry_symbols"][@(entry_names[i])];uint64_t offset,binding,visibility;int index;
        if(!dictionary(entry)||!string(entry[@"module"])||(index=module_index([entry[@"module"] UTF8String]))<0||
            !integer(entry[@"offset"],&offset)||offset%4||offset>=modules[index].layout.text_end||
            !integer(entry[@"binding"],&binding)||(binding!=1&&binding!=2)||!integer(entry[@"visibility"],&visibility)||visibility)goto done;
    }
    frameworks=directory_at(root,"Frameworks");if(frameworks<0)goto done;
    framework=directory_at(frameworks,"Guest.framework");if(framework<0)goto done;
    guest=openat(framework,"Guest",O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC);if(guest<0)goto done;
    if(nx_guard_verify_guest(guest,modules,6,NULL,0))goto done;
    contract=doc;rules=by_name;references=[NSMutableDictionary dictionary];
    metadata_path=[@(bundle) stringByAppendingPathComponent:@"dm"];phase=PRELOADED;result=0;
done:
    if(guest>=0)close(guest);if(framework>=0)close(framework);if(frameworks>=0)close(frameworks);if(dm>=0)close(dm);if(root>=0)close(root);
    if(result)fail();return result;
}}
int nxnative_base_v0_loader_begin(const char *metadata,const char *const *names,size_t count){
    if(phase!=PRELOADED||!metadata||![metadata_path isEqual:@(metadata)]||!names||count!=6)return fail();
    for(unsigned i=0;i<6;i++)if(!names[i]||strcmp(names[i],module_names[i]))return fail();phase=LAYOUTS;return 0;
}
int nxnative_base_v0_loader_layout(const char *name,const uint64_t fields[6],const unsigned keys[8],const char *build){
    if(phase!=LAYOUTS||layouts>=6||!name||strcmp(name,module_names[layouts])||!fields||!keys||!build||strlen(build)!=32||strncmp(build,source_ids[layouts],32))return fail();
    if(memcmp(fields,&modules[layouts].layout,6*sizeof *fields)||memcmp(keys,modules[layouts].layout.key_idx,8*sizeof *keys))return fail();
    if(++layouts==6)phase=IMAGES;return 0;
}
int nxnative_base_v0_loader_image(const char *name,const void *const parts[3],const size_t lengths[3]){
    if(phase!=IMAGES||images>=6||!name||strcmp(name,module_names[images])||!parts||!lengths)return fail();
    const nx_guard_module *m=modules+images;uint64_t text=(m->layout.text_end+16383u)&~UINT64_C(16383);
    size_t wanted[]={text,m->layout.patch_off-text,m->padded_bytes-m->layout.patch_off};CC_SHA256_CTX hash;CC_SHA256_Init(&hash);
    for(unsigned i=0;i<3;i++)if(!parts[i]||lengths[i]!=wanted[i])return fail();
    for(unsigned i=0;i<3;i++)CC_SHA256_Update(&hash,parts[i],(CC_LONG)lengths[i]);
    if(!hash_matches(&hash,m->padded_sha256))return fail();images++;return 0;
}
int nxnative_base_v0_loader_gate_count(const char *name,unsigned count){
    int index=module_index(name);if(phase!=IMAGES||index<0||gates!=(unsigned)index||images!=gates+1||count!=gate_counts[index])return fail();gates++;return 0;
}
int nxnative_base_v0_loader_registry(const void *actual,size_t count,uint8_t *selected){@autoreleasepool{
    if(phase!=IMAGES||images!=6||gates!=6||!actual||!selected||count!=rules.count)return fail();
    NSMutableSet *seen=[NSMutableSet set];uint8_t *flags=calloc(count,1);if(!flags)return fail();int result=-1;
    for(size_t i=0;i<count;i++){
        nxnative_base_v0_loader_force entry;memcpy(&entry,(const unsigned char*)actual+i*sizeof entry,sizeof entry);
        if(!entry.name||!entry.why||!entry.fn)goto done;
        NSString *name=@(entry.name);NSDictionary *rule=rules[name];if(!rule||[seen containsObject:name]||![rule[@"why"] isEqual:@(entry.why)])goto done;
        [seen addObject:name];flags[i]=[rule[@"selected"] boolValue];
    }
    memcpy(selected,flags,count);phase=REGISTRY;result=0;
done:free(flags);if(result)fail();return result;
}}
int nxnative_base_v0_loader_reference(const char *module,const char *name){@autoreleasepool{
    if(phase!=REGISTRY||module_index(module)<0||!name)return fail();NSDictionary *rule=rules[@(name)];uint64_t expected;
    if(!rule||!integer(rule[@"references"][@(module)],&expected)||!expected)return fail();
    NSMutableDictionary *hits=references[@(name)];if(!hits){hits=[NSMutableDictionary dictionary];references[@(name)]=hits;}
    uint64_t count=[hits[@(module)] unsignedLongLongValue];if(count>=expected)return fail();hits[@(module)]=@(count+1);return 0;
}}
int nxnative_base_v0_loader_complete(void){
    if(phase!=REGISTRY)return fail();for(NSString *name in rules){NSDictionary *wanted=rules[name][@"references"],*hits=references[name];
        for(NSString *module in wanted)if(!number(hits[module],[wanted[module] unsignedLongLongValue]))return fail();}
    phase=COMPLETE;return 0;
}
int nxnative_base_v0_loader_entry(const char *name,const char *module,uint64_t offset){
    if(phase!=COMPLETE||!name||!module)return fail();NSDictionary *entry=contract[@"entry_symbols"][@(name)];
    if(!entry||![entry[@"module"] isEqual:@(module)]||!number(entry[@"offset"],offset))return fail();return 0;
}
