/* BOTW native host experiment. No game content is distributed with this code. */
#ifndef BOTW_HOST_RUNTIME_H
#define BOTW_HOST_RUNTIME_H
#ifdef __cplusplus
extern "C" {
#endif

typedef void (*botw_host_event)(const char *message);
typedef struct botw_input botw_input;
enum botw_host_mode {
    BOTW_HOST_LOAD_ONLY = 0,
    BOTW_HOST_SDK_PROBE = 1,
    BOTW_HOST_BOOT_GAME = 2
};
/* Returns 0 for the requested stage only, 1 for unsupported/invalid guest state,
 * 2 if another run is active. Neither LOAD_ONLY nor SDK_PROBE enters nnMain.
 * The default is SDK_PROBE. Call on a dedicated background thread. */
int botw_host_run(const char *bundle_root, const char *documents_root, botw_host_event event);
int botw_host_run_mode(const char *bundle_root, const char *documents_root,
                       botw_host_event event, enum botw_host_mode mode);
/* The caller retains layer through the run; NULL selects an offscreen backend.
 * Call before run. Lifecycle notifications may be supplied from the main UI. */
int botw_host_set_graphics_layer(void *layer);
/* Input is owned by the frontend and must outlive all guest threads. */
int botw_host_set_input(botw_input *input);
/* Explicit target policy; default is the original 36-bit address space. The
 * 39-bit mode requires separately verified SDK transport adaptations. Returns
 * 1 for unsupported widths, 2 while running, 0 after saving configuration. */
int botw_host_set_address_space(unsigned bits);
void botw_host_set_applet_state(int foreground, int focused);
/* Call after frontend sampling to publish controller style notifications. */
void botw_host_refresh_input(void);
#ifdef __cplusplus
}
#endif
#endif
