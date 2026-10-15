/* Horizon HIPC/CMIF host services. No game data is included.
 * Host integration derived from jhaizhou-ops/d2r-mobile (MIT), commit
 * e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8. See THIRD_PARTY_LICENSE.txt. */
#ifndef BOTW_HOST_SERVICES_H
#define BOTW_HOST_SERVICES_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct botw_services botw_services;
typedef void (*botw_services_log_fn)(void *opaque, const char *message);
/* Return nonzero only when the entire guest range has the required access. */
typedef int (*botw_services_memory_fn)(void *opaque, uint64_t address, size_t length, int writable);
botw_services *botw_services_create(botw_services_log_fn log, botw_services_memory_fn memory, void *opaque);
void botw_services_destroy(botw_services *services);
/* mode is Horizon Normal=0 or Boost=1. active distinguishes the current host
 * mode from a stored future profile. The handler must validate configuration
 * and apply an actual host policy for active requests; 0 accepts, -1 rejects.
 * Configuration state changes only after handler acceptance. */
typedef int (*botw_services_performance_fn)(void *opaque, uint32_t mode,
    uint32_t configuration, int active, char *error, size_t error_capacity);
void botw_services_set_performance_handler(botw_services *services,
    botw_services_performance_fn handler, void *opaque);
/* Canonical Horizon focus modes: 0 SuspendHomeSleep, 1 NoSuspend,
 * 2 SuspendHomeSleepNotify, 3 AlwaysSuspend. Host confirms actual execution
 * policy before committing a request. Lifecycle updates use the same handler.
 * Handler must not reenter services; gate waits must not hold this mutex. */
typedef int (*botw_services_focus_fn)(void *opaque, uint32_t mode,
    int foreground, int focused, int suspended, char *error, size_t error_capacity);
void botw_services_set_focus_handler(botw_services *services,
    botw_services_focus_fn handler, void *opaque);
/* Kernel bridge: events retain independent read/write endpoints. GetEventHandle
 * returns a duplicated read endpoint to the guest using a HIPC copy handle. */
typedef struct {
    void *opaque;
    int (*create_event)(void *, int, uint32_t *, uint32_t *, char *, size_t);
    int (*duplicate_handle)(void *, uint32_t, uint32_t *, char *, size_t);
    int (*signal_event)(void *, uint32_t, char *, size_t);
    int (*clear_event)(void *, uint32_t, char *, size_t);
    int (*close_handle)(void *, uint32_t, char *, size_t);
    int (*is_process_handle)(void *, uint32_t);
} botw_services_kernel_api;
void botw_services_set_kernel_api(botw_services *services, const botw_services_kernel_api *api);
typedef struct botw_input botw_input;
typedef struct botw_romfs botw_romfs;
typedef struct botw_savefs botw_savefs;
/* Borrowed backends must outlive services. Input is already bound to this kernel
 * shared-memory handle; root retains the original handle until services stop. */
void botw_services_set_input(botw_services *services, botw_input *input, uint32_t shared_memory_handle);
/* Invoke after host input sampling to signal actual Npad style changes. Lock
 * order is services -> input/kernel; frontend callbacks must release input first. */
int botw_services_refresh_input(botw_services *services, char *error, size_t error_capacity);
void botw_services_set_romfs(botw_services *services, botw_romfs *romfs);
/* Publish verified absence of installed update/patch content for this process.
 * Default/0 means unknown and fails diagnostically; 1 permits fsp command 203
 * to return FS TargetNotFound, letting the guest use its base-content fallback.
 * This does not infer absence from a missing backend or substitute base bytes
 * for a patch. Installed patch storage needs a separate supported backend. */
int botw_services_set_patch_absent(botw_services *services, int known_absent,
    char *error, size_t error_capacity);
/* Share the actual per-title/per-user host save mount. This borrowed instance
 * must outlive services; future save requests validate its immutable IDs. */
void botw_services_set_savefs(botw_services *services, botw_savefs *savefs);
/* Publish only content actually installed/mounted for this application. Empty
 * lists are valid. base_id=0 means unknown metadata, not an inferred entitlement.
 * Changes signal the persistent AOC event; identical sets do not. */
int botw_services_set_addon_content(botw_services *services, uint64_t application_id,
    uint64_t base_id, const uint32_t *installed_ids, size_t count,
    char *error, size_t error_capacity);
/* Explicit host-local policy, independent of Nintendo accounts/entitlements.
 * source identifies the user-controlled profile/default that supplied it. */
int botw_services_set_parental_policy(botw_services *services, const char *source,
    int restrict_communication, int restrict_sns, int restrict_stereo,
    char *error, size_t error_capacity);
/* Host audio mixer reads the same gain policy queried/set by IAudioController.
 * Values are finite [0,1], initialized to unity. Each output is optional. */
void botw_services_get_audio_gain(botw_services *services, float *main_applet,
    float *library_applet, float *transparent_rate);
/* Publish a verified 1280x720 RGBA8 Horizon capture snapshot: kind 0 foreground,
 * 1 application, 2 caller applet. NULL/0 clears availability. Copies host bytes;
 * no screenshot is fabricated when no real snapshot has been published. */
int botw_services_set_capture_image(botw_services *services, unsigned kind,
    const void *rgba, size_t size, char *error, size_t error_capacity);
/* Transition compositor source: 1 copied, 0 invalid/absent, -1 insufficient
 * capacity. generation changes on clear/invalidation; each output is optional. */
int botw_services_get_transition_image(botw_services *services, void *rgba,
    size_t capacity, uint64_t *generation);
/* Supply measured host state. Focus/foreground are booleans; operation_mode is
 * Handheld=0 or Console=1. resource_user_id must be nonzero and stable per run.
 * If cmd14 SetRestartMessageEnabled subscribed, an accepted suspended->running
 * execution-policy transition queues Resume(16) on the actual message event. */
int botw_services_set_applet_state(botw_services *services, uint64_t resource_user_id,
    int foreground, int focused, uint32_t operation_mode, char *error, size_t error_capacity);
/* 0 means executed. -1 means unsupported/invalid and error contains a concrete
 * diagnostic; callers must stop guest execution instead of forging success. */
int botw_services_connect(botw_services *services, const char *name, size_t name_length,
                          uint32_t *handle, char *error, size_t error_capacity);
int botw_services_close(botw_services *services, uint32_t handle, char *error, size_t error_capacity);
int botw_services_send(botw_services *services, uint32_t handle, void *ipc, size_t ipc_capacity,
                      char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
#endif
