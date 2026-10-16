#import <UIKit/UIKit.h>
#include "whole_frontend.h"

int main(int argc, char **argv) {
    @autoreleasepool {
        const whole_frontend_runtime *runtime = NULL;
#if defined(BOTW_WHOLE_RUNTIME_CONFIGURED)
        runtime = whole_runtime_configuration();
#endif
        return whole_frontend_main(argc, argv, runtime);
    }
}
