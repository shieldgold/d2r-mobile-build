/* AppKit test frontend for the shared native runtime. No game assets. */
#import <AppKit/AppKit.h>
#import <Metal/Metal.h>
#import <QuartzCore/CADisplayLink.h>
#import <QuartzCore/CAMetalLayer.h>
#include "runtime.h"
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

/* Root runtime supplies the display clock; no timer fallback is permitted. */
extern void botw_host_display_tick(double target_timestamp_seconds);

static char *game_root; /* Process lifetime, including detached guest threads. */
static void host_event(const char *message) {
    fprintf(stdout, "%s\n", message); fflush(stdout);
}
__attribute__((noreturn))
static void exit_process(int status) {
    /* A failed cooperative stop may retain guest workers. Do not release their
     * view/layer or backend state while they can still access those objects. */
    fprintf(stdout, "mac_process_exit=%d guest_resources_released=false\n", status);
    fflush(stdout); fflush(stderr); _Exit(status);
}
static void *run_guest(void *unused) {
    (void)unused;
    @autoreleasepool {
        host_event("host_mode=boot-game frontend=appkit graphics_backend=metal gameplay_verified=false");
        int status = botw_host_run_mode(game_root, game_root, host_event, BOTW_HOST_BOOT_GAME);
        fprintf(stdout, "mac_host_return=%d gameplay_verified=false\n", status); fflush(stdout);
        dispatch_async(dispatch_get_main_queue(), ^{ exit_process(status); });
    }
    return NULL;
}

@interface BOTWMacMetalView : NSView
- (void)updateDrawableSize;
@end
@implementation BOTWMacMetalView
- (CALayer *)makeBackingLayer { return [CAMetalLayer layer]; }
- (void)updateDrawableSize {
    CAMetalLayer *metal = (CAMetalLayer *)self.layer;
    if (!metal) return;
    CGFloat scale = self.window.backingScaleFactor;
    if (scale <= 0) scale = 1;
    metal.contentsScale = scale;
    metal.drawableSize = CGSizeMake(MAX(1, self.bounds.size.width * scale), MAX(1, self.bounds.size.height * scale));
}
- (void)layout { [super layout]; [self updateDrawableSize]; }
- (void)viewDidMoveToWindow { [super viewDidMoveToWindow]; [self updateDrawableSize]; }
- (void)viewDidChangeBackingProperties { [super viewDidChangeBackingProperties]; [self updateDrawableSize]; }
@end

@interface BOTWMacDelegate : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) BOTWMacMetalView *metalView;
@property(nonatomic, strong) CADisplayLink *displayLink;
@property(nonatomic, assign) BOOL receivedDisplayTick;
@end
@implementation BOTWMacDelegate
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device) { host_event("error=mac_metal_device_unavailable"); exit_process(1); }
    NSRect bounds = NSMakeRect(0, 0, 1280, 720);
    self.window = [[NSWindow alloc] initWithContentRect:bounds
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"BOTW native — macOS test";
    self.window.delegate = self;
    self.window.releasedWhenClosed = NO;
    self.window.contentMinSize = NSMakeSize(640, 360);
    self.metalView = [[BOTWMacMetalView alloc] initWithFrame:bounds];
    self.metalView.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    self.metalView.wantsLayer = YES;
    CAMetalLayer *layer = (CAMetalLayer *)self.metalView.layer;
    layer.device = device; layer.pixelFormat = MTLPixelFormatBGRA8Unorm; layer.framebufferOnly = NO;
    self.window.contentView = self.metalView;
    [self.metalView updateDrawableSize];
    if (!layer || botw_host_set_graphics_layer((__bridge void *)layer)) {
        host_event("error=mac_graphics_layer_configuration_failed"); exit_process(1);
    }
    [self.window center]; [self.window makeKeyAndOrderFront:nil];
    [NSApplication.sharedApplication activateIgnoringOtherApps:YES];
    /* NSView's real display link follows this window when it changes screens.
     * SDK NSView.h promises no callbacks while hidden or off all displays. */
    self.displayLink = [self.metalView displayLinkWithTarget:self selector:@selector(displayTick:)];
    if (!self.displayLink) { host_event("error=mac_display_link_unavailable"); exit_process(1); }
    [self.displayLink addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
    host_event("mac_window_ready=true width=1280 height=720 display_clock=NSView.CADisplayLink");
    pthread_attr_t attributes; pthread_t thread;
    int result = pthread_attr_init(&attributes);
    if (result) { host_event("error=mac_pthread_attr_init_failed"); exit_process(1); }
    result = pthread_attr_setstacksize(&attributes, 64u * 1024u * 1024u);
    if (!result) result = pthread_attr_setdetachstate(&attributes, PTHREAD_CREATE_DETACHED);
    if (!result) result = pthread_create(&thread, &attributes, run_guest, NULL);
    pthread_attr_destroy(&attributes);
    if (result) {
        fprintf(stderr, "error=mac_guest_thread_start_failed: %s\n", strerror(result)); exit_process(1);
    }
}
- (void)displayTick:(CADisplayLink *)sender {
    if (!self.receivedDisplayTick) {
        self.receivedDisplayTick = YES;
        fprintf(stdout, "mac_display_link_started=true target_timestamp=%.9f gameplay_verified=false\n", sender.targetTimestamp);
        fflush(stdout);
    }
    botw_host_display_tick(sender.targetTimestamp);
    botw_host_refresh_input();
}
- (BOOL)windowShouldClose:(NSWindow *)sender { (void)sender; exit_process(0); }
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender { (void)sender; exit_process(0); }
- (void)applicationDidBecomeActive:(NSNotification *)notification {
    (void)notification; botw_host_set_applet_state(1, 1);
}
- (void)applicationDidResignActive:(NSNotification *)notification {
    (void)notification; botw_host_set_applet_state(1, 0);
}
- (void)applicationDidHide:(NSNotification *)notification {
    (void)notification; botw_host_set_applet_state(0, 0);
}
@end

static BOTWMacDelegate *application_delegate; /* NSApplication's delegate is weak. */
int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        puts("Usage: botw-mac ROOT\nROOT contains the prepared botw/ modules and local resource backends."); return 0;
    }
    if (argc != 2 || argv[1][0] == '-') { fprintf(stderr, "Usage: botw-mac ROOT\n"); return 64; }
    game_root = realpath(argv[1], NULL);
    if (!game_root) { fprintf(stderr, "error=mac_root_unavailable: %s\n", strerror(errno)); return 66; }
    struct stat info;
    if (stat(game_root, &info) || !S_ISDIR(info.st_mode)) {
        fprintf(stderr, "error=mac_root_is_not_directory\n"); free(game_root); return 66;
    }
    if (botw_host_set_address_space(39)) {
        fprintf(stderr, "error=mac_address_space_configuration_failed\n"); free(game_root); return 1;
    }
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        [application setActivationPolicy:NSApplicationActivationPolicyRegular];
        application_delegate = [BOTWMacDelegate new]; application.delegate = application_delegate;
        [application run];
    }
    exit_process(0);
}
