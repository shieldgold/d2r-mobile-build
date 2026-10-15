/* Development frontend for the real BOTW native bootstrap. No game assets. */
#import <UIKit/UIKit.h>
#import <QuartzCore/CAMetalLayer.h>
#import <Metal/Metal.h>
#import <AVFAudio/AVAudioSession.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "runtime.h"
#include "memory_policy.h"
#import "BOTWEventLog.h"
#include "graphics.h"
#include "BOTWInputOverlay.h"

@interface BOTWMetalView : UIView
@end
@implementation BOTWMetalView
+ (Class)layerClass { return CAMetalLayer.class; }
- (void)layoutSubviews {
    [super layoutSubviews];
    CAMetalLayer *layer = (CAMetalLayer *)self.layer;
    CGFloat scale = self.traitCollection.displayScale;
    if (scale <= 0) scale = 1;
    layer.contentsScale = scale;
    layer.drawableSize = CGSizeMake(self.bounds.size.width * scale, self.bounds.size.height * scale);
}
@end

@interface BOTWController : UIViewController
@property(nonatomic, strong) UITextView *logView;
@property(nonatomic, assign) BOOL started;
@property(nonatomic, assign) BOOL bootstrapReady;
@property(nonatomic, strong) BOTWMetalView *metalView;
@property(nonatomic, strong) BOTWInputOverlay *inputOverlay;
@property(nonatomic, strong) CADisplayLink *frameObserver;
@property(nonatomic, assign) BOOL firstFrameObserved;
- (void)appendEvent:(NSString *)message;
- (void)replaceLogText:(NSString *)text reveal:(BOOL)reveal;
- (void)startGuestIfReady;
@end

static __weak BOTWController *activeController;
static NSString *bundleRoot;
static NSString *documentsRoot;
static botw_input *frontendInput; /* Process lifetime: guest threads borrow it. */
static BOOL audioSessionPrepared, audioInterrupted, audioServicesLost;
static BOTWEventLog *frontendEventLog;

static void hostEvent(const char *message) {
    fprintf(stdout, "%s\n", message);
    fflush(stdout);
    @autoreleasepool {
        NSString *text = [NSString stringWithUTF8String:message];
        if (!text) text = @"日志含无法解码的字节";
        [frontendEventLog appendMessage:text reveal:[text hasPrefix:@"error="] ||
            [text containsString:@"当前版本尚不能游玩"]];
    }
}

static BOOL activateHostAudioSession(void) {
    if (audioInterrupted || audioServicesLost) return NO;
    botw_host_set_audio_session_active(0);
    AVAudioSession *session = AVAudioSession.sharedInstance;
    NSError *error = nil;
    BOOL success = [session setCategory:AVAudioSessionCategoryPlayback mode:AVAudioSessionModeDefault
        options:AVAudioSessionCategoryOptionMixWithOthers error:&error] && [session setActive:YES error:&error];
    audioSessionPrepared = success;
    botw_host_set_audio_session_active(success);
    if (!success) {
        hostEvent("error=unable to activate actual host audio session");
        if (error) hostEvent(error.localizedDescription.UTF8String);
    }
    return success;
}
static void pauseHostAudioSession(void) {
    /* Stop RemoteIO before deactivating its session. Queued PCM stays owned by
     * the audio module; lifecycle changes never fabricate release events. */
    botw_host_set_audio_session_active(0);
    if (!audioSessionPrepared) return;
    audioSessionPrepared = NO;
    NSError *error = nil;
    if (![AVAudioSession.sharedInstance setActive:NO
            withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:&error] && error)
        hostEvent(error.localizedDescription.UTF8String);
}

static void saveFirstPresentedFrame(void) {
    uint32_t width = 0, height = 0; uint64_t number = 0;
    botw_graphics_copy_latest_frame(NULL, 0, &width, &height, &number);
    if (!width || !height || width > 4096 || height > 4096) {
        hostEvent("game_draw_capture_saved=false reason=no-bounded-presented-image"); return;
    }
    size_t bytes = (size_t)width * height * 4;
    void *rgba = malloc(bytes);
    if (!rgba) { hostEvent("game_draw_capture_saved=false reason=allocation"); return; }
    if (!botw_graphics_copy_latest_frame(rgba, bytes, &width, &height, &number)) {
        free(rgba); hostEvent("game_draw_capture_saved=false reason=frame-changed"); return;
    }
    NSData *pixels = [NSData dataWithBytesNoCopy:rgba length:bytes freeWhenDone:YES];
    CGDataProviderRef provider = CGDataProviderCreateWithCFData((__bridge CFDataRef)pixels);
    CGColorSpaceRef color = CGColorSpaceCreateDeviceRGB();
    CGImageRef image = provider && color ? CGImageCreate(width, height, 8, 32, (size_t)width * 4, color,
        (CGBitmapInfo)kCGImageAlphaNoneSkipLast, provider, NULL, false, kCGRenderingIntentDefault) : NULL;
    NSData *png = image ? UIImagePNGRepresentation([UIImage imageWithCGImage:image]) : nil;
    NSString *path = [documentsRoot stringByAppendingPathComponent:@"first-game-frame.png"];
    NSError *error = nil;
    BOOL saved = png && [png writeToFile:path options:NSDataWritingAtomic error:&error];
    char result[256];
    snprintf(result, sizeof result, "game_draw_capture_saved=%s frame=%llu width=%u height=%u gameplay_verified=false",
        saved ? "true" : "false", (unsigned long long)number, width, height);
    hostEvent(result);
    if (error) hostEvent(error.localizedDescription.UTF8String);
    if (image) CGImageRelease(image);
    if (color) CGColorSpaceRelease(color);
    if (provider) CGDataProviderRelease(provider);
}

static void *runGuest(void *unused) {
    (void)unused;
    @autoreleasepool {
        hostEvent("host_mode=boot-game; graphics_backend=metal; gameplay_verified=false");
        int status = botw_host_run_mode(bundleRoot.fileSystemRepresentation,
            documentsRoot.fileSystemRepresentation, hostEvent, BOTW_HOST_BOOT_GAME);
        char result[160];
        snprintf(result, sizeof result, "host_return=%d; gameplay_verified=false", status);
        hostEvent(result);
        hostEvent(status == 0 ? "原程序已返回；尚未验证游戏画面与操作。" :
            "启动停在上述兼容层边界；当前版本尚不能游玩。日志已保存到 boot.log。");
    }
    return NULL;
}

@implementation BOTWController
- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.blackColor;
    self.metalView = [[BOTWMetalView alloc] initWithFrame:self.view.bounds];
    self.metalView.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    CAMetalLayer *layer = (CAMetalLayer *)self.metalView.layer;
    layer.device = MTLCreateSystemDefaultDevice();
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = NO;
    [self.view addSubview:self.metalView];
    self.logView = [[UITextView alloc] initWithFrame:self.view.bounds];
    self.logView.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    self.logView.editable = NO;
    self.logView.userInteractionEnabled = NO;
    self.logView.backgroundColor = [UIColor.blackColor colorWithAlphaComponent:0.72];
    self.logView.textColor = UIColor.whiteColor;
    self.logView.font = [UIFont monospacedSystemFontOfSize:12 weight:UIFontWeightRegular];
    self.logView.text = @"BOTW 原生兼容层开发版\n正在尝试执行本机嵌入的原程序。\n游戏画面、操作与存档尚未验证。\n\n";
    __weak BOTWController *weakController = self;
    frontendEventLog = [[BOTWEventLog alloc] initWithCharacterLimit:65536 handler:^(NSString *text, BOOL reveal) {
        [weakController replaceLogText:text reveal:reveal];
    }];
    [frontendEventLog appendMessage:self.logView.text reveal:NO];
    [self.view addSubview:self.logView];
    frontendInput = botw_input_create();
    if (frontendInput) self.inputOverlay = [BOTWInputOverlay attachToView:self.view input:frontendInput];
    self.frameObserver = [CADisplayLink displayLinkWithTarget:self selector:@selector(observeFrame:)];
    [self.frameObserver addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
    activeController = self;
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(audioInterruption:)
        name:AVAudioSessionInterruptionNotification object:AVAudioSession.sharedInstance];
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(audioServicesReset:)
        name:AVAudioSessionMediaServicesWereResetNotification object:AVAudioSession.sharedInstance];
}
- (void)observeFrame:(CADisplayLink *)sender {
    botw_host_display_tick(sender.targetTimestamp);
    botw_host_refresh_input();
    if (!self.firstFrameObserved && botw_graphics_presented_frame_count()) {
        self.firstFrameObserved = YES;
        hostEvent("first_translated_game_draw_presented=true gameplay_verified=false");
        saveFirstPresentedFrame();
        botw_graphics_set_capture_enabled(0);
        self.logView.hidden = YES;
    }
}
- (void)dealloc {
    [NSNotificationCenter.defaultCenter removeObserver:self];
    [self.frameObserver invalidate]; [self.inputOverlay invalidate];
}
- (void)audioInterruption:(NSNotification *)notification {
    if (!NSThread.isMainThread) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self audioInterruption:notification]; }); return;
    }
    NSUInteger kind = [notification.userInfo[AVAudioSessionInterruptionTypeKey] unsignedIntegerValue];
    if (kind == AVAudioSessionInterruptionTypeBegan) {
        audioInterrupted = YES; audioSessionPrepared = NO;
        botw_host_set_audio_session_active(0);
    } else if (kind == AVAudioSessionInterruptionTypeEnded) {
        audioInterrupted = NO;
        NSUInteger options = [notification.userInfo[AVAudioSessionInterruptionOptionKey] unsignedIntegerValue];
        if ((options & AVAudioSessionInterruptionOptionShouldResume) &&
                UIApplication.sharedApplication.applicationState == UIApplicationStateActive &&
                activateHostAudioSession()) [self startGuestIfReady];
    }
}
- (void)audioServicesReset:(NSNotification *)notification {
    (void)notification;
    if (!NSThread.isMainThread) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self audioServicesReset:notification]; }); return;
    }
    botw_host_set_audio_session_active(0); audioSessionPrepared = NO; audioServicesLost = YES;
    hostEvent("error=audio media services reset; restart the app to reopen audio");
}
- (void)appendEvent:(NSString *)message {
    [frontendEventLog appendMessage:message reveal:[message hasPrefix:@"error="] ||
        [message containsString:@"当前版本尚不能游玩"]];
}
- (void)replaceLogText:(NSString *)text reveal:(BOOL)reveal {
    self.logView.text = text;
    if (reveal) self.logView.hidden = NO;
    if (self.logView.text.length) {
        [self.logView scrollRangeToVisible:NSMakeRange(self.logView.text.length - 1, 1)];
    }
}
- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    if (self.started || self.bootstrapReady) return;
    bundleRoot = NSBundle.mainBundle.bundlePath;
    documentsRoot = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
    NSString *path = [documentsRoot stringByAppendingPathComponent:@"boot.log"];
    int descriptor = open(path.fileSystemRepresentation, O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (descriptor < 0) { [self appendEvent:@"无法创建 boot.log，启动已停止。"]; return; }
    fflush(stdout); fflush(stderr);
    if (dup2(descriptor, STDOUT_FILENO) < 0 || dup2(descriptor, STDERR_FILENO) < 0) {
        close(descriptor); [self appendEvent:@"无法接通启动日志，启动已停止。"]; return;
    }
    close(descriptor);
    setvbuf(stdout, NULL, _IOLBF, 0); setvbuf(stderr, NULL, _IOLBF, 0);
    hostEvent("\n=== BOTW native bootstrap ===");
    id configuredBits = [NSBundle.mainBundle objectForInfoDictionaryKey:@"BOTWGuestAddressBits"];
    if (configuredBits && ![configuredBits isKindOfClass:NSNumber.class]) {
        hostEvent("error=invalid packaged guest address policy"); return;
    }
    unsigned addressBits = configuredBits ? [configuredBits unsignedIntValue] : 36;
    if (botw_host_set_address_space(addressBits)) {
        hostEvent("error=invalid packaged guest address policy"); return;
    }
    if (configurePackagedHostMemoryPolicy(
            [NSBundle.mainBundle objectForInfoDictionaryKey:@"BOTWHostMemoryPolicy"])) {
        hostEvent("error=invalid packaged host memory policy"); return;
    }
    if (!frontendInput || !((CAMetalLayer *)self.metalView.layer).device ||
        botw_host_set_input(frontendInput) || botw_host_set_graphics_layer((__bridge void *)self.metalView.layer)) {
        hostEvent("error=unable to configure Metal/input frontend"); return;
    }
    self.bootstrapReady = YES;
    [self startGuestIfReady];
}
- (void)startGuestIfReady {
    if (!self.bootstrapReady || self.started || audioInterrupted || audioServicesLost) return;
    if (!audioSessionPrepared && !activateHostAudioSession()) return;
    pthread_attr_t attributes; pthread_t thread;
    int result = pthread_attr_init(&attributes);
    if (result) { hostEvent("error=pthread_attr_init"); return; }
    result = pthread_attr_setstacksize(&attributes, 64u * 1024u * 1024u);
    if (!result) result = pthread_attr_setdetachstate(&attributes, PTHREAD_CREATE_DETACHED);
    if (!result) result = pthread_create(&thread, &attributes, runGuest, NULL);
    pthread_attr_destroy(&attributes);
    if (result) hostEvent("error=unable to start guest thread");
    else self.started = YES;
}
@end

@interface BOTWSceneDelegate : UIResponder <UIWindowSceneDelegate>
@property(nonatomic, strong) UIWindow *window;
@end
@implementation BOTWSceneDelegate
- (void)sceneDidBecomeActive:(UIScene *)scene {
    (void)scene;
    UIApplication.sharedApplication.idleTimerDisabled = YES;
    botw_host_set_applet_state(1, 1);
    if (!audioSessionPrepared && !audioInterrupted) (void)activateHostAudioSession();
    [activeController startGuestIfReady];
}
- (void)sceneWillResignActive:(UIScene *)scene {
    (void)scene; botw_host_set_applet_state(1, 0); pauseHostAudioSession();
}
- (void)sceneWillEnterForeground:(UIScene *)scene { (void)scene; botw_host_set_applet_state(1, 0); }
- (void)sceneDidEnterBackground:(UIScene *)scene {
    (void)scene;
    UIApplication.sharedApplication.idleTimerDisabled = NO;
    botw_host_set_applet_state(0, 0);
    pauseHostAudioSession();
}
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    (void)session; (void)options;
    if (![scene isKindOfClass:UIWindowScene.class]) return;
    self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
    self.window.rootViewController = [BOTWController new];
    [self.window makeKeyAndVisible];
}
@end

@interface BOTWDelegate : UIResponder <UIApplicationDelegate>
@end
@implementation BOTWDelegate
- (UISceneConfiguration *)application:(UIApplication *)application
    configurationForConnectingSceneSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    (void)application; (void)options;
    UISceneConfiguration *configuration = [[UISceneConfiguration alloc] initWithName:@"BOTW" sessionRole:session.role];
    configuration.delegateClass = BOTWSceneDelegate.class;
    return configuration;
}
@end

int main(int argc, char **argv) {
    @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass(BOTWDelegate.class)); }
}
