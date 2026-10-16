#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

enum { WHOLE_APPLET_RESUME = 1, WHOLE_APPLET_OPERATION = 2, WHOLE_APPLET_PERFORMANCE = 4 };
typedef struct {
    int foreground, focused;
    unsigned focus_state, operation_mode, performance_mode;
    unsigned focus_policy;
    int policy_configured, suspended;
} whole_applet_snapshot;
/* A real execution-policy implementation, never guest code or diagnostic thread
 * suspension. Return 0 only after the requested execution state is actually
 * established, -1 with a diagnostic otherwise. Invoked under the applet lock;
 * it must not reenter this API or acquire frontend/PCM locks. context and code
 * remain valid for the process. Binding is optional until suspension is requested. */
typedef int (*whole_applet_execution)(void *context, unsigned mode, int foreground,
    int focused, int suspended, char *error, size_t error_capacity);
int whole_applet_bind_execution(whole_applet_execution handler, void *context,
    char *error, size_t error_capacity);
/* Host producer. States and notifications commit together; a rejected policy,
 * invalid input or full queue changes neither. Focus states match the SDK:
 * 1 = foreground/focused, 2 = foreground/unfocused, 3 = background. Without an
 * explicitly configured policy this reports state without inventing suspension.
 * Message order is focus(15), operation(30), performance(31), actual resume(16).
 * Resume is generated only for an execution-handler-accepted suspend->run edge. */
int whole_applet_publish(int foreground, int focused, unsigned operation_mode,
    unsigned performance_mode, char *error, size_t error_capacity);
/* Exact base-v0 public enum accepts 0..2. Mode 0/2 suspend in background;
 * mode 1 never requests suspension. Unsupported suspension returns a real error. */
int whole_applet_set_focus_policy(unsigned mode, char *error, size_t error_capacity);
int whole_applet_set_notifications(unsigned notification, int enabled,
    char *error, size_t error_capacity);
/* No default state: query/pop fail until the first measured host publication.
 * query returns 0/-1. try_pop returns 1 when a message was written, 0 when empty,
 * -1 on invalid/uninitialized input. Empty/failure does not write the output. */
int whole_applet_query(whole_applet_snapshot *out, char *error, size_t error_capacity);
int whole_applet_try_pop(uint32_t *message, char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
