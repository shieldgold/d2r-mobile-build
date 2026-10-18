#import <Foundation/Foundation.h>

typedef NS_ENUM(NSInteger, UIUserInterfaceIdiom) {
    UIUserInterfaceIdiomPhone, UIUserInterfaceIdiomPad
};
typedef NS_ENUM(NSInteger, UIApplicationState) {
    UIApplicationStateActive, UIApplicationStateInactive, UIApplicationStateBackground
};
typedef NS_ENUM(NSInteger, UIImpactFeedbackStyle) { UIImpactFeedbackStyleLight };

@interface UIDevice : NSObject
+ (instancetype)currentDevice;
@property(readonly) UIUserInterfaceIdiom userInterfaceIdiom;
@end
@interface UIApplication : NSObject
+ (instancetype)sharedApplication;
@property(readonly) UIApplicationState applicationState;
@end
@interface UIImpactFeedbackGenerator : NSObject
- (instancetype)initWithStyle:(UIImpactFeedbackStyle)style;
- (void)impactOccurredWithIntensity:(CGFloat)intensity;
- (void)prepare;
@end
