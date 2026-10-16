#include "whole_applet.h"
#include <pthread.h>
#include <stdio.h>

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static whole_applet_snapshot g_state;
static int g_ready;
static unsigned g_notifications;
static uint32_t g_messages[32];
static unsigned g_head, g_count;
static whole_applet_execution g_execution;
static void *g_execution_context;

static void clear_error(char *error, size_t capacity) { if (error && capacity) error[0] = 0; }
static int fail(char *error, size_t capacity, const char *message) {
    if (error && capacity) snprintf(error, capacity, "%s", message);
    return -1;
}
/* All state, queue, subscription and execution transitions hold g_lock. The
 * execution handler cannot reenter; no guest pointers or SDK events enter here. */
static int commit_locked(whole_applet_snapshot next, char *error, size_t capacity) {
    next.suspended = next.policy_configured && next.focus_policy != 1 && !next.foreground;
    uint32_t messages[4]; unsigned count = 0;
    if (!g_ready || next.focus_state != g_state.focus_state) messages[count++] = 15;
    if (g_ready && next.operation_mode != g_state.operation_mode && (g_notifications & WHOLE_APPLET_OPERATION)) messages[count++] = 30;
    if (g_ready && next.performance_mode != g_state.performance_mode && (g_notifications & WHOLE_APPLET_PERFORMANCE)) messages[count++] = 31;
    if (g_ready && g_state.suspended && !next.suspended && (g_notifications & WHOLE_APPLET_RESUME)) messages[count++] = 16;
    if (count > 32 - g_count) return fail(error, capacity, "Host applet notification queue is full.");
    if (next.suspended && !g_execution)
        return fail(error, capacity, "Requested guest suspension has no real execution-policy handler.");
    if (next.policy_configured && g_execution) {
        int result = g_execution(g_execution_context, next.focus_policy, next.foreground,
            next.focused, next.suspended, error, capacity);
        if (error && capacity) error[capacity - 1] = 0;
        if (result) {
            if (!error || !capacity || !error[0])
                return fail(error, capacity, "Host execution-policy handler rejected the applet transition.");
            return -1;
        }
    }
    for (unsigned i = 0; i < count; ++i) g_messages[(g_head + g_count++) % 32] = messages[i];
    g_state = next; g_ready = 1;
    return 0;
}
int whole_applet_bind_execution(whole_applet_execution handler, void *context, char *error, size_t capacity) {
    clear_error(error, capacity);
    if (!handler) return fail(error, capacity, "Execution-policy handler is required for binding.");
    pthread_mutex_lock(&g_lock);
    int result = 0;
    if (g_execution || g_state.suspended) result = fail(error, capacity, "Execution-policy handler cannot be replaced during a run.");
    else { g_execution = handler; g_execution_context = context; }
    pthread_mutex_unlock(&g_lock); return result;
}
int whole_applet_publish(int foreground, int focused, unsigned operation, unsigned performance,
                         char *error, size_t capacity) {
    clear_error(error, capacity);
    if ((foreground != 0 && foreground != 1) || (focused != 0 && focused != 1) ||
        (!foreground && focused) || operation > 1 || performance > 1)
        return fail(error, capacity, "Invalid measured host foreground/focus/operation/performance state.");
    pthread_mutex_lock(&g_lock);
    whole_applet_snapshot next = g_state;
    next.foreground = foreground; next.focused = focused;
    next.focus_state = !foreground ? 3u : focused ? 1u : 2u;
    next.operation_mode = operation; next.performance_mode = performance;
    int result = commit_locked(next, error, capacity);
    pthread_mutex_unlock(&g_lock); return result;
}
int whole_applet_set_focus_policy(unsigned mode, char *error, size_t capacity) {
    clear_error(error, capacity);
    if (mode > 2) return fail(error, capacity, "Base-v0 FocusHandlingMode must be 0, 1 or 2.");
    pthread_mutex_lock(&g_lock);
    int result;
    if (!g_ready) result = fail(error, capacity, "Focus policy requires measured host applet state.");
    else {
        whole_applet_snapshot next = g_state;
        next.focus_policy = mode; next.policy_configured = 1;
        result = commit_locked(next, error, capacity);
    }
    pthread_mutex_unlock(&g_lock); return result;
}
int whole_applet_set_notifications(unsigned bit, int enabled, char *error, size_t capacity) {
    clear_error(error, capacity);
    if ((bit != WHOLE_APPLET_RESUME && bit != WHOLE_APPLET_OPERATION && bit != WHOLE_APPLET_PERFORMANCE) ||
        (enabled != 0 && enabled != 1)) return fail(error, capacity, "Invalid host applet notification subscription.");
    pthread_mutex_lock(&g_lock);
    if (enabled) g_notifications |= bit;
    else g_notifications &= ~bit;
    pthread_mutex_unlock(&g_lock); return 0;
}
int whole_applet_query(whole_applet_snapshot *out, char *error, size_t capacity) {
    clear_error(error, capacity);
    if (!out) return fail(error, capacity, "Applet query requires an output snapshot.");
    pthread_mutex_lock(&g_lock);
    int result = 0;
    if (!g_ready) result = fail(error, capacity, "Host applet requires measured lifecycle state before SDK use.");
    else *out = g_state;
    pthread_mutex_unlock(&g_lock); return result;
}
int whole_applet_try_pop(uint32_t *message, char *error, size_t capacity) {
    clear_error(error, capacity);
    if (!message) return fail(error, capacity, "Applet notification requires an output word.");
    pthread_mutex_lock(&g_lock);
    int result = 0;
    if (!g_ready) result = fail(error, capacity, "Host applet requires measured lifecycle state before SDK use.");
    else if (g_count) { *message = g_messages[g_head]; g_head = (g_head + 1) % 32; --g_count; result = 1; }
    pthread_mutex_unlock(&g_lock); return result;
}
