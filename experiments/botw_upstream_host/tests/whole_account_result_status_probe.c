/* Reuse the complete account module, immutable owned UID seam and real
 * checked registry from the behavioral fixture. No logger is called here. */
#define main original_account_open_cases_main
#include "whole_account_open_probe.c"
#undef main
typedef struct { uint64_t before[2]; botw_uid users[2]; uint64_t after[2]; } list_guard;
static int original_post_errno(void *pointer, size_t bytes) {
    assert(pointer && bytes); errno = ERANGE; return 1;
}
static void status_setup(int initialized) {
    setup(initialized); assert(dm_guest_write_prep_register_checked(original_post_errno));
}
static void list_guard_ok(const list_guard *value) {
    for (unsigned i = 0; i < 2; ++i)
        assert(value->before[i] == UINT64_C(0xa5a5a5a5a5a5a5a5) && value->after[i] == UINT64_C(0xa5a5a5a5a5a5a5a5));
}
static void success_round(void) {
    guarded value; guarded_clear(&value);
    errno = EDOM; assert(open_user(&value.handle) == RES_OK && errno == ERANGE);
    handle_ok(&value.handle); guarded_ok(&value); assert(atomic_load(&g_account_user_open) == 1);
    botw_uid uid = {0}; errno = EDOM;
    assert(botw_account_get_uid(&uid, &value.handle) == RES_OK && errno == ERANGE);
    assert(!memcmp(&uid, &owned_uid, 16));
    list_guard users; memset(&users, 0xa5, sizeof users); int count = -1; errno = EDOM;
    assert(botw_account_list_users(&count, users.users, 2) == RES_OK && errno == ERANGE && count == 1);
    assert(!memcmp(users.users, &owned_uid, 16) && !users.users[1].low && !users.users[1].high); list_guard_ok(&users);
}
static void failure_round(int initialized) {
    guarded value; guarded_clear(&value); int was_open = atomic_load(&g_account_user_open);
    errno = EDOM; assert(open_user(initialized ? NULL : &value.handle) == RES_FAIL && errno == EDOM);
    untouched(&value, sizeof value); assert(atomic_load(&g_account_user_open) == was_open);
    botw_uid uid; memset(&uid, 0xa5, sizeof uid); errno = EDOM;
    assert(botw_account_get_uid(&uid, &value.handle) == RES_FAIL && errno == EDOM); untouched(&uid, sizeof uid);
    list_guard users; memset(&users, 0xa5, sizeof users); int count = -1; errno = EDOM;
    assert(botw_account_list_users(&count, users.users, initialized ? -1 : 2) == RES_FAIL && errno == EDOM);
    assert(count == -1); untouched(&users, sizeof users); assert(RES_FAIL == UINT32_C(0x2a2));
}
static void success(void) {
    status_setup(1); for (unsigned i = 0; i < 4; ++i) success_round();
}
static void outcomes(void) {
    status_setup(0); for (unsigned i = 0; i < 3; ++i) failure_round(0);
    botw_account_initialize();
    for (unsigned i = 0; i < 3; ++i) { success_round(); failure_round(1); }
}
static void uninitialized(void) {
    status_setup(0); for (unsigned i = 0; i < 3; ++i) failure_round(0);
    assert(!atomic_load(&prepares) && !atomic_load(&identity_reads));
}
static void prewrite(const char *mode) {
    status_setup(1); guarded handle; guarded_clear(&handle);
    botw_uid uid; memset(&uid, 0xa5, sizeof uid);
    list_guard users; memset(&users, 0xa5, sizeof users); int count = -1;
    if (!strcmp(mode, "prepare-get")) assert(open_user(&handle.handle) == RES_OK);
    int was_open = atomic_load(&g_account_user_open);
    rejected_destination = !strcmp(mode, "prepare-open") ? (void *)&handle.handle :
                           !strcmp(mode, "prepare-get") ? (void *)&uid : (void *)users.users;
    atomic_store(&armed, 1);
    if (!sigsetjmp(failure, 1)) {
        if (!strcmp(mode, "prepare-open")) (void)open_user(&handle.handle);
        else if (!strcmp(mode, "prepare-get")) (void)botw_account_get_uid(&uid, &handle.handle);
        else (void)botw_account_list_users(&count, users.users, 2);
        assert(!"a rejected pre-write produced a normal Result");
    }
    atomic_store(&armed, 0); assert(atomic_load(&failed) == 1);
    assert(atomic_load(&g_account_user_open) == was_open);
    if (!strcmp(mode, "prepare-open")) untouched(&handle, sizeof handle);
    else if (!strcmp(mode, "prepare-get")) { untouched(&uid, sizeof uid); handle_ok(&handle.handle); guarded_ok(&handle); }
    else { untouched(&users, sizeof users); assert(count == -1); }
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "success")) success();
    else if (!strcmp(argv[1], "outcomes")) outcomes();
    else if (!strcmp(argv[1], "uninitialized")) uninitialized();
    else if (!strcmp(argv[1], "prepare-open") || !strcmp(argv[1], "prepare-get") || !strcmp(argv[1], "prepare-list")) prewrite(argv[1]);
    else assert(!"unknown owned status mode");
    return 0;
}
