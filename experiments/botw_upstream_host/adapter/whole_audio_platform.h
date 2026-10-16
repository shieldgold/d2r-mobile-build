#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* Real AudioUnit subtype: RemoteIO on iOS, DefaultOutput on macOS. */
uint32_t whole_audio_platform_output_subtype(void);
/* Call only after the real AVAudioSession is active when enabling, and before
 * deactivating it when disabling. Returns 0 on success, -1 on any sink failure;
 * writes a terminated diagnostic when error_capacity is nonzero. Pausing invokes
 * every sink's original stop/quiesce/flush. Resume starts only guest-requested
 * sinks. A failed resume rolls every sink back to inactive and propagates error.
 * New sinks inherit this platform gate. iOS begins inactive; macOS begins active.
 * This controls host PCM only; it is not an applet/focus producer or AVAudioSession.
 * The callback never acquires this lock. Lifecycle never calls guest or audio-worker
 * code: lock order is worker-owned lock -> platform lock -> PCM/AudioUnit only. */
int whole_audio_platform_set_active(int active, char *error, size_t error_capacity);

/* Internal producer serialization for patched botw_pcm.c. These are not realtime
 * APIs. Hold the platform lock for every public PCM producer/control/close call.
 * register/request/unregister require that lock and never acquire it again. */
struct botw_pcm_sink;
typedef int (*whole_audio_sink_apply)(struct botw_pcm_sink *, int active);
void whole_audio_platform_lock(void);
void whole_audio_platform_unlock(void);
int whole_audio_platform_register_locked(struct botw_pcm_sink *sink, whole_audio_sink_apply apply);
int whole_audio_platform_request_locked(struct botw_pcm_sink *sink, int requested_active);
void whole_audio_platform_unregister_locked(struct botw_pcm_sink *sink);
#ifdef __cplusplus
}
#endif
