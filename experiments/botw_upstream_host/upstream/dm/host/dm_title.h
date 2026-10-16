// Title descriptors and selection: which titles are registered in a host binary (the __DATA,swtitle section), and one is chosen at run time by DM_TITLE.
// The title decides: the default module list, argv[0], the ProgramId reported by GetInfo, how unknown SVCs are handled, and the host hooks that belong to that title only (after loading, before nnMain, host call numbers, etc.).
// The core (main.c / loader.c / hle.c / hle_svc.c / thread.c) reaches title code only through here and never references the Diablo II add-on modules directly.
#pragma once
#include <stdint.h>

typedef struct dm_title_desc {
    int id;                          // DM_TITLE_* | DM_TITLE_*
    const char *name;                // The value of DM_TITLE ("d2r" / "botw")
    const char *argv0;               // Default argv[0] (DM_ARGV0 overrides)
    uint64_t program_id;             // Value reported by svc GetInfo(ProgramId)
    const char *const *modules;      // Default module list (NULL-terminated, in load order)
    // Forced takeover entries written for another title that this title uses as well (listed by symbol name one by one, NULL-terminated): still checked for being referenced at load time; listing a name that has no entry aborts
    const char *const *force_also;
    // The hooks below may be NULL (= the title has no such step)
    void (*on_select)(void);                     // Called right after the title is chosen (for installing hooks)
    int (*app_bootstrap)(int *argc, char ***argv);   // When packaged as a Mac .app and double-clicked, prepare environment and arguments; nonzero return = App form
    void (*app_boot)(void);                      // Step before mounting the save in App form (Diablo II: iCloud sync)
    void (*host_early)(void);                    // After crash_install, before loading modules
    void (*post_load)(void);                     // After loading modules: probes / tick gate / keys etc.
    void (*prefetch)(const char *dir);           // After loading modules: start background prefetch
    void (*guest_early)(void);                   // After guest main thread init, before SDK init
    void (*before_nnmain)(void);                 // After each module's init_array and SetHostArgv, before nnMain
    uint64_t (*svc_host_call)(int num, uint64_t *regs);   // Host call numbers from 0x80 (cut convention); nonzero return = handled
} dm_title_desc;

// Register a title (into the __DATA,swtitle section; one binary may register several)
#define DM_TITLE_REGISTER(desc) \
    __attribute__((used, section("__DATA,swtitle"))) static const dm_title_desc *const dm_title_reg_##desc = &desc

// Choose the title by DM_TITLE (default botw); may be called repeatedly, only the first call takes effect. Aborts with a red report if this binary does not register that title
void dm_title_select(void);
const dm_title_desc *dm_title(void);   // Current title (selects first if not yet chosen)
// Module list: the command line's if given; otherwise DM_MODULES (comma / space / colon separated); otherwise the title default. Returns the count
int dm_title_modules(const char **names, int n_cmdline, int cap);
const char *dm_title_argv0(void);      // DM_ARGV0 or the title default
// Bounded run: with DM_STOP_ON_UNIMPL=1, the first time something unimplemented is hit (UNIMPL stub / a title's UNIMPL boundary / unknown SVC of a strict title), log one line, print a backtrace and end with exit code 3
int dm_stop_on_unimpl(void);
void dm_unimpl_hit(const char *what, uint64_t lr, uint64_t fp);
