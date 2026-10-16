/* SPDX-License-Identifier: MIT */
#pragma once
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* Common bounded signed-bundle readers. The caller preserves the read-only
 * signed bundle through runtime preload; these helpers do not authorize mutable
 * concurrent filesystem publication. */
static int denied(char *error,size_t capacity,NSString *message){
    if(error&&capacity)snprintf(error,capacity,"shader publication: %s",message.UTF8String?:"invalid package");
    return -1;
}
static BOOL object(id value){return [value isKindOfClass:NSDictionary.class];}
static BOOL array(id value){return [value isKindOfClass:NSArray.class];}
static BOOL string(id value){return [value isKindOfClass:NSString.class];}
static BOOL boolean(id value,BOOL expected){
    return value&&CFGetTypeID((__bridge CFTypeRef)value)==CFBooleanGetTypeID()&&[value boolValue]==expected;
}
static BOOL hex(id value,NSUInteger length){
    if(!string(value)||[value length]!=length)return NO;
    const char *s=[value UTF8String];if(!s||strlen(s)!=length)return NO;
    for(NSUInteger i=0;i<length;i++)if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return NO;
    return YES;
}
static NSString *digest(NSData *data){
    unsigned char result[CC_SHA256_DIGEST_LENGTH];char out[65];
    CC_SHA256(data.bytes,(CC_LONG)data.length,result);
    for(unsigned i=0;i<sizeof result;i++)snprintf(out+2*i,3,"%02x",result[i]);
    return @(out);
}
static NSData *read_regular(NSString *path,size_t limit){
    int fd=open(path.fileSystemRepresentation,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(fd<0)return nil;
    struct stat st;
    if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_size<=0||(uint64_t)st.st_size>limit){close(fd);return nil;}
    NSMutableData *data=[NSMutableData dataWithLength:(NSUInteger)st.st_size];
    if(!data){close(fd);return nil;}
    size_t used=0;
    while(used<data.length){
        ssize_t n=read(fd,(char*)data.mutableBytes+used,data.length-used);
        if(n<0&&errno==EINTR)continue;
        if(n<=0)break;
        used+=(size_t)n;
    }
    close(fd);return used==data.length?data:nil;
}
static NSDictionary *document(NSData *data){
    id value=data?[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL]:nil;
    return object(value)?value:nil;
}
static BOOL count_matches(id value,NSUInteger expected){
    if(![value isKindOfClass:NSNumber.class]||CFGetTypeID((__bridge CFTypeRef)value)==CFBooleanGetTypeID())return NO;
    return [value doubleValue]==(double)expected;
}
static BOOL contained_directories(const char *bundle,NSArray<NSString*> *components){
    int parent=open(bundle,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    if(parent<0)return NO;
    for(NSString *component in components){
        int child=openat(parent,component.fileSystemRepresentation,
            O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        close(parent);
        if(child<0)return NO;
        parent=child;
    }
    close(parent);return YES;
}
