// BotW nn::hid on macOS: the bridge between the host front end / GameController backend (botw_input.m)
// and the HLE sampler that answers the game's nn::hid calls (botw_hid.c).
//
// Threading: every function here may be called from any thread. botw_hid.c keeps one mutex-protected
// snapshot; the front end writes into it from Cocoa event handlers, the GameController backend from its
// handler queue, and the guest thread reads a coherent copy on each nn::hid::GetNpadStates. Nothing in
// botw_hid.c calls back into Cocoa, so guest threads never touch AppKit.
//
// Keyboard / mouse mapping (macOS virtual key codes as delivered in NSEvent.keyCode):
//   W / A / S / D          left stick (full deflection; diagonals are normalized to the unit circle)
//   Arrow keys             D-pad Up / Left / Down / Right
//   E                      A            Shift (either)       B
//   Space                  X            left mouse button    Y
//   Q                      ZL           right mouse button   ZR
//   Return / keypad Enter  Plus         Tab                  Minus
//   F                      L            R                    R
//   C                      left stick click (LS)             V   right stick click (RS)
//   mouse motion           right stick (camera); the deflection decays back to center when the mouse stops
// Losing window focus releases every key, mouse button and controller input.
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

// ---- Input bridge: called by the Mac front end (botw_front.m) ----
void botw_input_start(void);                              // botw_input.m: attach GameController observers; idempotent
void botw_input_key(unsigned short keycode, int down);    // NSEvent.keyCode, down = 1 press / 0 release (auto-repeat is harmless)
void botw_input_mouse_button(unsigned button, int down);  // NSEvent.buttonNumber: 0 left, 1 right, 2 middle
void botw_input_mouse_delta(double dx, double dy);        // raw pointer motion in points (Cocoa deltaX / deltaY, positive dy = down)
void botw_input_focus(int focused);                       // 0 = focus lost: everything held is released

// ---- Controller snapshots: botw_input.m publishes, botw_hid.c merges them with the keyboard ----
// Button bits follow nn::hid::NpadButton; sticks are -32767..32767 with up and right positive.
enum {
    BOTW_BTN_A = 1u << 0, BOTW_BTN_B = 1u << 1, BOTW_BTN_X = 1u << 2, BOTW_BTN_Y = 1u << 3,
    BOTW_BTN_LS = 1u << 4, BOTW_BTN_RS = 1u << 5, BOTW_BTN_L = 1u << 6, BOTW_BTN_R = 1u << 7,
    BOTW_BTN_ZL = 1u << 8, BOTW_BTN_ZR = 1u << 9, BOTW_BTN_PLUS = 1u << 10, BOTW_BTN_MINUS = 1u << 11,
    BOTW_BTN_LEFT = 1u << 12, BOTW_BTN_UP = 1u << 13, BOTW_BTN_RIGHT = 1u << 14, BOTW_BTN_DOWN = 1u << 15,
};
enum { BOTW_STICK_MAX = 32767 };
typedef struct { uint64_t buttons; int32_t lx, ly, rx, ry; } botw_pad_state;
void botw_hid_pad_set(uintptr_t who, const botw_pad_state *s);   // publish one controller's complete state (out-of-range sticks are clamped)
void botw_hid_pad_drop(uintptr_t who);                           // that controller disconnected; its share is removed
void botw_hid_pad_clear(void);                                   // release every controller's buttons and sticks (focus loss)
// Face-button layout helper for the backend: GameController reports face buttons by position (a = bottom, b = right,
// x = left, y = top). Nintendo controllers are printed in Switch order, so position maps straight to the Switch bits;
// everything else is mapped by the printed letter (bottom = A, right = B, left = X, top = Y).
uint64_t botw_hid_face_buttons(int nintendo_layout, int a, int b, int x, int y);
int botw_hid_nintendo_layout(const char *product_category, const char *vendor_name);   // DM_HID_PAD_LAYOUT=nintendo|xbox overrides

// ---- Sampler / test hooks ----
// Merged keyboard + mouse + controller state exactly as the HLE layer hands it to the game.
// Returns 1 while the host virtual controller is available, 0 when it is disabled (out is zeroed then).
int botw_hid_sample(botw_pad_state *out);
void botw_hid_set_virtual_pad(int available);   // synthetic "no controller connected"; DM_HID_NOPAD=1 does the same at startup
#ifdef __cplusplus
}
#endif
