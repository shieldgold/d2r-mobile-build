// GameController backend for the BotW HID layer on macOS (botw_hid.c holds the merged state).
// Every connected controller with an extended gamepad profile (MFi, Xbox, PlayStation, Switch Pro) publishes
// its complete state into botw_hid.c whenever any element changes; the C side merges it with the keyboard
// and mouse. Handlers run on a private serial queue, so nothing here depends on the main run loop except the
// connect / disconnect notifications, which GameController posts on the main thread.
// Face buttons: GameController reports them by position (buttonA bottom, buttonB right, buttonX left,
// buttonY top). Nintendo controllers are printed in Switch order, so their positions map straight onto the
// Switch bits; other controllers are mapped by the printed letter so that "A" on the glyph is the button
// printed A. Whether macOS reports Nintendo pads by position has not been measured on hardware;
// DM_HID_PAD_LAYOUT=nintendo|xbox overrides the automatic choice.
// Sticks: GameController gives -1..1 with up and right positive, the same orientation nn::hid uses.
#import <Foundation/Foundation.h>
#import <GameController/GameController.h>
#include <math.h>
#include "botw_hid.h"
#include "dm.h"

static dispatch_queue_t g_queue;
// Accessed only on g_queue. Each attachment gets a distinct token so delayed callbacks from an old
// attachment cannot resurrect a disconnected controller, even if the same object reconnects.
static NSMutableDictionary<NSNumber *, NSObject *> *g_tokens;

static int publish_state(uintptr_t who, NSObject *token, const botw_pad_state *s) {
    if (g_tokens[@(who)] != token) return 0;
    botw_hid_pad_set(who, s);
    return 1;
}
static void drop_state(uintptr_t who) {
    [g_tokens removeObjectForKey:@(who)];
    botw_hid_pad_drop(who);
}

static int32_t axis(float v) {
    if (!(v == v)) return 0;   // NaN
    if (v > 1) v = 1;
    if (v < -1) v = -1;
    return (int32_t)lroundf(v * BOTW_STICK_MAX);
}
static const char *str(NSString *s) { return s.UTF8String ? s.UTF8String : "?"; }

static void publish(GCController *c, GCExtendedGamepad *g, int nintendo, NSObject *token) {
    botw_pad_state s = {0};
    s.buttons = botw_hid_face_buttons(nintendo, g.buttonA.pressed, g.buttonB.pressed, g.buttonX.pressed, g.buttonY.pressed);
    if (g.leftShoulder.pressed) s.buttons |= BOTW_BTN_L;
    if (g.rightShoulder.pressed) s.buttons |= BOTW_BTN_R;
    if (g.leftTrigger.pressed) s.buttons |= BOTW_BTN_ZL;
    if (g.rightTrigger.pressed) s.buttons |= BOTW_BTN_ZR;
    if (g.leftThumbstickButton.pressed) s.buttons |= BOTW_BTN_LS;
    if (g.rightThumbstickButton.pressed) s.buttons |= BOTW_BTN_RS;
    if (g.buttonMenu.pressed) s.buttons |= BOTW_BTN_PLUS;
    if (g.buttonOptions.pressed) s.buttons |= BOTW_BTN_MINUS;
    if (g.dpad.left.pressed) s.buttons |= BOTW_BTN_LEFT;
    if (g.dpad.up.pressed) s.buttons |= BOTW_BTN_UP;
    if (g.dpad.right.pressed) s.buttons |= BOTW_BTN_RIGHT;
    if (g.dpad.down.pressed) s.buttons |= BOTW_BTN_DOWN;
    s.lx = axis(g.leftThumbstick.xAxis.value);
    s.ly = axis(g.leftThumbstick.yAxis.value);
    s.rx = axis(g.rightThumbstick.xAxis.value);
    s.ry = axis(g.rightThumbstick.yAxis.value);
    publish_state((uintptr_t)(__bridge void *)c, token, &s);
}

static void attach(GCController *c) {
    uintptr_t who = (uintptr_t)(__bridge void *)c;
    if (g_tokens[@(who)]) return;
    GCExtendedGamepad *g = c.extendedGamepad;
    if (!g) {
        LOG("BotW HID: controller %s (%s) has no extended gamepad profile; ignored", str(c.vendorName), str(c.productCategory));
        return;
    }
    int nintendo = botw_hid_nintendo_layout(c.productCategory.UTF8String, c.vendorName.UTF8String);
    LOG("BotW HID: controller connected: %s (%s); face buttons %s", str(c.vendorName), str(c.productCategory),
        nintendo ? "in Switch positions (right = A, bottom = B)" : "by printed letter (bottom = A, right = B)");
    c.handlerQueue = g_queue;
    NSObject *token = [NSObject new];
    g_tokens[@(who)] = token;
    __weak GCController *weak = c;
    g.valueChangedHandler = ^(GCExtendedGamepad *gp, GCControllerElement *element) {
        (void)element;
        GCController *strong = weak;
        if (strong) publish(strong, gp, nintendo, token);
    };
    publish(c, g, nintendo, token);
}

static void detach(GCController *c) {
    LOG("BotW HID: controller disconnected: %s", str(c.vendorName));
    c.extendedGamepad.valueChangedHandler = nil;
    drop_state((uintptr_t)(__bridge void *)c);
}

void botw_input_start(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        g_queue = dispatch_queue_create("dev.botw-mobile.hid.gamecontroller", DISPATCH_QUEUE_SERIAL);
        g_tokens = [NSMutableDictionary dictionary];
        NSNotificationCenter *nc = NSNotificationCenter.defaultCenter;
        [nc addObserverForName:GCControllerDidConnectNotification object:nil queue:nil usingBlock:^(NSNotification *n) {
            GCController *c = n.object;
            dispatch_async(g_queue, ^{ attach(c); });
        }];
        [nc addObserverForName:GCControllerDidDisconnectNotification object:nil queue:nil usingBlock:^(NSNotification *n) {
            GCController *c = n.object;
            dispatch_async(g_queue, ^{ detach(c); });
        }];
        NSArray<GCController *> *present = GCController.controllers;
        for (GCController *c in present) dispatch_async(g_queue, ^{ attach(c); });
        LOG("BotW HID: GameController backend started with %lu controller(s) present; keyboard / mouse mapping in botw_hid.h", (unsigned long)present.count);
    });
}

#ifdef BOTW_HID_LINK_SMOKE
// Exercise the production callback gate without requiring physical controller hardware.
int botw_input_lifecycle_selftest(void) {
    botw_input_start();
    __block int ok = 1;
    dispatch_sync(g_queue, ^{
        uintptr_t who = UINTPTR_MAX;
        NSObject *old = [NSObject new], *fresh = [NSObject new];
        botw_pad_state state = {.buttons = BOTW_BTN_ZR, .rx = 30000};
        g_tokens[@(who)] = old;
        ok &= publish_state(who, old, &state);
        drop_state(who);
        ok &= !publish_state(who, old, &state);
        g_tokens[@(who)] = fresh;
        ok &= !publish_state(who, old, &state);
        ok &= publish_state(who, fresh, &state);
        drop_state(who);
    });
    return ok;
}
#endif
