// nn::hid service layer for Breath of the Wild on macOS.
// The console answers these calls from the hid shared memory filled by the HID service; here the host
// keyboard, mouse and any GameController pad (botw_input.m) are merged into one virtual controller that
// the game sees as a Pro Controller (NpadStyleFullKey) on Npad id 0. The mapping is documented in botw_hid.h.
// Motion sensors and rumble have no host backend: the handle getters report 0 handles, so the game never
// starts a sensor or sends a vibration value; the remaining sensor / vibration entries are registered anyway
// because the executable imports them, and each one logs once and does nothing.
// Imported contracts were revalidated against BotW 1.9.0 and its SDK 20.5.22 in the private workspace:
//   - the five GetNpadStates overloads are int(out, capacity, const NpadIdType&) and accept ids 0..7, 0x10, 0x20
//     (anything else aborts in the SDK with hid result 0x58aca); every Npad*State is 0x28 bytes;
//   - BitFlagSet<32, NpadStyleTag> is passed by value in w0 / w3; the SDK aborts on the value 0xffffffff;
//   - the setters are void and abort on an internal failure, the handle / state getters return an int count;
//   - ControllerSupportArg is 0x430 bytes, SetDefault writes {0, 4, 1, 1, 1} and zeroes the remaining 0x42b bytes;
//   - ShowControllerSupport(const ControllerSupportArg&) returns a Result;
//   - StartSevenSixAxisSensor() returns the service's 32-bit Result without aborting;
//   - GetSevenSixAxisSensorStates(out, capacity) takes two arguments and returns a sample count;
//   - InitializeSevenSixAxisSensor(void*, size_t) passes the buffer through to the service and returns nothing
//     the game can use (w0 holds the internal success code 0 on return).
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include "botw_hid.h"
#include "botw_keys.h"
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <strings.h>

#define LOG_ONCE(...) do { static _Atomic int once_; if (!atomic_exchange(&once_, 1)) LOG(__VA_ARGS__); } while (0)

// ---------- Results ----------
// nn::Result for the hid module: module 202 in the low 9 bits, description above. The game only tests
// ShowControllerSupport for failure; the description follows the public libnx naming.
#define HID_RESULT(desc) ((Result)(((uint32_t)(desc) << 9) | 202u))
enum { RES_HID_CONTROLLER_SUPPORT_CANCELED = HID_RESULT(3101) };

// ---------- Public ABI ----------
typedef struct {
    int64_t sampling;
    uint64_t buttons;
    int32_t lx, ly, rx, ry;
    uint32_t attributes;
    uint32_t reserved;
} NpadState;   // NpadFullKeyState / NpadHandheldState / NpadJoyDualState / NpadJoyLeftState / NpadJoyRightState
_Static_assert(sizeof(NpadState) == 0x28, "Npad*State must be 0x28 bytes");
_Static_assert(offsetof(NpadState, buttons) == 0x08, "NpadState.buttons must sit at 0x08");
_Static_assert(offsetof(NpadState, lx) == 0x10, "NpadState left stick must sit at 0x10");
_Static_assert(offsetof(NpadState, rx) == 0x18, "NpadState right stick must sit at 0x18");
_Static_assert(offsetof(NpadState, attributes) == 0x20, "NpadState.attributes must sit at 0x20");
_Static_assert(sizeof(Result) == 4, "Result must be a 32-bit word");

enum { NPAD_STATE_COUNT_MAX = 16, SIXAXIS_STATE_COUNT_MAX = 16 };
enum { SIXAXIS_STATE_SIZE = 0x60, SEVEN_SIXAXIS_STATE_SIZE = 0x40 };
enum { NPAD_ID_PLAYER_MAX = 7, NPAD_ID_OTHER = 0x10, NPAD_ID_HANDHELD = 0x20, NPAD_ID_COUNT_MAX = 10 };
enum { STYLE_FULLKEY = 1u << 0, STYLE_HANDHELD = 1u << 1, STYLE_JOYDUAL = 1u << 2, STYLE_JOYLEFT = 1u << 3, STYLE_JOYRIGHT = 1u << 4 };
enum { ATTR_CONNECTED = 1u << 0 };
enum { JOY_HOLD_VERTICAL = 0, JOY_HOLD_HORIZONTAL = 1 };

typedef struct __attribute__((packed)) {
    int8_t player_count_min;
    int8_t player_count_max;
    uint8_t enable_take_over_connection;
    uint8_t enable_left_justify;
    uint8_t enable_permit_joy_dual;
    uint8_t enable_single_mode;
    uint8_t enable_identification_color;
    uint32_t identification_color[8];
    uint8_t enable_explain_text;
    char explain_text[8][0x81];
} ControllerSupportArg;
_Static_assert(sizeof(ControllerSupportArg) == 0x430, "ControllerSupportArg must be 0x430 bytes");
_Static_assert(offsetof(ControllerSupportArg, enable_permit_joy_dual) == 4, "ControllerSupportArg flag layout");

// ---------- Host state ----------
// One mutex covers the configuration and the input snapshot: the guest reads once per frame, the front end
// writes on events, and neither side ever holds it across a blocking call.
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static struct {
    int initialized;              // InitializeNpad was called
    int styles_set, ids_set;
    uint32_t supported_styles;    // SetSupportedNpadStyleSet value
    unsigned supported_ids;       // bit per id, see id_bit()
    int virtual_enabled;          // -1 undecided (DM_HID_NOPAD read on first use), else 0 / 1
    uint32_t virtual_id;          // where the host virtual controller is presented
    uint32_t virtual_style;       // its style bit; 0 = not presented anywhere
    int presented_logged;
    int hold_type;
    int seven_initialized;
} g_cfg = { .virtual_enabled = -1 };
static _Atomic int64_t g_sampling;

enum { KEY_CODES = 128, MOUSE_BUTTONS = 3, PAD_SLOTS = 4 };
enum { KEY_A = 0, KEY_S = 1, KEY_D = 2, KEY_W = 13 };
static const struct { unsigned short code; uint64_t button; } g_key_buttons[] = {
    {14, BOTW_BTN_A},                       // E
    {56, BOTW_BTN_B}, {60, BOTW_BTN_B},     // left / right Shift
    {49, BOTW_BTN_X},                       // Space
    {12, BOTW_BTN_ZL},                      // Q
    {36, BOTW_BTN_PLUS}, {76, BOTW_BTN_PLUS},   // Return / keypad Enter
    {48, BOTW_BTN_MINUS},                   // Tab
    {3, BOTW_BTN_L},                        // F
    {15, BOTW_BTN_R},                       // R
    {8, BOTW_BTN_LS},                       // C
    {9, BOTW_BTN_RS},                       // V
    {123, BOTW_BTN_LEFT}, {124, BOTW_BTN_RIGHT}, {125, BOTW_BTN_DOWN}, {126, BOTW_BTN_UP},
};
static const uint64_t g_mouse_map[MOUSE_BUTTONS] = { BOTW_BTN_Y, BOTW_BTN_ZR, 0 };
// Camera: pointer motion is integrated into a right-stick deflection that decays exponentially (time constant
// CAM_TAU_NS) and snaps to center below CAM_FLOOR, so the camera stops when the mouse stops.
enum { CAM_TAU_NS = 50 * 1000 * 1000, CAM_FLOOR = 200, CAM_GAIN_DEFAULT = 2000 };
enum { PAD_DEADZONE = 3932 };   // 12% of full deflection: below it a controller stick does not override the keyboard / mouse
enum { STICK_DIAGONAL = 23170 };   // 32767 / sqrt(2)

static uint8_t g_key_down[KEY_CODES];
static int g_focused = 1;
static unsigned g_mouse_down;
static double g_cam_x, g_cam_y, g_cam_gain = -1;
static uint64_t g_cam_ns;
static struct { uintptr_t who; botw_pad_state s; } g_pads[PAD_SLOTS];

static int id_bit(uint32_t id) {
    if (id <= NPAD_ID_PLAYER_MAX) return (int)id;
    if (id == NPAD_ID_OTHER) return 8;
    if (id == NPAD_ID_HANDHELD) return 9;
    return -1;
}
static const char *style_name(uint32_t style) {
    switch (style) {
        case STYLE_FULLKEY: return "FullKey (Pro Controller)";
        case STYLE_HANDHELD: return "Handheld";
        case STYLE_JOYDUAL: return "JoyDual";
        case STYLE_JOYLEFT: return "JoyLeft";
        case STYLE_JOYRIGHT: return "JoyRight";
        default: return "other";
    }
}
static int32_t clamp_axis(int32_t v) { return v > BOTW_STICK_MAX ? BOTW_STICK_MAX : v < -BOTW_STICK_MAX ? -BOTW_STICK_MAX : v; }
static double magnitude(int32_t x, int32_t y) { return hypot((double)x, (double)y); }

static int virtual_enabled_locked(void) {
    if (g_cfg.virtual_enabled < 0) {
        const char *e = getenv("DM_HID_NOPAD");
        g_cfg.virtual_enabled = !(e && *e && strcmp(e, "0"));
        if (!g_cfg.virtual_enabled) LOG("BotW HID: DM_HID_NOPAD set; no virtual controller will be presented");
    }
    return g_cfg.virtual_enabled;
}

// Decide where the host virtual controller appears from the game's declared support. Preferred: Pro
// Controller on Npad id 0; otherwise the handheld slot; otherwise nothing (the game then sees no controller).
static void reconfigure_locked(void) {
    uint32_t id = 0, style = 0;
    if (virtual_enabled_locked() && g_cfg.styles_set && g_cfg.ids_set) {
        if ((g_cfg.supported_styles & STYLE_FULLKEY) && (g_cfg.supported_ids & (1u << id_bit(0)))) { id = 0; style = STYLE_FULLKEY; }
        else if ((g_cfg.supported_styles & STYLE_HANDHELD) && (g_cfg.supported_ids & (1u << id_bit(NPAD_ID_HANDHELD)))) { id = NPAD_ID_HANDHELD; style = STYLE_HANDHELD; }
    }
    int changed = !g_cfg.presented_logged || id != g_cfg.virtual_id || style != g_cfg.virtual_style;
    g_cfg.virtual_id = id; g_cfg.virtual_style = style;
    if (!(g_cfg.styles_set && g_cfg.ids_set) || !changed) return;
    g_cfg.presented_logged = 1;
    if (style) LOG("BotW HID: host keyboard / mouse / controller presented as Npad id %#x style %s", id, style_name(style));
    else LOG("BotW HID: no virtual controller presented: the game supports styles %#x and ids %#x, the host offers FullKey on id 0 or Handheld on id 0x20",
             g_cfg.supported_styles, g_cfg.supported_ids);
}

// ---------- Input bridge ----------
static void camera_decay_locked(uint64_t now) {
    if (g_cam_ns && now > g_cam_ns) {
        double f = exp(-(double)(now - g_cam_ns) / (double)CAM_TAU_NS);
        g_cam_x *= f; g_cam_y *= f;
    }
    g_cam_ns = now;
    if (fabs(g_cam_x) < CAM_FLOOR) g_cam_x = 0;
    if (fabs(g_cam_y) < CAM_FLOOR) g_cam_y = 0;
}
static void release_all_locked(void) {
    memset(g_key_down, 0, sizeof g_key_down);
    g_mouse_down = 0;
    g_cam_x = g_cam_y = 0;
    g_cam_ns = 0;
    for (int i = 0; i < PAD_SLOTS; i++) memset(&g_pads[i].s, 0, sizeof g_pads[i].s);
}
void botw_input_key(unsigned short keycode, int down) {
    if (keycode >= KEY_CODES) return;
    pthread_mutex_lock(&g_lock);
    if (g_focused) g_key_down[keycode] = down ? 1 : 0;
    pthread_mutex_unlock(&g_lock);
}
void botw_input_mouse_button(unsigned button, int down) {
    if (button >= MOUSE_BUTTONS) return;
    pthread_mutex_lock(&g_lock);
    if (g_focused) { if (down) g_mouse_down |= 1u << button; else g_mouse_down &= ~(1u << button); }
    pthread_mutex_unlock(&g_lock);
}
void botw_input_mouse_delta(double dx, double dy) {
    if (!(isfinite(dx) && isfinite(dy))) return;
    pthread_mutex_lock(&g_lock);
    if (!g_focused) { pthread_mutex_unlock(&g_lock); return; }
    if (g_cam_gain < 0) {
        const char *e = getenv("DM_HID_MOUSE_GAIN");
        char *end = NULL;
        double gain = e ? strtod(e, &end) : 0;
        g_cam_gain = e && end != e && !*end && isfinite(gain) && gain > 0 ? gain : CAM_GAIN_DEFAULT;
    }
    camera_decay_locked(now_ns());
    g_cam_x += dx * g_cam_gain;
    g_cam_y -= dy * g_cam_gain;   // Cocoa deltas grow downward; stick up is positive
    if (g_cam_x > BOTW_STICK_MAX) g_cam_x = BOTW_STICK_MAX; if (g_cam_x < -BOTW_STICK_MAX) g_cam_x = -BOTW_STICK_MAX;
    if (g_cam_y > BOTW_STICK_MAX) g_cam_y = BOTW_STICK_MAX; if (g_cam_y < -BOTW_STICK_MAX) g_cam_y = -BOTW_STICK_MAX;
    pthread_mutex_unlock(&g_lock);
}
void botw_input_focus(int focused) {
    pthread_mutex_lock(&g_lock);
    g_focused = focused != 0;
    if (!focused) release_all_locked();
    pthread_mutex_unlock(&g_lock);
    LOG("BotW HID: window focus %s%s", focused ? "gained" : "lost", focused ? "" : "; all keys, mouse buttons and controller inputs released");
}
void botw_hid_pad_set(uintptr_t who, const botw_pad_state *s) {
    if (!s || !who) return;
    pthread_mutex_lock(&g_lock);
    if (!g_focused) { pthread_mutex_unlock(&g_lock); return; }
    int slot = -1;
    for (int i = 0; i < PAD_SLOTS; i++) {
        if (g_pads[i].who == who) { slot = i; break; }
        if (slot < 0 && !g_pads[i].who) slot = i;
    }
    if (slot >= 0) {
        g_pads[slot].who = who;
        g_pads[slot].s.buttons = s->buttons;
        g_pads[slot].s.lx = clamp_axis(s->lx); g_pads[slot].s.ly = clamp_axis(s->ly);
        g_pads[slot].s.rx = clamp_axis(s->rx); g_pads[slot].s.ry = clamp_axis(s->ry);
    }
    pthread_mutex_unlock(&g_lock);
    if (slot < 0) LOG_ONCE("BotW HID: more than %d controllers connected; extra controllers are ignored", PAD_SLOTS);
}
void botw_hid_pad_drop(uintptr_t who) {
    pthread_mutex_lock(&g_lock);
    for (int i = 0; i < PAD_SLOTS; i++) if (g_pads[i].who == who) memset(&g_pads[i], 0, sizeof g_pads[i]);
    pthread_mutex_unlock(&g_lock);
}
void botw_hid_pad_clear(void) {
    pthread_mutex_lock(&g_lock);
    for (int i = 0; i < PAD_SLOTS; i++) memset(&g_pads[i].s, 0, sizeof g_pads[i].s);
    pthread_mutex_unlock(&g_lock);
}
void botw_hid_set_virtual_pad(int available) {
    pthread_mutex_lock(&g_lock);
    g_cfg.virtual_enabled = available ? 1 : 0;
    reconfigure_locked();
    pthread_mutex_unlock(&g_lock);
}
uint64_t botw_hid_face_buttons(int nintendo_layout, int a, int b, int x, int y) {
    uint64_t r = 0;
    if (nintendo_layout) {   // positions: bottom = B, right = A, left = Y, top = X
        if (a) r |= BOTW_BTN_B; if (b) r |= BOTW_BTN_A; if (x) r |= BOTW_BTN_Y; if (y) r |= BOTW_BTN_X;
    } else {                 // printed letters
        if (a) r |= BOTW_BTN_A; if (b) r |= BOTW_BTN_B; if (x) r |= BOTW_BTN_X; if (y) r |= BOTW_BTN_Y;
    }
    return r;
}
int botw_hid_nintendo_layout(const char *product_category, const char *vendor_name) {
    const char *e = getenv("DM_HID_PAD_LAYOUT");
    if (e && !strcasecmp(e, "nintendo")) return 1;
    if (e && !strcasecmp(e, "xbox")) return 0;
    const char *c = product_category ? product_category : "", *v = vendor_name ? vendor_name : "";
    return strcasestr(c, "switch") || strcasestr(c, "nintendo") || strcasestr(c, "joy-con") || strcasestr(v, "nintendo") ? 1 : 0;
}

// Merge keyboard + mouse + controllers into one coherent state. Buttons are ORed; each stick comes from the
// controller pushed furthest when that is beyond the deadzone, otherwise from the keyboard (left) or the
// decaying mouse camera (right).
static int sample_locked(botw_pad_state *out) {
    memset(out, 0, sizeof *out);
    int available = virtual_enabled_locked();
    if (available && g_focused) {
        uint64_t b = 0;
        for (size_t i = 0; i < sizeof g_key_buttons / sizeof g_key_buttons[0]; i++)
            if (g_key_down[g_key_buttons[i].code]) b |= g_key_buttons[i].button;
        for (unsigned i = 0; i < MOUSE_BUTTONS; i++) if (g_mouse_down & (1u << i)) b |= g_mouse_map[i];
        int kx = (g_key_down[KEY_D] ? 1 : 0) - (g_key_down[KEY_A] ? 1 : 0);
        int ky = (g_key_down[KEY_W] ? 1 : 0) - (g_key_down[KEY_S] ? 1 : 0);
        int32_t full = kx && ky ? STICK_DIAGONAL : BOTW_STICK_MAX;
        int32_t lx = kx * full, ly = ky * full;
        camera_decay_locked(now_ns());
        int32_t rx = (int32_t)lround(g_cam_x), ry = (int32_t)lround(g_cam_y);
        double best_l = 0, best_r = 0;
        int32_t plx = 0, ply = 0, prx = 0, pry = 0;
        for (int i = 0; i < PAD_SLOTS; i++) {
            if (!g_pads[i].who) continue;
            const botw_pad_state *p = &g_pads[i].s;
            b |= p->buttons;
            double ml = magnitude(p->lx, p->ly), mr = magnitude(p->rx, p->ry);
            if (ml > best_l) { best_l = ml; plx = p->lx; ply = p->ly; }
            if (mr > best_r) { best_r = mr; prx = p->rx; pry = p->ry; }
        }
        if (best_l >= PAD_DEADZONE) { lx = plx; ly = ply; }
        if (best_r >= PAD_DEADZONE) { rx = prx; ry = pry; }
        out->buttons = b;
        out->lx = clamp_axis(lx); out->ly = clamp_axis(ly); out->rx = clamp_axis(rx); out->ry = clamp_axis(ry);
    }
    return available;
}
int botw_hid_sample(botw_pad_state *out) {
    pthread_mutex_lock(&g_lock);
    int available = sample_locked(out);
    pthread_mutex_unlock(&g_lock);
    return available;
}

// ---------- Npad ----------
static void require_npad_locked(const char *what) {
    if (!g_cfg.initialized) die("BotW HID: %s before InitializeNpad", what);
}
static uint32_t npad_id_checked(const uint32_t *id, const char *what) {
    if (!id) die("BotW HID: %s with a null NpadIdType pointer", what);
    uint32_t v = *id;
    if (id_bit(v) < 0) die("BotW HID: %s with invalid NpadIdType %#x (the SDK aborts with hid result 0x58aca)", what, v);
    return v;
}
static int npad_states(NpadState *out, int capacity, const uint32_t *id, uint32_t style, const char *what) {
    uint32_t npad = npad_id_checked(id, what);
    if (!out) { LOG_ONCE("BotW HID: %s with a null output buffer; 0 states returned", what); return 0; }
    if (capacity <= 0) { LOG_ONCE("BotW HID: %s with capacity %d; 0 states returned", what, capacity); return 0; }
    int n = capacity > NPAD_STATE_COUNT_MAX ? NPAD_STATE_COUNT_MAX : capacity;
    if (capacity > NPAD_STATE_COUNT_MAX) LOG_ONCE("BotW HID: %s asked for %d states; the SDK never writes more than %d", what, capacity, NPAD_STATE_COUNT_MAX);
    dm_guest_write_prepare(out, (size_t)n * sizeof *out);
    memset(out, 0, (size_t)n * sizeof *out);
    pthread_mutex_lock(&g_lock);
    require_npad_locked(what);
    int active = g_cfg.virtual_style && npad == g_cfg.virtual_id && (g_cfg.virtual_style & style) != 0;
    botw_pad_state s;
    if (active) active = sample_locked(&s);
    pthread_mutex_unlock(&g_lock);
    if (!active) return 0;
    out[0].sampling = atomic_fetch_add(&g_sampling, 1) + 1;
    out[0].buttons = s.buttons;
    out[0].lx = s.lx; out[0].ly = s.ly; out[0].rx = s.rx; out[0].ry = s.ry;
    out[0].attributes = ATTR_CONNECTED;
    return 1;
}

HLE_FORCE("_ZN2nn3hid14InitializeNpadEv", "BotW: Npad backed by the host keyboard / mouse / GameController", void, botw_hid_initialize_npad, void) {
    pthread_mutex_lock(&g_lock);
    g_cfg.initialized = 1;
    int enabled = virtual_enabled_locked();
    pthread_mutex_unlock(&g_lock);
    LOG("BotW HID: Npad initialized; %s", enabled ? "the host keyboard / mouse is the default virtual controller on Npad id 0 (FullKey) once the game declares support for it"
                                                 : "no virtual controller (DM_HID_NOPAD)");
    botw_keys_start();   // DM_KEYS scripted input, once the game can read the controller
}
HLE_FORCE("_ZN2nn3hid24SetSupportedNpadStyleSetENS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE", "BotW: supported styles decide where the host virtual controller appears", void, botw_hid_set_supported_styles, uint32_t styles) {
    if (styles == 0xffffffffu) die("BotW HID: SetSupportedNpadStyleSet(0xffffffff) is rejected by the SDK");
    pthread_mutex_lock(&g_lock);
    require_npad_locked("SetSupportedNpadStyleSet");
    g_cfg.supported_styles = styles; g_cfg.styles_set = 1;
    LOG("BotW HID: supported Npad styles %#x%s%s%s%s%s", styles,
        styles & STYLE_FULLKEY ? " FullKey" : "", styles & STYLE_HANDHELD ? " Handheld" : "", styles & STYLE_JOYDUAL ? " JoyDual" : "",
        styles & STYLE_JOYLEFT ? " JoyLeft" : "", styles & STYLE_JOYRIGHT ? " JoyRight" : "");
    reconfigure_locked();
    pthread_mutex_unlock(&g_lock);
}
HLE_FORCE("_ZN2nn3hid22SetSupportedNpadIdTypeEPKjm", "BotW: supported ids decide where the host virtual controller appears", void, botw_hid_set_supported_ids, const uint32_t *ids, uint64_t count) {
    if (!ids || count == 0 || count > NPAD_ID_COUNT_MAX) die("BotW HID: SetSupportedNpadIdType with %llu ids at %p", (unsigned long long)count, (const void *)ids);
    unsigned mask = 0;
    char list[NPAD_ID_COUNT_MAX * 6 + 1] = ""; size_t len = 0;
    for (uint64_t i = 0; i < count; i++) {
        int bit = id_bit(ids[i]);
        if (bit < 0) die("BotW HID: SetSupportedNpadIdType with invalid NpadIdType %#x", ids[i]);
        mask |= 1u << bit;
        len += (size_t)snprintf(list + len, sizeof list - len, "%s%#x", i ? " " : "", ids[i]);
    }
    pthread_mutex_lock(&g_lock);
    require_npad_locked("SetSupportedNpadIdType");
    g_cfg.supported_ids = mask; g_cfg.ids_set = 1;
    LOG("BotW HID: supported Npad ids [%s]", list);
    reconfigure_locked();
    pthread_mutex_unlock(&g_lock);
}
HLE_FORCE("_ZN2nn3hid15GetNpadStyleSetERKj", "BotW: style of the host virtual controller", uint32_t, botw_hid_get_style_set, const uint32_t *id) {
    uint32_t npad = npad_id_checked(id, "GetNpadStyleSet");
    pthread_mutex_lock(&g_lock);
    require_npad_locked("GetNpadStyleSet");
    uint32_t style = npad == g_cfg.virtual_id ? g_cfg.virtual_style : 0;
    pthread_mutex_unlock(&g_lock);
    return style;
}
HLE_FORCE("_ZN2nn3hid18SetNpadJoyHoldTypeENS0_15NpadJoyHoldTypeE", "BotW: Joy-Con hold type is recorded only", void, botw_hid_set_joy_hold_type, int hold_type) {
    if (hold_type != JOY_HOLD_VERTICAL && hold_type != JOY_HOLD_HORIZONTAL) die("BotW HID: SetNpadJoyHoldType(%d) is out of range", hold_type);
    pthread_mutex_lock(&g_lock);
    g_cfg.hold_type = hold_type;
    pthread_mutex_unlock(&g_lock);
    LOG("BotW HID: Joy-Con hold type %s (no Joy-Con on the host)", hold_type == JOY_HOLD_VERTICAL ? "vertical" : "horizontal");
}
HLE_FORCE("_ZN2nn3hid28SetNpadJoyAssignmentModeDualERKj", "BotW: Joy-Con assignment has no host Joy-Con", void, botw_hid_set_joy_assignment_dual, const uint32_t *id) {
    uint32_t npad = npad_id_checked(id, "SetNpadJoyAssignmentModeDual");
    LOG_ONCE("BotW HID: SetNpadJoyAssignmentModeDual(%#x) accepted; no Joy-Con on the host", npad);
}
HLE_FORCE("_ZN2nn3hid13GetNpadStatesEPNS0_16NpadFullKeyStateEiRKj", "BotW: Pro Controller states from the host virtual controller", int, botw_hid_get_states_fullkey, NpadState *out, int capacity, const uint32_t *id) {
    return npad_states(out, capacity, id, STYLE_FULLKEY, "GetNpadStates<FullKey>");
}
HLE_FORCE("_ZN2nn3hid13GetNpadStatesEPNS0_17NpadHandheldStateEiRKj", "BotW: handheld states from the host virtual controller", int, botw_hid_get_states_handheld, NpadState *out, int capacity, const uint32_t *id) {
    return npad_states(out, capacity, id, STYLE_HANDHELD, "GetNpadStates<Handheld>");
}
HLE_FORCE("_ZN2nn3hid13GetNpadStatesEPNS0_16NpadJoyDualStateEiRKj", "BotW: no dual Joy-Con on the host; 0 states", int, botw_hid_get_states_joydual, NpadState *out, int capacity, const uint32_t *id) {
    return npad_states(out, capacity, id, STYLE_JOYDUAL, "GetNpadStates<JoyDual>");
}
HLE_FORCE("_ZN2nn3hid13GetNpadStatesEPNS0_16NpadJoyLeftStateEiRKj", "BotW: no left Joy-Con on the host; 0 states", int, botw_hid_get_states_joyleft, NpadState *out, int capacity, const uint32_t *id) {
    return npad_states(out, capacity, id, STYLE_JOYLEFT, "GetNpadStates<JoyLeft>");
}
HLE_FORCE("_ZN2nn3hid13GetNpadStatesEPNS0_17NpadJoyRightStateEiRKj", "BotW: no right Joy-Con on the host; 0 states", int, botw_hid_get_states_joyright, NpadState *out, int capacity, const uint32_t *id) {
    return npad_states(out, capacity, id, STYLE_JOYRIGHT, "GetNpadStates<JoyRight>");
}

// ---------- Six-axis sensors (no host backend) ----------
HLE_FORCE("_ZN2nn3hid23GetSixAxisSensorHandlesEPNS0_19SixAxisSensorHandleEiRKjNS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE", "BotW: host motion sensors unsupported; 0 handles", int, botw_hid_get_sixaxis_handles, uint32_t *out, int capacity, const uint32_t *id, uint32_t style) {
    (void)out;
    uint32_t npad = npad_id_checked(id, "GetSixAxisSensorHandles");
    LOG_ONCE("BotW HID: GetSixAxisSensorHandles(Npad %#x, style %#x, capacity %d): host motion sensors unsupported, reporting 0 handles", npad, style, capacity);
    return 0;
}
HLE_FORCE("_ZN2nn3hid18StartSixAxisSensorERKNS0_19SixAxisSensorHandleE", "BotW: no six-axis handle is ever issued", void, botw_hid_start_sixaxis, const uint32_t *handle) {
    LOG_ONCE("BotW HID: StartSixAxisSensor(%#x) ignored: no six-axis handle was issued", handle ? *handle : 0u);
}
HLE_FORCE("_ZN2nn3hid25SetGyroscopeZeroDriftModeERKNS0_19SixAxisSensorHandleERKNS0_22GyroscopeZeroDriftModeE", "BotW: no six-axis handle is ever issued", void, botw_hid_set_gyro_drift_mode, const uint32_t *handle, const int *mode) {
    LOG_ONCE("BotW HID: SetGyroscopeZeroDriftMode(%#x, %d) ignored: no six-axis handle was issued", handle ? *handle : 0u, mode ? *mode : -1);
}
HLE_FORCE("_ZN2nn3hid22GetSixAxisSensorStatesEPNS0_18SixAxisSensorStateEiRKNS0_19SixAxisSensorHandleE", "BotW: no six-axis handle is ever issued; 0 states", int, botw_hid_get_sixaxis_states, void *out, int capacity, const uint32_t *handle) {
    LOG_ONCE("BotW HID: GetSixAxisSensorStates(%#x, capacity %d): no six-axis handle was issued, 0 states", handle ? *handle : 0u, capacity);
    if (out && capacity > 0) {
        size_t n = (size_t)(capacity > SIXAXIS_STATE_COUNT_MAX ? SIXAXIS_STATE_COUNT_MAX : capacity) * SIXAXIS_STATE_SIZE;
        dm_guest_write_prepare(out, n);
        memset(out, 0, n);
    }
    return 0;
}
HLE_FORCE("_ZN2nn3hid28InitializeSevenSixAxisSensorEPvm", "BotW: VR sensor buffer accepted, host motion unsupported", void, botw_hid_initialize_seven_sixaxis, void *buffer, uint64_t size) {
    pthread_mutex_lock(&g_lock);
    g_cfg.seven_initialized = 1;
    pthread_mutex_unlock(&g_lock);
    LOG("BotW HID: InitializeSevenSixAxisSensor(buffer %p, size %#llx) accepted; host motion unsupported, no samples will be produced", buffer, (unsigned long long)size);
}
HLE_FORCE("_ZN2nn3hid26FinalizeSevenSixAxisSensorEv", "BotW: VR sensor has no host backend", void, botw_hid_finalize_seven_sixaxis, void) {
    pthread_mutex_lock(&g_lock);
    g_cfg.seven_initialized = 0;
    pthread_mutex_unlock(&g_lock);
    LOG_ONCE("BotW HID: FinalizeSevenSixAxisSensor: no-op");
}
HLE_FORCE("_ZN2nn3hid23StartSevenSixAxisSensorEv", "BotW: VR sensor has no host backend", Result, botw_hid_start_seven_sixaxis, void) {
    LOG_ONCE("BotW HID: StartSevenSixAxisSensor unavailable on this host; returning generic failure %#x", RES_FAIL);
    return RES_FAIL;
}
HLE_FORCE("_ZN2nn3hid22StopSevenSixAxisSensorEv", "BotW: VR sensor has no host backend", void, botw_hid_stop_seven_sixaxis, void) {
    LOG_ONCE("BotW HID: StopSevenSixAxisSensor: no-op");
}
HLE_FORCE("_ZN2nn3hid32ResetSevenSixAxisSensorTimestampEv", "BotW: VR sensor has no host backend", void, botw_hid_reset_seven_sixaxis_timestamp, void) {
    LOG_ONCE("BotW HID: ResetSevenSixAxisSensorTimestamp: no-op");
}
HLE_FORCE("_ZN2nn3hid27GetSevenSixAxisSensorStatesEPNS0_23SevenSixAxisSensorStateEi", "BotW: VR sensor has no host backend; 0 states", int, botw_hid_get_seven_sixaxis_states, void *out, int capacity) {
    pthread_mutex_lock(&g_lock);
    int initialized = g_cfg.seven_initialized;
    pthread_mutex_unlock(&g_lock);
    if (!initialized) LOG_ONCE("BotW HID: GetSevenSixAxisSensorStates before InitializeSevenSixAxisSensor; 0 states");
    else LOG_ONCE("BotW HID: GetSevenSixAxisSensorStates(capacity %d): host motion unsupported, 0 states", capacity);
    if (out && capacity > 0) {
        size_t n = (size_t)(capacity > SIXAXIS_STATE_COUNT_MAX ? SIXAXIS_STATE_COUNT_MAX : capacity) * SEVEN_SIXAXIS_STATE_SIZE;
        dm_guest_write_prepare(out, n);
        memset(out, 0, n);
    }
    return 0;
}

// ---------- Vibration (no host backend) ----------
HLE_FORCE("_ZN2nn3hid25GetVibrationDeviceHandlesEPNS0_21VibrationDeviceHandleEiRKjNS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE", "BotW: host rumble unsupported; 0 handles", int, botw_hid_get_vibration_handles, uint32_t *out, int capacity, const uint32_t *id, uint32_t style) {
    (void)out;
    // The SDK aborts unless capacity > 0 and exactly one style bit is set.
    if (capacity <= 0 || style == 0 || (style & (style - 1)) != 0)
        die("BotW HID: GetVibrationDeviceHandles(capacity %d, style %#x) is rejected by the SDK", capacity, style);
    uint32_t npad = npad_id_checked(id, "GetVibrationDeviceHandles");
    LOG_ONCE("BotW HID: GetVibrationDeviceHandles(Npad %#x, style %s, capacity %d): host rumble unsupported, reporting 0 handles", npad, style_name(style), capacity);
    return 0;
}
HLE_FORCE("_ZN2nn3hid25InitializeVibrationDeviceERKNS0_21VibrationDeviceHandleE", "BotW: no vibration handle is ever issued", void, botw_hid_initialize_vibration, const uint32_t *handle) {
    LOG_ONCE("BotW HID: InitializeVibrationDevice(%#x) ignored: no vibration handle was issued", handle ? *handle : 0u);
}
HLE_FORCE("_ZN2nn3hid18SendVibrationValueERKNS0_21VibrationDeviceHandleERKNS0_14VibrationValueE", "BotW: no vibration handle is ever issued", void, botw_hid_send_vibration, const uint32_t *handle, const void *value) {
    (void)value;
    LOG_ONCE("BotW HID: SendVibrationValue(%#x) ignored: no vibration handle was issued", handle ? *handle : 0u);
}

// ---------- Controller support applet ----------
HLE_FORCE("_ZN2nn3hid20ControllerSupportArg10SetDefaultEv", "BotW: SDK default arguments, verified at SDK+0x53e414", void, botw_hid_controller_support_arg_default, ControllerSupportArg *arg) {
    if (!arg) die("BotW HID: ControllerSupportArg::SetDefault on a null object");
    dm_guest_write_prepare(arg, sizeof *arg);
    memset(arg, 0, sizeof *arg);
    arg->player_count_min = 0;
    arg->player_count_max = 4;
    arg->enable_take_over_connection = 1;
    arg->enable_left_justify = 1;
    arg->enable_permit_joy_dual = 1;
}
// The console shows the controller applet and waits for the player; the host has exactly one virtual controller
// (keyboard / mouse plus any attached pad), so the applet is answered immediately.
HLE_FORCE("_ZN2nn3hid21ShowControllerSupportERKNS0_20ControllerSupportArgE", "BotW: controller applet answered by the host virtual controller", Result, botw_hid_show_controller_support, const ControllerSupportArg *arg) {
    if (!arg) die("BotW HID: ShowControllerSupport with a null ControllerSupportArg");
    int min = arg->player_count_min, max = arg->player_count_max;
    pthread_mutex_lock(&g_lock);
    int connected = g_cfg.virtual_style ? 1 : 0;
    uint32_t id = g_cfg.virtual_id, style = g_cfg.virtual_style;
    pthread_mutex_unlock(&g_lock);
    if (min < 0 || max < min || max > 8) {
        LOG("BotW HID: ShowControllerSupport rejected: player count range %d..%d is invalid", min, max);
        return RES_HID_CONTROLLER_SUPPORT_CANCELED;
    }
    if (connected < min || connected > max || !connected) {
        LOG("BotW HID: ShowControllerSupport failed: %d controller(s) connected, the game needs %d..%d (host virtual controller %s)",
            connected, min, max, connected ? "connected" : "unavailable");
        return RES_HID_CONTROLLER_SUPPORT_CANCELED;
    }
    LOG("BotW HID: ShowControllerSupport: host virtual controller on Npad id %#x (%s) counts as player 1 for the %d..%d players requested; returning success without showing an applet",
        id, style_name(style), min, max);
    return RES_OK;
}
