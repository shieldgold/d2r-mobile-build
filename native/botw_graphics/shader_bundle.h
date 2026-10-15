#ifndef BOTW_SHADER_BUNDLE_H
#define BOTW_SHADER_BUNDLE_H
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    size_t declared_entries;       /* Unique control/code/stage identities. */
    size_t registered_entries;     /* Exact, supported identities registered. */
    size_t unsupported_entries;    /* Diagnosed individually; never registered. */
} botw_graphics_shader_bundle_result;
/* Returns 1 for a valid manifest and its registered subset, 0 for any error.
 * Success is not a claim that unsupported entries work. Result is populated
 * only after a complete preflight. Diagnostic identities/reasons go to stderr.
 * Call after botw_graphics_init, before starting guest shader requests. */
int botw_graphics_load_shader_bundle(const char *directory,
    botw_graphics_shader_bundle_result *result, char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
#endif
