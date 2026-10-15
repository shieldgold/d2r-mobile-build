/* BOTW native host experiment. No game content is distributed with this code. */
#ifndef BOTW_HOST_RUNTIME_H
#define BOTW_HOST_RUNTIME_H
#ifdef __cplusplus
extern "C" {
#endif

typedef void (*botw_host_event)(const char *message);
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
#ifdef __cplusplus
}
#endif
#endif
