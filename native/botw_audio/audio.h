#ifndef BOTW_HOST_AUDIO_H
#define BOTW_HOST_AUDIO_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct botw_audio botw_audio;
typedef struct {
    void *opaque;
    /* Protected synchronous guest copies (including concurrent VM changes). Return
     * 0/-1 and never leave a borrowed guest pointer with an audio thread. */
    int (*read_guest)(void *, uint64_t source, void *destination, size_t bytes, char *, size_t);
    int (*write_guest)(void *, uint64_t destination, const void *source, size_t bytes, char *, size_t);
    int (*create_event)(void *, int initial_signaled, uint32_t *writer, uint32_t *reader, char *, size_t);
    int (*signal_event)(void *, uint32_t writer, char *, size_t);
    int (*close_handle)(void *, uint32_t handle, char *, size_t);
    /* Called only on the guest calling thread, never the AudioUnit/notifier.
     * Use the verified original SDK AttachReadableHandleToSystemEvent with
     * managed=true, clearMode=1. On success the guest owns reader; on failure
     * it remains the caller's responsibility. Validate the guest event span. */
    int (*attach_system_event)(void *, uint64_t guest_event, uint32_t reader, char *, size_t);
    /* SDK wrapper faults are reported after all audio locks are released.
     * Worker errors are retained until the next guest call, not reported from
     * AudioUnit, event notifier, lifecycle or shutdown threads. */
    void (*fault)(void *, const char *message);
    void (*log)(void *, const char *message);
} botw_audio_callbacks;

/* One live context per host process. No AudioUnit is opened until the guest
 * opens AudioOut. Product output requires actual CoreAudio; there is no silent
 * timer fallback. initial_active is explicit measured host foreground/session
 * state (0/1). Non-Apple hosts reject creation. */
botw_audio *botw_audio_create(const botw_audio_callbacks *, int initial_active, char *error, size_t error_capacity);
/* Pause real playback while retaining queued PCM. No buffers are consumed or
 * release events fabricated while inactive. Resume failure is reported and
 * retained for the next guest call. Callers combine focus/interruption causes. */
int botw_audio_set_active(botw_audio *, int active, char *error, size_t error_capacity);
/* Unbind, stop/dispose AudioUnits, join notification threads, then close owned
 * writers and free owned PCM/context. 0 confirms quiescence and frees context;
 * caller must clear its pointer. -1 retains context/backing for a safe retry.
 * Stop guest callers before shutdown. NULL is an already-complete shutdown. */
int botw_audio_shutdown(botw_audio *, char *error, size_t error_capacity);
/* Pure fixed-base-1.0 symbol profile: 1 resolved, 0 outside this module (all
 * AudioRenderer symbols included), -1 recognized name with wrong SDK identity,
 * value, size or text bounds. Only sdk at canonical module index=5 is accepted.
 * Returns typed SDK ABI entry points; create the context before guest use. */
int botw_audio_resolve_symbol(const char *module_name, unsigned module_index,
    const char *symbol, uint64_t original_value, uint64_t original_size,
    size_t text_end, void **function, char *error, size_t error_capacity);
typedef struct {
    /* Valid callback frames include underrun silence; queued_bytes retains
     * pending plus completed nodes until the guest collects them. */
    uint64_t render_callbacks, rendered_frames, completed_buffers, queued_bytes;
    uint32_t live_outputs;
} botw_audio_stats;
int botw_audio_get_stats(botw_audio *, botw_audio_stats *);

#ifdef __cplusplus
}
#endif
#endif
