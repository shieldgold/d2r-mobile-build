/* BOTW native host experiment.
 * Loader/gate/TSD design derived from jhaizhou-ops/d2r-mobile, MIT, commit
 * e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8. See THIRD_PARTY_LICENSE.txt.
 * This implementation rejects unsupported operations; it never substitutes
 * a successful return value for an unimplemented SDK call or kernel service.
 */
#include "runtime.h"
#include "services.h"
#include "kernel.h"
#include "hipc_address.h"
#include "address_space.h"
#include "../botw_input/input.h"
#include "../botw_resources/romfs.h"
#include "../botw_resources/savefs.h"
#include "../botw_resources/local_user.h"
#include <TargetConditionals.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <pthread.h>
#include <setjmp.h>
#include <signal.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/ucontext.h>
#include <time.h>
#include <unistd.h>
#if defined(BOTW_WITH_AUDIO)
#include "../botw_audio/audio.h"
#endif
#if TARGET_OS_IPHONE
#include <os/proc.h>
#endif

/* Headless tests have no Metal dependency. Production builds must link the
 * actual backend; an absent symbol is a link failure rather than a stub. */
#if defined(BOTW_WITH_GRAPHICS)
#include "../botw_graphics/shader_bundle.h"
#include "../botw_graphics/graphics.h"
extern int botw_graphics_init(void *, void (*)(const char *), int (*)(const void *, size_t));
extern void botw_graphics_shutdown(void);
extern void *botw_nvn_bootstrap_loader(const char *);
static int (*graphics_backend_init)(void *, void (*)(const char *), int (*)(const void *, size_t)) = botw_graphics_init;
static void (*graphics_backend_shutdown)(void) = botw_graphics_shutdown;
static void *(*graphics_backend_loader)(const char *) = botw_nvn_bootstrap_loader;
#else
static int (*graphics_backend_init)(void *, void (*)(const char *), int (*)(const void *, size_t));
static void (*graphics_backend_shutdown)(void);
static void *(*graphics_backend_loader)(const char *);
#endif
static void *(*host_pool_push)(void);
static void (*host_pool_pop)(void *);

/* Immutable after release publication. The signal path never takes the
 * registration mutex; process-lifetime functions avoid retirement races. */
static pthread_mutex_t tracking_registration_lock = PTHREAD_MUTEX_INITIALIZER;
static _Atomic int tracking_ready;
static botw_host_memory_fault tracking_fault;
static botw_host_memory_prepare tracking_prepare;
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "signal tracking publication must be lock-free");

int botw_host_register_memory_tracking(botw_host_memory_fault fault, botw_host_memory_prepare prepare) {
    if (!fault || !prepare) return -1;
    pthread_mutex_lock(&tracking_registration_lock);
    int result = 0;
    if (atomic_load_explicit(&tracking_ready, memory_order_acquire)) {
        if (tracking_fault != fault || tracking_prepare != prepare) result = -1;
    } else {
        tracking_fault = fault; tracking_prepare = prepare;
        atomic_store_explicit(&tracking_ready, 1, memory_order_release);
    }
    pthread_mutex_unlock(&tracking_registration_lock);
    return result;
}
int botw_host_prepare_guest_write(void *address, size_t bytes, char *error, size_t capacity) {
    if (error && capacity) error[0] = 0;
    if (!bytes) return 0;
    if (!address || bytes > UINTPTR_MAX - (uintptr_t)address) {
        if (error && capacity) snprintf(error, capacity, "memory tracking: invalid host write span");
        return -1;
    }
    if (!atomic_load_explicit(&tracking_ready, memory_order_acquire)) return 0;
    if (tracking_prepare(address, bytes, error, capacity)) {
        if (error && capacity && !error[0]) snprintf(error, capacity, "memory tracking: host write preparation failed");
        return -1;
    }
    return 0;
}
static int handle_memory_tracking_fault(int number, siginfo_t *info, void *context) {
    if ((number != SIGSEGV && number != SIGBUS) || !info || !context || !info->si_addr ||
            !atomic_load_explicit(&tracking_ready, memory_order_acquire)) return 0;
    ucontext_t *uc = context;
    uint32_t esr = uc->uc_mcontext->__es.__esr;
    unsigned exception_class = esr >> 26, fault_status = esr & 0x3f;
    /* ARM64 data-write permission faults only. Instruction/translation/read
     * faults cannot be attributed to this module's temporary read protection. */
    if ((exception_class != 0x24 && exception_class != 0x25) || !(esr & (1u << 6)) ||
            fault_status < 0x0c || fault_status > 0x0f) return 0;
    return tracking_fault(info->si_addr, 1, context) == 1;
}

#define PAGE 0x4000u
#define MODULE_COUNT 6
#define MAX_IMAGE (512u * 1024u * 1024u)
#define MAX_STUB 256
#define MAX_GATES 4096
#define MAX_SYMBOLS 1000000u
/* Fixed base-v0 main.npdm META flags[0x0c]=0x03: 64BitOld/36-bit AS.
 * nnosInitialize also initializes its allocator with upper bound 0x1000000000. */
#define BOTW_ADDRESS_SPACE_SIZE (1ull << 36)
#define ROUND_PAGE(n) (((n) + PAGE - 1) & ~(size_t)(PAGE - 1))
typedef struct { int64_t tag; uint64_t value; } Dyn;
typedef struct { uint32_t name; uint8_t info, other; uint16_t section; uint64_t value, size; } Sym;
typedef struct { uint64_t offset, info; int64_t addend; } Rela;
typedef struct {
    const char *name;
    uint8_t *base;
    size_t size, text_end, ro_off, data_off, patch_off, key_off, key_size;
    unsigned key_idx[8];
    uint64_t dyn_off, sym_off, str_off, str_size, sym_count, rela_off, rela_size, plt_off, plt_size, init;
} Module;
typedef struct { uint64_t ret, original; int kind, arg; } Gate;
static const char *const module_names[MODULE_COUNT] = {"main", "subsdk0", "subsdk1", "subsdk2", "subsdk3", "sdk"};
static const char *const key_names[8] = {"K18", "KTLS", "KTLR", "KGATE", "KRES0", "KRES1", "KASPECT", "KASPECTUI"};
static pthread_mutex_t run_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t lifecycle_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t focus_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t focus_changed = PTHREAD_COND_INITIALIZER;
static int focus_suspended, focus_stopping;
static void *configured_graphics_layer;
static botw_input *configured_input;
static unsigned configured_address_bits = 36;
static int host_foreground = 1, host_focused = 1;
static int host_audio_session_active = 1;
static uint32_t run_sequence;
/* Kernel condition waiters must register here before waiting. The current host
 * rejects CreateThread and WaitProcessWideKeyAtomic, so this list is provably
 * empty during the single guest-thread SDK probe. Future wait support must use
 * this same mutex/list and remove nodes only after observing `signalled`. */
typedef struct ConditionWaiter {
    struct ConditionWaiter *next;
    uint64_t key;
    pthread_cond_t condition;
    int signalled;
} ConditionWaiter;
static pthread_mutex_t condition_lock = PTHREAD_MUTEX_INITIALIZER;
static struct {
    Module modules[MODULE_COUNT];
    Gate gates[MAX_GATES];
    unsigned gate_count, stub_count, key_count;
    size_t weak_zero, relocations;
    char *stubs[MAX_STUB];
    pthread_key_t keys[8];
    void *framework, *tls, *tlr;
    ConditionWaiter *condition_waiters;
    uint64_t random_entropy[4];
    botw_services *services;
    botw_kernel *kernel;
    botw_input *input;
    botw_romfs *romfs;
    botw_savefs *savefs;
    botw_local_user local_user;
#if defined(BOTW_WITH_AUDIO)
    botw_audio *audio;
#endif
    int input_owned;
    uint32_t input_shared_handle;
    uint64_t resource_user_id;
    int graphics_started;
    /* Guest NVIDIA transport workspace is borrowed by the host platform until
     * the Metal backend has waited for GPU completion during cleanup. The host
     * does not construct an original NVIDIA client or write its SDK globals. */
    void *graphics_workspace;
    size_t graphics_workspace_size;
    int host39_transport_audited;
    int heap_policy_active, heap_initial_sized;
    size_t heap_size, root_size, image_bytes;
    /* Accessed by the real frontend display callback under lifecycle_lock. */
    void *vi_display, *vi_layer;
    uint32_t vi_vsync_write, vi_vsync_read;
    double vi_last_target_timestamp;
    int vi_initialized, vi_display_live;
    botw_host_event event;
    sigjmp_buf recovery;
    pthread_t owner;
    volatile sig_atomic_t active, fault_signal, guest_executing;
    volatile uintptr_t fault_pc;
    volatile uintptr_t fault_address, fault_registers[29];
    stack_t previous_stack;
    void *signal_stack;
    unsigned signal_count;
    int alternate_stack_installed;
    int guest_started;
    unsigned address_bits;
    botw_address_space_policy allocator_policy[2];
    const char *current_entry;
    struct sigaction previous[4];
} state;
typedef struct {
    sigjmp_buf recovery;
    volatile sig_atomic_t active, fault_signal;
    volatile uintptr_t fault_pc;
    volatile uintptr_t fault_address, fault_registers[29];
    void *tls, *tlr, *signal_stack, *autorelease_pool;
    stack_t previous_stack;
    int alternate_stack_installed;
    char error[512];
} ChildContext;
static _Thread_local ChildContext *child_context;
/* A synchronous guest callback must return through its C caller before a
 * graphics failure can unwind the outer run. These thread-local boundaries
 * contain no Objective-C ownership and nest without replacing worker state. */
typedef struct CallbackContext {
    sigjmp_buf recovery;
    volatile sig_atomic_t active, fault_signal;
    volatile uintptr_t fault_pc, fault_address, fault_registers[29];
    char error[1536];
    struct CallbackContext *previous;
    sig_atomic_t previous_guest_executing;
} CallbackContext;
static _Thread_local CallbackContext *callback_context;
#if defined(BOTW_WITH_GRAPHICS) || defined(BOTW_WITH_AUDIO)
static int invoke_guest_callback(uint64_t target, const char *entry, const char *fault_prefix,
    void (*invoke)(uint64_t, void *), void *arguments, char *error, size_t capacity);
#endif
extern void botw_gate(void);
extern unsigned char botw_unimplemented_stubs[];
static const int caught_signals[] = {SIGSEGV, SIGBUS, SIGILL, SIGTRAP};

static void report(const char *format, ...) {
    char message[2048]; va_list ap; va_start(ap, format);
    vsnprintf(message, sizeof message, format, ap); va_end(ap);
    if (state.event) state.event(message);
}
__attribute__((noreturn, format(printf, 1, 2)))
static void fail(const char *format, ...) {
    char message[1536]; va_list ap; va_start(ap, format);
    vsnprintf(message, sizeof message, format, ap); va_end(ap);
    if (callback_context && callback_context->active) {
        snprintf(callback_context->error, sizeof callback_context->error, "%s", message);
        siglongjmp(callback_context->recovery, 1);
    }
    report("error=%s", message);
    if (child_context && child_context->active) {
        snprintf(child_context->error, sizeof child_context->error, "%s", message);
        siglongjmp(child_context->recovery, 1);
    }
    if (!pthread_equal(pthread_self(), state.owner)) abort();
    state.guest_executing = 0;
    siglongjmp(state.recovery, 1);
}
static int in_range(size_t off, size_t size, size_t total) { return off <= total && size <= total - off; }
static void require_range(Module *m, uint64_t off, uint64_t count, const char *what) {
    if (!in_range(off, count, m->size)) fail("%s: %s outside mapped image", m->name, what);
}
static void path_for(char *out, size_t cap, const char *root, const char *name, const char *ext) {
    int n = snprintf(out, cap, "%s/botw/%s.%s", root, name, ext);
    if (n < 0 || (size_t)n >= cap) fail("bundle path exceeds limit");
}
static void read_layout(Module *m, const char *root) {
    char path[4096], line[256], key[64], tail; unsigned long long value;
    path_for(path, sizeof path, root, m->name, "fixups.tsv");
    FILE *f = fopen(path, "r"); if (!f) fail("open layout for %s: %s", m->name, strerror(errno));
    unsigned seen = 0;
    while (fgets(line, sizeof line, f)) {
        if (sscanf(line, "# %63s %lli %c", key, &value, &tail) != 2) { fclose(f); fail("%s: invalid layout row", m->name); }
        size_t *slot = NULL; unsigned index = 0;
        if (!strcmp(key, "PATCH_OFF")) { slot = &m->patch_off; index = 0; }
        else if (!strcmp(key, "KEY_PAGE_OFF")) { slot = &m->key_off; index = 1; }
        else if (!strcmp(key, "KEY_PAGE_SIZE")) { slot = &m->key_size; index = 2; }
        else if (!strcmp(key, "TEXT_END")) { slot = &m->text_end; index = 3; }
        else if (!strcmp(key, "RO_OFF")) { slot = &m->ro_off; index = 4; }
        else if (!strcmp(key, "DATA_OFF")) { slot = &m->data_off; index = 5; }
        else for (unsigned k = 0; k < 8; k++) {
            char expected[64]; snprintf(expected, sizeof expected, "KEY_%s_IDX", key_names[k]);
            if (!strcmp(key, expected)) {
                index = 6 + k;
                if (value >= PAGE / 8) { fclose(f); fail("%s: key index outside key page", m->name); }
                m->key_idx[k] = (unsigned)value; break;
            }
        }
        if (!slot && index < 6) { fclose(f); fail("%s: unknown layout key %s", m->name, key); }
        if (seen & (1u << index)) { fclose(f); fail("%s: duplicate layout key", m->name); }
        seen |= 1u << index; if (slot) *slot = (size_t)value;
    }
    int bad_read = ferror(f); fclose(f);
    if (bad_read || seen != 0x3fff || !m->text_end || m->text_end > MAX_IMAGE ||
        m->ro_off < m->text_end || m->data_off < m->ro_off || m->data_off > m->key_off ||
        m->key_size != PAGE || m->key_off % PAGE || m->patch_off % PAGE ||
        m->key_off < ROUND_PAGE(m->text_end) || m->key_off + PAGE > m->patch_off || m->patch_off > MAX_IMAGE)
        fail("%s: invalid module layout", m->name);
    for (unsigned i = 0; i < 8; i++) for (unsigned j = i + 1; j < 8; j++)
        if (m->key_idx[i] == m->key_idx[j]) fail("%s: duplicate key indices", m->name);
}
#if TARGET_OS_IPHONE
static const struct mach_header_64 *framework_header(const char *root) {
    char path[4096]; int n = snprintf(path, sizeof path, "%s/Frameworks/Guest.framework/Guest", root);
    if (n < 0 || (size_t)n >= sizeof path) fail("framework path exceeds limit");
    state.framework = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!state.framework) fail("load signed Guest.framework: %s", dlerror());
    for (uint32_t i = 0; i < _dyld_image_count(); i++)
        if (!strcmp(_dyld_get_image_name(i), path)) return (const struct mach_header_64 *)_dyld_get_image_header(i);
    fail("loaded Guest.framework header not found");
}
static void remap(void *target, const void *source, size_t size, int copy, vm_prot_t need) {
    vm_address_t to = (vm_address_t)target; vm_prot_t cur = 0, max = 0;
    kern_return_t kr = vm_remap(mach_task_self(), &to, size, 0, VM_FLAGS_FIXED | VM_FLAGS_OVERWRITE,
                              mach_task_self(), (vm_address_t)source, copy, &cur, &max, VM_INHERIT_NONE);
    if (kr != KERN_SUCCESS || to != (vm_address_t)target || (cur & need) != need)
        fail("signed image remap failed: kernel=%d permissions=%x", kr, cur);
}
static void map_module(Module *m, const struct mach_header_64 *header) {
    char section0[17], section1[17], segment[17]; unsigned long s0 = 0, sw = 0, s1 = 0;
    snprintf(section0, sizeof section0, "__dm%sx0", m->name);
    snprintf(section1, sizeof section1, "__dm%sx1", m->name);
    snprintf(segment, sizeof segment, "__DM%sW", m->name);
    const uint8_t *x0 = getsectiondata(header, "__TEXT", section0, &s0);
    const uint8_t *w = getsectiondata(header, segment, "__img", &sw);
    const uint8_t *x1 = getsectiondata(header, "__TEXT", section1, &s1);
    if (!x0 || !w || !x1 || s0 != ROUND_PAGE(m->text_end) || s0 + sw != m->patch_off ||
        !s1 || s1 > MAX_IMAGE - m->patch_off || s0 % PAGE || sw % PAGE || s1 % PAGE ||
        (uintptr_t)x0 % PAGE || (uintptr_t)w % PAGE || (uintptr_t)x1 % PAGE)
        fail("%s: signed framework section layout mismatch", m->name);
    m->size = m->patch_off + s1; vm_address_t address = 0;
    if (vm_allocate(mach_task_self(), &address, m->size, VM_FLAGS_ANYWHERE)) fail("%s: reserve image address", m->name);
    m->base = (uint8_t *)address;
    remap(m->base, x0, s0, FALSE, VM_PROT_READ | VM_PROT_EXECUTE);
    remap(m->base + s0, w, sw, TRUE, VM_PROT_READ | VM_PROT_WRITE);
    remap(m->base + m->patch_off, x1, s1, FALSE, VM_PROT_READ | VM_PROT_EXECUTE);
}
#else
static void map_module(Module *m, const char *root) {
    char path[4096]; path_for(path, sizeof path, root, m->name, "patched.bin");
    int fd = open(path, O_RDONLY); if (fd < 0) fail("open image %s: %s", m->name, strerror(errno));
    struct stat st;
    if (fstat(fd, &st) || st.st_size < (off_t)(m->patch_off + 4) || st.st_size > MAX_IMAGE) { close(fd); fail("%s: invalid image size", m->name); }
    m->size = ROUND_PAGE((size_t)st.st_size);
    m->base = mmap(NULL, m->size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (m->base == MAP_FAILED) { m->base = NULL; close(fd); fail("%s: map image", m->name); }
    size_t done = 0;
    while (done < (size_t)st.st_size) {
        ssize_t got = read(fd, m->base + done, (size_t)st.st_size - done);
        if (got <= 0) { close(fd); fail("%s: truncated image", m->name); } done += (size_t)got;
    }
    close(fd);
    if (mprotect(m->base, ROUND_PAGE(m->text_end), PROT_READ | PROT_EXEC) ||
        mprotect(m->base + m->patch_off, m->size - m->patch_off, PROT_READ | PROT_EXEC))
        fail("%s: seal code pages", m->name);
}
#endif
static void *at(Module *m, uint64_t off, size_t size, const char *what) { require_range(m, off, size, what); return m->base + off; }
static void parse_dynamic(Module *m) {
    uint32_t mod; memcpy(&mod, at(m, 4, 4, "MOD0 pointer"), 4);
    uint8_t *header = at(m, mod, 8, "MOD0 header"); if (memcmp(header, "MOD0", 4)) fail("%s: missing MOD0", m->name);
    int32_t offset; memcpy(&offset, header + 4, 4);
    int64_t dynamic = (int64_t)mod + offset; if (dynamic < 0) fail("%s: negative dynamic offset", m->name);
    m->dyn_off = (uint64_t)dynamic; uint64_t hash_off = 0; unsigned terminated = 0;
    for (unsigned i = 0; i < 65536; i++) {
        Dyn d; memcpy(&d, at(m, m->dyn_off + 16ull * i, 16, "dynamic table"), 16);
        if (!d.tag) { terminated = 1; break; }
        switch (d.tag) {
            case 4: hash_off = d.value; break;
            case 5: m->str_off = d.value; break; case 6: m->sym_off = d.value; break;
            case 10: m->str_size = d.value; break;
            case 11: if (d.value != sizeof(Sym)) fail("%s: unsupported symbol entry size", m->name); break;
            case 7: m->rela_off = d.value; break; case 8: m->rela_size = d.value; break;
            case 9: if (d.value != sizeof(Rela)) fail("%s: unsupported relocation entry size", m->name); break;
            case 12: m->init = d.value; break;
            case 23: m->plt_off = d.value; break; case 2: m->plt_size = d.value; break;
            case 20: if (d.value != 7) fail("%s: unsupported PLT relocation format", m->name); break;
            default: break;
        }
    }
    if (!terminated || !m->sym_off || !m->str_off || !m->str_size) fail("%s: incomplete dynamic metadata", m->name);
    if (hash_off) { uint32_t *h = at(m, hash_off, 8, "symbol hash"); m->sym_count = h[1]; }
    else {
        if (m->str_off < m->sym_off || (m->str_off - m->sym_off) % sizeof(Sym)) fail("%s: missing bounded symbol count", m->name);
        m->sym_count = (m->str_off - m->sym_off) / sizeof(Sym);
    }
    if (m->sym_count > MAX_SYMBOLS) fail("%s: symbol count exceeds limit", m->name);
    at(m, m->sym_off, m->sym_count * sizeof(Sym), "symbol table"); at(m, m->str_off, m->str_size, "string table");
    if (m->rela_size % sizeof(Rela) || m->plt_size % sizeof(Rela)) fail("%s: partial relocation record", m->name);
    at(m, m->rela_off, m->rela_size, "relocations"); at(m, m->plt_off, m->plt_size, "PLT relocations");
}
static const Sym *symbol_at(Module *m, unsigned index) {
    if (index >= m->sym_count) fail("%s: symbol index exceeds table", m->name);
    return (const Sym *)(m->base + m->sym_off + (size_t)index * sizeof(Sym));
}
static const char *symbol_name(Module *m, const Sym *s) {
    if (s->name >= m->str_size) fail("%s: symbol string exceeds table", m->name);
    const char *name = (const char *)m->base + m->str_off + s->name;
    if (!memchr(name, 0, m->str_size - s->name)) fail("%s: unterminated symbol name", m->name);
    return name;
}
static uint64_t defined_value(Module *m, const Sym *s) {
    if (s->section == 0xfff1) return s->value; /* ELF SHN_ABS. */
    require_range(m, s->value, s->size, "defined symbol"); return (uint64_t)m->base + s->value;
}
static uint64_t find_raw_definition(const char *name, Module **module, const Sym **symbol) {
    if (module) *module = NULL;
    if (symbol) *symbol = NULL;
    for (unsigned j = 0; j < MODULE_COUNT; j++) {
        Module *m = &state.modules[j];
        for (unsigned i = 1; i < m->sym_count; i++) {
            const Sym *s = symbol_at(m, i);
            if (s->section && s->info >= 0x10 && !strcmp(symbol_name(m, s), name)) {
                if (module) *module = m;
                if (symbol) *symbol = s;
                return defined_value(m, s);
            }
        }
    }
    return 0;
}
static uint64_t find_raw_symbol(const char *name) { return find_raw_definition(name, NULL, NULL); }
/* Two fixed base-v0 parameters use the pinned upstream PARAMS thunk contract:
 * save x16 below SP, read the allocated key-page/TSD slot, restore x16, resume.
 * Reusing KRES0/KRES1 here is explicit in the packaged policy; no D2R display
 * parameters are installed. The offline packager first verifies the complete
 * original NSO SHA/build-ID profile, then independently unwinds these thunks
 * and verifies the existing Host39 chain. Check actual loaded instructions
 * again before any guest execution. Signed code is never changed at runtime. */
#define HEAP_POLICY_UNIT ((size_t)0x200000)
#define HEAP_POLICY_RESERVE ((size_t)0x10000000)
#define HEAP_POLICY_STARTUP ((size_t)0x02000000)
#define HEAP_POLICY_MIN_ROOT ((size_t)0x20000000)
#define HEAP_POLICY_ORIGINAL ((size_t)0xc2600000)
int botw_host_heap_policy_plan(size_t kernel_available, size_t live_available,
        size_t *heap, size_t *root, char *error, size_t capacity) {
    if (!heap || !root || heap == root || live_available < HEAP_POLICY_RESERVE) {
        if (error && capacity) snprintf(error, capacity, "bounded heap: invalid output or live host memory below reserve");
        return 1;
    }
    size_t value = live_available - HEAP_POLICY_RESERVE;
    if (value > kernel_available) value = kernel_available;
    if (value > HEAP_POLICY_ORIGINAL) value = HEAP_POLICY_ORIGINAL;
    value &= ~(HEAP_POLICY_UNIT - 1);
    if (value < HEAP_POLICY_STARTUP + HEAP_POLICY_MIN_ROOT) {
        if (error && capacity) snprintf(error, capacity, "bounded heap: cannot fit 512 MiB experimental root plus 32 MiB startup block");
        return 1;
    }
    *heap = value; *root = value - HEAP_POLICY_STARTUP; return 0;
}
static uint32_t heap_policy_word(const unsigned char *image, size_t offset) {
    uint32_t word; memcpy(&word, image + offset, sizeof word); return word;
}
static int heap_policy_error(char *error, size_t capacity, const char *message) {
    if (error && capacity) snprintf(error, capacity, "bounded heap image: %s", message);
    return -1;
}
static uint32_t heap_policy_branch(size_t source, size_t target) {
    int64_t delta = (int64_t)target - (int64_t)source;
    return 0x14000000u | ((uint32_t)(delta / 4) & 0x3ffffffu);
}
static int heap_policy_image_mode(Module *m, char *error, size_t capacity) {
    static const size_t offsets[2] = {0x735304, 0x7353b0};
    static const unsigned registers[2] = {0, 8};
    static const uint32_t contexts[2][5] = {
        {0x910003fd, 0xd10043ff, 0x52b84c00, 0x943bf422, 0x910023e0},
        {0x910043e0, 0x940b23a2, 0x52b80c08, 0x910043e0, 0xf9000be8}};
    if (!m || !m->base || !m->name || strcmp(m->name, "main"))
        return heap_policy_error(error, capacity, "main layout is outside the fixed profile");
    /* A legacy/synthetic image without these sites cannot opt into this
     * explicit transformation. Preserve the existing generic loader path. */
    if (!in_range(offsets[1] + 8, 4, m->size)) {
        if (m->size > offsets[0] && (!in_range(offsets[0], 4, m->size) ||
                heap_policy_word(m->base, offsets[0]) != contexts[0][2]))
            return heap_policy_error(error, capacity, "truncated or single patched parameter site");
        return 0;
    }
    const unsigned char *image = m->base;
    uint32_t a = heap_policy_word(image, offsets[0]), b = heap_policy_word(image, offsets[1]);
    if (a == contexts[0][2] && b == contexts[1][2]) return 0; /* Original image: no policy. */
    if ((a & 0xfc000000u) != 0x14000000u || (b & 0xfc000000u) != 0x14000000u ||
            m->key_idx[4] != 4 || m->key_idx[5] != 5 || m->key_off % PAGE ||
            m->key_size != PAGE || offsets[1] + 12 > m->text_end ||
            !in_range(m->key_off, PAGE, m->patch_off) || m->key_off < ROUND_PAGE(m->text_end))
        return heap_policy_error(error, capacity, "requires both audited parameters and the dedicated key slots");
    size_t prior = 0;
    for (unsigned i = 0; i < 2; i++) {
        size_t offset = offsets[i];
        for (unsigned j = 0; j < 5; j++) if (j != 2 &&
                heap_policy_word(image, offset - 8 + j * 4) != contexts[i][j])
            return heap_policy_error(error, capacity, "original parameter context differs");
        uint32_t word = heap_policy_word(image, offset);
        int64_t delta = ((int64_t)((word & 0x3ffffffu) ^ 0x2000000u) - 0x2000000) * 4;
        int64_t target = (int64_t)offset + delta;
        if (target < (int64_t)m->patch_off || target < 0 ||
                !in_range((size_t)target, 32, m->size) || (i && (size_t)target != prior + 32))
            return heap_policy_error(error, capacity, "parameter thunk leaves executable patch range");
        size_t thunk = (size_t)target; unsigned reg = registers[i], tmp = 16;
        int64_t back = (int64_t)(offset + 4) - (int64_t)(thunk + 28);
        if (back < -(1 << 27) || back >= (1 << 27))
            return heap_policy_error(error, capacity, "parameter return branch exceeds ARM64 range");
        int64_t pages = (int64_t)(m->key_off >> 12) - (int64_t)((thunk + 8) >> 12);
        if (pages < -(1 << 20) || pages >= (1 << 20))
            return heap_policy_error(error, capacity, "parameter key page exceeds ADRP range");
        uint32_t imm = (uint32_t)pages & 0x1fffffu;
        uint32_t expected[8] = {0xf81f03f0u, 0xd53bd060u | reg,
            0x90000000u | (imm & 3u) << 29 | (imm >> 2) << 5 | tmp,
            0xf9400000u | m->key_idx[4 + i] << 10 | tmp << 5 | tmp,
            0x8b000000u | tmp << 16 | reg << 5 | reg,
            0xf9400000u | reg << 5 | reg, 0xf85f03f0u,
            heap_policy_branch(thunk + 28, offset + 4)};
        for (unsigned j = 0; j < 8; j++) if (heap_policy_word(image, thunk + j * 4) != expected[j])
            return heap_policy_error(error, capacity, "parameter thunk instruction differs");
        prior = thunk;
    }
    return 1;
}
static void install_heap_parameter_slots(void) {
    if (pthread_setspecific(state.keys[4], (void *)(uintptr_t)state.heap_size) ||
            pthread_setspecific(state.keys[5], (void *)(uintptr_t)state.root_size))
        fail("install bounded heap parameters on actual guest thread");
}
static int services_memory(void *opaque, uint64_t address, size_t length, int writable);
static const char *const allocator_constructor = "_ZN2nn2os6detail21AddressSpaceAllocatorC1Emmm";
static const char *const allocator_constructor_alias = "_ZN2nn2os6detail21AddressSpaceAllocatorC2Emmm";
static const char *const allocator_allocate = "_ZN2nn2os6detail21AddressSpaceAllocator13AllocateSpaceEmm";
static uint64_t original_sdk_symbol(const char *name, uint64_t offset, size_t bytes) {
    Module *sdk = &state.modules[MODULE_COUNT - 1];
    require_range(sdk, offset, bytes, "host39 SDK target");
    uint64_t address = (uint64_t)sdk->base + offset;
    if (strcmp(sdk->name, "sdk") || find_raw_symbol(name) != address)
        fail("host39 SDK symbol does not match audited target: %s", name);
    return address;
}
static uint64_t allocator_kernel_info(unsigned id) {
    uint64_t regs[31] = {0, id, BOTW_KERNEL_CURRENT_PROCESS, 0}; char error[512];
    if (!state.kernel || botw_kernel_dispatch(state.kernel, 0x29, regs, error, sizeof error) != BOTW_KERNEL_HANDLED || regs[0])
        fail("host39 allocator cannot query actual kernel region: info=%u", id);
    return regs[1];
}
static void host_allocator_constructor(void *object, uint64_t begin, uint64_t end, uint64_t guard) {
    uint64_t singleton = original_sdk_symbol("_ZN2nn2os6detail26g_OsResourceManagerStorageE", 0x6cac28, 0x3a8);
    uint64_t original = original_sdk_symbol(allocator_constructor, 0x8a308, 0x4c);
    botw_address_space_region alias = {allocator_kernel_info(2), allocator_kernel_info(3)};
    botw_address_space_region stack = {allocator_kernel_info(14), allocator_kernel_info(15)};
    botw_address_space_policy policy; char error[512];
    if (state.address_bits != 39) fail("host39 allocator requires the explicit 39-bit policy");
    if (botw_address_space_adapt_constructor((uint64_t)object, singleton,
            begin, end, guard, &alias, &stack, botw_kernel_address_space_limit(state.kernel),
            (size_t)getpagesize(), &policy, error, sizeof error)) fail("host39 allocator constructor: %s", error);
    if (!services_memory(NULL, (uint64_t)object, 0x28, 1)) fail("host39 allocator object is not writable guest memory");
    ((void (*)(void *, uint64_t, uint64_t, uint64_t))original)(object, policy.begin, policy.end, policy.guard);
    state.allocator_policy[policy.kind - 1] = policy;
    report("guest_allocator_region=%s base=0x%llx bytes=%llu guard=%llu",
        policy.kind == BOTW_ADDRESS_SPACE_ALIAS ? "alias" : "stack",
        (unsigned long long)policy.region.base, (unsigned long long)policy.region.size, (unsigned long long)policy.guard);
}
static uint64_t host_allocator_allocate(void *object, uint64_t bytes, uint64_t alignment) {
    const botw_address_space_policy *policy = NULL;
    for (unsigned i = 0; i < 2; i++)
        if (state.allocator_policy[i].kind && state.allocator_policy[i].object == (uint64_t)object) policy = &state.allocator_policy[i];
    if (!policy || state.address_bits != 39) fail("host39 allocation from an unaudited allocator object");
    uint64_t normalized; char error[512];
    if (botw_address_space_normalize_alignment(alignment, policy->host_page, &normalized, error, sizeof error))
        fail("host39 allocator alignment: %s", error);
    uint64_t original = original_sdk_symbol(allocator_allocate, 0x8a3b4, 0xfc);
    uint64_t address = ((uint64_t (*)(void *, uint64_t, uint64_t))original)(object, bytes, normalized);
    if (address && botw_address_space_validate_allocation(policy, address, bytes, normalized, error, sizeof error))
        fail("host39 allocator result: %s", error);
    return address;
}
#if defined(BOTW_WITH_GRAPHICS)
static const char *const graphics_platform_initialize = "_ZN2nv18InitializeGraphicsEPvm";
static uint64_t audited_platform_export(unsigned index, const char *name, uint64_t offset, size_t bytes) {
    Module *m = &state.modules[index];
    if (state.address_bits != 39 || !state.host39_transport_audited)
        fail("graphics platform requires the completed host39 transport audit");
    if (!m->name || strcmp(m->name, module_names[index]) || !m->base ||
            !in_range(offset, bytes, m->text_end) || !in_range(offset, bytes, m->size))
        fail("graphics platform profile requires %s index=%u text entry=0x%llx bytes=0x%zx",
            module_names[index], index, (unsigned long long)offset, bytes);
    const Sym *entry = NULL;
    for (unsigned i = 1; i < m->sym_count; i++) {
        const Sym *s = symbol_at(m, i);
        if (strcmp(symbol_name(m, s), name)) continue;
        if (entry || s->section != 1 || s->info != 0x12 || (s->other & 3) ||
                s->value != offset || s->size != bytes)
            fail("graphics platform symbol does not match the audited %s profile: %s", m->name, name);
        entry = s;
    }
    if (!entry || find_raw_symbol(name) != (uint64_t)m->base + offset)
        fail("graphics platform symbol resolution does not match the audited %s profile: %s", m->name, name);
    return (uint64_t)m->base + offset;
}
static void audit_graphics_platform(void) {
    /* main+0x9fc4b8 uses its PLT; seven-module text audit found no direct call.
     * Original allocator APIs and NVIDIA globals are deliberately preserved. */
    (void)audited_platform_export(1, graphics_platform_initialize, 0xc794, 0x94);
}
static void host_initialize_graphics(void *workspace, size_t bytes) {
    audit_graphics_platform();
    if (!state.graphics_started || !graphics_backend_init || !graphics_backend_shutdown || !graphics_backend_loader)
        fail("graphics platform requires an initialized Metal device and command queue");
    uint64_t address = (uint64_t)workspace;
    size_t host_page = (size_t)getpagesize();
    if (!workspace) fail("graphics platform workspace must not be null");
    if (host_page < 4096 || (host_page & (host_page - 1)) || address % 4096 || address % host_page)
        fail("graphics platform workspace must be aligned to guest and host pages");
    if (!bytes || bytes > UINT32_MAX || bytes % 4096 || bytes % host_page)
        fail("graphics platform workspace size must be nonzero whole pages and fit the audited u32 ABI");
    if (bytes > UINT64_MAX - address || !services_memory(NULL, address, bytes, 1))
        fail("graphics platform workspace is not completely writable guest memory");
    pthread_mutex_lock(&lifecycle_lock);
    int duplicate = state.graphics_workspace != NULL;
    if (!duplicate) { state.graphics_workspace = workspace; state.graphics_workspace_size = bytes; }
    pthread_mutex_unlock(&lifecycle_lock);
    if (duplicate) fail("graphics platform is already initialized");
    report("graphics_platform=Metal initialized=true workspace=0x%llx bytes=%zu nvidia_transport=false",
        (unsigned long long)address, bytes);
}
enum { VI_INITIALIZE, VI_OPEN_DEFAULT, VI_CREATE_LAYER, VI_SCALING_MODE, VI_VSYNC_EVENT, VI_NATIVE_WINDOW };
static const struct { const char *name; uint64_t offset; size_t size; } vi_exports[] = {
    {"_ZN2nn2vi10InitializeEv", 0x288c, 0x9b0},
    {"_ZN2nn2vi18OpenDefaultDisplayEPPNS0_7DisplayE", 0xce70, 0x20},
    {"_ZN2nn2vi11CreateLayerEPPNS0_5LayerEPNS0_7DisplayE", 0xdda0, 0x288},
    {"_ZN2nn2vi19SetLayerScalingModeEPNS0_5LayerENS0_11ScalingModeE", 0xe20c, 0x64},
    {"_ZN2nn2vi20GetDisplayVsyncEventEPNS_2os15SystemEventTypeEPNS0_7DisplayE", 0xd118, 0xa0},
    {"_ZN2nn2vi15GetNativeWindowEPPvPNS0_5LayerE", 0xe270, 0x2c}
};
static void audit_vi_export(unsigned which) {
    (void)audited_platform_export(2, vi_exports[which].name, vi_exports[which].offset, vi_exports[which].size);
}
static void require_vi_output(void *out, size_t bytes) {
    if (!out || (uintptr_t)out % 8 || !services_memory(NULL, (uintptr_t)out, bytes, 1))
        fail("VI output is not an aligned, completely writable guest range (%zu bytes)", bytes);
}
/* Backend platform methods only update last_error, never reenter this runtime.
 * Release lifecycle_lock before a failing guest call unwinds its boundary. */
static void vi_failure(char *error, size_t bytes, const char *message) {
    snprintf(error, bytes, "%s", message && *message ? message : "VI backend rejected request");
}
static void host_vi_initialize(void) {
    audit_vi_export(VI_INITIALIZE);
    char error[512] = {0};
    pthread_mutex_lock(&lifecycle_lock);
    if (!state.graphics_started || !state.graphics_workspace)
        vi_failure(error, sizeof error, "VI requires the initialized Metal platform workspace");
    else if (state.vi_initialized) vi_failure(error, sizeof error, "VI is already initialized");
    else if (!botw_graphics_platform_initialize()) vi_failure(error, sizeof error, botw_graphics_last_error());
    else state.vi_initialized = 1;
    pthread_mutex_unlock(&lifecycle_lock);
    if (*error) fail("VI Initialize: %s", error);
    report("vi_platform_initialized=true backend=Metal");
}
static uint32_t host_vi_open_default_display(void **out) {
    audit_vi_export(VI_OPEN_DEFAULT); require_vi_output(out, sizeof *out);
    char error[512] = {0}; uintptr_t display = 0; uint32_t width = 0, height = 0;
    pthread_mutex_lock(&lifecycle_lock);
    if (!state.vi_initialized) vi_failure(error, sizeof error, "VI is not initialized");
    else if (state.vi_display) vi_failure(error, sizeof error, "default display is already open");
    else if (!botw_graphics_platform_open_default_display(&display) ||
            !botw_graphics_platform_display_size(display, &width, &height) ||
            !width || !height || !botw_graphics_platform_display_is_live(display))
        vi_failure(error, sizeof error, botw_graphics_last_error());
    else { state.vi_display = (void *)display; state.vi_display_live = 1; }
    pthread_mutex_unlock(&lifecycle_lock);
    if (*error) fail("VI OpenDefaultDisplay: %s", error);
    *out = (void *)display;
    report("vi_display_opened=true drawable_width=%u drawable_height=%u", width, height); return 0;
}
static uint32_t host_vi_create_layer(void **out, void *display) {
    audit_vi_export(VI_CREATE_LAYER); require_vi_output(out, sizeof *out);
    char error[512] = {0}; uintptr_t layer = 0;
    pthread_mutex_lock(&lifecycle_lock);
    if (!state.vi_initialized || !state.vi_display_live || !display || display != state.vi_display)
        vi_failure(error, sizeof error, "display is not the live host display");
    else if (state.vi_layer) vi_failure(error, sizeof error, "host display layer is already created");
    else if (!botw_graphics_platform_create_layer((uintptr_t)display, &layer) || !layer)
        vi_failure(error, sizeof error, botw_graphics_last_error());
    else state.vi_layer = (void *)layer;
    pthread_mutex_unlock(&lifecycle_lock);
    if (*error) fail("VI CreateLayer: %s", error);
    *out = (void *)layer; report("vi_layer_created=true backend=CAMetalLayer"); return 0;
}
static uint32_t host_vi_set_scaling_mode(void *layer, int mode) {
    audit_vi_export(VI_SCALING_MODE); char error[512] = {0};
    pthread_mutex_lock(&lifecycle_lock);
    if (!state.vi_initialized || !layer || layer != state.vi_layer)
        vi_failure(error, sizeof error, "layer is not the host display layer");
    else if (!botw_graphics_platform_set_scaling_mode((uintptr_t)layer, mode))
        vi_failure(error, sizeof error, botw_graphics_last_error());
    pthread_mutex_unlock(&lifecycle_lock);
    if (*error) fail("VI SetLayerScalingMode: %s", error);
    report("vi_layer_scaling_mode=%d host_policy_applied=true", mode); return 0;
}
static uint32_t host_vi_get_native_window(void **out, void *layer) {
    audit_vi_export(VI_NATIVE_WINDOW); require_vi_output(out, sizeof *out);
    char error[512] = {0}; uintptr_t native = 0;
    pthread_mutex_lock(&lifecycle_lock);
    if (!state.vi_initialized || !layer || layer != state.vi_layer)
        vi_failure(error, sizeof error, "layer is not the host display layer");
    else if (!botw_graphics_platform_get_native_window((uintptr_t)layer, &native) || !native)
        vi_failure(error, sizeof error, botw_graphics_last_error());
    pthread_mutex_unlock(&lifecycle_lock);
    if (*error) fail("VI GetNativeWindow: %s", error);
    *out = (void *)native; return 0;
}
static uint32_t host_vi_get_vsync_event(void *out, void *display) {
    audit_vi_export(VI_VSYNC_EVENT);
    /* Original AttachReadable writes through +0x28; 48 is the conservative
     * 8-aligned span covering all those writes, not an invented SDK layout. */
    require_vi_output(out, 48);
    const char *name = "_ZN2nn2os33AttachReadableHandleToSystemEventEPNS0_15SystemEventTypeEjbNS0_14EventClearModeE";
    uint64_t attach = audited_platform_export(5, name, 0x851c8, 0x3c);
    char error[512] = {0}; uint32_t reader = 0;
    pthread_mutex_lock(&lifecycle_lock);
    if (!state.vi_initialized || !state.vi_display_live || !display || display != state.vi_display ||
            !state.kernel || !botw_graphics_platform_display_is_live((uintptr_t)display))
        vi_failure(error, sizeof error, "display is not the live host display");
    else if ((!state.vi_vsync_write && botw_kernel_create_event(state.kernel, 0,
                &state.vi_vsync_write, &state.vi_vsync_read, error, sizeof error)) ||
            botw_kernel_duplicate_handle(state.kernel, state.vi_vsync_read, &reader, error, sizeof error)) {
        if (!*error) vi_failure(error, sizeof error, "kernel did not create an independent vsync reader");
    }
    pthread_mutex_unlock(&lifecycle_lock);
    if (*error) fail("VI GetDisplayVsyncEvent: %s", error);
    /* Exact original VI+d188..d198 ABI: owned reader, EventClearMode=1.
     * Call the actual mapped SDK to construct its intrusive links and tags. */
    ((void (*)(void *, uint32_t, _Bool, int))attach)(out, reader, 1, 1);
    report("vi_vsync_event_attached=true source=host_display_callback reader=0x%x", reader); return 0;
}
static uint64_t find_vi_symbol(const char *name) {
    const uint64_t wrappers[] = {(uint64_t)host_vi_initialize, (uint64_t)host_vi_open_default_display,
        (uint64_t)host_vi_create_layer, (uint64_t)host_vi_set_scaling_mode,
        (uint64_t)host_vi_get_vsync_event, (uint64_t)host_vi_get_native_window};
    for (unsigned i = 0; i < sizeof vi_exports / sizeof vi_exports[0]; i++) {
        if (!strcmp(name, vi_exports[i].name)) { audit_vi_export(i); return wrappers[i]; }
    }
    return 0;
}
#endif
static uint64_t find_symbol(const char *name) {
    if (!strcmp(name, "nvnBootstrapLoader") && graphics_backend_loader)
        return (uint64_t)graphics_backend_loader;
    if (state.address_bits == 39) {
#if defined(BOTW_WITH_GRAPHICS)
        if (!strcmp(name, graphics_platform_initialize)) {
            audit_graphics_platform(); return (uint64_t)host_initialize_graphics;
        }
        uint64_t vi = find_vi_symbol(name); if (vi) return vi;
#endif
        if (!strcmp(name, allocator_constructor) || !strcmp(name, allocator_constructor_alias))
            return (uint64_t)host_allocator_constructor;
        if (!strcmp(name, allocator_allocate)) return (uint64_t)host_allocator_allocate;
        uintptr_t bridge = botw_hipc_address_resolve(name);
        if (bridge) return bridge;
    }
#if defined(BOTW_WITH_AUDIO)
    Module *module = NULL; const Sym *symbol = NULL;
    uint64_t raw = find_raw_definition(name, &module, &symbol);
    if (module && symbol) {
        char error[512]; void *function = NULL;
        int resolved = botw_audio_resolve_symbol(module->name, (unsigned)(module - state.modules),
            name, symbol->value, symbol->size, module->text_end, &function, error, sizeof error);
        if (resolved < 0) fail("AudioOut symbol profile: %s", error);
        if (resolved) {
            if (!function || symbol->section != 1 || symbol->info != 0x12 || (symbol->other & 3))
                fail("AudioOut symbol must be a default-visible SDK function: %s", name);
            return (uint64_t)function;
        }
    }
    return raw;
#else
    return find_raw_symbol(name);
#endif
}
static uint64_t unsupported_stub(const char *name) {
    for (unsigned i = 0; i < state.stub_count; i++) if (!strcmp(state.stubs[i], name)) return (uint64_t)botw_unimplemented_stubs + i * 8;
    if (state.stub_count == MAX_STUB) fail("unimplemented import table overflow");
    unsigned i = state.stub_count++; state.stubs[i] = strdup(name);
    if (!state.stubs[i]) fail("allocate import name");
    return (uint64_t)botw_unimplemented_stubs + i * 8;
}
__attribute__((noreturn)) void botw_unimplemented(unsigned index) {
    fail("unsupported import: %s", index < state.stub_count ? state.stubs[index] : "invalid stub index");
}
static void relocate(Module *m) {
    uint64_t starts[2] = {m->rela_off, m->plt_off}, sizes[2] = {m->rela_size, m->plt_size};
    for (unsigned t = 0; t < 2; t++) for (uint64_t off = 0; off < sizes[t]; off += sizeof(Rela)) {
        Rela r; memcpy(&r, m->base + starts[t] + off, sizeof r);
        if (r.offset < ROUND_PAGE(m->text_end) || !in_range(r.offset, 8, m->patch_off) ||
            (r.offset < m->key_off + m->key_size && r.offset + 8 > m->key_off))
            fail("%s: relocation target outside writable data", m->name);
        unsigned type = (unsigned)r.info, index = (unsigned)(r.info >> 32); uint64_t value;
        if (type == 1027) {
            if (index || r.addend < 0 || (uint64_t)r.addend > m->size) fail("%s: invalid relative relocation", m->name);
            value = (uint64_t)m->base + r.addend;
        } else if (type == 257 || type == 1025 || type == 1026) {
            const Sym *s = symbol_at(m, index); const char *name = symbol_name(m, s);
            /* BOTW rtld +0xcd8 checks visibility; default-visible symbols use
               global first-definition lookup at +0xd28 even when defined here.
               This allows main's strong allocators to preempt SDK weak exports. */
            if (s->section && ((s->other & 3) || (s->info >> 4) == 0)) value = defined_value(m, s);
            else { value = find_symbol(name); if (!value && s->section) value = defined_value(m, s); }
            if (!value && !s->section) {
                if (s->info >> 4 == 2) { state.weak_zero++; value = 0; }
                else value = unsupported_stub(name);
            }
            value += r.addend;
        } else fail("%s: unsupported relocation type %u", m->name, type);
        memcpy(m->base + r.offset, &value, 8); state.relocations++;
    }
}
static void register_gates(Module *m, const char *root) {
    char path[4096], line[256], kind[24], extra; unsigned long long ret, original; int arg;
    path_for(path, sizeof path, root, m->name, "gates.tsv"); FILE *f = fopen(path, "r");
    if (!f) fail("missing gate table for %s", m->name);
    while (fgets(line, sizeof line, f)) {
        if (sscanf(line, "%lli %23s %d %lli %c", &ret, kind, &arg, &original, &extra) != 4) { fclose(f); fail("%s: invalid gate record", m->name); }
        int type = !strcmp(kind, "svc") ? 0 : !strcmp(kind, "cntpct") ? 1 : !strcmp(kind, "cntvct") ? 2 : -1;
        if (type < 0 || arg < 0 || (type == 0 ? arg > 255 : arg > 30) || ret < m->patch_off || ret >= m->size || original >= m->text_end || state.gate_count >= MAX_GATES) {
            fclose(f); fail("%s: invalid native gate", m->name);
        }
        uint64_t address = (uint64_t)m->base + ret;
        for (unsigned i = 0; i < state.gate_count; i++) if (state.gates[i].ret == address) { fclose(f); fail("%s: duplicate gate", m->name); }
        state.gates[state.gate_count++] = (Gate){address, original, type, arg};
    }
    int bad_read = ferror(f); fclose(f); if (bad_read) fail("%s: read gate table", m->name);
}
static int writable_guest_range(uint64_t address, size_t length) {
    uint64_t top = (uint64_t)pthread_get_stackaddr_np(pthread_self());
    size_t stack_size = pthread_get_stacksize_np(pthread_self());
    if (address >= top - stack_size && in_range(address - (top - stack_size), length, stack_size)) return 1;
    if (state.tls && address >= (uint64_t)state.tls && in_range(address - (uint64_t)state.tls, length, 8192)) return 1;
    if (state.tlr && address >= (uint64_t)state.tlr && in_range(address - (uint64_t)state.tlr, length, 0x600)) return 1;
    for (unsigned i = 0; i < MODULE_COUNT; i++) {
        Module *m = &state.modules[i]; uint64_t base = (uint64_t)m->base;
        if (address < base) continue;
        uint64_t off = address - base;
        if (off >= ROUND_PAGE(m->text_end) && in_range(off, length, m->patch_off) &&
            !(off < m->key_off + m->key_size && off + length > m->key_off)) return 1;
    }
    return 0;
}
static int external_memory(void *opaque, uint64_t address, size_t length, int writable) {
    (void)opaque;
    if (child_context) {
        if (child_context->tls && address >= (uint64_t)child_context->tls && in_range(address - (uint64_t)child_context->tls, length, 8192)) return 1;
        if (child_context->tlr && address >= (uint64_t)child_context->tlr && in_range(address - (uint64_t)child_context->tlr, length, 0x600)) return 1;
    }
    if (writable_guest_range(address, length)) return 1;
    if (writable) return 0;
    for (unsigned i = 0; i < MODULE_COUNT; i++) {
        Module *m = &state.modules[i]; uint64_t base = (uint64_t)m->base;
        if (address >= base && in_range(address - base, length, m->size)) return 1;
    }
    return 0;
}
static int services_memory(void *opaque, uint64_t address, size_t length, int writable) {
    if (state.kernel && botw_kernel_memory(state.kernel, address, length, writable)) return 1;
    return external_memory(opaque, address, length, writable);
}
#if defined(BOTW_WITH_AUDIO)
static int audio_copy_error(char *error, size_t capacity, const char *operation, kern_return_t result) {
    if (error && capacity) snprintf(error, capacity, "AudioOut %s failed: Mach result=%d", operation, result);
    return -1;
}
static int audio_read_guest(void *opaque, uint64_t source, void *destination, size_t bytes,
        char *error, size_t capacity) {
    (void)opaque;
    if (!bytes) return 0;
    if (!destination || !services_memory(NULL, source, bytes, 0))
        return audio_copy_error(error, capacity, "guest read range", KERN_INVALID_ADDRESS);
    vm_size_t copied = 0;
    kern_return_t result = vm_read_overwrite(mach_task_self(), (vm_address_t)source, (vm_size_t)bytes,
        (vm_address_t)(uintptr_t)destination, &copied);
    if (result != KERN_SUCCESS || copied != bytes)
        return audio_copy_error(error, capacity, "protected guest read", result == KERN_SUCCESS ? KERN_FAILURE : result);
    return 0;
}
static int audio_write_guest(void *opaque, uint64_t destination, const void *source, size_t bytes,
        char *error, size_t capacity) {
    (void)opaque;
    if (!bytes) return 0;
    if (!source || bytes > UINT32_MAX || !services_memory(NULL, destination, bytes, 1))
        return audio_copy_error(error, capacity, "guest write range", KERN_INVALID_ADDRESS);
    if (botw_host_prepare_guest_write((void *)(uintptr_t)destination, bytes, error, capacity)) return -1;
    kern_return_t result = vm_write(mach_task_self(), (vm_address_t)destination,
        (vm_offset_t)(uintptr_t)source, (mach_msg_type_number_t)bytes);
    return result == KERN_SUCCESS ? 0 : audio_copy_error(error, capacity, "protected guest write", result);
}
typedef struct { uint64_t event; uint32_t reader; } AudioAttachArguments;
static void call_audio_attach(uint64_t function, void *opaque) {
    AudioAttachArguments *args = opaque;
    ((void (*)(void *, uint32_t, _Bool, int))(uintptr_t)function)((void *)(uintptr_t)args->event, args->reader, 1, 1);
}
static int audio_attach_system_event(void *opaque, uint64_t event, uint32_t reader,
        char *error, size_t capacity) {
    (void)opaque;
    const char *name = "_ZN2nn2os33AttachReadableHandleToSystemEventEPNS0_15SystemEventTypeEjbNS0_14EventClearModeE";
    Module *module = NULL; const Sym *symbol = NULL;
    uint64_t target = find_raw_definition(name, &module, &symbol);
    if (!reader || !event || (event & 7) || !services_memory(NULL, event, 48, 1) ||
            module != &state.modules[5] || !symbol || !module->name || strcmp(module->name, "sdk") ||
            symbol->value != 0x851c8 || symbol->size != 0x3c || symbol->section != 1 ||
            symbol->info != 0x12 || (symbol->other & 3) || !in_range(0x851c8, 0x3c, module->text_end)) {
        if (error && capacity) snprintf(error, capacity, "AudioOut SystemEvent requires the audited SDK profile and writable 48-byte event");
        return -1;
    }
    AudioAttachArguments args = {event, reader};
    return invoke_guest_callback(target, "AudioOut SystemEvent attach", "audio attach ",
        call_audio_attach, &args, error, capacity) ? 0 : -1;
}
static void audio_fault(void *opaque, const char *message) { (void)opaque; fail("AudioOut: %s", message); }
static void audio_log(void *opaque, const char *message) { (void)opaque; report("%s", message); }
#endif
static void services_log(void *opaque, const char *message) { (void)opaque; report("%s", message); }
static int executable_address(void *opaque, uint64_t pointer) {
    (void)opaque;
    if (pointer & 3) return 0;
    for (unsigned i = 0; i < MODULE_COUNT; i++) {
        Module *m = &state.modules[i]; uint64_t base = (uint64_t)m->base;
        if (pointer < base) continue;
        uint64_t offset = pointer - base;
        if (offset < m->text_end || (offset >= m->patch_off && offset < m->size)) return 1;
    }
    return 0;
}
static size_t host_available_memory(void) {
    size_t available = 0;
#if TARGET_OS_IPHONE
    available = os_proc_available_memory();
#else
    uint64_t physical = 0; size_t length = sizeof physical;
    if (sysctlbyname("hw.memsize", &physical, &length, NULL, 0)) fail("query physical memory");
    available = physical > SIZE_MAX ? SIZE_MAX : (size_t)physical;
#endif
    return available;
}
static void configure_kernel_memory(void) {
    const size_t mib = 1024u * 1024u;
    size_t image = 0;
    for (unsigned i = 0; i < MODULE_COUNT; i++) image += state.modules[i].size;
    state.image_bytes = image;
    size_t available = host_available_memory();
    /* Include already mapped modules while respecting the host's live limit.
     * Virtual capacities do not grant committed physical memory. */
    size_t budget = 4096u * mib;
    if (available < budget && image < budget - available) budget = available + image;
    if (budget < image) fail("host memory budget below loaded guest image");
    char error[512];
    if (botw_kernel_configure_address_space(state.kernel, state.address_bits, error, sizeof error))
        fail("configure guest address policy: %s", error);
    if (botw_kernel_configure_memory(state.kernel, 4096u * mib, 256u * mib,
            512u * mib, budget, image, error, sizeof error)) fail("configure guest memory: %s", error);
    report("guest_address_bits=%u guest_memory_budget=%zu image_bytes=%zu host_available=%zu",
        state.address_bits, budget, image, available);
}
static void child_cleanup(void *opaque) {
    ChildContext *context = opaque;
    if (context->autorelease_pool && host_pool_pop) host_pool_pop(context->autorelease_pool);
    for (unsigned i = 0; i < state.key_count; i++) pthread_setspecific(state.keys[i], NULL);
    if (context->previous_stack.ss_flags & SS_DISABLE) context->previous_stack.ss_size = MINSIGSTKSZ;
    if (!context->alternate_stack_installed || !sigaltstack(&context->previous_stack, NULL)) free(context->signal_stack);
    free(context->tls); free(context->tlr); context->active = 0; child_context = NULL; free(context);
}
static void describe_fault(char *output, size_t capacity, const char *scope, int number,
                            uintptr_t pc, uintptr_t address, const volatile uintptr_t *regs) {
    const char *module = "outside"; uint64_t offset = pc;
    for (unsigned i = 0; i < MODULE_COUNT; i++) {
        uintptr_t base = (uintptr_t)state.modules[i].base;
        if (base && pc >= base && pc - base < state.modules[i].size) { module = state.modules[i].name; offset = pc - base; break; }
    }
    snprintf(output, capacity,
        "guest %sCPU fault signal=%d pc=0x%llx module=%s offset=0x%llx address=0x%llx x0=0x%llx x1=0x%llx x2=0x%llx x3=0x%llx x8=0x%llx x19=0x%llx x20=0x%llx x28=0x%llx",
        scope, number, (unsigned long long)pc, module, (unsigned long long)offset, (unsigned long long)address,
        (unsigned long long)regs[0], (unsigned long long)regs[1], (unsigned long long)regs[2],
        (unsigned long long)regs[3], (unsigned long long)regs[8], (unsigned long long)regs[19],
        (unsigned long long)regs[20], (unsigned long long)regs[28]);
}
static int execute_child(void *opaque, uint64_t entry, uint64_t argument, uint64_t stack_top,
                          uint32_t handle, char *error, size_t error_capacity) {
    (void)opaque; (void)stack_top;
    if (!executable_address(NULL, entry)) { snprintf(error, error_capacity, "thread entry outside signed guest code"); return -1; }
    ChildContext *context = calloc(1, sizeof *context); int result = -1;
    if (!context) { snprintf(error, error_capacity, "allocate child recovery context"); return -1; }
    child_context = context;
    pthread_cleanup_push(child_cleanup, context);
    if (!sigsetjmp(context->recovery, 1)) {
        context->active = 1;
        if (sigaltstack(NULL, &context->previous_stack)) fail("read child signal stack");
        context->signal_stack = malloc(64 * 1024); context->tls = calloc(1, 8192); context->tlr = calloc(1, 0x600);
        if (!context->signal_stack || !context->tls || !context->tlr) fail("allocate guest child thread context");
        stack_t alternate = {.ss_sp = context->signal_stack, .ss_size = 64 * 1024};
        if (sigaltstack(&alternate, NULL)) fail("install child signal stack");
        context->alternate_stack_installed = 1;
        *(void **)((uint8_t *)context->tlr + 0x1f8) = (uint8_t *)context->tlr + 0x200;
        pthread_setspecific(state.keys[0], NULL); pthread_setspecific(state.keys[1], context->tls);
        pthread_setspecific(state.keys[2], context->tlr); pthread_setspecific(state.keys[3], (void *)botw_gate);
        install_heap_parameter_slots();
        if (host_pool_push) context->autorelease_pool = host_pool_push();
        report("guest_thread_entered handle=0x%x", handle);
        ((void (*)(uint64_t, uint64_t))entry)(argument, handle);
        snprintf(context->error, sizeof context->error, "guest thread returned without ExitThread handle=0x%x", handle);
    } else if (context->fault_signal) {
        describe_fault(context->error, sizeof context->error, "child ", context->fault_signal,
            context->fault_pc, context->fault_address, context->fault_registers);
    }
    snprintf(error, error_capacity, "%s", context->error);
    pthread_cleanup_pop(1);
    return result;
}
static int kernel_event_create(void *ctx, int initial, uint32_t *write, uint32_t *read, char *error, size_t cap) { return botw_kernel_create_event(ctx, initial, write, read, error, cap); }
static int kernel_duplicate(void *ctx, uint32_t handle, uint32_t *out, char *error, size_t cap) { return botw_kernel_duplicate_handle(ctx, handle, out, error, cap); }
static int kernel_signal(void *ctx, uint32_t handle, char *error, size_t cap) { return botw_kernel_signal_event(ctx, handle, error, cap); }
static int kernel_clear(void *ctx, uint32_t handle, char *error, size_t cap) { return botw_kernel_clear_event(ctx, handle, error, cap); }
static int kernel_close(void *ctx, uint32_t handle, char *error, size_t cap) { return botw_kernel_close_handle(ctx, handle, error, cap); }
static int kernel_process(void *ctx, uint32_t handle) { return botw_kernel_is_process_handle(ctx, handle); }
#if defined(BOTW_WITH_AUDIO)
static void initialize_audio(void) {
    const botw_audio_callbacks callbacks = {.opaque = state.kernel,
        .read_guest = audio_read_guest, .write_guest = audio_write_guest,
        .create_event = kernel_event_create, .signal_event = kernel_signal,
        .close_handle = kernel_close, .attach_system_event = audio_attach_system_event,
        .fault = audio_fault, .log = audio_log};
    char error[512];
    pthread_mutex_lock(&lifecycle_lock);
    state.audio = botw_audio_create(&callbacks, host_foreground && host_focused && host_audio_session_active, error, sizeof error);
    pthread_mutex_unlock(&lifecycle_lock);
    if (!state.audio) fail("initialize AudioOut backend: %s", error);
    report("audio_backend=AudioUnit guest_audio_renderer_implemented=false");
}
static void update_audio_active(void) {
    char error[512];
    if (state.audio && botw_audio_set_active(state.audio,
            host_foreground && host_focused && host_audio_session_active, error, sizeof error))
        report("audio_lifecycle_error=%s", error);
}
#endif
void botw_host_set_audio_session_active(int active) {
    pthread_mutex_lock(&lifecycle_lock);
    host_audio_session_active = !!active;
#if defined(BOTW_WITH_AUDIO)
    update_audio_active();
#endif
    pthread_mutex_unlock(&lifecycle_lock);
}
void botw_host_set_applet_state(int foreground, int focused) {
    pthread_mutex_lock(&lifecycle_lock);
    host_foreground = !!foreground; host_focused = !!focused;
#if defined(BOTW_WITH_AUDIO)
    update_audio_active();
#endif
    if (state.services) {
        char error[512];
        if (botw_services_set_applet_state(state.services, state.resource_user_id, host_foreground, host_focused, 0, error, sizeof error))
            report("applet_state_update_failed=%s", error);
    }
    pthread_mutex_unlock(&lifecycle_lock);
}
static void configure_resource_backends(const char *documents) {
    char error[512]; void *shared = NULL;
    state.input = configured_input;
    if (!state.input) { state.input = botw_input_create(); state.input_owned = 1; }
    if (!state.input) fail("allocate host input state");
    if (botw_kernel_create_shared_memory(state.kernel, BOTW_HID_SHARED_MEMORY_SIZE, 1,
            &state.input_shared_handle, &shared, error, sizeof error)) fail("create HID shared memory: %s", error);
    if (botw_input_bind_shared_memory(state.input, shared, BOTW_HID_SHARED_MEMORY_SIZE)) fail("bind HID shared memory");
    botw_input_sample(state.input, botw_input_monotonic_ticks());
    botw_services_set_input(state.services, state.input, state.input_shared_handle);
    if (!documents || !*documents) fail("missing application documents directory");
    char path[4096]; int length = snprintf(path, sizeof path, "%s/botw-romfs.bin", documents);
    if (length < 0 || (size_t)length >= sizeof path) fail("RomFS image path exceeds limit");
    if (access(path, F_OK)) {
        if (errno != ENOENT) fail("read RomFS image: %s", strerror(errno));
        report("romfs_available=false; filesystem requests will report missing backend"); return;
    }
    botw_romfs_error fs_error;
    state.romfs = botw_romfs_open(path, &fs_error);
    if (!state.romfs) fail("open RomFS image: %s", fs_error.message);
    botw_services_set_romfs(state.services, state.romfs);
    char version[32] = {0}; size_t actual = 0;
    if (botw_romfs_pread(state.romfs, "/System/Version.txt", 0, version, sizeof version - 1, &actual, &fs_error))
        fail("read game resource version: %s", fs_error.message);
    report("romfs_available=true bytes=%llu version=%s", (unsigned long long)botw_romfs_image_size(state.romfs), version);
    botw_local_user_error user_error;
    if (botw_local_user_open(documents, &state.local_user, &user_error))
        fail("open local offline user: %s", user_error.message);
    length = snprintf(path, sizeof path, "%s/botw-saves", documents);
    if (length < 0 || (size_t)length >= sizeof path) fail("save directory path exceeds limit");
    botw_savefs_error save_error;
    state.savefs = botw_savefs_open(path, "01007ef00011e000", state.local_user.uid_hex, 0, &save_error);
    if (!state.savefs) fail("open local save filesystem: %s", save_error.message);
    botw_services_set_savefs(state.services, state.savefs);
    /* Publish and explicitly preselect the persistent host-local user for this
     * process launch. The account bridge checks this exact SaveFS title/UID;
     * no Nintendo profile or online authorization is inferred from it. */
    if (botw_services_set_local_user(state.services, state.local_user.uid,
            "app-documents:botw-profile/uid.bin", error, sizeof error))
        fail("publish local offline account: %s", error);
    if (botw_services_set_preselected_local_user(state.services, state.local_user.uid,
            error, sizeof error)) fail("preselect local offline account: %s", error);
    report("local_offline_user_ready=true save_backend_ready=true game_save_verified=false "
        "local_account_published=true local_user_preselected=true");
    /* This target registers only the mounted base image. A downloaded update
     * archive is not installed content and must not stand in for patch RomFS. */
    if (botw_services_set_patch_absent(state.services, 1, error, sizeof error))
        fail("configure uninstalled patch storage: %s", error);
    /* The fixed base-v0 profile's verified main.npdm ACI0 program id. Only the
     * base RomFS is mounted: downloaded DLC packages are not installed content.
     * NACP add-on base metadata has not been extracted, so it stays unknown. */
    if (botw_services_set_addon_content(state.services, UINT64_C(0x01007ef00011e000), 0,
            NULL, 0, error, sizeof error)) fail("configure installed add-on content: %s", error);
    if (botw_services_set_parental_policy(state.services,
            "local-offline-container:no-nintendo-account-policy", 0, 0, 0, error, sizeof error))
        fail("configure local guest policy: %s", error);
}
int botw_host_set_graphics_layer(void *layer) {
    if (pthread_mutex_trylock(&run_lock)) return 2;
    configured_graphics_layer = layer; pthread_mutex_unlock(&run_lock); return 0;
}
void botw_host_display_tick(double target_timestamp_seconds) {
#if defined(BOTW_WITH_GRAPHICS)
    if (!isfinite(target_timestamp_seconds) || target_timestamp_seconds <= 0) return;
    char error[512] = {0};
    pthread_mutex_lock(&lifecycle_lock);
    if (state.vi_display_live && state.graphics_started && state.vi_initialized && state.vi_display &&
            state.vi_vsync_write && state.kernel && target_timestamp_seconds > state.vi_last_target_timestamp &&
            botw_graphics_platform_display_is_live((uintptr_t)state.vi_display)) {
        state.vi_last_target_timestamp = target_timestamp_seconds;
        if (botw_kernel_signal_event(state.kernel, state.vi_vsync_write, error, sizeof error))
            state.vi_display_live = 0;
    }
    pthread_mutex_unlock(&lifecycle_lock);
    if (*error) report("error=VI host display event signal: %s", error);
#else
    (void)target_timestamp_seconds;
#endif
}
int botw_host_set_input(botw_input *input) {
    if (pthread_mutex_trylock(&run_lock)) return 2;
    configured_input = input; pthread_mutex_unlock(&run_lock); return 0;
}
int botw_host_set_address_space(unsigned bits) {
    if (bits != 36 && bits != 39) return 1;
    if (pthread_mutex_trylock(&run_lock)) return 2;
    configured_address_bits = bits; pthread_mutex_unlock(&run_lock); return 0;
}
void botw_host_refresh_input(void) {
    pthread_mutex_lock(&lifecycle_lock);
    if (state.services) {
        char error[512];
        if (botw_services_refresh_input(state.services, error, sizeof error)) report("input_notification_error=%s", error);
    }
    pthread_mutex_unlock(&lifecycle_lock);
}
static int graphics_memory(const void *address, size_t length) { return services_memory(NULL, (uint64_t)address, length, 0); }
#if defined(BOTW_WITH_GRAPHICS)
static int graphics_memory_access(const void *address, size_t length, int writable) {
    return services_memory(NULL, (uint64_t)address, length, writable != 0);
}
#endif
static int services_focus(void *opaque, uint32_t mode, int foreground, int focused,
                          int suspended, char *error, size_t capacity) {
    (void)opaque; (void)error; (void)capacity;
    pthread_mutex_lock(&focus_lock);
    focus_suspended = !!suspended; pthread_cond_broadcast(&focus_changed);
    pthread_mutex_unlock(&focus_lock);
    report("focus_policy mode=%u foreground=%d focused=%d suspended=%d gate_boundary=true",
        mode, foreground, focused, suspended);
    return 0;
}
static int wait_for_focus(void) {
    pthread_mutex_lock(&focus_lock);
    int result = 0;
    while (focus_suspended && !focus_stopping && !result) result = pthread_cond_wait(&focus_changed, &focus_lock);
    int stopped = focus_stopping;
    pthread_mutex_unlock(&focus_lock);
    return result || stopped ? -1 : 0;
}
static void graphics_error(const char *message) {
    if (child_context || pthread_equal(pthread_self(), state.owner)) fail("NVN: %s", message);
    report("graphics_async_error=%s", message);
}
static int services_performance(void *opaque, uint32_t mode, uint32_t configuration,
                                int active, char *error, size_t error_capacity) {
    (void)opaque;
    /* Public APM policy identifiers documented by the Switch homebrew kernel
       researchers. These are guest policy selections; physical Apple clocks
       remain under the host OS governor. Apply an actual scheduling QoS request
       for the active policy, and retain inactive policy choices in services.c. */
    const uint32_t supported[] = {0x10000, 0x10001, 0x10002, 0x20000, 0x20001,
        0x20002, 0x20003, 0x20004, 0x20005, 0x20006, 0x92220007, 0x92220008,
        0x92220009, 0x9222000a, 0x9222000b, 0x9222000c};
    unsigned known = 0;
    for (unsigned i = 0; i < sizeof supported / sizeof supported[0]; i++)
        if (supported[i] == configuration) known = 1;
    if (mode > 1 || !known) {
        snprintf(error, error_capacity, "unsupported APM policy mode=%u configuration=0x%x", mode, configuration); return -1;
    }
    if (active) {
        qos_class_t qos = configuration == 0x92220009 || configuration == 0x9222000a
            ? QOS_CLASS_USER_INTERACTIVE : QOS_CLASS_USER_INITIATED;
        int result = pthread_set_qos_class_self_np(qos, 0);
        if (result) { snprintf(error, error_capacity, "host performance QoS request failed: %s", strerror(result)); return -1; }
    }
    report("performance_policy mode=%u configuration=0x%x active=%d host_qos_applied=%s",
           mode, configuration, active, active ? "true" : "false");
    return 0;
}
void botw_gate_dispatch(uint64_t ret, uint64_t *regs) {
    if (wait_for_focus()) fail("guest execution stopped at focus boundary");
    for (unsigned i = 0; i < state.gate_count; i++) {
        Gate *g = &state.gates[i]; if (g->ret != ret) continue;
        if (callback_context && callback_context->active && !g->kind && g->arg == 0xa)
            fail("SVC ExitThread cannot terminate a synchronous graphics callback caller");
        if (g->kind) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); regs[g->arg] = (uint64_t)ts.tv_sec * 19200000ull + (uint64_t)ts.tv_nsec * 12 / 625; return; }
        if (state.kernel && g->arg != 0x06 && g->arg != 0x16 &&
            !(g->arg == 0x29 && regs[1] == 11)) {
            uint64_t heap_request = g->arg == 1 ? regs[1] : 0;
            int main_priority = g->arg == 0x0c && ((uint32_t)regs[1] == 0x1001 ||
                ((uint32_t)regs[1] == 0xffff8000 && pthread_equal(pthread_self(), state.owner)));
            char error[512]; int handled = botw_kernel_dispatch(state.kernel, (unsigned)g->arg, regs, error, sizeof error);
            if (handled == BOTW_KERNEL_ERROR) fail("kernel: %s", error);
            if (handled == BOTW_KERNEL_HANDLED) {
                if (g->arg == 1) report("heap_request=%llu result=0x%llx heap_base=0x%llx",
                    (unsigned long long)heap_request, (unsigned long long)regs[0], (unsigned long long)regs[1]);
                if (g->arg == 1 && !regs[0] && state.heap_policy_active &&
                        pthread_equal(pthread_self(), state.owner) && state.current_entry &&
                        !strcmp(state.current_entry, "nninitStartup")) {
                    if (!state.heap_size || heap_request != state.heap_size)
                        fail("bounded heap: original SDK requested an unexpected heap size");
                    state.heap_initial_sized = 1;
                }
                /* The audited base-v0 startup ignores allocation errors before
                 * consuming its output pointer. Stop this known initial-heap
                 * failure here; ordinary guest resizing keeps Horizon results. */
                if (g->arg == 1 && regs[0] && pthread_equal(pthread_self(), state.owner) &&
                        state.current_entry && !strcmp(state.current_entry, "nninitStartup"))
                    fail("initial game heap allocation failed: requested=%llu result=0x%llx",
                        (unsigned long long)heap_request, (unsigned long long)regs[0]);
                if (main_priority && !regs[0]) report("main_thread_priority=%llu", (unsigned long long)regs[1]);
                return;
            }
        }
        if (g->arg == 0x1d) { /* SignalProcessWideKey(key, count). */
            uint64_t key = regs[0]; int32_t requested = (int32_t)regs[1];
            if ((key & 3) || !services_memory(NULL, key, 4, 1)) fail("condition key is not aligned guest writable memory");
            unsigned waiters = 0, woken = 0;
            pthread_mutex_lock(&condition_lock);
            for (ConditionWaiter *waiter = state.condition_waiters; waiter; waiter = waiter->next) {
                if (waiter->key != key || waiter->signalled) continue;
                waiters++;
                if (requested > 0 && woken >= (unsigned)requested) continue;
                waiter->signalled = 1; woken++; pthread_cond_signal(&waiter->condition);
            }
            unsigned remaining = 0;
            for (ConditionWaiter *waiter = state.condition_waiters; waiter; waiter = waiter->next)
                if (waiter->key == key && !waiter->signalled) remaining++;
            /* Horizon clears the guest HasWaiterFlag when its condition tree no
               longer contains this key, including an initially empty queue. */
            if (!remaining) __atomic_store_n((uint32_t *)key, 0, __ATOMIC_RELEASE);
            pthread_mutex_unlock(&condition_lock);
            regs[0] = 0;
            report("condition_signal requested=%d waiters=%u woken=%u remaining=%u", requested, waiters, woken, remaining); return;
        }
        if (g->arg == 0x06) {
            if (!services_memory(NULL, regs[0], 40, 1)) fail("QueryMemory output is not guest writable memory");
            uint64_t address = regs[2], lower = 0;
            uint64_t upper = state.kernel ? botw_kernel_address_space_limit(state.kernel) : BOTW_ADDRESS_SPACE_SIZE;
            uint64_t top = (uint64_t)pthread_get_stackaddr_np(pthread_self());
            size_t stack_size = pthread_get_stacksize_np(pthread_self());
            struct { uint64_t base, size; uint32_t type, attr, permission, ipc, device, padding; } info = {0};
            const char *kind = "free";
            botw_kernel_memory_info kernel_info;
            if (state.kernel && botw_kernel_query_memory(state.kernel, address, &kernel_info) == 1) {
                memcpy((void *)regs[0], &kernel_info, sizeof kernel_info); regs[0] = 0; regs[1] = 0;
                report("query_memory=kernel bytes=%llu", (unsigned long long)kernel_info.size); return;
            }
            if (address >= upper) { info.base = upper; info.size = 0 - upper; info.type = 0x10; kind = "inaccessible"; }
            else {
                /* Query the guest-visible map, not unrelated host libraries. The
                   signed module page protections and actual stack are known. */
                struct { uint64_t base, size; unsigned type, permission; } regions[MODULE_COUNT * 5 + 1];
                unsigned count = 0;
                for (unsigned mindex = 0; mindex < MODULE_COUNT; mindex++) {
                    Module *m = &state.modules[mindex];
                    size_t starts[5] = {0, ROUND_PAGE(m->text_end), m->key_off, m->key_off + PAGE, m->patch_off};
                    size_t ends[5] = {ROUND_PAGE(m->text_end), m->key_off, m->key_off + PAGE, m->patch_off, m->size};
                    for (unsigned segment = 0; segment < 5; segment++) if (ends[segment] > starts[segment]) {
                        regions[count].base = (uint64_t)m->base + starts[segment];
                        regions[count].size = ends[segment] - starts[segment];
                        regions[count].type = segment == 0 || segment == 4 ? 3 : 4;
                        regions[count++].permission = segment == 0 || segment == 4 ? 5 : segment == 2 ? 1 : 3;
                    }
                }
                regions[count].base = top - stack_size; regions[count].size = stack_size;
                regions[count].type = 0xb; regions[count++].permission = 3;
                for (unsigned region = 0; region < count; region++) {
                    uint64_t base = regions[region].base, size = regions[region].size;
                    unsigned type = regions[region].type, permission = regions[region].permission;
                    if (address >= base && address - base < size) {
                        info.base = base; info.size = size; info.type = type; info.permission = permission;
                        kind = type == 0xb ? "stack" : "module"; break;
                    }
                    if (base + size <= address && base + size > lower) lower = base + size;
                    if (base > address && base < upper) upper = base;
                }
                if (!info.size) { info.base = lower; info.size = upper - lower; }
                uint64_t reserved_base, reserved_size;
                if (state.kernel && botw_kernel_reservation(state.kernel, &reserved_base, &reserved_size) == 1 && !strcmp(kind, "free")) {
                    if (reserved_base > address && reserved_base < info.base + info.size) info.size = reserved_base - info.base;
                    if (reserved_base + reserved_size <= address && reserved_base + reserved_size > info.base) {
                        uint64_t end = info.base + info.size; info.base = reserved_base + reserved_size; info.size = end - info.base;
                    }
                }
            }
            memcpy((void *)regs[0], &info, sizeof info); regs[0] = 0; regs[1] = 0;
            report("query_memory=%s bytes=%llu", kind, (unsigned long long)info.size); return;
        }
        if (g->arg == 0x29 && regs[1] == 11) {
            /* Horizon exposes four random values fixed for the process. */
            if ((uint32_t)regs[2] != 0 || regs[3] >= 4) fail("RandomEntropy requires handle=0 and subtype<4");
            unsigned index = (unsigned)regs[3]; regs[0] = 0; regs[1] = state.random_entropy[index]; return;
        }
        if (g->arg == 0x1f) {
            char port[16]; size_t length;
            for (length = 0; length < sizeof port; length++) {
                if (regs[1] > UINT64_MAX - length || !services_memory(NULL, regs[1] + length, 1, 0))
                    fail("named port string is outside guest readable memory");
                port[length] = *(const char *)(regs[1] + length);
                if (!port[length]) break;
            }
            if (length == sizeof port) fail("named port string exceeds length limit");
            char error[512]; uint32_t handle;
            if (botw_services_connect(state.services, port, length, &handle, error, sizeof error)) fail("ConnectToNamedPort: %s", error);
            regs[0] = 0; regs[1] = handle; report("named_port_connected=%s handle=0x%x", port, handle); return;
        }
        if (g->arg == 0x16) {
            char error[512]; uint32_t handle = (uint32_t)regs[0];
            if (handle >= 0x20000) {
                int handled = botw_kernel_dispatch(state.kernel, 0x16, regs, error, sizeof error);
                if (handled != BOTW_KERNEL_HANDLED) fail("kernel CloseHandle: %s", error);
                return;
            }
            if (botw_services_close(state.services, handle, error, sizeof error)) fail("CloseHandle: %s", error);
            regs[0] = 0; return;
        }
        if (g->arg == 0x21 || g->arg == 0x22) {
            uint32_t handle = (uint32_t)(g->arg == 0x21 ? regs[0] : regs[2]);
            void *ipc = g->arg == 0x21 ? pthread_getspecific(state.keys[2]) : (void *)regs[0];
            size_t capacity = g->arg == 0x21 ? 0x100 : (size_t)regs[1];
            if (!capacity || capacity > 0x1000 || !services_memory(NULL, (uint64_t)ipc, capacity, 1))
                fail("IPC buffer outside guest writable memory");
            uint32_t *header = ipc;
            report("ipc_send handle=0x%x type=%u header1=0x%x bytes=%zu", handle, header[0] & 0xffff, header[1], capacity);
            char error[512];
            if (botw_services_send(state.services, handle, ipc, capacity, error, sizeof error)) fail("SendSyncRequest: %s", error);
            regs[0] = 0; return;
        }
        fail("unsupported SVC 0x%x at original offset 0x%llx (x0=0x%llx x1=0x%llx x2=0x%llx x3=0x%llx x4=0x%llx)",
             g->arg, (unsigned long long)g->original, (unsigned long long)regs[0], (unsigned long long)regs[1],
             (unsigned long long)regs[2], (unsigned long long)regs[3], (unsigned long long)regs[4]);
    }
    fail("unregistered host gate");
}
static void initialize_keys(void) {
    for (unsigned i = 0; i < 8; i++) { if (pthread_key_create(&state.keys[i], NULL)) fail("allocate host thread key"); state.key_count++; }
    for (unsigned j = 0; j < MODULE_COUNT; j++) {
        Module *m = &state.modules[j]; uint64_t *page = at(m, m->key_off, m->key_size, "key page");
        for (unsigned i = 0; i < 8; i++) page[m->key_idx[i]] = (uint64_t)state.keys[i] * 8;
        if (mprotect(page, PAGE, PROT_READ)) fail("%s: seal key page", m->name);
    }
    pthread_setspecific(state.keys[0], (void *)0x1234); uint64_t tsd;
    __asm__ volatile("mrs %0, tpidrro_el0" : "=r"(tsd));
    if (*(uint64_t *)((tsd & ~7ull) + state.keys[0] * 8) != 0x1234) fail("Apple pthread TSD layout differs from rewrite ABI");
    pthread_setspecific(state.keys[0], NULL);
    /* The SDK owns its eventual ThreadType. Initial context matches Horizon's
       0x200-byte TLR ABI; no D2R resolution or aspect parameters are installed. */
    state.tls = calloc(1, 8192); state.tlr = calloc(1, 0x600);
    if (!state.tls || !state.tlr) fail("allocate guest initial thread context");
    *(void **)((uint8_t *)state.tlr + 0x1f8) = (uint8_t *)state.tlr + 0x200;
    pthread_setspecific(state.keys[1], state.tls); pthread_setspecific(state.keys[2], state.tlr);
    pthread_setspecific(state.keys[3], (void *)botw_gate);
}
static void require_executable(uint64_t pointer, const char *entry) {
    if (pointer & 3) fail("unaligned guest executable entry %s", entry);
    for (unsigned i = 0; i < MODULE_COUNT; i++) {
        Module *m = &state.modules[i]; uint64_t base = (uint64_t)m->base;
        if (pointer < base) continue;
        uint64_t offset = pointer - base;
        if (offset < m->text_end || (offset >= m->patch_off && offset < m->size)) return;
    }
    fail("guest entry %s does not point to signed code", entry);
}
#if defined(BOTW_WITH_GRAPHICS) || defined(BOTW_WITH_AUDIO)
static int invoke_guest_callback(uint64_t target, const char *entry, const char *fault_prefix,
        void (*invoke)(uint64_t, void *), void *arguments, char *error, size_t capacity) {
    int owner = pthread_equal(pthread_self(), state.owner);
    if (!(owner && state.active && state.guest_executing) && !(child_context && child_context->active)) {
        if (error && capacity) snprintf(error, capacity, "%s requires an active guest thread", entry);
        return 0;
    }
    /* Heap storage avoids indeterminate automatic values after siglongjmp. */
    CallbackContext *context = calloc(1, sizeof *context);
    if (!context) {
        if (error && capacity) snprintf(error, capacity, "allocate %s recovery boundary", entry);
        return 0;
    }
    context->previous = callback_context;
    if (owner) context->previous_guest_executing = state.guest_executing;
    callback_context = context;
    int accepted = 0;
    if (!sigsetjmp(context->recovery, 1)) {
        context->active = 1;
        require_executable(target, entry);
        invoke(target, arguments);
        accepted = 1;
    } else if (context->fault_signal) {
        describe_fault(context->error, sizeof context->error, fault_prefix,
            context->fault_signal, context->fault_pc, context->fault_address, context->fault_registers);
    }
    context->active = 0;
    callback_context = context->previous;
    if (owner) state.guest_executing = context->previous_guest_executing;
    if (error && capacity) snprintf(error, capacity, "%s", accepted ? "" : context->error);
    free(context);
    return accepted;
}
#endif
#if defined(BOTW_WITH_GRAPHICS)
typedef struct {
    NVNcommandBuffer *command;
    NVNcommandBufferMemoryEvent event;
    size_t minimum;
    void *userdata;
} GraphicsCallbackArguments;
static void call_graphics_memory_callback(uint64_t function, void *opaque) {
    GraphicsCallbackArguments *args = opaque;
    ((PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC)(uintptr_t)function)(
        args->command, args->event, args->minimum, args->userdata);
}
static int graphics_invoke_memory_callback(PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC function,
        NVNcommandBuffer *command, NVNcommandBufferMemoryEvent event, size_t minimum,
        void *userdata, char *error, size_t capacity) {
    GraphicsCallbackArguments args = {command, event, minimum, userdata};
    return invoke_guest_callback((uint64_t)(uintptr_t)function, "NVN command-memory callback",
        "graphics callback ", call_graphics_memory_callback, &args, error, capacity);
}
#endif
static void run_module_constructors(void) {
    /* The fixed BOTW rtld builds its module list by inserting the ascending
       Horizon NSO sequence at the head (+0x51c..0x53c). InitModules (+0x748)
       traverses that reversed list and +0xb0c invokes each module's DT_INIT.
       NSO order is documented by Atmosphere's loader implementation:
       https://github.com/Atmosphere-NX/Atmosphere/blob/master/docs/components/modules/loader.md
       DT_INIT itself registers the DSO and invokes its init_array; invoking the
       array separately here would run constructors twice. */
    for (int i = MODULE_COUNT - 1; i >= 0; i--) {
        Module *m = &state.modules[i]; if (!m->init) continue;
        require_range(m, m->init, 4, "DT_INIT");
        uint64_t pointer = (uint64_t)m->base + m->init; require_executable(pointer, m->name);
        report("constructor_called=%s", m->name); state.guest_started = 1; state.guest_executing = 1;
        ((void (*)(void))pointer)(); state.guest_executing = 0;
        report("constructor_returned=%s", m->name);
    }
}
static uint64_t need_entry(const char *name) {
    uint64_t p = find_symbol(name); if (!p) fail("missing SDK entry %s", name);
    require_executable(p, name); report("entry_resolved=%s", name); return p;
}
static void call_entry(const char *name, int two_args) {
    if (state.heap_policy_active && !strcmp(name, "nninitStartup")) {
        if (state.heap_size) fail("bounded heap: initial parameters may only be selected once");
        uint64_t total = allocator_kernel_info(6);
        if (total < state.image_bytes || total - state.image_bytes > SIZE_MAX)
            fail("bounded heap: invalid actual kernel memory accounting");
        size_t live = host_available_memory(); char error[512];
        if (botw_host_heap_policy_plan((size_t)(total - state.image_bytes), live,
                &state.heap_size, &state.root_size, error, sizeof error)) fail("%s", error);
        install_heap_parameter_slots();
        report("heap_policy=botw-switch-base-v0-bounded-heap-v1 experiment=true kernel_heap_available=%llu live_host_available=%zu host_reserve=%zu heap=%zu root=%zu startup_block=%zu game_playable=false",
            (unsigned long long)(total - state.image_bytes), live, HEAP_POLICY_RESERVE,
            state.heap_size, state.root_size, HEAP_POLICY_STARTUP);
    }
    if (state.heap_policy_active && !strcmp(name, "nnMain")) {
        uint64_t used = allocator_kernel_info(7);
        if (!state.heap_initial_sized || used < state.image_bytes ||
                used - state.image_bytes != state.heap_size ||
                state.root_size + HEAP_POLICY_STARTUP != state.heap_size)
            fail("bounded heap: actual SDK heap and game root parameters are inconsistent");
        report("heap_policy_actual_heap_verified=true heap=%zu root=%zu original_sdk_allocator=true", state.heap_size, state.root_size);
    }
    uint64_t p = need_entry(name); report("entry_called=%s tls=0x%llx tlr=0x%llx", name,
        (unsigned long long)pthread_getspecific(state.keys[1]), (unsigned long long)pthread_getspecific(state.keys[2]));
    state.guest_started = 1; state.guest_executing = 1;
    state.current_entry = name;
    if (two_args) ((void (*)(uint64_t, uint64_t))p)(0x1001, 0);
    else ((void (*)(void))p)();
    state.guest_executing = 0;
    state.current_entry = NULL;
    report("entry_returned=%s guest_execution_tested=true", name);
}
static void signal_handler(int number, siginfo_t *info, void *context) {
    if (handle_memory_tracking_fault(number, info, context)) return;
    ucontext_t *uc = context;
    uintptr_t pc = (uintptr_t)__darwin_arm_thread_state64_get_pc(uc->uc_mcontext->__ss);
    /* A callback PC=0 cannot distinguish guest BLR NULL from a host helper's
     * tail call to NULL, even with a guest LR. Preserve the prior disposition
     * for that ambiguous fault; do not fall into the outer run's recovery.
     * The established primary-entry corrupt-stack boundary stays unchanged. */
    int guest_pc = pc == 0 && !(callback_context && callback_context->active);
    int current_is_owner = pthread_equal(pthread_self(), state.owner);
    for (unsigned i = 0; (current_is_owner || child_context) && i < MODULE_COUNT; i++) {
        uintptr_t base = (uintptr_t)state.modules[i].base;
        if (base && pc >= base && pc - base < state.modules[i].size) guest_pc = 1;
    }
    if (callback_context && callback_context->active && guest_pc) {
        callback_context->fault_signal = number; callback_context->fault_pc = pc;
        callback_context->fault_address = (uintptr_t)info->si_addr;
        for (unsigned i = 0; i < 29; i++) callback_context->fault_registers[i] = uc->uc_mcontext->__ss.__x[i];
        siglongjmp(callback_context->recovery, 1);
    }
    if (current_is_owner && state.active && state.guest_executing && guest_pc) {
        state.fault_signal = number; state.fault_pc = pc;
        state.fault_address = (uintptr_t)info->si_addr;
        for (unsigned i = 0; i < 29; i++) state.fault_registers[i] = uc->uc_mcontext->__ss.__x[i];
        state.guest_executing = 0; siglongjmp(state.recovery, 1);
    }
    if (child_context && child_context->active && guest_pc) {
        child_context->fault_signal = number; child_context->fault_pc = pc;
        child_context->fault_address = (uintptr_t)info->si_addr;
        for (unsigned i = 0; i < 29; i++) child_context->fault_registers[i] = uc->uc_mcontext->__ss.__x[i];
        siglongjmp(child_context->recovery, 1);
    }
    /* Do not relabel bugs in the loader, host callbacks, or unrelated app threads
       as guest CPU faults. Preserve the process's previous signal disposition. */
    for (unsigned i = 0; i < state.signal_count; i++) if (caught_signals[i] == number) {
        sigaction(number, &state.previous[i], NULL); raise(number); return;
    }
}
static void install_signal_boundary(void) {
    if (sigaltstack(NULL, &state.previous_stack)) fail("read existing alternate signal stack");
    size_t size = 64 * 1024; state.signal_stack = malloc(size);
    if (!state.signal_stack) fail("allocate guest fault signal stack");
    stack_t alternate = {.ss_sp = state.signal_stack, .ss_size = size, .ss_flags = 0};
    if (sigaltstack(&alternate, NULL)) fail("install guest fault signal stack");
    state.alternate_stack_installed = 1;
    struct sigaction action; memset(&action, 0, sizeof action);
    action.sa_sigaction = signal_handler; action.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigemptyset(&action.sa_mask);
    for (unsigned i = 0; i < sizeof caught_signals / sizeof caught_signals[0]; i++) {
        if (sigaction(caught_signals[i], &action, &state.previous[i])) fail("install guest CPU fault handler");
        state.signal_count++;
    }
}
static int cleanup(void) {
    char error[512];
    pthread_mutex_lock(&lifecycle_lock);
    state.vi_display_live = 0;
    pthread_mutex_unlock(&lifecycle_lock);
    pthread_mutex_lock(&focus_lock); focus_stopping = 1;
    pthread_cond_broadcast(&focus_changed); pthread_mutex_unlock(&focus_lock);
    int pending = 0;
    if (state.kernel) {
        for (unsigned attempt = 0; attempt < 20; attempt++) {
            pending = botw_kernel_shutdown(state.kernel, error, sizeof error);
            if (!pending) break;
            struct timespec interval = {.tv_nsec = 10000000}; nanosleep(&interval, NULL);
        }
    }
    if (pending) {
        report("error=guest workers still active; retaining code and host resources: %s", error); return -1;
    }
#if defined(BOTW_WITH_AUDIO)
    pthread_mutex_lock(&lifecycle_lock);
    int audio_pending = botw_audio_shutdown(state.audio, error, sizeof error);
    if (!audio_pending) state.audio = NULL;
    pthread_mutex_unlock(&lifecycle_lock);
    if (audio_pending) {
        report("error=audio still active; retaining kernel and host resources: %s", error); return -1;
    }
#endif
    pthread_mutex_lock(&lifecycle_lock);
    botw_services_destroy(state.services); state.services = NULL;
    if (state.vi_vsync_write && botw_kernel_close_handle(state.kernel, state.vi_vsync_write, error, sizeof error))
        report("error=close VI vsync write endpoint: %s", error);
    if (state.vi_vsync_read && botw_kernel_close_handle(state.kernel, state.vi_vsync_read, error, sizeof error))
        report("error=close VI vsync read endpoint: %s", error);
    state.vi_vsync_write = state.vi_vsync_read = 0;
    pthread_mutex_unlock(&lifecycle_lock);
    if (state.graphics_started && graphics_backend_shutdown) graphics_backend_shutdown();
    pthread_mutex_lock(&lifecycle_lock);
    state.graphics_started = 0; state.graphics_workspace = NULL; state.graphics_workspace_size = 0;
    state.host39_transport_audited = 0;
    state.vi_initialized = 0; state.vi_display = state.vi_layer = NULL; state.vi_last_target_timestamp = 0;
    pthread_mutex_unlock(&lifecycle_lock);
    if (state.input) botw_input_unbind_shared_memory(state.input);
    if (state.input_owned) botw_input_destroy(state.input);
    state.input = NULL;
    botw_romfs_close(state.romfs); state.romfs = NULL;
    botw_savefs_close(state.savefs); state.savefs = NULL;
    botw_kernel_destroy(state.kernel); state.kernel = NULL;
    for (unsigned i = 0; i < state.signal_count; i++) sigaction(caught_signals[i], &state.previous[i], NULL);
    /* Darwin validates ss_size even with SS_DISABLE (an initially disabled
       stack is reported with size zero). Supply the documented minimum. */
    if (state.previous_stack.ss_flags & SS_DISABLE) state.previous_stack.ss_size = MINSIGSTKSZ;
    if (!state.alternate_stack_installed || !sigaltstack(&state.previous_stack, NULL)) free(state.signal_stack);
    else report("error=restore alternate signal stack failed; retaining fault stack memory");
    for (unsigned i = 0; i < state.key_count; i++) { pthread_setspecific(state.keys[i], NULL); pthread_key_delete(state.keys[i]); }
    for (unsigned i = 0; i < MODULE_COUNT; i++) if (state.modules[i].base) munmap(state.modules[i].base, state.modules[i].size);
    for (unsigned i = 0; i < state.stub_count; i++) free(state.stubs[i]);
    free(state.tls); free(state.tlr); if (state.framework) dlclose(state.framework);
    return 0;
}
int botw_host_run_mode(const char *root, const char *documents, botw_host_event event, enum botw_host_mode mode) {
    if (pthread_mutex_trylock(&run_lock)) return 2;
    pthread_mutex_lock(&lifecycle_lock);
    memset(&state, 0, sizeof state); state.event = event; state.owner = pthread_self();
    state.address_bits = configured_address_bits;
    pthread_mutex_unlock(&lifecycle_lock);
    pthread_mutex_lock(&focus_lock); focus_suspended = 0; focus_stopping = 0;
    pthread_mutex_unlock(&focus_lock);
    int result = 0;
    if (sigsetjmp(state.recovery, 1)) {
        result = 1;
        if (state.fault_signal) {
            char fault[512]; describe_fault(fault, sizeof fault, "", state.fault_signal,
                state.fault_pc, state.fault_address, state.fault_registers); report("error=%s", fault);
        }
        report("host_stage=stopped guest_execution_started=%s phone_ready=false nnMain_completed=false", state.guest_started ? "true" : "false");
    } else {
        install_signal_boundary(); state.active = 1;
        arc4random_buf(state.random_entropy, sizeof state.random_entropy);
        if (!root || !*root || mode < BOTW_HOST_LOAD_ONLY || mode > BOTW_HOST_BOOT_GAME) fail("invalid host run arguments");
        report("host_stage=loading phone_ready=false guest_execution_tested=false");
#if TARGET_OS_IPHONE
        const struct mach_header_64 *header = framework_header(root);
#endif
        for (unsigned i = 0; i < MODULE_COUNT; i++) {
            Module *m = &state.modules[i]; m->name = module_names[i]; read_layout(m, root);
#if TARGET_OS_IPHONE
            map_module(m, header);
#else
            map_module(m, root);
#endif
            parse_dynamic(m); register_gates(m, root); report("module_mapped=%s bytes=%zu symbols=%llu base=0x%llx", m->name, m->size, (unsigned long long)m->sym_count, (unsigned long long)m->base);
        }
        if (state.address_bits == 39) {
            for (unsigned i = 0; i < MODULE_COUNT; i++) {
                Module *m = &state.modules[i]; char error[512];
                if (botw_hipc_address_audit_image(m->name, m->base, m->size, m->text_end,
                        m->patch_off, error, sizeof error)) fail("host39 transport audit: %s", error);
            }
            report("host39_loaded_transport_audited=true inline_sites=36 host_helpers=5");
            state.host39_transport_audited = 1;
        }
        char heap_error[512];
        int heap_mode = heap_policy_image_mode(&state.modules[0], heap_error, sizeof heap_error);
        if (heap_mode < 0) fail("%s", heap_error);
        if (heap_mode) {
            if (state.address_bits != 39 || !state.host39_transport_audited ||
                    find_raw_symbol("nninitStartup") != (uint64_t)state.modules[0].base + 0x7352f8 ||
                    find_raw_symbol("nnMain") != (uint64_t)state.modules[0].base + 0x735330)
                fail("bounded heap requires the fixed base-v0 Host39 entry profile");
            state.heap_policy_active = 1;
            report("heap_policy_loaded_audited=true parameter_sites=2 tsd_slots=KRES0,KRES1 experimental=true");
        }
        initialize_keys();
        host_pool_push = (void *(*)(void))dlsym(RTLD_DEFAULT, "objc_autoreleasePoolPush");
        host_pool_pop = (void (*)(void *))dlsym(RTLD_DEFAULT, "objc_autoreleasePoolPop");
        const botw_kernel_callbacks kernel_callbacks = {.log = services_log, .memory = external_memory,
            .executable = executable_address, .execute_thread = execute_child};
        state.kernel = botw_kernel_create(&kernel_callbacks, NULL);
        if (!state.kernel) fail("allocate Horizon kernel context");
        /* Fixed base-v0 NPDM KAC KernelFlags 0x020073b7 allows priorities
         * 28 through 59. Host QoS remains a separate measured host policy. */
        char priority_error[512];
        if (botw_kernel_configure_thread_priority_mask(state.kernel, UINT64_C(0x0ffffffff0000000),
                priority_error, sizeof priority_error)) fail("configure guest priorities: %s", priority_error);
        if (botw_kernel_configure_core_mask(state.kernel, 7, priority_error, sizeof priority_error))
            fail("configure guest processor capabilities: %s", priority_error);
        configure_kernel_memory();
        pthread_mutex_lock(&lifecycle_lock);
        state.services = botw_services_create(services_log, services_memory, NULL);
        state.resource_user_id = ((uint64_t)(uint32_t)getpid() << 32) | ++run_sequence;
        pthread_mutex_unlock(&lifecycle_lock);
        if (!state.services) fail("allocate Horizon service sessions");
        botw_services_set_performance_handler(state.services, services_performance, NULL);
        botw_services_set_focus_handler(state.services, services_focus, NULL);
        const botw_services_kernel_api kernel_api = {.opaque = state.kernel,
            .create_event = kernel_event_create, .duplicate_handle = kernel_duplicate,
            .signal_event = kernel_signal, .clear_event = kernel_clear, .close_handle = kernel_close,
            .is_process_handle = kernel_process};
        botw_services_set_kernel_api(state.services, &kernel_api);
        configure_resource_backends(documents);
#if defined(BOTW_WITH_AUDIO)
        initialize_audio();
#endif
        botw_host_set_applet_state(host_foreground, host_focused);
        for (unsigned i = 0; i < MODULE_COUNT; i++) relocate(&state.modules[i]);
        report("modules_loaded=6 relocations=%zu weak_undefined_zero=%zu unsupported_imports=%u gates=%u", state.relocations, state.weak_zero, state.stub_count, state.gate_count);
        const char *entries[] = {"_init_libc0", "nnosInitialize", "_init_libc1", "nndiagStartup", "nninitInitializeSdkModule", "nninitStartup", "_init_libc2", "nnMain"};
        for (unsigned i = 0; i < sizeof entries / sizeof entries[0]; i++) need_entry(entries[i]);
        if (mode == BOTW_HOST_LOAD_ONLY) report("load_validation_completed=true guest_execution_tested=false nnMain_called=false phone_ready=false");
        else {
            if (graphics_backend_init) {
                if (!graphics_backend_init(configured_graphics_layer, graphics_error, graphics_memory)) fail("initialize Metal graphics backend");
                state.graphics_started = 1;
#if defined(BOTW_WITH_GRAPHICS)
                botw_graphics_set_memory_access_validator(graphics_memory_access);
                botw_graphics_set_command_memory_invoker(graphics_invoke_memory_callback);
                /* Preserve the first actual presented draw for device review.
                 * The frontend disables capture after saving that frame. */
                botw_graphics_set_capture_enabled(1);
                char path[4096]; int length = snprintf(path, sizeof path, "%s/botw-shaders", documents);
                if (length < 0 || (size_t)length >= sizeof path) fail("shader bundle path exceeds limit");
                if (!access(path, F_OK)) {
                    botw_graphics_shader_bundle_result shaders; char error[512];
                    if (!botw_graphics_load_shader_bundle(path, &shaders, error, sizeof error)) fail("load private shader bundle: %s", error);
                    report("shader_bundle_entries=%zu registered=%zu unsupported=%zu gameplay_verified=false",
                        shaders.declared_entries, shaders.registered_entries, shaders.unsupported_entries);
                } else if (errno == ENOENT) report("shader_bundle_available=false; unmatched shader identities will fail");
                else fail("read private shader bundle: %s", strerror(errno));
#endif
            }
            call_entry("_init_libc0", 0); call_entry("nnosInitialize", 1); call_entry("_init_libc1", 0);
            call_entry("nndiagStartup", 0); call_entry("nninitInitializeSdkModule", 0); call_entry("nninitStartup", 0); call_entry("_init_libc2", 0);
            if (mode == BOTW_HOST_SDK_PROBE) report("sdk_probe_completed=true nnMain_called=false phone_ready=false");
            else {
                run_module_constructors();
                report("nnMain_called=true phone_ready=false"); call_entry("nnMain", 0);
                report("nnMain_returned=true game_playable=false phone_ready=false");
            }
        }
    }
    state.active = 0;
    if (cleanup()) return 1; /* Keep run_lock: live workers still own this state. */
    pthread_mutex_unlock(&run_lock); return result;
}
int botw_host_run(const char *root, const char *documents, botw_host_event event) {
    return botw_host_run_mode(root, documents, event, BOTW_HOST_SDK_PROBE);
}
