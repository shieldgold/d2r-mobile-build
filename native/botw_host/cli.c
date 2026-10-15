#include "runtime.h"
#include <stdio.h>
#include <string.h>
static void report(const char *s) { puts(s); fflush(stdout); }
int main(int argc, char **argv) {
    if (argc < 2 || argc > 3) { fprintf(stderr, "usage: botw-host BUNDLE_ROOT [--load-only|--sdk-probe|--boot-game]\n"); return 64; }
    enum botw_host_mode mode = BOTW_HOST_SDK_PROBE;
    if (argc == 3) {
        if (!strcmp(argv[2], "--load-only")) mode = BOTW_HOST_LOAD_ONLY;
        else if (!strcmp(argv[2], "--boot-game")) mode = BOTW_HOST_BOOT_GAME;
        else if (strcmp(argv[2], "--sdk-probe")) return 64;
    }
    return botw_host_run_mode(argv[1], argv[1], report, mode);
}
