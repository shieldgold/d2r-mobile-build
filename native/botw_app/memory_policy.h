/* Explicit local package selection. No process environment or device guess. */
#ifndef BOTW_IOS_MEMORY_POLICY_H
#define BOTW_IOS_MEMORY_POLICY_H
#import <Foundation/Foundation.h>
#include "runtime.h"

static int configurePackagedHostMemoryPolicy(id configured) {
    enum botw_host_memory_policy policy = BOTW_HOST_MEMORY_BOUNDED;
    if (configured) {
        if (![configured isKindOfClass:NSString.class]) return 1;
        if ([configured isEqualToString:@"original-logical-v1"])
            policy = BOTW_HOST_MEMORY_ORIGINAL_LOGICAL;
        else if (![configured isEqualToString:@"bounded-v1"])
            return 1;
    }
    return botw_host_set_memory_policy(policy);
}
#endif
