#import <UIKit/UIKit.h>
#include <math.h>
#include "DmButtonHaptics.h"

void dm_button_haptic(double now) {
    // Supported game iPhones have a Taptic Engine; iPads have no built-in haptics.
    if (!NSThread.isMainThread || UIDevice.currentDevice.userInterfaceIdiom != UIUserInterfaceIdiomPhone ||
        UIApplication.sharedApplication.applicationState != UIApplicationStateActive ||
        !isfinite(now) || now < 0) return;
    static double previous = -1;
    if (previous >= 0 && now - previous < 0.05) return;
    static UIImpactFeedbackGenerator *feedback;
    if (!feedback) feedback = [[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleLight];
    [feedback impactOccurredWithIntensity:0.55];
    [feedback prepare];
    previous = now;
}
