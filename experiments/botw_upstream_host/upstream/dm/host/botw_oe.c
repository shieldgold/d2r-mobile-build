// BotW application environment on macOS. Switch clock requests are host-owned hints.
// The exported contracts were verified against BotW binaries; current target: 1.9.0.
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include <pthread.h>
#include <unistd.h>

// The game requests a Switch clock profile through nn::oe.
// macOS owns hardware frequencies; accept the profile hint without creating a Horizon APM session.
HLE_FORCE("_ZN2nn2oe27SetPerformanceConfigurationENS0_15PerformanceModeEi", "BotW: macOS manages CPU/GPU clocks", void, botw_oe_performance, int mode, int configuration) {
    if (mode != 0 && mode != 1) die("invalid BotW performance mode %d", mode);
    LOG("BotW performance hint: mode=%d configuration=%#x (clocks managed by macOS)", mode, (unsigned)configuration);
}

// The game requests fast-load CPU boost; macOS retains frequency control.
HLE_FORCE("_ZN2nn2oe15SetCpuBoostModeENS0_12CpuBoostModeE", "BotW: host-managed CPU boost hint", void, botw_oe_boost, int mode) {
    if ((unsigned)mode >= 2) die("invalid BotW CPU boost mode %d", mode);
    LOG("BotW CPU boost hint: mode=%d (clocks managed by macOS)", mode);
}

#include <stdatomic.h>
static _Atomic unsigned g_notifications;
static _Atomic int g_focus_policy;

// These SDK exports only configure applet notifications; retain their host preferences.
static void notification(unsigned bit, unsigned enabled) {
    if (enabled & 0xff) atomic_fetch_or(&g_notifications, bit);
    else atomic_fetch_and(&g_notifications, ~bit);
}
HLE_FORCE("_ZN2nn2oe28SetResumeNotificationEnabledEb", "BotW: host resume notification preference", void, botw_oe_resume, unsigned enabled) {
    notification(1, enabled);
}
HLE_FORCE("_ZN2nn2oe42SetOperationModeChangedNotificationEnabledEb", "BotW: host operation-mode notification preference", void, botw_oe_operation_notify, unsigned enabled) {
    notification(2, enabled);
}
HLE_FORCE("_ZN2nn2oe44SetPerformanceModeChangedNotificationEnabledEb", "BotW: host performance-mode notification preference", void, botw_oe_performance_notify, unsigned enabled) {
    notification(4, enabled);
}
// Extends the Switch's automatic screen-dimming / sleep delay (cutscenes, movies). macOS owns display sleep, so the
// preference is retained without host effect. The SDK path would dereference the applet functions object the host
// never creates.
static _Atomic unsigned g_inactivity_extended;
HLE_FORCE("_ZN2nn2oe38SetUserInactivityDetectionTimeExtendedEb", "BotW: host display sleep is not changed", void, botw_oe_inactivity_extended, unsigned enabled) {
    atomic_store(&g_inactivity_extended, enabled & 0xff ? 1u : 0u);
}
unsigned botw_oe_inactivity_extended_state(void) { return atomic_load(&g_inactivity_extended); }

// Expected master volumes of the application and of library applets, 1.0 until the game sets them (the
// value Ryubing reports). They describe the system mix; the host mixer is unaffected.
static pthread_mutex_t g_volume_mu = PTHREAD_MUTEX_INITIALIZER;
static float g_volume_main = 1.0f, g_volume_applet = 1.0f;
HLE_FORCE("_ZN2nn2oe24SetExpectedVolumeBalanceEff", "BotW: retain expected volume balance", void, botw_oe_set_volume_balance, float main_volume, float applet_volume) {
    if (!(main_volume >= 0.0f && main_volume <= 1.0f) || !(applet_volume >= 0.0f && applet_volume <= 1.0f))
        die("invalid BotW expected volume balance %a / %a", (double)main_volume, (double)applet_volume);
    pthread_mutex_lock(&g_volume_mu); g_volume_main = main_volume; g_volume_applet = applet_volume; pthread_mutex_unlock(&g_volume_mu);
}
HLE_FORCE("_ZN2nn2oe24GetExpectedVolumeBalanceEPfS1_", "BotW: report the retained expected volume balance", void, botw_oe_get_volume_balance, float *main_volume, float *applet_volume) {
    if (!main_volume || !applet_volume) die("GetExpectedVolumeBalance with a null output");
    pthread_mutex_lock(&g_volume_mu); *main_volume = g_volume_main; *applet_volume = g_volume_applet; pthread_mutex_unlock(&g_volume_mu);
}

// Retain focus policy modes 0..3; the headless guest remains in the foreground.
HLE_FORCE("_ZN2nn2oe20SetFocusHandlingModeENS0_17FocusHandlingModeE", "BotW: retain host focus policy", void, botw_oe_focus_policy, int mode) {
    if ((unsigned)mode >= 4) die("invalid BotW focus policy %d", mode);
    atomic_store(&g_focus_policy, mode);
    LOG("BotW focus policy %d", mode);
}
static int operation_mode(void) {
    const char *value = getenv("DM_OPMODE");
    if (!value || !*value) return 1;
    if (!strcmp(value, "0")) return 0;
    if (!strcmp(value, "1")) return 1;
    die("DM_OPMODE must be 0 (handheld) or 1 (docked)");
}
HLE_FORCE("_ZN2nn2oe16GetOperationModeEv", "BotW: desktop operation mode", int, botw_oe_operation_mode, void) {
    return operation_mode();
}
HLE_FORCE("_ZN2nn2oe18GetPerformanceModeEv", "BotW: performance mode follows operation mode", int, botw_oe_performance_mode, void) {
    return operation_mode();
}

// The host currently has no applet-message producer. An empty notification source
// must not enter the SDK event path, whose Horizon session was never initialized.
// Return false without touching the output, matching the SDK empty-event branch.
HLE_FORCE("_ZN2nn2oe25TryPopNotificationMessageEPj", "BotW: no pending host applet notifications", unsigned, botw_oe_try_pop_notification, uint32_t *message) {
    (void)message;
    static atomic_flag logged = ATOMIC_FLAG_INIT;
    if (!atomic_flag_test_and_set(&logged))
        LOG("BotW applet notifications: no host messages pending");
    return 0;
}

// LanguageCode is an eight-byte value returned in x0, not a pointer or Result.
HLE_FORCE("_ZN2nn2oe18GetDesiredLanguageEv", "BotW: host-selected application language", uint64_t, botw_oe_desired_language, void) {
    const char *language = getenv("DM_LANG");
    if (!language || !*language) language = "en-US";
    size_t size = strlen(language);
    if (size > 7) die("DM_LANG must be an ASCII language code of at most 7 characters");
    for (size_t i = 0; i < size; ++i) {
        unsigned char c = (unsigned char)language[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '-'))
            die("DM_LANG must contain only ASCII letters, digits and hyphens");
    }
    uint64_t code = 0;
    memcpy(&code, language, size);
    return code;
}

// Horizon assigns an applet-resource identity during applet-service initialization.
// This single-process host owns one application instance, identified by its PID for
// this process lifetime. This is not a kernel handle, account UID or title ID.
static uint64_t botw_applet_instance_id(void) { return (uint64_t)(unsigned)getpid(); }
HLE_FORCE("_ZN2nn6applet23GetAppletResourceUserIdEv", "BotW: host application-instance identity", uint64_t, botw_applet_resource_id, void) {
    return botw_applet_instance_id();
}
HLE_FORCE("_ZN2nn2am23GetAppletResourceUserIdEv", "BotW: shared host application-instance identity", uint64_t, botw_am_resource_id, void) {
    return botw_applet_instance_id();
}

// Ordinary host launches have no Horizon application-parameter storage queued.
// The SDK empty-channel path returns false without writing either output.
HLE_FORCE("_ZN2nn2oe21TryPopLaunchParameterEPmPvm", "BotW: no queued application launch parameters", unsigned,
          botw_oe_try_pop_launch_parameter, size_t *out_size, void *buffer, size_t capacity) {
    (void)out_size; (void)buffer; (void)capacity;
    static atomic_flag logged = ATOMIC_FLAG_INIT;
    if (!atomic_flag_test_and_set(&logged))
        LOG("BotW launch parameters: no host application parameters queued");
    return 0;
}

// The desktop host has no VR display path. Ending VR is idempotent; beginning
// it returns the denial Result used by the native SDK, without changing state.
HLE_FORCE("_ZN2nn2pl9EndVrModeEv", "BotW: desktop display remains outside VR mode", void, botw_pl_end_vr, void) {
    static atomic_flag logged = ATOMIC_FLAG_INIT;
    if (!atomic_flag_test_and_set(&logged)) LOG("BotW VR: desktop display remains in non-VR mode");
}
HLE_FORCE("_ZN2nn2pl8IsVrModeEv", "BotW: desktop VR mode is inactive", unsigned, botw_pl_is_vr, void) { return 0; }
HLE_FORCE("_ZN2nn2pl11BeginVrModeEv", "BotW: VR display mode is unavailable", Result, botw_pl_begin_vr, void) {
    LOG("BotW VR: BeginVrMode denied; no host VR display backend (Result %#x)", 0xd08eu);
    return 0xd08eu;
}
// This is an applet recording-permission hint, not a request to start capture.
// The host has no Horizon system recording service to enable.
HLE_FORCE("_ZN2nn2oe15EnableRecordingEv", "BotW: system recording permission hint", void, botw_oe_enable_recording, void) {
    static atomic_flag logged = ATOMIC_FLAG_INIT;
    if (!atomic_flag_test_and_set(&logged)) LOG("BotW recording: permission hint accepted; system recording is unavailable");
}

// No Horizon startup overlay is present on the host. This acknowledges its
// completion without changing the game's own logo rendering or command stream.
HLE_FORCE("_ZN2nn2oe17FinishStartupLogoEv", "BotW: acknowledge startup overlay completion", void, botw_oe_finish_startup_logo, void) {
    static atomic_flag logged = ATOMIC_FLAG_INIT;
    if (!atomic_flag_test_and_set(&logged)) LOG("BotW startup logo: host overlay completion acknowledged");
}
