#import <UIKit/UIKit.h>
#import <QuartzCore/QuartzCore.h>
#import <Metal/Metal.h>
#import <AVFAudio/AVAudioSession.h>
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "whole_frontend.h"
#include "whole_front_state.h"
#include "front.h"
#include "botw_front.h"
#include "botw_hid.h"
void botw_base_v0_debug_pad_tick(void);

enum { WHOLE_THREAD_STACK = 8 * 1024 * 1024 };
static whole_front_state g_state = WHOLE_FRONT_STATE_INITIALIZER;
static whole_frontend_runtime g_runtime;
static char **g_host_argv;
static int g_configured;
static _Atomic int g_prepared;
static _Atomic int g_host_running;
static _Atomic(void *) g_layer_pointer;
/* A running backend may cache the layer. Retain it for the process lifetime. */
static CAMetalLayer *g_metal_layer;
static char g_touch_identity;
static pthread_mutex_t g_display_lock = PTHREAD_MUTEX_INITIALIZER;
static double g_refresh;
static char g_display_name[64];
static NSString *g_configuration_error;
static BOOL g_foreground, g_focused, g_audio_active, g_audio_interrupted, g_media_lost, g_platform_failed;
static void (^g_text_cancel)(void);
static int g_hid_focused = -1;

@class WholeController, WholeOverlay;
static __weak WholeController *g_controller;
static void apply_lifecycle(void);
static void update_display_link(void);
static void report(NSString *message);
static void publish_touch(void) {
    whole_front_pad p;
    if (whole_front_state_touch(&g_state, &p)) {
        botw_pad_state s = {p.buttons, p.lx, p.ly, p.rx, p.ry};
        botw_hid_pad_set((uintptr_t)&g_touch_identity, &s);
    } else botw_hid_pad_drop((uintptr_t)&g_touch_identity);
}
static void input_focus(int focused) {
    if (g_hid_focused == focused) return;
    g_hid_focused = focused;
    botw_input_focus(focused);
    if (!focused) botw_hid_pad_drop((uintptr_t)&g_touch_identity);
}
static int start_thread(pthread_t *thread, void *(*entry)(void *), void *context) {
    pthread_attr_t attributes;
    int error = pthread_attr_init(&attributes);
    if (error) return error;
    error = pthread_attr_setstacksize(&attributes, WHOLE_THREAD_STACK);
    if (!error) error = pthread_create(thread, &attributes, entry, context);
    pthread_attr_destroy(&attributes);
    return error;
}

@interface WholeButton : UIControl
@property(nonatomic) uint64_t mask;
@property(nonatomic, strong) UILabel *label;
- (void)releaseInput;
@end
@implementation WholeButton
- (instancetype)initWithFrame:(CGRect)frame {
    if ((self = [super initWithFrame:frame])) {
        self.exclusiveTouch = NO;
        self.layer.borderColor = [UIColor colorWithWhite:1 alpha:0.65].CGColor;
        self.layer.borderWidth = 1;
        self.backgroundColor = [UIColor colorWithWhite:0.1 alpha:0.45];
        _label = [[UILabel alloc] initWithFrame:self.bounds];
        _label.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        _label.textAlignment = NSTextAlignmentCenter;
        _label.textColor = UIColor.whiteColor;
        _label.font = [UIFont systemFontOfSize:16 weight:UIFontWeightBold];
        [self addSubview:_label];
        self.isAccessibilityElement = YES;
        self.accessibilityTraits = UIAccessibilityTraitButton;
    }
    return self;
}
- (void)layoutSubviews {
    [super layoutSubviews];
    self.layer.cornerRadius = MIN(self.bounds.size.width, self.bounds.size.height) / 2;
}
- (void)setDown:(BOOL)down {
    self.backgroundColor = [UIColor colorWithWhite:down ? 0.6 : 0.1 alpha:0.5];
    whole_front_state_button(&g_state, self.mask, down);
    publish_touch();
}
- (BOOL)beginTracking:(UITouch *)touch withEvent:(UIEvent *)event {
    (void)touch; (void)event; [self setDown:YES]; return YES;
}
- (BOOL)continueTracking:(UITouch *)touch withEvent:(UIEvent *)event {
    (void)event;
    [self setDown:CGRectContainsPoint(CGRectInset(self.bounds, -10, -10), [touch locationInView:self])];
    return YES;
}
- (void)endTracking:(UITouch *)touch withEvent:(UIEvent *)event { (void)touch; (void)event; [self setDown:NO]; }
- (void)cancelTrackingWithEvent:(UIEvent *)event { (void)event; [self setDown:NO]; }
- (void)releaseInput { [self cancelTrackingWithEvent:nil]; }
@end

@interface WholeStick : UIView
@property(nonatomic) BOOL right;
@property(nonatomic, strong) UIView *thumb;
@property(nonatomic, strong) UITouch *tracked;
- (void)releaseInput;
@end
@implementation WholeStick
- (instancetype)initWithFrame:(CGRect)frame {
    if ((self = [super initWithFrame:frame])) {
        self.multipleTouchEnabled = YES;
        self.backgroundColor = [UIColor colorWithWhite:0.1 alpha:0.35];
        self.layer.borderColor = [UIColor colorWithWhite:1 alpha:0.6].CGColor;
        self.layer.borderWidth = 1;
        _thumb = [[UIView alloc] initWithFrame:CGRectMake(0, 0, 40, 40)];
        _thumb.backgroundColor = [UIColor colorWithWhite:1 alpha:0.5];
        _thumb.userInteractionEnabled = NO;
        _thumb.layer.cornerRadius = 20;
        [self addSubview:_thumb];
    }
    return self;
}
- (void)updateThumb {
    whole_front_pad p;
    whole_front_state_touch(&g_state, &p);
    int32_t x = self.right ? p.rx : p.lx, y = self.right ? p.ry : p.ly;
    CGFloat range = MAX(1, MIN(self.bounds.size.width, self.bounds.size.height) / 2 - 20);
    self.thumb.center = CGPointMake(CGRectGetMidX(self.bounds) + (CGFloat)x / 32767 * range,
                                   CGRectGetMidY(self.bounds) - (CGFloat)y / 32767 * range);
}
- (void)layoutSubviews { [super layoutSubviews]; self.layer.cornerRadius = self.bounds.size.width / 2; [self updateThumb]; }
- (void)updateTouch:(UITouch *)touch {
    CGPoint p = [touch locationInView:self];
    CGFloat range = MAX(1, MIN(self.bounds.size.width, self.bounds.size.height) / 2 - 20);
    whole_front_state_stick(&g_state, self.right,
        (float)((p.x - CGRectGetMidX(self.bounds)) / range),
        (float)((CGRectGetMidY(self.bounds) - p.y) / range));
    [self updateThumb]; publish_touch();
}
- (void)touchesBegan:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;
    if (!self.tracked) { self.tracked = touches.anyObject; [self updateTouch:self.tracked]; }
}
- (void)touchesMoved:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;
    if (self.tracked && [touches containsObject:self.tracked]) [self updateTouch:self.tracked];
}
- (void)touchesEnded:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;
    if (self.tracked && [touches containsObject:self.tracked]) [self releaseInput];
}
- (void)touchesCancelled:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event { [self touchesEnded:touches withEvent:event]; }
- (void)releaseInput {
    self.tracked = nil;
    whole_front_state_stick(&g_state, self.right, 0, 0);
    [self updateThumb]; publish_touch();
}
@end

@interface WholeOverlay : UIView
@property(nonatomic, strong) NSArray<WholeButton *> *buttons;
@property(nonatomic, strong) WholeStick *leftStick;
@property(nonatomic, strong) WholeStick *rightStick;
- (void)releaseInput;
@end
@implementation WholeOverlay
- (instancetype)initWithFrame:(CGRect)frame {
    if ((self = [super initWithFrame:frame])) {
        self.multipleTouchEnabled = YES;
        NSArray<NSString *> *names = @[@"A", @"B", @"X", @"Y", @"L", @"R", @"ZL", @"ZR",
                                      @"+", @"-", @"Left", @"Up", @"Right", @"Down", @"L3", @"R3"];
        const uint64_t masks[] = {BOTW_BTN_A, BOTW_BTN_B, BOTW_BTN_X, BOTW_BTN_Y,
            BOTW_BTN_L, BOTW_BTN_R, BOTW_BTN_ZL, BOTW_BTN_ZR, BOTW_BTN_PLUS, BOTW_BTN_MINUS,
            BOTW_BTN_LEFT, BOTW_BTN_UP, BOTW_BTN_RIGHT, BOTW_BTN_DOWN, BOTW_BTN_LS, BOTW_BTN_RS};
        NSMutableArray<WholeButton *> *buttons = [NSMutableArray array];
        for (NSUInteger i = 0; i < names.count; ++i) {
            WholeButton *button = [[WholeButton alloc] initWithFrame:CGRectZero];
            button.mask = masks[i]; button.label.text = names[i]; button.accessibilityLabel = names[i];
            [self addSubview:button]; [buttons addObject:button];
        }
        self.buttons = buttons;
        self.leftStick = [[WholeStick alloc] initWithFrame:CGRectZero];
        self.rightStick = [[WholeStick alloc] initWithFrame:CGRectZero]; self.rightStick.right = YES;
        [self addSubview:self.leftStick]; [self addSubview:self.rightStick];
    }
    return self;
}
- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    UIView *hit = [super hitTest:point withEvent:event]; return hit == self ? nil : hit;
}
- (void)layoutSubviews {
    [super layoutSubviews];
    CGRect area = UIEdgeInsetsInsetRect(self.bounds, self.safeAreaInsets);
    CGFloat w = area.size.width, h = area.size.height;
    CGFloat diameter = MIN(112, MAX(70, h * 0.32)), size = MIN(48, MAX(34, h * 0.135));
    CGFloat y = area.origin.y + h * 0.68, step = size * 1.05, top = area.origin.y + size * 0.7;
    CGPoint left = CGPointMake(area.origin.x + w * 0.14, y), right = CGPointMake(area.origin.x + w * 0.68, y);
    self.leftStick.frame = CGRectMake(left.x - diameter / 2, left.y - diameter / 2, diameter, diameter);
    self.rightStick.frame = CGRectMake(right.x - diameter / 2, right.y - diameter / 2, diameter, diameter);
    CGFloat face = area.origin.x + w * 0.87, dpad = area.origin.x + w * 0.34;
    CGPoint centers[] = {{face + step,y},{face,y + step},{face,y - step},{face - step,y},
        {area.origin.x + size * 0.7,top},{CGRectGetMaxX(area) - size * 0.7,top},
        {area.origin.x + size * 2.6,top},{CGRectGetMaxX(area) - size * 2.6,top},
        {area.origin.x + w * 0.56,top},{area.origin.x + w * 0.44,top},
        {dpad - step,y},{dpad,y - step},{dpad + step,y},{dpad,y + step},
        {left.x,MIN(CGRectGetMaxY(area) - size / 2,y + diameter / 2 + size * 0.6)},
        {right.x,MIN(CGRectGetMaxY(area) - size / 2,y + diameter / 2 + size * 0.6)}};
    for (NSUInteger i = 0; i < self.buttons.count; ++i)
        self.buttons[i].frame = CGRectMake(centers[i].x - size / 2, centers[i].y - size / 2, size, size);
}
- (void)releaseInput {
    for (WholeButton *button in self.buttons) [button releaseInput];
    [self.leftStick releaseInput]; [self.rightStick releaseInput];
}
@end

@interface WholeMetalView : UIView
@end
@implementation WholeMetalView
+ (Class)layerClass { return CAMetalLayer.class; }
- (void)layoutSubviews {
    [super layoutSubviews];
    CAMetalLayer *layer = (CAMetalLayer *)self.layer;
    CGFloat scale = self.window.screen.scale;
    if (scale <= 0) scale = self.traitCollection.displayScale;
    if (scale <= 0) scale = 1;
    [CATransaction begin]; [CATransaction setDisableActions:YES];
    layer.contentsScale = scale;
    layer.drawableSize = CGSizeMake(round(self.bounds.size.width * scale), round(self.bounds.size.height * scale));
    [CATransaction commit];
}
@end

@interface WholeController : UIViewController
@property(nonatomic, strong) WholeMetalView *metalView;
@property(nonatomic, strong) WholeOverlay *overlay;
@property(nonatomic, strong) UILabel *status;
@property(nonatomic, strong) CADisplayLink *displayLink;
@property(nonatomic) BOOL preparing;
- (void)beginPreparation;
- (void)startHostIfReady;
- (void)displayTick:(CADisplayLink *)link;
@end
/* CADisplayLink retains its target, so the forwarding target borrows the owner. */
@interface WholeDisplayTarget : NSObject
@property(nonatomic, weak) WholeController *owner;
- (void)displayTick:(CADisplayLink *)link;
@end
@implementation WholeDisplayTarget
- (void)displayTick:(CADisplayLink *)link { [self.owner displayTick:link]; }
@end

static void report(NSString *message) {
    fprintf(stderr, "BotW whole frontend: %s\n", message.UTF8String ?: "invalid diagnostic");
    dispatch_async(dispatch_get_main_queue(), ^{ g_controller.status.text = message; });
}
static void update_display_link(void) {
    NSCAssert(NSThread.isMainThread, @"UIKit main thread required");
    whole_front_pad ignored;
    BOOL ready = whole_front_state_touch(&g_state, &ignored);
    g_controller.displayLink.paused = !ready;
    g_controller.overlay.userInteractionEnabled = ready && !g_text_cancel;
}
static void *prepare_runtime(void *unused) {
    (void)unused;
    @autoreleasepool {
        char error[512] = {0};
        int result = g_runtime.prepare(g_runtime.context, error, sizeof error);
        error[sizeof error - 1] = 0;
        NSString *message = [NSString stringWithUTF8String:error];
        dispatch_async(dispatch_get_main_queue(), ^{
            if (result) {
                whole_front_state_lifecycle(&g_state, 0, 0, 0, result);
                report(message.length ? message : @"Runtime preparation failed; host was not started.");
                input_focus(0); update_display_link();
            } else {
                atomic_store_explicit(&g_prepared, 1, memory_order_release);
                whole_front_state_runtime(&g_state, 1);
                apply_lifecycle();
            }
        });
    }
    return NULL;
}
static void *run_host(void *unused) {
    (void)unused;
    @autoreleasepool {
        if (whole_front_state_wait_ready(&g_state)) {
            atomic_store_explicit(&g_host_running, 0, memory_order_release);
            report(@"Host entry was cancelled by platform failure or permanent stop.");
            dispatch_async(dispatch_get_main_queue(), ^{ update_display_link(); });
            return NULL;
        }
        int result = g_runtime.host_main(g_runtime.host_argc, g_host_argv);
        atomic_store_explicit(&g_host_running, 0, memory_order_release);
        dispatch_async(dispatch_get_main_queue(), ^{
            whole_front_state_stop(&g_state);
            [g_controller.overlay releaseInput]; input_focus(0); update_display_link();
            report([NSString stringWithFormat:@"Host returned %d. Gameplay has not been verified.", result]);
        });
    }
    return NULL;
}
static void apply_lifecycle(void) {
    NSCAssert(NSThread.isMainThread, @"UIKit main thread required");
    if (!g_focused && g_text_cancel) { void (^cancel)(void) = g_text_cancel; g_text_cancel = nil; cancel(); }
    BOOL wanted = g_foreground && g_focused && !g_audio_interrupted && !g_media_lost && !g_platform_failed;
    if (!wanted) { [g_controller.overlay releaseInput]; input_focus(0); }
    if (!atomic_load_explicit(&g_prepared, memory_order_acquire)) {
        whole_front_state_lifecycle(&g_state, g_foreground, g_focused, 0, 0);
        update_display_link(); [g_controller beginPreparation]; return;
    }
    NSError *audio_error = nil;
    if (wanted && !g_audio_active) {
        AVAudioSession *session = AVAudioSession.sharedInstance;
        g_audio_active = [session setCategory:AVAudioSessionCategoryPlayback mode:AVAudioSessionModeDefault
            options:AVAudioSessionCategoryOptionMixWithOthers error:&audio_error] &&
            [session setActive:YES error:&audio_error];
    }
    char error[512] = {0};
    int result = g_runtime.lifecycle(g_runtime.context, g_platform_failed ? 0 : g_foreground,
        g_platform_failed ? 0 : g_focused,
        wanted && g_audio_active, error, sizeof error);
    error[sizeof error - 1] = 0;
    if (g_media_lost && !result) result = EIO;
    if (result) g_platform_failed = YES;
    whole_front_state_lifecycle(&g_state, g_foreground, g_focused,
        wanted && g_audio_active, result);
    if (!whole_front_state_touch(&g_state, NULL)) [g_controller.overlay releaseInput];
    if (!wanted || result) {
        input_focus(0);
        [g_controller.overlay releaseInput];
        if (g_audio_active) {
            [AVAudioSession.sharedInstance setActive:NO
                withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:&audio_error];
            g_audio_active = NO;
        }
    } else input_focus(whole_front_state_touch(&g_state, NULL) && !g_text_cancel);
    if (result) report(error[0] ? [NSString stringWithUTF8String:error] :
        @"Platform lifecycle failed; guest start and display callbacks are blocked.");
    if (audio_error) report(audio_error.localizedDescription);
    update_display_link();
    [g_controller startHostIfReady];
}
@implementation WholeController
- (void)viewDidLoad {
    [super viewDidLoad]; g_controller = self;
    self.view.backgroundColor = UIColor.blackColor;
    self.metalView = [[WholeMetalView alloc] initWithFrame:CGRectZero];
    CAMetalLayer *layer = (CAMetalLayer *)self.metalView.layer;
    layer.device = MTLCreateSystemDefaultDevice();
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = YES; layer.opaque = YES;
    [self.view addSubview:self.metalView];
    self.overlay = [[WholeOverlay alloc] initWithFrame:self.view.bounds];
    self.overlay.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    self.overlay.userInteractionEnabled = NO; [self.view addSubview:self.overlay];
    self.status = [[UILabel alloc] initWithFrame:CGRectZero];
    self.status.numberOfLines = 0; self.status.textColor = UIColor.whiteColor;
    self.status.backgroundColor = [UIColor colorWithWhite:0 alpha:0.7];
    self.status.font = [UIFont monospacedSystemFontOfSize:12 weight:UIFontWeightRegular];
    self.status.text = g_configuration_error ?: @"Preparing the complete host. Gameplay has not been verified.";
    [self.view addSubview:self.status];
    WholeDisplayTarget *target = [WholeDisplayTarget new]; target.owner = self;
    self.displayLink = [CADisplayLink displayLinkWithTarget:target selector:@selector(displayTick:)];
    self.displayLink.paused = YES;
    [self.displayLink addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
    if (g_configured) { botw_input_start(); input_focus(0); }
    if (!layer.device) report(@"Metal device is unavailable; host was not started.");
}
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    CGRect area = self.view.bounds;
    CGFloat width = MIN(area.size.width, area.size.height * 16.0 / 9.0), height = width * 9.0 / 16.0;
    self.metalView.frame = CGRectMake(CGRectGetMidX(area) - width / 2, CGRectGetMidY(area) - height / 2, width, height);
    self.status.frame = CGRectMake(self.view.safeAreaInsets.left + 12, self.view.safeAreaInsets.top + 8,
        MAX(0, area.size.width - self.view.safeAreaInsets.left - self.view.safeAreaInsets.right - 24), 68);
}
- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    if (self.metalView.window && ((CAMetalLayer *)self.metalView.layer).device) {
        g_metal_layer = (CAMetalLayer *)self.metalView.layer;
        atomic_store_explicit(&g_layer_pointer, (__bridge void *)g_metal_layer, memory_order_release);
        whole_front_state_surface(&g_state, 1);
        pthread_mutex_lock(&g_display_lock);
        g_refresh = self.view.window.screen.maximumFramesPerSecond;
        snprintf(g_display_name, sizeof g_display_name, "UIKit screen");
        pthread_mutex_unlock(&g_display_lock);
    }
    apply_lifecycle();
}
- (void)beginPreparation {
    if (!g_configured || self.preparing || !front_metal_layer()) return;
    self.preparing = YES;
    pthread_t thread;
    int result = start_thread(&thread, prepare_runtime, NULL);
    if (result) {
        whole_front_state_lifecycle(&g_state, 0, 0, 0, result);
        report([NSString stringWithFormat:@"Could not start runtime preparation: %s", strerror(result)]);
    } else pthread_detach(thread);
}
- (void)startHostIfReady {
    if (!whole_front_state_claim_start(&g_state)) return;
    pthread_t thread;
    atomic_store_explicit(&g_host_running, 1, memory_order_release);
    int result = start_thread(&thread, run_host, NULL);
    if (result) {
        atomic_store_explicit(&g_host_running, 0, memory_order_release);
        whole_front_state_lifecycle(&g_state, 0, 0, 0, result);
        input_focus(0); update_display_link();
        report([NSString stringWithFormat:@"Could not start host thread: %s", strerror(result)]);
    } else { pthread_detach(thread); report(@"Complete host started. Gameplay has not been verified."); }
}
- (void)displayTick:(CADisplayLink *)link {
    double interval = link.targetTimestamp - link.timestamp;
    if (interval > 0 && isfinite(interval)) {
        pthread_mutex_lock(&g_display_lock); g_refresh = 1 / interval; pthread_mutex_unlock(&g_display_lock);
    }
    /* VI has already bound its system event. Never acquire or submit a drawable. */
    if (whole_front_state_touch(&g_state, NULL)) botw_base_v0_debug_pad_tick();
    whole_front_state_display_tick(&g_state);
}
- (BOOL)prefersStatusBarHidden { return YES; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskLandscape; }
- (void)dealloc { [self.displayLink invalidate]; }
@end

void *front_metal_layer(void) { return atomic_load_explicit(&g_layer_pointer, memory_order_acquire); }
uint64_t front_game_res(int docked) { return docked ? (900ull << 32) | 1600ull : (720ull << 32) | 1280ull; }
double front_display_refresh(char *name, size_t capacity) {
    pthread_mutex_lock(&g_display_lock);
    double refresh = g_refresh;
    if (name && capacity) snprintf(name, capacity, "%s", g_display_name);
    pthread_mutex_unlock(&g_display_lock); return refresh;
}
void botw_front_set_vsync_callback(void (*callback)(void)) {
    whole_front_state_bind_vsync(&g_state, callback);
    dispatch_async(dispatch_get_main_queue(), ^{ update_display_link(); });
}
extern void crash_thread_init(void);
static void *run_guest(void *opaque) {
    void (*guest)(void) = *(void (**)(void))opaque;
    free(opaque);
    @autoreleasepool { crash_thread_init(); guest(); }
    return NULL;
}
void front_run(void (*guest)(void)) {
    if (!guest || NSThread.isMainThread ||
        !atomic_load_explicit(&g_prepared, memory_order_acquire) ||
        !atomic_load_explicit(&g_host_running, memory_order_acquire)) {
        report(@"Invalid front_run call; guest was not entered."); return;
    }
    if (whole_front_state_wait_ready(&g_state) || !front_metal_layer()) {
        report(@"Guest entry was cancelled by platform failure or permanent stop."); return;
    }
    void (**context)(void) = malloc(sizeof *context);
    if (!context) { report(@"Could not allocate guest thread context."); return; }
    *context = guest;
    pthread_t thread;
    int result = start_thread(&thread, run_guest, context);
    if (result) {
        free(context); report([NSString stringWithFormat:@"Could not start guest thread: %s", strerror(result)]); return;
    }
    /* The original main.c guest_main initializes guest TLS, SDK and module inits. */
    result = pthread_join(thread, NULL);
    if (result) report([NSString stringWithFormat:@"Could not join guest thread: %s", strerror(result)]);
    botw_front_set_vsync_callback(NULL);
}
void front_save_failed(int error) {
    report([NSString stringWithFormat:@"Save transaction failed (%s). Previous committed progress is preserved.", strerror(error)]);
    dispatch_async(dispatch_get_main_queue(), ^{
        WholeController *controller = g_controller;
        if (!controller.view.window || !g_focused || controller.presentedViewController) return;
        [controller.overlay releaseInput];
        UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Progress was not saved"
            message:[NSString stringWithFormat:@"Save write failed: %s. Resolve the storage error before saving again.", strerror(error)]
            preferredStyle:UIAlertControllerStyleAlert];
        [alert addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
        [controller presentViewController:alert animated:YES completion:nil];
    });
}
int front_text_input(const char *title, const char *sub, const char *guide, const char *initial,
                     int max_chars, char *out, int out_cap) {
    (void)max_chars;
    if (!out || out_cap < 1 || NSThread.isMainThread) return -1;
    NSString *(^utf8)(const char *) = ^NSString *(const char *s) { return s ? [NSString stringWithUTF8String:s] : @""; };
    NSString *t = utf8(title), *s = utf8(sub), *g = utf8(guide), *i = utf8(initial);
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    __block int result = -1;
    dispatch_async(dispatch_get_main_queue(), ^{
        WholeController *controller = g_controller;
        if (!controller.view.window || !g_focused || controller.presentedViewController) {
            dispatch_semaphore_signal(completed); return;
        }
        [controller.overlay releaseInput]; input_focus(0);
        UIAlertController *alert = [UIAlertController alertControllerWithTitle:t message:s preferredStyle:UIAlertControllerStyleAlert];
        [alert addTextFieldWithConfigurationHandler:^(UITextField *field) { field.placeholder = g; field.text = i; }];
        __weak UIAlertController *weak_alert = alert;
        __block BOOL finished = NO;
        void (^finish)(int) = ^(int accepted) {
            if (finished) return;
            finished = YES; g_text_cancel = nil;
            if (accepted) {
                const char *value = weak_alert.textFields.firstObject.text.UTF8String ?: "";
                size_t length = strlen(value);
                /* Do not split a UTF-8 scalar when respecting the byte capacity. */
                if (length >= (size_t)out_cap) {
                    length = (size_t)out_cap - 1;
                    while (length && (((unsigned char)value[length] & 0xc0) == 0x80)) --length;
                }
                memcpy(out, value, length); out[length] = 0;
            }
            result = accepted;
            dispatch_semaphore_signal(completed);
            dispatch_async(dispatch_get_main_queue(), ^{ apply_lifecycle(); });
        };
        g_text_cancel = ^{ [weak_alert dismissViewControllerAnimated:NO completion:nil]; finish(0); };
        update_display_link();
        [alert addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:^(UIAlertAction *a) { (void)a; finish(0); }]];
        [alert addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) { (void)a; finish(1); }]];
        [controller presentViewController:alert animated:YES completion:nil];
    });
    dispatch_semaphore_wait(completed, DISPATCH_TIME_FOREVER);
    return result;
}

static void retire_frontend(void) {
    g_foreground = g_focused = NO; apply_lifecycle();
    [g_controller.overlay releaseInput]; input_focus(0);
    whole_front_state_stop(&g_state);
    [g_controller.displayLink invalidate];
    atomic_store_explicit(&g_layer_pointer, NULL, memory_order_release);
    /* No unload or free: host/guest workers and cached Metal objects may be live. */
}
@interface WholeSceneDelegate : UIResponder <UIWindowSceneDelegate>
@property(nonatomic, strong) UIWindow *window;
@end
@implementation WholeSceneDelegate
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    (void)session; (void)options;
    if (![scene isKindOfClass:UIWindowScene.class]) return;
    self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
    self.window.rootViewController = [WholeController new]; [self.window makeKeyAndVisible];
}
- (void)sceneWillEnterForeground:(UIScene *)scene { (void)scene; g_foreground = YES; g_focused = NO; apply_lifecycle(); }
- (void)sceneDidBecomeActive:(UIScene *)scene { (void)scene; g_foreground = g_focused = YES; apply_lifecycle(); }
- (void)sceneWillResignActive:(UIScene *)scene { (void)scene; g_focused = NO; apply_lifecycle(); }
- (void)sceneDidEnterBackground:(UIScene *)scene { (void)scene; g_foreground = g_focused = NO; apply_lifecycle(); }
- (void)sceneDidDisconnect:(UIScene *)scene { (void)scene; retire_frontend(); }
@end
@interface WholeAppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@end
@implementation WholeAppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    (void)application; (void)options;
    if (![NSBundle.mainBundle objectForInfoDictionaryKey:@"UIApplicationSceneManifest"]) {
        self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
        self.window.rootViewController = [WholeController new]; [self.window makeKeyAndVisible];
    }
    NSNotificationCenter *nc = NSNotificationCenter.defaultCenter;
    [nc addObserver:self selector:@selector(audioInterruption:) name:AVAudioSessionInterruptionNotification object:nil];
    [nc addObserver:self selector:@selector(audioReset:) name:AVAudioSessionMediaServicesWereResetNotification object:nil];
    return YES;
}
- (UISceneConfiguration *)application:(UIApplication *)application configurationForConnectingSceneSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    (void)application; (void)options;
    UISceneConfiguration *configuration = [[UISceneConfiguration alloc] initWithName:@"WholeHost" sessionRole:session.role];
    configuration.delegateClass = WholeSceneDelegate.class; return configuration;
}
- (void)applicationDidBecomeActive:(UIApplication *)application { (void)application; if (self.window) { g_foreground = g_focused = YES; apply_lifecycle(); } }
- (void)applicationWillResignActive:(UIApplication *)application { (void)application; if (self.window) { g_focused = NO; apply_lifecycle(); } }
- (void)applicationWillEnterForeground:(UIApplication *)application { (void)application; if (self.window) { g_foreground = YES; apply_lifecycle(); } }
- (void)applicationDidEnterBackground:(UIApplication *)application { (void)application; if (self.window) { g_foreground = g_focused = NO; apply_lifecycle(); } }
- (void)applicationWillTerminate:(UIApplication *)application { (void)application; retire_frontend(); }
- (void)audioInterruption:(NSNotification *)notification {
    if (!NSThread.isMainThread) { dispatch_async(dispatch_get_main_queue(), ^{ [self audioInterruption:notification]; }); return; }
    NSUInteger type = [notification.userInfo[AVAudioSessionInterruptionTypeKey] unsignedIntegerValue];
    if (type == AVAudioSessionInterruptionTypeBegan) g_audio_interrupted = YES;
    else if (type == AVAudioSessionInterruptionTypeEnded) {
        NSUInteger options = [notification.userInfo[AVAudioSessionInterruptionOptionKey] unsignedIntegerValue];
        g_audio_interrupted = !(options & AVAudioSessionInterruptionOptionShouldResume);
    }
    apply_lifecycle();
}
- (void)audioReset:(NSNotification *)notification {
    (void)notification;
    if (!NSThread.isMainThread) { dispatch_async(dispatch_get_main_queue(), ^{ [self audioReset:notification]; }); return; }
    g_media_lost = YES; g_audio_active = NO; apply_lifecycle();
    report(@"Audio media services were reset. Restart to reopen the complete backend.");
}
@end

int whole_frontend_main(int argc, char **argv, const whole_frontend_runtime *runtime) {
    if (!NSThread.isMainThread) return EINVAL;
    if (!runtime) g_configuration_error = @"Runtime is unconfigured. Complete host and guest execution are blocked.";
    else if (!whole_frontend_runtime_contract_valid(runtime))
        g_configuration_error = @"Runtime configuration is incomplete. Host execution is blocked.";
    else {
        g_runtime = *runtime;
        g_host_argv = calloc((size_t)runtime->host_argc + 1, sizeof *g_host_argv);
        if (!g_host_argv) return ENOMEM;
        for (int i = 0; i < runtime->host_argc; ++i) {
            if (!runtime->host_argv[i] || !(g_host_argv[i] = strdup(runtime->host_argv[i]))) {
                for (int j = 0; j < i; ++j) free(g_host_argv[j]);
                free(g_host_argv); g_host_argv = NULL;
                g_configuration_error = @"Runtime arguments are invalid. Host execution is blocked."; break;
            }
        }
        if (g_host_argv) { g_runtime.host_argv = (const char *const *)g_host_argv; g_configured = 1; }
    }
    return UIApplicationMain(argc, argv, nil, NSStringFromClass(WholeAppDelegate.class));
}
