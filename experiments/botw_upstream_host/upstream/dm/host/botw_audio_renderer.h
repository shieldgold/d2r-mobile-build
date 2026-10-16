// Explicit BotW 1.9.0 CPU AudioRenderer bring-up (DM_BOTW_AUDIO_CPU=1).
// With the variable unset every intercepted SDK export forwards to the native SDK.
#pragma once

// Host shutdown: halts rendering workers and closes PCM sinks without calling the SDK.
// Later guest renderer calls stay native and produce no PCM. Not async-signal-safe;
// may wait for one in-flight native Execute. No-op unless CPU mode is active.
void botw_audio_renderer_shutdown(void);

// Device-service integration. Mode selection is latched and profile checked by the renderer.
int botw_audio_renderer_cpu_enabled(void);
// Applies finite, clamped [0,128] gain to existing and future host PCM sinks.
void botw_audio_renderer_set_output_volume(float volume);
