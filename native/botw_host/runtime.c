/* BOTW native host experiment.
 * Loader/gate/TSD design derived from jhaizhou-ops/d2r-mobile, MIT, commit
 * e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8. See THIRD_PARTY_LICENSE.txt.
 * This implementation rejects unsupported operations; it never substitutes
 * a successful return value for an unimplemented SDK call or kernel service.
 */
#include "runtime.h"
#include "services.h"
#include "kernel.h"
#include "../botw_input/input.h"
#include "../botw_resources/romfs.h"
#include <TargetConditionals.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <pthread.h>
#include <setjmp.h>
#include <signal.h>
#include <stdarg.h>
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
#if TARGET_OS_IPHONE
#include <os/proc.h>
#endif

/* Headless tests have no Metal dependency. Production builds must link the
 * actual backend; an absent symbol is a link failure rather than a stub. */
#if defined(BOTW_WITH_GRAPHICS)
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
static void *configured_graphics_layer;
static botw_input *configured_input;
static int host_foreground = 1, host_focused = 1;
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
    int input_owned;
    uint32_t input_shared_handle;
    uint64_t resource_user_id;
    int graphics_started;
    botw_host_event event;
    sigjmp_buf recovery;
    pthread_t owner;
    volatile sig_atomic_t active, fault_signal, guest_executing;
    volatile uintptr_t fault_pc;
    stack_t previous_stack;
    void *signal_stack;
    unsigned signal_count;
    int alternate_stack_installed;
    int guest_started;
    struct sigaction previous[4];
} state;
typedef struct {
    sigjmp_buf recovery;
    int active, fault_signal;
    uintptr_t fault_pc;
    void *tls, *tlr, *signal_stack, *autorelease_pool;
    stack_t previous_stack;
    int alternate_stack_installed;
    char error[512];
} ChildContext;
static _Thread_local ChildContext *child_context;
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
    report("error=%s", message);
    if (child_context && child_context->active) {
        snprintf(child_context->error, sizeof child_context->error, "%s", message);
        siglongjmp(child_context->recovery, 1);
    }
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
static uint64_t find_symbol(const char *name) {
    if (!strcmp(name, "nvnBootstrapLoader") && graphics_backend_loader)
        return (uint64_t)graphics_backend_loader;
    for (unsigned j = 0; j < MODULE_COUNT; j++) {
        Module *m = &state.modules[j];
        for (unsigned i = 1; i < m->sym_count; i++) {
            const Sym *s = symbol_at(m, i);
            if (s->section && s->info >= 0x10 && !strcmp(symbol_name(m, s), name)) return defined_value(m, s);
        }
    }
    return 0;
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
static void configure_kernel_memory(void) {
    const size_t mib = 1024u * 1024u;
    size_t image = 0;
    for (unsigned i = 0; i < MODULE_COUNT; i++) image += state.modules[i].size;
    size_t available = 0;
#if TARGET_OS_IPHONE
    available = os_proc_available_memory();
#else
    uint64_t physical = 0; size_t length = sizeof physical;
    if (sysctlbyname("hw.memsize", &physical, &length, NULL, 0)) fail("query physical memory");
    available = physical > SIZE_MAX ? SIZE_MAX : (size_t)physical;
#endif
    /* Include already mapped modules while respecting the host's live limit.
     * Virtual capacities do not grant committed physical memory. */
    size_t budget = 3072u * mib;
    if (available < budget && image < budget - available) budget = available + image;
    if (budget < image) fail("host memory budget below loaded guest image");
    char error[512];
    if (botw_kernel_configure_memory(state.kernel, 3072u * mib, 256u * mib,
            512u * mib, budget, image, error, sizeof error)) fail("configure guest memory: %s", error);
    report("guest_memory_budget=%zu image_bytes=%zu host_available=%zu", budget, image, available);
}
static void child_cleanup(void *opaque) {
    ChildContext *context = opaque;
    if (context->autorelease_pool && host_pool_pop) host_pool_pop(context->autorelease_pool);
    for (unsigned i = 0; i < state.key_count; i++) pthread_setspecific(state.keys[i], NULL);
    if (context->previous_stack.ss_flags & SS_DISABLE) context->previous_stack.ss_size = MINSIGSTKSZ;
    if (!context->alternate_stack_installed || !sigaltstack(&context->previous_stack, NULL)) free(context->signal_stack);
    free(context->tls); free(context->tlr); context->active = 0; child_context = NULL; free(context);
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
        if (host_pool_push) context->autorelease_pool = host_pool_push();
        report("guest_thread_entered handle=0x%x", handle);
        ((void (*)(uint64_t, uint64_t))entry)(argument, handle);
        snprintf(context->error, sizeof context->error, "guest thread returned without ExitThread handle=0x%x", handle);
    } else if (context->fault_signal) {
        snprintf(context->error, sizeof context->error, "guest child CPU fault signal=%d pc=0x%llx", context->fault_signal, (unsigned long long)context->fault_pc);
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
void botw_host_set_applet_state(int foreground, int focused) {
    pthread_mutex_lock(&lifecycle_lock);
    host_foreground = !!foreground; host_focused = !!focused;
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
}
int botw_host_set_graphics_layer(void *layer) {
    if (pthread_mutex_trylock(&run_lock)) return 2;
    configured_graphics_layer = layer; pthread_mutex_unlock(&run_lock); return 0;
}
int botw_host_set_input(botw_input *input) {
    if (pthread_mutex_trylock(&run_lock)) return 2;
    configured_input = input; pthread_mutex_unlock(&run_lock); return 0;
}
static int graphics_memory(const void *address, size_t length) { return services_memory(NULL, (uint64_t)address, length, 0); }
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
    for (unsigned i = 0; i < state.gate_count; i++) {
        Gate *g = &state.gates[i]; if (g->ret != ret) continue;
        if (g->kind) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); regs[g->arg] = (uint64_t)ts.tv_sec * 19200000ull + (uint64_t)ts.tv_nsec * 12 / 625; return; }
        if (state.kernel && g->arg != 0x06 && g->arg != 0x16 &&
            !(g->arg == 0x29 && regs[1] == 11) &&
            !(g->arg == 0x0c && ((uint32_t)regs[1] == 0x1001 || (uint32_t)regs[1] == 0xffff8000))) {
            char error[512]; int handled = botw_kernel_dispatch(state.kernel, (unsigned)g->arg, regs, error, sizeof error);
            if (handled == BOTW_KERNEL_ERROR) fail("kernel: %s", error);
            if (handled == BOTW_KERNEL_HANDLED) return;
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
        if (g->arg == 0x0c && ((uint32_t)regs[1] == 0x1001 || (uint32_t)regs[1] == 0xffff8000)) {
            /* main.npdm for fixed BOTW base v0: main_thread_priority = 0x2c. */
            regs[0] = 0; regs[1] = 44; report("main_thread_priority=44"); return;
        }
        if (g->arg == 0x06) {
            if (!services_memory(NULL, regs[0], 40, 1)) fail("QueryMemory output is not guest writable memory");
            uint64_t address = regs[2], lower = 0, upper = BOTW_ADDRESS_SPACE_SIZE;
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
    uint64_t p = need_entry(name); report("entry_called=%s", name); state.guest_started = 1; state.guest_executing = 1;
    if (two_args) ((void (*)(uint64_t, uint64_t))p)(0x1001, 0);
    else ((void (*)(void))p)();
    state.guest_executing = 0;
    report("entry_returned=%s guest_execution_tested=true", name);
}
static void signal_handler(int number, siginfo_t *info, void *context) {
    (void)info;
    ucontext_t *uc = context;
    uintptr_t pc = (uintptr_t)__darwin_arm_thread_state64_get_pc(uc->uc_mcontext->__ss);
    int guest_pc = pc == 0; /* A guest indirect branch to NULL also needs recovery. */
    for (unsigned i = 0; i < MODULE_COUNT; i++) {
        uintptr_t base = (uintptr_t)state.modules[i].base;
        if (base && pc >= base && pc - base < state.modules[i].size) guest_pc = 1;
    }
    if (state.active && state.guest_executing && guest_pc && pthread_equal(pthread_self(), state.owner)) {
        state.fault_signal = number; state.fault_pc = pc;
        state.guest_executing = 0; siglongjmp(state.recovery, 1);
    }
    if (child_context && child_context->active && guest_pc) {
        child_context->fault_signal = number; child_context->fault_pc = pc; siglongjmp(child_context->recovery, 1);
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
    if (state.kernel && botw_kernel_shutdown(state.kernel, error, sizeof error)) {
        report("error=guest workers still active; retaining code and host resources: %s", error); return -1;
    }
    pthread_mutex_lock(&lifecycle_lock);
    botw_services_destroy(state.services); state.services = NULL;
    pthread_mutex_unlock(&lifecycle_lock);
    if (state.graphics_started && graphics_backend_shutdown) graphics_backend_shutdown();
    if (state.input) botw_input_unbind_shared_memory(state.input);
    if (state.input_owned) botw_input_destroy(state.input);
    state.input = NULL;
    botw_romfs_close(state.romfs); state.romfs = NULL;
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
    memset(&state, 0, sizeof state); state.event = event; state.owner = pthread_self();
    int result = 0;
    if (sigsetjmp(state.recovery, 1)) {
        result = 1;
        if (state.fault_signal) report("error=guest CPU fault signal=%d pc=0x%llx", state.fault_signal, (unsigned long long)state.fault_pc);
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
            parse_dynamic(m); register_gates(m, root); report("module_mapped=%s bytes=%zu symbols=%llu", m->name, m->size, (unsigned long long)m->sym_count);
        }
        initialize_keys();
        host_pool_push = (void *(*)(void))dlsym(RTLD_DEFAULT, "objc_autoreleasePoolPush");
        host_pool_pop = (void (*)(void *))dlsym(RTLD_DEFAULT, "objc_autoreleasePoolPop");
        const botw_kernel_callbacks kernel_callbacks = {.log = services_log, .memory = external_memory,
            .executable = executable_address, .execute_thread = execute_child};
        state.kernel = botw_kernel_create(&kernel_callbacks, NULL);
        if (!state.kernel) fail("allocate Horizon kernel context");
        configure_kernel_memory();
        pthread_mutex_lock(&lifecycle_lock);
        state.services = botw_services_create(services_log, services_memory, NULL);
        state.resource_user_id = ((uint64_t)(uint32_t)getpid() << 32) | ++run_sequence;
        pthread_mutex_unlock(&lifecycle_lock);
        if (!state.services) fail("allocate Horizon service sessions");
        botw_services_set_performance_handler(state.services, services_performance, NULL);
        const botw_services_kernel_api kernel_api = {.opaque = state.kernel,
            .create_event = kernel_event_create, .duplicate_handle = kernel_duplicate,
            .signal_event = kernel_signal, .clear_event = kernel_clear, .close_handle = kernel_close,
            .is_process_handle = kernel_process};
        botw_services_set_kernel_api(state.services, &kernel_api);
        configure_resource_backends(documents);
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
