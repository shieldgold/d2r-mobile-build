#import <UIKit/UIKit.h>
#import <dispatch/dispatch.h>
#include <assert.h>
#include <math.h>
#include "DmButtonHaptics.h"

static UIUserInterfaceIdiom idiom = UIUserInterfaceIdiomPad;
static UIApplicationState state = UIApplicationStateActive;
static int allocations, impacts, preparations;
static CGFloat lastIntensity;

@implementation UIDevice
+ (instancetype)currentDevice { static UIDevice *d; if (!d) d = [self new]; return d; }
- (UIUserInterfaceIdiom)userInterfaceIdiom { return idiom; }
@end
@implementation UIApplication
+ (instancetype)sharedApplication { static UIApplication *a; if (!a) a = [self new]; return a; }
- (UIApplicationState)applicationState { return state; }
@end
@implementation UIImpactFeedbackGenerator
- (instancetype)initWithStyle:(UIImpactFeedbackStyle)style {
    self = [super init]; assert(style == UIImpactFeedbackStyleLight); allocations++; return self;
}
- (void)impactOccurredWithIntensity:(CGFloat)value {
    assert(NSThread.isMainThread); impacts++; lastIntensity = value;
}
- (void)prepare { preparations++; }
@end

int main(void) {
    @autoreleasepool {
        // An iPad must not allocate or attempt to play unsupported feedback.
        dm_button_haptic(0); assert(allocations == 0 && impacts == 0);
        idiom = UIUserInterfaceIdiomPhone;
        state = UIApplicationStateInactive; dm_button_haptic(0.1);
        state = UIApplicationStateBackground; dm_button_haptic(0.2);
        assert(allocations == 0 && impacts == 0);
        state = UIApplicationStateActive;
        dispatch_group_t group = dispatch_group_create();
        dispatch_group_async(group, dispatch_get_global_queue(0, 0), ^{ dm_button_haptic(0.3); });
        dispatch_group_wait(group, DISPATCH_TIME_FOREVER);
        assert(allocations == 0 && impacts == 0);

        dm_button_haptic(NAN); dm_button_haptic(INFINITY); dm_button_haptic(-1);
        assert(allocations == 0 && impacts == 0);
        dm_button_haptic(1); assert(allocations == 1 && impacts == 1);
        assert(lastIntensity > 0 && lastIntensity <= 1 && preparations == 1);
        // Coalesce simultaneous fingers, but accept the next distinct press.
        dm_button_haptic(1); dm_button_haptic(1.01); dm_button_haptic(1.049);
        assert(impacts == 1);
        dm_button_haptic(1.051); assert(impacts == 2 && allocations == 1);
        dm_button_haptic(0.5); dm_button_haptic(NAN); assert(impacts == 2);
        dm_button_haptic(1000); assert(impacts == 3 && allocations == 1 && preparations == 3);
        state = UIApplicationStateInactive; dm_button_haptic(1001); assert(impacts == 3);
        state = UIApplicationStateActive; dm_button_haptic(1002); assert(impacts == 4);
    }
    return 0;
}
