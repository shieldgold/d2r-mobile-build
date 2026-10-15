/* Actual UIKit multi-touch / Apple GameController input; no game-specific actions. */
#import "BOTWInputOverlay.h"
#import <GameController/GameController.h>
#include <math.h>

@class BOTWInputButton, BOTWInputStick;
@interface BOTWInputOverlay ()
@property(nonatomic, assign) botw_input *input;
@property(nonatomic, strong) NSMutableArray<BOTWInputButton *> *buttons;
@property(nonatomic, strong) BOTWInputStick *leftStick;
@property(nonatomic, strong) BOTWInputStick *rightStick;
@property(nonatomic, strong) CADisplayLink *displayLink;
@property(nonatomic, strong, nullable) GCController *controller;
@property(nonatomic, assign) BOOL valid;
- (void)publishTouch;
- (void)publishController;
- (void)sample:(CADisplayLink *)link;
@end

@interface BOTWInputButton : UIControl
@property(nonatomic, assign) uint64_t mask;
@property(nonatomic, assign) BOOL held;
@property(nonatomic, weak) BOTWInputOverlay *owner;
@property(nonatomic, strong) UILabel *label;
- (void)releaseInput;
@end
@implementation BOTWInputButton
- (instancetype)initWithFrame:(CGRect)frame {
    if ((self=[super initWithFrame:frame])) {
        self.exclusiveTouch=NO;
        self.layer.borderColor=[UIColor colorWithWhite:1 alpha:0.7].CGColor;
        self.layer.borderWidth=1;
        _label=[[UILabel alloc] initWithFrame:self.bounds];
        _label.autoresizingMask=UIViewAutoresizingFlexibleWidth|UIViewAutoresizingFlexibleHeight;
        _label.textAlignment=NSTextAlignmentCenter;
        _label.font=[UIFont systemFontOfSize:16 weight:UIFontWeightBold];
        _label.textColor=UIColor.whiteColor;
        [self addSubview:_label];
        self.isAccessibilityElement=YES;
        self.accessibilityTraits=UIAccessibilityTraitButton;
        [self updateAppearance];
    }
    return self;
}
- (void)layoutSubviews { [super layoutSubviews];self.layer.cornerRadius=MIN(self.bounds.size.width,self.bounds.size.height)/2; }
- (void)updateAppearance { self.backgroundColor=[UIColor colorWithWhite:self.held?0.55:0.08 alpha:self.held?0.85:0.45]; }
- (void)setPressed:(BOOL)pressed { self.held=pressed;[self updateAppearance];[self.owner publishTouch]; }
- (BOOL)beginTracking:(UITouch *)touch withEvent:(UIEvent *)event {
    (void)touch;(void)event;[self setPressed:YES];return YES;
}
- (BOOL)continueTracking:(UITouch *)touch withEvent:(UIEvent *)event {
    (void)event;[self setPressed:CGRectContainsPoint(CGRectInset(self.bounds,-10,-10),[touch locationInView:self])];return YES;
}
- (void)endTracking:(UITouch *)touch withEvent:(UIEvent *)event { (void)touch;(void)event;[self setPressed:NO]; }
- (void)cancelTrackingWithEvent:(UIEvent *)event { (void)event;[self setPressed:NO]; }
- (void)releaseInput { self.held=NO;[self updateAppearance]; }
@end

@interface BOTWInputStick : UIView
@property(nonatomic, weak) BOTWInputOverlay *owner;
@property(nonatomic, strong) UIView *thumb;
@property(nonatomic, strong, nullable) UITouch *tracked;
@property(nonatomic, assign) int32_t x;
@property(nonatomic, assign) int32_t y;
- (void)releaseInput;
@end
@implementation BOTWInputStick
- (instancetype)initWithFrame:(CGRect)frame {
    if((self=[super initWithFrame:frame])) {
        self.multipleTouchEnabled=YES;
        self.backgroundColor=[UIColor colorWithWhite:0.08 alpha:0.35];
        self.layer.borderColor=[UIColor colorWithWhite:1 alpha:0.6].CGColor;
        self.layer.borderWidth=1;
        _thumb=[[UIView alloc] initWithFrame:CGRectMake(0,0,44,44)];
        _thumb.backgroundColor=[UIColor colorWithWhite:1 alpha:0.45];
        _thumb.userInteractionEnabled=NO;_thumb.layer.cornerRadius=22;
        [self addSubview:_thumb];
    }
    return self;
}
- (void)layoutSubviews { [super layoutSubviews];self.layer.cornerRadius=self.bounds.size.width/2;[self updateThumb]; }
- (void)updateThumb {
    CGFloat range=MAX(1,self.bounds.size.width/2-22);
    self.thumb.center=CGPointMake(self.bounds.size.width/2+(CGFloat)self.x/32768*range,
                                 self.bounds.size.height/2-(CGFloat)self.y/32768*range);
}
- (void)updateTouch:(UITouch *)touch {
    CGPoint point=[touch locationInView:self];CGFloat range=MAX(1,self.bounds.size.width/2-22);
    CGFloat x=(point.x-self.bounds.size.width/2)/range,y=(self.bounds.size.height/2-point.y)/range;
    CGFloat length=hypot(x,y);if(length>1){x/=length;y/=length;}
    /* Small physical dead zone prevents a resting thumb from drifting. */
    if(length<0.10){x=0;y=0;}
    self.x=(int32_t)lrint(x*32767);self.y=(int32_t)lrint(y*32767);
    [self updateThumb];[self.owner publishTouch];
}
- (void)touchesBegan:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;if(!self.tracked){self.tracked=touches.anyObject;[self updateTouch:self.tracked];}
}
- (void)touchesMoved:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;if(self.tracked&&[touches containsObject:self.tracked])[self updateTouch:self.tracked];
}
- (void)touchesEnded:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;if(self.tracked&&[touches containsObject:self.tracked]){[self releaseInput];[self.owner publishTouch];}
}
- (void)touchesCancelled:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event { [self touchesEnded:touches withEvent:event]; }
- (void)releaseInput { self.tracked=nil;self.x=0;self.y=0;[self updateThumb]; }
@end

/* CADisplayLink retains its target. Keep the target's owner weak. */
@interface BOTWInputDisplayTarget : NSObject
@property(nonatomic, weak) BOTWInputOverlay *owner;
- (void)sample:(CADisplayLink *)link;
@end
@implementation BOTWInputDisplayTarget
- (void)sample:(CADisplayLink *)link { [self.owner sample:link]; }
@end

@implementation BOTWInputOverlay
+ (instancetype)attachToView:(UIView *)view input:(botw_input *)input {
    NSParameterAssert(NSThread.isMainThread);NSParameterAssert(input);
    BOTWInputOverlay *overlay=[[self alloc] initWithFrame:view.bounds];
    overlay.autoresizingMask=UIViewAutoresizingFlexibleWidth|UIViewAutoresizingFlexibleHeight;
    overlay.input=input;overlay.valid=YES;overlay.multipleTouchEnabled=YES;
    overlay.buttons=[NSMutableArray array];
    NSArray<NSString *> *names=@[@"A",@"B",@"X",@"Y",@"L",@"R",@"ZL",@"ZR",@"+",@"−",@"◀",@"▲",@"▶",@"▼",@"L3",@"R3"];
    const uint64_t masks[]={BOTW_BUTTON_A,BOTW_BUTTON_B,BOTW_BUTTON_X,BOTW_BUTTON_Y,
        BOTW_BUTTON_L,BOTW_BUTTON_R,BOTW_BUTTON_ZL,BOTW_BUTTON_ZR,BOTW_BUTTON_PLUS,
        BOTW_BUTTON_MINUS,BOTW_BUTTON_LEFT,BOTW_BUTTON_UP,BOTW_BUTTON_RIGHT,
        BOTW_BUTTON_DOWN,BOTW_BUTTON_LSTICK,BOTW_BUTTON_RSTICK};
    for(NSUInteger i=0;i<names.count;i++) {
        BOTWInputButton *button=[[BOTWInputButton alloc] initWithFrame:CGRectZero];
        button.mask=masks[i];button.label.text=names[i];button.accessibilityLabel=names[i];button.owner=overlay;
        [overlay.buttons addObject:button];[overlay addSubview:button];
    }
    overlay.leftStick=[[BOTWInputStick alloc] initWithFrame:CGRectZero];overlay.leftStick.owner=overlay;
    overlay.rightStick=[[BOTWInputStick alloc] initWithFrame:CGRectZero];overlay.rightStick.owner=overlay;
    [overlay addSubview:overlay.leftStick];[overlay addSubview:overlay.rightStick];
    NSNotificationCenter *center=NSNotificationCenter.defaultCenter;
    [center addObserver:overlay selector:@selector(controllersChanged:) name:GCControllerDidConnectNotification object:nil];
    [center addObserver:overlay selector:@selector(controllersChanged:) name:GCControllerDidDisconnectNotification object:nil];
    [center addObserver:overlay selector:@selector(resignActive:) name:UIApplicationWillResignActiveNotification object:nil];
    [center addObserver:overlay selector:@selector(becomeActive:) name:UIApplicationDidBecomeActiveNotification object:nil];
    [view addSubview:overlay];
    botw_input_set_active(input,UIApplication.sharedApplication.applicationState==UIApplicationStateActive);
    [overlay publishTouch];[overlay controllersChanged:nil];
    BOTWInputDisplayTarget *target=[BOTWInputDisplayTarget new];target.owner=overlay;
    overlay.displayLink=[CADisplayLink displayLinkWithTarget:target selector:@selector(sample:)];
    overlay.displayLink.preferredFramesPerSecond=60;
    [overlay.displayLink addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
    return overlay;
}
- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    UIView *hit=[super hitTest:point withEvent:event];return hit==self?nil:hit;
}
- (void)layoutSubviews {
    [super layoutSubviews];
    CGRect area=UIEdgeInsetsInsetRect(self.bounds,self.safeAreaInsets);
    CGFloat w=area.size.width,h=area.size.height;
    CGFloat diameter=MIN(112,MAX(70,h*0.32)),size=MIN(48,MAX(34,h*0.135));
    CGFloat y=area.origin.y+h*0.68;
    CGPoint left=CGPointMake(area.origin.x+w*0.14,y),right=CGPointMake(area.origin.x+w*0.68,y);
    self.leftStick.frame=CGRectMake(left.x-diameter/2,left.y-diameter/2,diameter,diameter);
    self.rightStick.frame=CGRectMake(right.x-diameter/2,right.y-diameter/2,diameter,diameter);
    CGFloat faceX=area.origin.x+w*0.87,dpadX=area.origin.x+w*0.34,step=size*1.05;
    CGFloat top=area.origin.y+size*0.7;
    CGPoint centers[]={ {faceX+step,y},{faceX,y+step},{faceX,y-step},{faceX-step,y},
        {area.origin.x+size*0.7,top},{CGRectGetMaxX(area)-size*0.7,top},
        {area.origin.x+size*2.6,top},{CGRectGetMaxX(area)-size*2.6,top},
        {area.origin.x+w*0.56,top},{area.origin.x+w*0.44,top},
        {dpadX-step,y},{dpadX,y-step},{dpadX+step,y},{dpadX,y+step},
        {left.x,MIN(CGRectGetMaxY(area)-size/2,y+diameter/2+size*0.6)},
        {right.x,MIN(CGRectGetMaxY(area)-size/2,y+diameter/2+size*0.6)} };
    for(NSUInteger i=0;i<self.buttons.count;i++)self.buttons[i].frame=CGRectMake(centers[i].x-size/2,centers[i].y-size/2,size,size);
}
- (void)publishTouch {
    if(!self.valid)return;uint64_t buttons=0;
    for(BOTWInputButton *button in self.buttons)if(button.held)buttons|=button.mask;
    botw_input_set_source(self.input,BOTW_INPUT_TOUCH,buttons,self.leftStick.x,self.leftStick.y,self.rightStick.x,self.rightStick.y,1);
}
- (void)controllersChanged:(NSNotification *)notification {
    (void)notification;if(!self.valid)return;
    self.controller.extendedGamepad.valueChangedHandler=nil;self.controller=nil;
    for(GCController *candidate in GCController.controllers)if(candidate.extendedGamepad){self.controller=candidate;break;}
    if(self.controller) {
        self.controller.handlerQueue=dispatch_get_main_queue();
        __weak BOTWInputOverlay *weakSelf=self;
        self.controller.extendedGamepad.valueChangedHandler=^(GCExtendedGamepad *gamepad,GCControllerElement *element){
            (void)gamepad;(void)element;[weakSelf publishController];
        };
    }
    [self publishController];
}
- (void)publishController {
    if(!self.valid)return;
    GCExtendedGamepad *pad=self.controller.extendedGamepad;uint64_t buttons=0;
    if(pad) {
        /* Apple standard profile names are physical positions: A=south, B=east. */
        if(pad.buttonA.isPressed)buttons|=BOTW_BUTTON_B;
        if(pad.buttonB.isPressed)buttons|=BOTW_BUTTON_A;
        if(pad.buttonX.isPressed)buttons|=BOTW_BUTTON_Y;
        if(pad.buttonY.isPressed)buttons|=BOTW_BUTTON_X;
        if(pad.leftShoulder.isPressed)buttons|=BOTW_BUTTON_L;
        if(pad.rightShoulder.isPressed)buttons|=BOTW_BUTTON_R;
        if(pad.leftTrigger.isPressed)buttons|=BOTW_BUTTON_ZL;
        if(pad.rightTrigger.isPressed)buttons|=BOTW_BUTTON_ZR;
        if(pad.buttonMenu.isPressed)buttons|=BOTW_BUTTON_PLUS;
        if(pad.buttonOptions.isPressed)buttons|=BOTW_BUTTON_MINUS;
        if(pad.leftThumbstickButton.isPressed)buttons|=BOTW_BUTTON_LSTICK;
        if(pad.rightThumbstickButton.isPressed)buttons|=BOTW_BUTTON_RSTICK;
        if(pad.dpad.left.isPressed)buttons|=BOTW_BUTTON_LEFT;
        if(pad.dpad.up.isPressed)buttons|=BOTW_BUTTON_UP;
        if(pad.dpad.right.isPressed)buttons|=BOTW_BUTTON_RIGHT;
        if(pad.dpad.down.isPressed)buttons|=BOTW_BUTTON_DOWN;
    }
    botw_input_set_source(self.input,BOTW_INPUT_GAMECONTROLLER,buttons,
        (int32_t)lrintf(pad.leftThumbstick.xAxis.value*32767),
        (int32_t)lrintf(pad.leftThumbstick.yAxis.value*32767),
        (int32_t)lrintf(pad.rightThumbstick.xAxis.value*32767),
        (int32_t)lrintf(pad.rightThumbstick.yAxis.value*32767),pad!=nil);
}
- (void)sample:(CADisplayLink *)link {
    (void)link;if(self.valid)botw_input_sample(self.input,botw_input_monotonic_ticks());
}
- (void)releaseTouches {
    for(BOTWInputButton *button in self.buttons)[button releaseInput];
    [self.leftStick releaseInput];[self.rightStick releaseInput];
}
- (void)resignActive:(NSNotification *)notification {
    (void)notification;if(!self.valid)return;[self releaseTouches];
    botw_input_set_active(self.input,0);botw_input_sample(self.input,botw_input_monotonic_ticks());self.displayLink.paused=YES;
}
- (void)becomeActive:(NSNotification *)notification {
    (void)notification;if(!self.valid)return;botw_input_set_active(self.input,1);
    [self publishTouch];[self publishController];self.displayLink.paused=NO;
}
- (void)invalidate {
    if(!self.valid)return;self.valid=NO;[self.displayLink invalidate];self.displayLink=nil;
    self.controller.extendedGamepad.valueChangedHandler=nil;self.controller=nil;
    [NSNotificationCenter.defaultCenter removeObserver:self];[self releaseTouches];
    botw_input_set_source(self.input,BOTW_INPUT_TOUCH,0,0,0,0,0,0);
    botw_input_set_source(self.input,BOTW_INPUT_GAMECONTROLLER,0,0,0,0,0,0);
    botw_input_sample(self.input,botw_input_monotonic_ticks());self.input=NULL;[self removeFromSuperview];
}
- (void)dealloc { [self invalidate]; }
@end
