#import <Cocoa/Cocoa.h>
#import <CoreVideo/CoreVideo.h>
#import <Metal/Metal.h>
#import <QuartzCore/QuartzCore.h>

#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "botw_front.h"
#include "botw_hid.h"
#include "front.h"

enum { BOTW_GUEST_STACK_SIZE = 8 * 1024 * 1024 };

static CAMetalLayer *g_metal_layer;
static NSWindow *g_window;
static _Atomic int g_guest_finished;
static _Atomic int g_user_closed;
static _Atomic(void (*)(void)) g_vsync_callback;

static pthread_mutex_t g_display_lock = PTHREAD_MUTEX_INITIALIZER;
static double g_display_refresh;
static char g_display_name[128];
static CVDisplayLinkRef g_display_link;
static pthread_mutex_t g_headless_timer_lock = PTHREAD_MUTEX_INITIALIZER;
static dispatch_source_t g_headless_timer;
static int g_input_focused = -1;

static void set_input_focus(int focused) {
    focused = focused ? 1 : 0;
    if (g_input_focused == focused) return;
    g_input_focused = focused;
    botw_input_focus(focused);
}

static void stop_app_event_loop(void) {
    [NSApp stop:nil];
    NSEvent *event = [NSEvent otherEventWithType:NSEventTypeApplicationDefined
                                        location:NSZeroPoint
                                   modifierFlags:0
                                       timestamp:NSProcessInfo.processInfo.systemUptime
                                    windowNumber:0
                                         context:nil
                                         subtype:0
                                           data1:0
                                           data2:0];
    [NSApp postEvent:event atStart:NO];
}

static CVReturn display_link_tick(CVDisplayLinkRef display_link,
                                  const CVTimeStamp *now,
                                  const CVTimeStamp *output_time,
                                  CVOptionFlags flags_in,
                                  CVOptionFlags *flags_out,
                                  void *context) {
    (void)display_link;
    (void)now;
    (void)output_time;
    (void)flags_in;
    (void)flags_out;
    (void)context;
    void (*callback)(void) = atomic_load_explicit(&g_vsync_callback, memory_order_acquire);
    if (callback) callback();
    return kCVReturnSuccess;
}

static void stop_display_link(void) {
    if (!g_display_link) return;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    CVDisplayLinkStop(g_display_link);
#pragma clang diagnostic pop
    CVDisplayLinkRelease(g_display_link);
    g_display_link = NULL;
}

static void rebuild_display_link(void) {
    stop_display_link();
    if (!atomic_load_explicit(&g_vsync_callback, memory_order_acquire)) return;

    NSScreen *screen = g_window.screen ? g_window.screen : NSScreen.mainScreen;
    if (!screen) return;
    CGDirectDisplayID display_id = [screen.deviceDescription[@"NSScreenNumber"] unsignedIntValue];

#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    CVReturn result = CVDisplayLinkCreateWithCGDisplay(display_id, &g_display_link);
    if (result == kCVReturnSuccess)
        result = CVDisplayLinkSetOutputCallback(g_display_link, display_link_tick, NULL);
    if (result == kCVReturnSuccess) result = CVDisplayLinkStart(g_display_link);
#pragma clang diagnostic pop

    if (result != kCVReturnSuccess) {
        fprintf(stderr, "BotW frontend: could not start the display link (%d).\n", result);
        stop_display_link();
    }
}

static void start_headless_timer(void) {
    pthread_mutex_lock(&g_headless_timer_lock);
    if (!g_headless_timer) {
        dispatch_queue_t queue = dispatch_queue_create("dev.botw-mobile.vsync", DISPATCH_QUEUE_SERIAL);
        g_headless_timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, queue);
        dispatch_source_set_timer(g_headless_timer, dispatch_time(DISPATCH_TIME_NOW, 0),
                                  NSEC_PER_SEC / 60, NSEC_PER_MSEC / 4);
        dispatch_source_set_event_handler(g_headless_timer, ^{
            void (*callback)(void) = atomic_load_explicit(&g_vsync_callback, memory_order_acquire);
            if (callback) callback();
        });
        dispatch_resume(g_headless_timer);
        fprintf(stderr, "BotW frontend: using the 60 Hz monotonic headless vsync timer.\n");
    }
    pthread_mutex_unlock(&g_headless_timer_lock);
}

static void stop_headless_timer(void) {
    pthread_mutex_lock(&g_headless_timer_lock);
    if (g_headless_timer) {
        dispatch_source_cancel(g_headless_timer);
        g_headless_timer = nil;
    }
    pthread_mutex_unlock(&g_headless_timer_lock);
}

void botw_front_set_vsync_callback(void (*callback)(void)) {
    atomic_store_explicit(&g_vsync_callback, callback, memory_order_release);
    if (g_metal_layer) {
        if (NSThread.isMainThread)
            rebuild_display_link();
        else
            dispatch_async(dispatch_get_main_queue(), ^{ rebuild_display_link(); });
        return;
    }

    if (callback)
        start_headless_timer();
    else
        stop_headless_timer();
}

@interface BotWMetalView : NSView
- (void)updateDrawableSize;
@end

@implementation BotWMetalView

- (BOOL)acceptsFirstResponder {
    return YES;
}

- (void)keyDown:(NSEvent *)event {
    botw_input_key(event.keyCode, 1);
}

- (void)keyUp:(NSEvent *)event {
    botw_input_key(event.keyCode, 0);
}

- (void)flagsChanged:(NSEvent *)event {
    if (event.keyCode == 56 || event.keyCode == 60) {
        int down = CGEventSourceKeyState(kCGEventSourceStateCombinedSessionState, (CGKeyCode)event.keyCode);
        botw_input_key(event.keyCode, down);
    }
}

- (void)mouseDown:(NSEvent *)event {
    botw_input_mouse_button((unsigned)event.buttonNumber, 1);
}

- (void)mouseUp:(NSEvent *)event {
    botw_input_mouse_button((unsigned)event.buttonNumber, 0);
}

- (void)rightMouseDown:(NSEvent *)event {
    botw_input_mouse_button((unsigned)event.buttonNumber, 1);
}

- (void)rightMouseUp:(NSEvent *)event {
    botw_input_mouse_button((unsigned)event.buttonNumber, 0);
}

- (void)otherMouseDown:(NSEvent *)event {
    botw_input_mouse_button((unsigned)event.buttonNumber, 1);
}

- (void)otherMouseUp:(NSEvent *)event {
    botw_input_mouse_button((unsigned)event.buttonNumber, 0);
}

- (void)mouseMoved:(NSEvent *)event {
    botw_input_mouse_delta(event.deltaX, event.deltaY);
}

- (void)mouseDragged:(NSEvent *)event {
    botw_input_mouse_delta(event.deltaX, event.deltaY);
}

- (void)rightMouseDragged:(NSEvent *)event {
    botw_input_mouse_delta(event.deltaX, event.deltaY);
}

- (void)otherMouseDragged:(NSEvent *)event {
    botw_input_mouse_delta(event.deltaX, event.deltaY);
}

- (CALayer *)makeBackingLayer {
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = MTLCreateSystemDefaultDevice();
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = YES;
    layer.opaque = YES;
    return layer;
}

- (instancetype)initWithFrame:(NSRect)frame {
    self = [super initWithFrame:frame];
    if (self) {
        self.wantsLayer = YES;
        self.layerContentsRedrawPolicy = NSViewLayerContentsRedrawNever;
        g_metal_layer = (CAMetalLayer *)self.layer;
        [self updateDrawableSize];
    }
    return self;
}

- (void)layout {
    [super layout];
    [self updateDrawableSize];
}

- (void)viewDidChangeBackingProperties {
    [super viewDidChangeBackingProperties];
    [self updateDrawableSize];
}

- (void)updateDrawableSize {
    CAMetalLayer *layer = (CAMetalLayer *)self.layer;
    CGFloat scale = self.window.backingScaleFactor;
    if (scale <= 0) scale = NSScreen.mainScreen.backingScaleFactor;
    if (scale <= 0) scale = 1;

    NSSize size = self.bounds.size;
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    layer.contentsScale = scale;
    layer.drawableSize = CGSizeMake(round(size.width * scale), round(size.height * scale));
    [CATransaction commit];
}

@end


static void note_display(void) {
    NSScreen *screen = g_window.screen ? g_window.screen : NSScreen.mainScreen;
    if (!screen) return;

    CGDirectDisplayID display_id = [screen.deviceDescription[@"NSScreenNumber"] unsignedIntValue];
    CGDisplayModeRef mode = CGDisplayCopyDisplayMode(display_id);
    double refresh = mode ? CGDisplayModeGetRefreshRate(mode) : 0;
    if (mode) CGDisplayModeRelease(mode);
    if (refresh <= 0) {
        if (@available(macOS 12.0, *)) refresh = screen.maximumFramesPerSecond;
    }

    const char *name = screen.localizedName.UTF8String;
    if (!name) name = "";
    pthread_mutex_lock(&g_display_lock);
    g_display_refresh = refresh;
    snprintf(g_display_name, sizeof g_display_name, "%s", name);
    pthread_mutex_unlock(&g_display_lock);
}

@interface BotWWindowDelegate : NSObject <NSWindowDelegate>
@end

@implementation BotWWindowDelegate

- (BOOL)windowShouldClose:(NSWindow *)window {
    (void)window;
    set_input_focus(0);
    atomic_store_explicit(&g_user_closed, 1, memory_order_release);
    stop_app_event_loop();
    return YES;
}

- (void)windowDidBecomeKey:(NSNotification *)notification {
    (void)notification;
    if (NSApp.active) set_input_focus(1);
}

- (void)windowDidResignKey:(NSNotification *)notification {
    (void)notification;
    set_input_focus(0);
}

- (void)applicationDidBecomeActive:(NSNotification *)notification {
    (void)notification;
    if (g_window.isKeyWindow) set_input_focus(1);
}

- (void)applicationDidResignActive:(NSNotification *)notification {
    (void)notification;
    set_input_focus(0);
}

- (void)windowDidChangeBackingProperties:(NSNotification *)notification {
    NSWindow *window = (NSWindow *)notification.object;
    BotWMetalView *view = (BotWMetalView *)window.contentView;
    [view updateDrawableSize];
}

- (void)windowDidChangeScreen:(NSNotification *)notification {
    NSWindow *window = (NSWindow *)notification.object;
    BotWMetalView *view = (BotWMetalView *)window.contentView;
    [view updateDrawableSize];
    note_display();
    rebuild_display_link();
}

- (void)screenParametersDidChange:(NSNotification *)notification {
    (void)notification;
    [(BotWMetalView *)g_window.contentView updateDrawableSize];
    note_display();
    rebuild_display_link();
}

@end


void *front_metal_layer(void) {
    return (__bridge void *)g_metal_layer;
}

uint64_t front_game_res(int docked) {
    return docked ? (900ull << 32) | 1600ull : (720ull << 32) | 1280ull;
}

double front_display_refresh(char *name, size_t capacity) {
    pthread_mutex_lock(&g_display_lock);
    double refresh = g_display_refresh;
    if (name && capacity) snprintf(name, capacity, "%s", g_display_name);
    pthread_mutex_unlock(&g_display_lock);
    return refresh;
}

int front_text_input(const char *title, const char *subtitle, const char *guide,
                     const char *initial, int max_chars, char *out, int out_capacity) {
    (void)title;
    (void)subtitle;
    (void)guide;
    (void)initial;
    (void)max_chars;
    (void)out;
    (void)out_capacity;

    static atomic_flag reported = ATOMIC_FLAG_INIT;
    if (!atomic_flag_test_and_set_explicit(&reported, memory_order_relaxed))
        fprintf(stderr, "BotW frontend: native text input is unavailable.\n");
    return -1;
}

typedef struct {
    void (*guest)(void);
} BotWGuestContext;

extern void crash_thread_init(void);

static void *run_guest(void *opaque) {
    BotWGuestContext *context = opaque;
    void (*guest)(void) = context->guest;
    free(context);
    @autoreleasepool {
        crash_thread_init();
        guest();
    }

    atomic_store_explicit(&g_guest_finished, 1, memory_order_release);
    dispatch_async(dispatch_get_main_queue(), ^{
        if (!atomic_load_explicit(&g_user_closed, memory_order_acquire)) stop_app_event_loop();
    });
    return NULL;
}

void front_run(void (*guest)(void)) {
    const char *window_enabled = getenv("DM_WINDOW");
    if (!window_enabled || strcmp(window_enabled, "1") != 0) {
        guest();
        botw_front_set_vsync_callback(NULL);
        return;
    }

    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];

        NSRect content_rect = NSMakeRect(0, 0, 1280, 720);
        NSWindowStyleMask style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                                  NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable;
        g_window = [[NSWindow alloc] initWithContentRect:content_rect
                                               styleMask:style
                                                 backing:NSBackingStoreBuffered
                                                   defer:NO];
        g_window.title = @"BotW Mobile";
        g_window.restorable = NO;
        g_window.contentAspectRatio = NSMakeSize(16, 9);
        g_window.contentMinSize = NSMakeSize(640, 360);
        g_window.backgroundColor = NSColor.blackColor;

        BotWMetalView *view = [[BotWMetalView alloc] initWithFrame:content_rect];
        g_window.contentView = view;

        static BotWWindowDelegate *delegate;
        delegate = [BotWWindowDelegate new];
        g_window.delegate = delegate;
        [NSNotificationCenter.defaultCenter addObserver:delegate
                                               selector:@selector(screenParametersDidChange:)
                                                   name:NSApplicationDidChangeScreenParametersNotification
                                                 object:nil];
        [NSNotificationCenter.defaultCenter addObserver:delegate
                                               selector:@selector(applicationDidBecomeActive:)
                                                   name:NSApplicationDidBecomeActiveNotification
                                                 object:nil];
        [NSNotificationCenter.defaultCenter addObserver:delegate
                                               selector:@selector(applicationDidResignActive:)
                                                   name:NSApplicationDidResignActiveNotification
                                                 object:nil];

        g_window.acceptsMouseMovedEvents = YES;
        botw_input_start();
        [g_window center];
        [g_window makeKeyAndOrderFront:nil];
        [g_window makeFirstResponder:view];
        [view updateDrawableSize];
        note_display();
        [NSApp activateIgnoringOtherApps:YES];

        BotWGuestContext *context = malloc(sizeof *context);
        if (!context) {
            fprintf(stderr, "BotW frontend: could not allocate guest thread context.\n");
            [g_window close];
            return;
        }
        context->guest = guest;
        pthread_attr_t attributes;
        pthread_attr_init(&attributes);
        int attr_error = pthread_attr_setstacksize(&attributes, BOTW_GUEST_STACK_SIZE);
        pthread_t guest_thread;
        int create_error = attr_error ? attr_error : pthread_create(&guest_thread, &attributes, run_guest, context);
        pthread_attr_destroy(&attributes);
        if (create_error) {
            fprintf(stderr, "BotW frontend: could not start guest thread: %s\n", strerror(create_error));
            free(context);
            [g_window close];
            return;
        }

        [NSApp run];

        atomic_store_explicit(&g_vsync_callback, NULL, memory_order_release);
        stop_display_link();

        if (atomic_load_explicit(&g_guest_finished, memory_order_acquire))
            pthread_join(guest_thread, NULL);
        else
            pthread_detach(guest_thread);
    }
}
