#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void report(const char *s) { puts(s); fflush(stdout); }
int main(int argc, char **argv) {
    if (argc < 2 || argc > 4) { fprintf(stderr, "usage: botw-host BUNDLE_ROOT [--load-only|--sdk-probe|--boot-game] [--host39]\n"); return 64; }
    enum botw_host_mode mode = BOTW_HOST_SDK_PROBE;
    int have_mode = 0, host39 = 0;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--host39")) { if (host39++) return 64; continue; }
        if (have_mode++) return 64;
        if (!strcmp(argv[i], "--load-only")) mode = BOTW_HOST_LOAD_ONLY;
        else if (!strcmp(argv[i], "--boot-game")) mode = BOTW_HOST_BOOT_GAME;
        else if (strcmp(argv[i], "--sdk-probe")) return 64;
    }
    if (host39 && botw_host_set_address_space(39)) return 64;
    char *root = realpath(argv[1], NULL);
    if (!root) { perror("bundle directory"); return 66; }
    int result = botw_host_run_mode(root, root, report, mode);
    free(root); return result;
}
