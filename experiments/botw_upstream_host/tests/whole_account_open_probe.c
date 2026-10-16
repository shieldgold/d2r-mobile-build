/* Complete original account producer and actual checked-write registry.
 * The only identity provider is this owned immutable fixture: no UID/save I/O,
 * Guest instructions, device calls or SDK emulation are used. */
#include "dm.h"
#include <assert.h>
#include <errno.h>
#include <setjmp.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include <unistd.h>
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>
static sigjmp_buf failure;
static _Atomic int armed, failed;
static _Noreturn void owned_failure(const char *format, ...) {
    (void)format;
    atomic_fetch_add(&failed, 1);
    if (atomic_load(&armed)) siglongjmp(failure, 1);
    abort();
}
#undef die
#define die(...) owned_failure(__VA_ARGS__)
#include "normal_registry.inc"
#include "dm/host/botw_account.c"
static const botw_uid owned_uid = {UINT64_C(0x0706050403020100), UINT64_C(0x0f0e0d0c0b0a0908)};
static _Atomic unsigned identity_reads;
const botw_uid *whole_identity_uid(void) { atomic_fetch_add(&identity_reads, 1); return &owned_uid; }
static const char name[] = "_ZN2nn7account19OpenPreselectedUserEPNS0_10UserHandleE";
static const hle_force_ent *find(const char *symbol) {
    unsigned long bytes = 0;
    const hle_force_ent *entries = (const void *)getsectiondata(&_mh_execute_header, "__DATA", "swforce", &bytes);
    assert(entries && !(bytes % sizeof *entries));
    for (size_t i = 0; i < bytes / sizeof *entries; ++i)
        if (!strcmp(entries[i].name, symbol)) return entries + i;
    return NULL;
}
static void registry(void) {
    const hle_force_ent *entry = find(name);
    assert(entry && "base-v0 Result Open still resolves outside the host account producer");
    assert(entry->fn && entry->titles == DM_TM_BOTW);
    assert(!strcmp(entry->why, "base-v0 preselected user uses the complete native account handle"));
}
_Static_assert(sizeof(Result) == 4, "Result must use w0, not a boolean return");
typedef struct {
    uint64_t before[2];
    botw_user_handle handle;
    uint64_t after[2];
} guarded;
static void *rejected_destination;
static _Atomic unsigned prepares, handle_prepares;
static int require_closed_at_prepare;
static int prepare(void *pointer, size_t bytes) {
    assert(pointer && (bytes == 4 || bytes == 16 || bytes == 24 || bytes == 32));
    atomic_fetch_add(&prepares, 1);
    if (bytes == 24) atomic_fetch_add(&handle_prepares, 1);
    if (require_closed_at_prepare) assert(!atomic_load(&g_account_user_open));
    return pointer != rejected_destination;
}
static void setup(int initialized) {
    assert(dm_guest_write_prep_register_checked(prepare));
    if (initialized) botw_account_initialize();
}
static Result open_user(botw_user_handle *out) {
    const hle_force_ent *entry = find(name); assert(entry);
    return ((Result (*)(botw_user_handle *))entry->fn)(out);
}
static void guarded_clear(guarded *value) { memset(value, 0xa5, sizeof *value); }
static void untouched(const void *p, size_t bytes) {
    for (size_t i = 0; i < bytes; ++i) assert(((const unsigned char *)p)[i] == 0xa5);
}
static void guarded_ok(const guarded *value) {
    for (unsigned i = 0; i < 2; ++i)
        assert(value->before[i] == UINT64_C(0xa5a5a5a5a5a5a5a5) && value->after[i] == UINT64_C(0xa5a5a5a5a5a5a5a5));
}
static void handle_ok(const botw_user_handle *handle) {
    assert(!memcmp(&handle->uid, &owned_uid, 16) && handle->token == LOCAL_USER_TOKEN);
}
static void roundtrip(void) {
    setup(1); guarded value; guarded_clear(&value);
    errno = EDOM; assert(open_user(&value.handle) == RES_OK && errno == EDOM);
    guarded_ok(&value); handle_ok(&value.handle); assert(atomic_load(&g_account_user_open) == 1);
    botw_uid resolved = {0};
    assert(botw_account_get_uid(&resolved, &value.handle) == RES_OK && !memcmp(&resolved, &owned_uid, 16));
    botw_uid users[2]; memset(users, 0xa5, sizeof users); int count = -1;
    assert(botw_account_list_users(&count, users, 2) == RES_OK && count == 1);
    assert(!memcmp(users, &owned_uid, 16) && !users[1].low && !users[1].high);
    guarded pop; guarded_clear(&pop); count = -1;
    botw_account_pop_users(&count, &pop.handle, 1); assert(count == 0); untouched(&pop, sizeof pop);
    /* The original boolean API and the new Result API retain one namespace. */
    assert(botw_account_try_open(&pop.handle) == 1); handle_ok(&pop.handle); guarded_ok(&pop);
    assert(atomic_load(&handle_prepares) == 2);
}
static void pop_then_open(void) {
    setup(1); guarded first, second; guarded_clear(&first); guarded_clear(&second); int count = -1;
    botw_account_pop_users(&count, &first.handle, 1); assert(count == 1); handle_ok(&first.handle); guarded_ok(&first);
    assert(open_user(&second.handle) == RES_OK); handle_ok(&second.handle); guarded_ok(&second);
    assert(!memcmp(&first.handle, &second.handle, sizeof first.handle));
    guarded_clear(&first); count = -1; botw_account_pop_users(&count, &first.handle, 1);
    assert(count == 0); untouched(&first, sizeof first);
}
static void ordinary_failure(const char *mode) {
    setup(strcmp(mode, "uninitialized") != 0); guarded value; guarded_clear(&value);
    errno = ECANCELED;
    assert(open_user(!strcmp(mode, "null-out") ? NULL : &value.handle) == RES_FAIL);
    assert(RES_FAIL == UINT32_C(0x2a2) && errno == ECANCELED);
    untouched(&value, sizeof value); assert(!atomic_load(&g_account_user_open));
    assert(!atomic_load(&prepares) && !atomic_load(&identity_reads));
}
static void prepare_failed(void) {
    setup(1); guarded value; guarded_clear(&value); rejected_destination = &value.handle;
    require_closed_at_prepare = 1; atomic_store(&armed, 1);
    if (!sigsetjmp(failure, 1)) {
        (void)open_user(&value.handle);
        assert(!"checked write rejection was accepted");
    }
    atomic_store(&armed, 0); require_closed_at_prepare = 0;
    assert(atomic_load(&failed) == 1 && atomic_load(&handle_prepares) == 1);
    untouched(&value, sizeof value); assert(!atomic_load(&g_account_user_open));
    /* A failed Open did not consume the original fallback/open state. */
    rejected_destination = NULL; int count = -1;
    botw_account_pop_users(&count, &value.handle, 1);
    assert(count == 1); handle_ok(&value.handle); guarded_ok(&value);
}
static void alignment_and_readonly(void) {
    setup(1); unsigned char *allocation = NULL;
    assert(!posix_memalign((void **)&allocation, 16, 80)); memset(allocation, 0xa5, 80);
    botw_user_handle *out = (void *)(allocation + 8);
    assert(((uintptr_t)out & 15) == 8); assert(open_user(out) == RES_OK);
    untouched(allocation, 8); untouched(allocation + 32, 48); handle_ok(out);
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    void *memory = mmap(NULL, page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(memory != MAP_FAILED); memcpy(memory, out, 24); assert(!mprotect(memory, page, PROT_READ));
    botw_uid uid = {0}; assert(botw_account_get_uid(&uid, memory) == RES_OK && !memcmp(&uid, &owned_uid, 16));
    botw_user_handle foreign = *out; foreign.token ^= 1; memset(&uid, 0xa5, sizeof uid);
    assert(botw_account_get_uid(&uid, &foreign) == RES_FAIL); untouched(&uid, sizeof uid);
    foreign = *out; foreign.uid.low ^= 1;
    assert(botw_account_get_uid(&uid, &foreign) == RES_FAIL); untouched(&uid, sizeof uid);
    assert(!munmap(memory, page)); free(allocation);
}
static void *concurrent_open(void *opaque) {
    unsigned *completed = opaque;
    for (unsigned i = 0; i < 2000; ++i) {
        guarded value; guarded_clear(&value); assert(open_user(&value.handle) == RES_OK);
        guarded_ok(&value); handle_ok(&value.handle);
        botw_uid uid = {0}; assert(botw_account_get_uid(&uid, &value.handle) == RES_OK && !memcmp(&uid, &owned_uid, 16));
        ++*completed;
    }
    return NULL;
}
static void concurrent(void) {
    setup(1); pthread_t threads[4]; unsigned completed[4] = {0};
    for (unsigned i = 0; i < 4; ++i) assert(!pthread_create(threads + i, NULL, concurrent_open, completed + i));
    for (unsigned i = 0; i < 4; ++i) { assert(!pthread_join(threads[i], NULL)); assert(completed[i] == 2000); }
    assert(atomic_load(&g_account_user_open) && atomic_load(&handle_prepares) == 8000);
    guarded value; guarded_clear(&value); int count = -1;
    botw_account_pop_users(&count, &value.handle, 1); assert(count == 0); untouched(&value, sizeof value);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "registry")) registry();
    else if (!strcmp(argv[1], "roundtrip")) roundtrip();
    else if (!strcmp(argv[1], "pop-then-open")) pop_then_open();
    else if (!strcmp(argv[1], "uninitialized") || !strcmp(argv[1], "null-out")) ordinary_failure(argv[1]);
    else if (!strcmp(argv[1], "prepare-failed")) prepare_failed();
    else if (!strcmp(argv[1], "alignment-readonly")) alignment_and_readonly();
    else if (!strcmp(argv[1], "concurrent")) concurrent();
    else assert(!"unknown owned mode");
    return 0;
}
