/* Development frontend for the real BOTW native bootstrap. No game assets. */
#import <UIKit/UIKit.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>
#include "runtime.h"

@interface BOTWController : UIViewController
@property(nonatomic, strong) UITextView *logView;
@property(nonatomic, assign) BOOL started;
- (void)appendEvent:(NSString *)message;
@end

static __weak BOTWController *activeController;
static NSString *bundleRoot;
static NSString *documentsRoot;

static void hostEvent(const char *message) {
    fprintf(stdout, "%s\n", message);
    fflush(stdout);
    NSString *text = [NSString stringWithUTF8String:message];
    if (!text) text = @"日志含无法解码的字节";
    dispatch_async(dispatch_get_main_queue(), ^{ [activeController appendEvent:text]; });
}

static void *runGuest(void *unused) {
    (void)unused;
    @autoreleasepool {
        hostEvent("host_mode=boot-game; graphics_ready=false; gameplay_verified=false");
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
    self.view.backgroundColor = UIColor.systemBackgroundColor;
    self.logView = [[UITextView alloc] initWithFrame:self.view.bounds];
    self.logView.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    self.logView.editable = NO;
    self.logView.font = [UIFont monospacedSystemFontOfSize:12 weight:UIFontWeightRegular];
    self.logView.text = @"BOTW 原生兼容层开发版\n正在尝试执行本机嵌入的原程序。\n游戏画面、操作与存档尚未验证。\n\n";
    [self.view addSubview:self.logView];
    activeController = self;
}
- (void)appendEvent:(NSString *)message {
    self.logView.text = [self.logView.text stringByAppendingFormat:@"%@\n", message];
    if (self.logView.text.length) {
        [self.logView scrollRangeToVisible:NSMakeRange(self.logView.text.length - 1, 1)];
    }
}
- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    if (self.started) return;
    self.started = YES;
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
    pthread_attr_t attributes; pthread_t thread;
    int result = pthread_attr_init(&attributes);
    if (result) { hostEvent("error=pthread_attr_init"); return; }
    result = pthread_attr_setstacksize(&attributes, 64u * 1024u * 1024u);
    if (!result) result = pthread_attr_setdetachstate(&attributes, PTHREAD_CREATE_DETACHED);
    if (!result) result = pthread_create(&thread, &attributes, runGuest, NULL);
    pthread_attr_destroy(&attributes);
    if (result) hostEvent("error=unable to start guest thread");
}
@end

@interface BOTWSceneDelegate : UIResponder <UIWindowSceneDelegate>
@property(nonatomic, strong) UIWindow *window;
@end
@implementation BOTWSceneDelegate
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
