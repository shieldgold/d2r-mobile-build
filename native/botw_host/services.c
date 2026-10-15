/* Horizon HIPC/CMIF host services. No Nintendo code or data is included.
 * Host architecture derived from jhaizhou-ops/d2r-mobile (MIT), commit
 * e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8. See THIRD_PARTY_LICENSE.txt.
 * Protocol references (layouts, independently implemented with bounds checks):
 * https://github.com/switchbrew/libnx/blob/master/nx/include/switch/sf/hipc.h
 * https://github.com/switchbrew/libnx/blob/master/nx/include/switch/sf/cmif.h
 * https://github.com/switchbrew/libnx/blob/master/nx/source/services/sm.c
 * https://github.com/switchbrew/libnx/blob/master/nx/source/services/apm.c
 * https://switchbrew.org/wiki/Log_services
 */
#include "services.h"
#include "../botw_resources/romfs.h"
#include "../botw_resources/savefs.h"
#include "../botw_input/input.h"
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define HANDLE_LIMIT 256u
#define DOMAIN_LIMIT 64u
#define IPC_LIMIT 0x100u
#define POINTER_CAPACITY 0x500u
#define LOG_LIMIT (1024u * 1024u)
#define BUFFER_LIMIT (16u * 1024u * 1024u)
#define STORAGE_TOTAL_LIMIT (64u * 1024u * 1024u)
#define CAPTURE_SIZE (1280u * 720u * 4u)
#define TRACE_LIMIT 256u
#define FS_ACCESS_LOG_NONE 0u
#define SFCI 0x49434653u
#define SFCO 0x4f434653u
#define LOCAL_USER_TITLE "01007ef00011e000"
#define PRESELECTED_STORAGE_SIZE 0x88u
#define AM_NOT_AVAILABLE (128u | (2u << 9))

enum ObjectKind { OBJECT_SM, OBJECT_LM, OBJECT_LOGGER, OBJECT_APM, OBJECT_APM_SESSION,
    OBJECT_APPLET_OE, OBJECT_APPLET_PROXY, OBJECT_COMMON_STATE,
    OBJECT_SELF_CONTROLLER, OBJECT_WINDOW_CONTROLLER, OBJECT_APPLICATION_FUNCTIONS,
    OBJECT_LIBRARY_APPLET_CREATOR, OBJECT_APPLET_STORAGE, OBJECT_STORAGE_ACCESSOR, OBJECT_AUDIO_CONTROLLER, OBJECT_DISPLAY_CONTROLLER, OBJECT_DEBUG_FUNCTIONS,
    OBJECT_FSP, OBJECT_ROMFS_STORAGE, OBJECT_ROMFS_FILESYSTEM, OBJECT_ROMFS_FILE, OBJECT_HID, OBJECT_HID_APPLET_RESOURCE, OBJECT_AOC, OBJECT_PCTL_FACTORY, OBJECT_PCTL_SERVICE,
    OBJECT_ACCOUNT, OBJECT_ACCOUNT_CONTEXT, OBJECT_SAVE_FILESYSTEM, OBJECT_SAVE_FILE };
typedef struct StorageBacking {
    botw_services *owner;
    unsigned refs;
    size_t size;
    uint8_t *bytes;
} StorageBacking;
typedef struct Object {
    enum ObjectKind kind;
    unsigned refs, initialized;
    uint64_t pid;
    StorageBacking *storage;
    botw_romfs *romfs;
    botw_savefs *savefs;
    uint32_t file_mode;
    char *file_path;
    uint8_t account_uid[16];
} Object;
typedef struct Session {
    unsigned refs, domain;
    Object *root;
    Object *objects[DOMAIN_LIMIT];
} Session;
/* A nonzero id owns a client kernel handle. session == NULL means its server
 * endpoint has closed: SendSyncRequest fails, but CloseHandle still releases
 * that client handle once. This matches the SDK's CMIF-Close/SVC-Close sequence. */
typedef struct { uint32_t id; Session *session; uint8_t *pointer_memory; } Handle;
typedef struct {
    uint64_t ipc_address, output_address;
    uint8_t *arena;
    size_t output_size, output_written;
} PointerTransfer;
struct botw_services {
    pthread_mutex_t lock;
    Handle handles[HANDLE_LIMIT];
    uint32_t next_handle, log_destination, fs_access_log_mode;
    uint32_t performance_mode, performance_configuration[2];
    unsigned performance_configured[2];
    botw_services_performance_fn performance_handler;
    void *performance_opaque;
    botw_services_focus_fn focus_handler;
    void *focus_opaque;
    botw_services_write_prepare_fn write_prepare;
    void *write_prepare_opaque;
    botw_services_read_guest_fn read_guest;
    botw_services_write_guest_fn write_guest;
    void *copy_opaque;
    uint32_t focus_mode;
    unsigned focus_suspend_out, focus_policy_configured;
    botw_services_kernel_api kernel;
    botw_input *input;
    botw_romfs *romfs;
    botw_savefs *savefs;
    uint64_t host_process_id;
    uint8_t local_uid[16];
    unsigned local_user_ready, preselected_configured, preselected_pending;
    char local_user_source[128];
    unsigned patch_known_absent;
    uint32_t input_shared_memory_handle;
    uint32_t hid_supported_styles, hid_npad_ids[10], hid_last_style[10];
    uint32_t hid_style_event_write[10], hid_style_event_read[10];
    size_t hid_npad_count;
    unsigned hid_npad_active;
    uint64_t hid_joy_hold;
    unsigned aoc_ready;
    uint64_t aoc_application_id, aoc_base_id;
    uint32_t aoc_installed_ids[128], aoc_event_write, aoc_event_read;
    size_t aoc_installed_count;
    unsigned parental_ready, parental_communication, parental_sns, parental_stereo;
    char parental_source[128];
    unsigned application_language_ready;
    char application_language[8], application_language_source[128];
    uint32_t parental_event_write, parental_event_read;
    size_t storage_bytes;
    float audio_main_gain, audio_library_gain, audio_transparent_rate;
    uint8_t *capture_images[3], *transition_image;
    uint64_t transition_generation;
    unsigned applet_ready, applet_foreground, applet_focus, applet_operation;
    unsigned applet_foreground_rights, applet_running, applet_notify_operation, applet_notify_performance, applet_notify_resume;
    uint64_t applet_resource_user_id;
    uint32_t applet_event_write, applet_event_read;
    uint32_t applet_messages[32];
    unsigned applet_message_head, applet_message_count;
    botw_services_log_fn log;
    botw_services_memory_fn memory;
    void *opaque;
    struct { enum ObjectKind kind; uint32_t command; } trace_seen[TRACE_LIMIT];
    unsigned trace_seen_count;
    uint64_t trace_suppressed;
};
typedef struct {
    uint32_t type, command, token;
    unsigned pid, domain, domain_close, copy_count;
    uint32_t copy_handle;
    uint32_t object_id;
    const uint8_t *payload;
    size_t payload_size;
    uint64_t input_address;
    size_t input_size;
    unsigned input_count, output_count, input_static_count, output_static_count;
    unsigned buffer_descriptor_count;
    uint64_t output_address;
    size_t output_size;
    size_t raw_start, raw_end, cmif_offset, pointer_input_bytes, pointer_output_bytes;
    unsigned input_is_static, output_is_static;
    PointerTransfer *transfer;
} Request;

static uint32_t load32(const void *p) { uint32_t n; memcpy(&n, p, 4); return n; }
static uint64_t load64(const void *p) { uint64_t n; memcpy(&n, p, 8); return n; }
static void store32(void *p, uint32_t n) { memcpy(p, &n, 4); }
static int problem(char *error, size_t cap, const char *format, ...) {
    if (error && cap) { va_list ap; va_start(ap, format); vsnprintf(error, cap, format, ap); va_end(ap); }
    return -1;
}
static void emit(botw_services *s, const char *format, ...) {
    if (!s->log) return;
    char text[2048]; va_list ap; va_start(ap, format); vsnprintf(text, sizeof text, format, ap); va_end(ap);
    s->log(s->opaque, text);
}
static const char *kind_name(enum ObjectKind kind) {
    switch (kind) {
        case OBJECT_SM: return "sm:";
        case OBJECT_LM: return "lm";
        case OBJECT_LOGGER: return "lm.ILogger";
        case OBJECT_APM: return "apm.IPerformanceManager";
        case OBJECT_APM_SESSION: return "apm.ISession";
        case OBJECT_APPLET_OE: return "appletOE";
        case OBJECT_APPLET_PROXY: return "am.IApplicationProxy";
        case OBJECT_COMMON_STATE: return "am.ICommonStateGetter";
        case OBJECT_SELF_CONTROLLER: return "am.ISelfController";
        case OBJECT_WINDOW_CONTROLLER: return "am.IWindowController";
        case OBJECT_APPLICATION_FUNCTIONS: return "am.IApplicationFunctions";
        case OBJECT_LIBRARY_APPLET_CREATOR: return "am.ILibraryAppletCreator";
        case OBJECT_APPLET_STORAGE: return "am.IStorage";
        case OBJECT_STORAGE_ACCESSOR: return "am.IStorageAccessor";
        case OBJECT_AUDIO_CONTROLLER: return "am.IAudioController";
        case OBJECT_DISPLAY_CONTROLLER: return "am.IDisplayController";
        case OBJECT_DEBUG_FUNCTIONS: return "am.IDebugFunctions";
        case OBJECT_FSP: return "fsp-srv";
        case OBJECT_ROMFS_STORAGE: return "fs.IStorage(RomFS)";
        case OBJECT_ROMFS_FILESYSTEM: return "fs.IFileSystem(RomFS)";
        case OBJECT_ROMFS_FILE: return "fs.IFile(RomFS)";
        case OBJECT_HID: return "hid";
        case OBJECT_HID_APPLET_RESOURCE: return "hid.IAppletResource";
        case OBJECT_AOC: return "aoc:u";
        case OBJECT_PCTL_FACTORY: return "pctl:a.IParentalControlServiceFactory";
        case OBJECT_PCTL_SERVICE: return "pctl.IParentalControlService";
        case OBJECT_ACCOUNT: return "acc:u0";
        case OBJECT_ACCOUNT_CONTEXT: return "acc.IBaasAccountManagerForApplication(local-context)";
        case OBJECT_SAVE_FILESYSTEM: return "fs.IFileSystem(SaveFS)";
        case OBJECT_SAVE_FILE: return "fs.IFile(SaveFS)";
    }
    return "<invalid>";
}
static void trace_request(botw_services *s, enum ObjectKind kind, const Request *r, uint32_t handle_id) {
    for (unsigned i = 0; i < s->trace_seen_count; i++) {
        if (s->trace_seen[i].kind == kind && s->trace_seen[i].command == r->command) {
            s->trace_suppressed++; return;
        }
    }
    /* Exhausting the fixed cache keeps tracing new commands; arbitrary guest
     * command IDs cannot grow host memory or hide the first diagnostic. */
    if (s->trace_seen_count < TRACE_LIMIT) {
        s->trace_seen[s->trace_seen_count].kind = kind;
        s->trace_seen[s->trace_seen_count++].command = r->command;
    }
    emit(s, "ipc_request service=%s command=%u handle=0x%x domain=%u object=%u bytes=%zu", kind_name(kind), r->command, handle_id, r->domain, r->object_id, r->payload_size);
}
static Object *new_object(enum ObjectKind kind) {
    Object *o = calloc(1, sizeof *o);
    if (o) { o->kind = kind; o->refs = 1; }
    return o;
}
static void release_object(Object *o) {
    if (!o || --o->refs) return;
    if (o->storage && !--o->storage->refs) {
        o->storage->owner->storage_bytes -= o->storage->size;
        free(o->storage->bytes); free(o->storage);
    }
    free(o->file_path); free(o);
}
static Session *new_session(Object *o) {
    Session *session = calloc(1, sizeof *session);
    if (session) { session->refs = 1; session->root = o; }
    return session;
}
static void release_session(Session *session) {
    if (!session || --session->refs) return;
    if (session->domain) {
        for (unsigned i = 0; i < DOMAIN_LIMIT; i++) release_object(session->objects[i]);
    } else release_object(session->root);
    free(session);
}
static Handle *find_handle(botw_services *s, uint32_t handle) {
    for (unsigned i = 0; i < HANDLE_LIMIT; i++) if (s->handles[i].id && s->handles[i].id == handle) return &s->handles[i];
    return NULL;
}
static int assign_handle(botw_services *s, Session *session, uint32_t *out, char *error, size_t cap) {
    if (s->next_handle < 0x10000 || s->next_handle >= 0x20000) return problem(error, cap, "IPC handle identity space exhausted before kernel namespace");
    for (unsigned i = 0; i < HANDLE_LIMIT; i++) if (!s->handles[i].id) {
        uint8_t *arena = aligned_alloc(16, POINTER_CAPACITY);
        if (!arena) return problem(error, cap, "allocate bounded IPC pointer arena failed");
        memset(arena, 0, POINTER_CAPACITY);
        s->handles[i].id = s->next_handle++; s->handles[i].session = session; *out = s->handles[i].id;
        s->handles[i].pointer_memory = arena;
        return 0;
    }
    return problem(error, cap, "IPC handle table exhausted (%u live sessions)", HANDLE_LIMIT);
}
static int object_handle(botw_services *s, Object *o, uint32_t *handle, char *error, size_t cap) {
    Session *session = new_session(o);
    if (!session) { release_object(o); return problem(error, cap, "allocate IPC session failed"); }
    if (assign_handle(s, session, handle, error, cap)) { release_session(session); return -1; }
    return 0;
}

botw_services *botw_services_create(botw_services_log_fn log, botw_services_memory_fn memory, void *opaque) {
    if (!memory) return NULL;
    botw_services *s = calloc(1, sizeof *s);
    if (s && pthread_mutex_init(&s->lock, NULL)) { free(s); return NULL; }
    if (s) { s->next_handle = 0x10000; s->host_process_id = (uint64_t)getpid(); s->hid_supported_styles = 3; s->hid_npad_ids[0] = 0; s->hid_npad_ids[1] = 0x20; s->hid_npad_count = 2; s->log_destination = 0xffff; s->fs_access_log_mode = FS_ACCESS_LOG_NONE; s->audio_main_gain = 1.0f; s->audio_library_gain = 1.0f; s->audio_transparent_rate = 1.0f; s->log = log; s->memory = memory; s->opaque = opaque; }
    return s;
}
void botw_services_set_performance_handler(botw_services *s, botw_services_performance_fn handler, void *opaque) {
    if (s) { pthread_mutex_lock(&s->lock); s->performance_handler = handler; s->performance_opaque = opaque; pthread_mutex_unlock(&s->lock); }
}
void botw_services_set_memory_write_preparer(botw_services *s,
        botw_services_write_prepare_fn prepare, void *opaque) {
    if (!s) return;
    pthread_mutex_lock(&s->lock);
    s->write_prepare = prepare; s->write_prepare_opaque = opaque;
    pthread_mutex_unlock(&s->lock);
}
void botw_services_set_memory_copiers(botw_services *s,
        botw_services_read_guest_fn read_guest, botw_services_write_guest_fn write_guest,
        void *opaque) {
    if (!s) return;
    pthread_mutex_lock(&s->lock);
    s->read_guest = read_guest; s->write_guest = write_guest; s->copy_opaque = opaque;
    pthread_mutex_unlock(&s->lock);
}
/* Called only after parsing and whole-descriptor permission validation, and
 * before the first backend write. Preparing the entire intended span avoids
 * changing an initial chunk before a later preparation failure. */
static int prepare_host_output(botw_services *s, uint64_t address, size_t length,
        const char *operation, char *error, size_t capacity) {
    if (!length || !s->write_prepare) return 0;
    if (!address || length > UINT64_MAX - address)
        return problem(error, capacity, "%s invalid host output span", operation);
    if (error && capacity) error[0] = 0;
    if (s->write_prepare(s->write_prepare_opaque, address, length, error, capacity)) {
        if (error && capacity && error[0]) return -1;
        return problem(error, capacity, "%s host write preparation failed", operation);
    }
    return 0;
}
void botw_services_set_focus_handler(botw_services *s, botw_services_focus_fn handler, void *opaque) {
    if (!s) return;
    pthread_mutex_lock(&s->lock); s->focus_handler = handler; s->focus_opaque = opaque; pthread_mutex_unlock(&s->lock);
}
static int focus_suspended(uint32_t base_mode, unsigned suspend_out, unsigned foreground, unsigned focus) {
    uint32_t mode = suspend_out ? 3 : base_mode;
    return mode == 3 ? focus != 1 : mode == 1 ? 0 : !foreground;
}
static int apply_focus_policy(botw_services *s, uint32_t base_mode, unsigned suspend_out, unsigned foreground, unsigned focus, char *error, size_t cap) {
    if (!s->focus_handler) return problem(error, cap, "SelfController focus policy requires actual host focus handler");
    uint32_t mode = suspend_out ? 3 : base_mode;
    int suspended = focus_suspended(base_mode, suspend_out, foreground, focus);
    if (error && cap) error[0] = 0;
    if (s->focus_handler(s->focus_opaque, mode, (int)foreground, focus == 1, suspended, error, cap)) {
        if (error && cap && error[0]) return -1;
        return problem(error, cap, "host rejected applet focus mode=%u suspended=%d", mode, suspended);
    }
    emit(s, "applet_focus_policy mode=%u foreground=%u focus=%u suspended=%s host_policy_accepted=true", mode, foreground, focus, suspended ? "true" : "false");
    return 0;
}
void botw_services_set_kernel_api(botw_services *s, const botw_services_kernel_api *api) {
    if (!s) return;
    pthread_mutex_lock(&s->lock);
    if (!s->applet_event_write && !s->applet_event_read) s->kernel = api ? *api : (botw_services_kernel_api){0};
    pthread_mutex_unlock(&s->lock);
}
void botw_services_set_input(botw_services *s, botw_input *input, uint32_t shared_memory_handle) {
    if (!s) return;
    pthread_mutex_lock(&s->lock); s->input = input; s->input_shared_memory_handle = shared_memory_handle;
    if (input) (void)botw_input_configure(input, s->hid_npad_active ? s->hid_supported_styles : 0, s->hid_npad_ids, s->hid_npad_count);
    pthread_mutex_unlock(&s->lock);
}
static uint32_t npad_id(unsigned index) { return index < 8 ? index : index == 8 ? 0x20 : 0x10; }
static int npad_index(uint32_t id) { return id < 8 ? (int)id : id == 0x20 ? 8 : id == 0x10 ? 9 : -1; }
static int refresh_input(botw_services *s, char *error, size_t cap) {
    if (!s->input) return 0;
    for (unsigned index = 0; index < 10; index++) {
        uint32_t style = botw_input_style(s->input, npad_id(index));
        if (style != s->hid_last_style[index]) {
            if (s->hid_style_event_write[index] && s->kernel.signal_event(s->kernel.opaque, s->hid_style_event_write[index], error, cap)) return -1;
            s->hid_last_style[index] = style;
        }
    }
    return 0;
}
int botw_services_refresh_input(botw_services *s, char *error, size_t cap) {
    if (!s) return problem(error, cap, "refresh input requires service context");
    pthread_mutex_lock(&s->lock); int result = refresh_input(s, error, cap); pthread_mutex_unlock(&s->lock); return result;
}
void botw_services_set_romfs(botw_services *s, botw_romfs *romfs) {
    if (!s) return;
    pthread_mutex_lock(&s->lock); s->romfs = romfs; pthread_mutex_unlock(&s->lock);
}
int botw_services_set_patch_absent(botw_services *s, int known_absent, char *error, size_t cap) {
    if (!s || (known_absent != 0 && known_absent != 1))
        return problem(error, cap, "patch installation state requires service context and canonical absence bool");
    pthread_mutex_lock(&s->lock); s->patch_known_absent = (unsigned)known_absent; pthread_mutex_unlock(&s->lock); return 0;
}
void botw_services_set_savefs(botw_services *s, botw_savefs *savefs) {
    if (!s) return;
    pthread_mutex_lock(&s->lock); s->savefs = savefs; pthread_mutex_unlock(&s->lock);
}
static int local_user_savefs(botw_savefs *savefs, const uint8_t uid[16], char *error, size_t cap) {
    static const char hex[] = "0123456789abcdef";
    char user_hex[33]; unsigned nonzero = 0;
    for (unsigned i = 0; i < 16; i++) {
        nonzero |= uid[i]; user_hex[i * 2] = hex[uid[i] >> 4]; user_hex[i * 2 + 1] = hex[uid[i] & 15];
    }
    user_hex[32] = 0;
    if (!nonzero) return problem(error, cap, "local user UID must be nonzero");
    if (!savefs) return problem(error, cap, "local user requires borrowed SaveFS mount");
    if (strcmp(botw_savefs_title_id(savefs), LOCAL_USER_TITLE) || strcmp(botw_savefs_user_id(savefs), user_hex))
        return problem(error, cap, "local user does not match SaveFS immutable BOTW title/user identity");
    return 0;
}
static int local_user_mount(botw_services *s, const uint8_t uid[16], char *error, size_t cap) {
    return local_user_savefs(s->savefs, uid, error, cap);
}
int botw_services_set_local_user(botw_services *s, const uint8_t uid[16], const char *source, char *error, size_t cap) {
    if (!s || !uid || !source || !*source) return problem(error, cap, "local user requires context, nonzero UID and explicit local identity source");
    size_t length = 0;
    while (length < sizeof s->local_user_source && source[length]) {
        if ((unsigned char)source[length] < 32 || source[length] == 127) return problem(error, cap, "local user source contains control characters");
        length++;
    }
    if (length == sizeof s->local_user_source) return problem(error, cap, "local user source exceeds limit");
    pthread_mutex_lock(&s->lock);
    int result;
    if (s->local_user_ready) result = problem(error, cap, "local user can only be published once per process launch");
    else if ((result = local_user_mount(s, uid, error, cap)) == 0) {
        memcpy(s->local_uid, uid, 16); memcpy(s->local_user_source, source, length + 1); s->local_user_ready = 1;
        emit(s, "local_user configured=true source=%s scope=host_local_offline nintendo_online_backend=false", source);
    }
    pthread_mutex_unlock(&s->lock); return result;
}
int botw_services_set_preselected_local_user(botw_services *s, const uint8_t uid[16], char *error, size_t cap) {
    if (!s || !uid) return problem(error, cap, "local user preselection requires context and matching UID");
    pthread_mutex_lock(&s->lock);
    int result;
    if (!s->local_user_ready || memcmp(s->local_uid, uid, 16)) result = problem(error, cap, "preselection requires the published local user UID");
    else if (s->preselected_configured) result = problem(error, cap, "local user preselection can only be configured once per process launch");
    else if ((result = local_user_mount(s, uid, error, cap)) == 0) {
        s->preselected_configured = 1; s->preselected_pending = 1;
        emit(s, "local_user preselected=true launch_parameter_kind=2 scope=host_local_offline");
    }
    pthread_mutex_unlock(&s->lock); return result;
}
static int compare_u32(const void *a, const void *b) {
    uint32_t first = *(const uint32_t *)a, second = *(const uint32_t *)b;
    return (first > second) - (first < second);
}
int botw_services_set_addon_content(botw_services *s, uint64_t application_id, uint64_t base_id, const uint32_t *ids, size_t count, char *error, size_t cap) {
    if (!s || !application_id || count > 128 || (count && !ids)) return problem(error, cap, "AOC requires application identity and at most 128 actual installed IDs");
    uint32_t installed[128] = {0}; if (count) memcpy(installed, ids, count * 4);
    qsort(installed, count, 4, compare_u32);
    for (size_t i = 1; i < count; i++) if (installed[i] == installed[i - 1]) return problem(error, cap, "AOC installed content set contains duplicate ID=%u", installed[i]);
    pthread_mutex_lock(&s->lock);
    if (s->aoc_ready && s->aoc_application_id != application_id) {
        pthread_mutex_unlock(&s->lock); return problem(error, cap, "AOC application identity cannot change during a run");
    }
    int changed = s->aoc_ready && (count != s->aoc_installed_count || memcmp(installed, s->aoc_installed_ids, count * 4));
    if (changed && s->aoc_event_write && s->kernel.signal_event(s->kernel.opaque, s->aoc_event_write, error, cap)) { pthread_mutex_unlock(&s->lock); return -1; }
    s->aoc_ready = 1; s->aoc_application_id = application_id; s->aoc_base_id = base_id;
    s->aoc_installed_count = count; memcpy(s->aoc_installed_ids, installed, sizeof installed);
    pthread_mutex_unlock(&s->lock); return 0;
}
int botw_services_set_parental_policy(botw_services *s, const char *source, int communication, int sns, int stereo, char *error, size_t cap) {
    if (!s || !source || !*source || (communication != 0 && communication != 1) || (sns != 0 && sns != 1) || (stereo != 0 && stereo != 1))
        return problem(error, cap, "parental policy requires explicit source and boolean local restrictions");
    size_t length = 0; while (length < 128 && source[length]) length++;
    if (length >= 128) return problem(error, cap, "parental policy source exceeds limit");
    pthread_mutex_lock(&s->lock);
    int changed = s->parental_ready && (s->parental_communication != (unsigned)communication || s->parental_sns != (unsigned)sns || s->parental_stereo != (unsigned)stereo);
    if (changed && s->parental_event_write && s->kernel.signal_event(s->kernel.opaque, s->parental_event_write, error, cap)) { pthread_mutex_unlock(&s->lock); return -1; }
    s->parental_ready = 1; s->parental_communication = (unsigned)communication; s->parental_sns = (unsigned)sns; s->parental_stereo = (unsigned)stereo;
    memcpy(s->parental_source, source, length + 1);
    emit(s, "parental_policy source=%s communication_restricted=%d sns_restricted=%d stereo_restricted=%d nintendo_account_linked=false", source, communication, sns, stereo);
    pthread_mutex_unlock(&s->lock); return 0;
}
int botw_services_set_application_language(botw_services *s, const char *code,
        const char *source, char *error, size_t cap) {
    /* Generic registry: libnx SetLanguage (20cbcfd90567f7bf84df7725f27e345e2164fa6b)
     * and Switchbrew Settings_services LanguageCode. Asset selection belongs
     * to the caller; notably zh-Hans being canonical does not install assets. */
    static const char *const canonical[] = {
        "ja", "en-US", "fr", "de", "it", "es", "zh-CN", "ko", "nl", "pt", "ru",
        "zh-TW", "en-GB", "fr-CA", "es-419", "zh-Hans", "zh-Hant", "pt-BR"
    };
    if (!s || !code || !source || !*source)
        return problem(error, cap, "application language requires explicit code and source");
    size_t length = 0; while (length < 8 && code[length]) length++;
    if (!length || length >= 8)
        return problem(error, cap, "application language code must fit eight NUL-terminated bytes");
    unsigned known = 0;
    for (size_t i = 0; i < sizeof canonical / sizeof canonical[0]; i++)
        if (strlen(canonical[i]) == length && !memcmp(code, canonical[i], length)) known = 1;
    if (!known) return problem(error, cap, "application language code is not canonical Nintendo LanguageCode");
    size_t source_length = 0;
    while (source_length < 128 && source[source_length]) {
        unsigned char c = (unsigned char)source[source_length++];
        if (c < 32 || c > 126) return problem(error, cap, "application language source must be printable ASCII");
    }
    if (source_length >= 128) return problem(error, cap, "application language source exceeds limit");
    char encoded[8] = {0}; memcpy(encoded, code, length);
    pthread_mutex_lock(&s->lock);
    if (s->application_language_ready) {
        int same = !memcmp(s->application_language, encoded, sizeof encoded) &&
            !strcmp(s->application_language_source, source);
        pthread_mutex_unlock(&s->lock);
        return same ? 0 : problem(error, cap, "application language is already configured for this context");
    }
    memcpy(s->application_language, encoded, sizeof encoded);
    memcpy(s->application_language_source, source, source_length + 1);
    s->application_language_ready = 1;
    emit(s, "application_language language=%s source=%s device_locale_backend=false",
        s->application_language, s->application_language_source);
    pthread_mutex_unlock(&s->lock); return 0;
}
void botw_services_get_audio_gain(botw_services *s, float *main_applet, float *library_applet, float *transparent_rate) {
    if (!s) return;
    pthread_mutex_lock(&s->lock);
    if (main_applet) *main_applet = s->audio_main_gain;
    if (library_applet) *library_applet = s->audio_library_gain;
    if (transparent_rate) *transparent_rate = s->audio_transparent_rate;
    pthread_mutex_unlock(&s->lock);
}
int botw_services_set_capture_image(botw_services *s, unsigned kind, const void *rgba, size_t size, char *error, size_t cap) {
    if (!s || kind > 2 || (rgba ? size != CAPTURE_SIZE : size != 0)) return problem(error, cap, "capture requires kind=0..2 and verified 1280x720 RGBA8 bytes, or NULL/0");
    uint8_t *copy = NULL;
    if (rgba) { copy = malloc(CAPTURE_SIZE); if (!copy) return problem(error, cap, "allocate host capture snapshot failed"); memcpy(copy, rgba, CAPTURE_SIZE); }
    pthread_mutex_lock(&s->lock); free(s->capture_images[kind]); s->capture_images[kind] = copy; pthread_mutex_unlock(&s->lock); return 0;
}
int botw_services_get_transition_image(botw_services *s, void *rgba, size_t capacity, uint64_t *generation) {
    if (!s) return -1;
    pthread_mutex_lock(&s->lock);
    if (generation) *generation = s->transition_generation;
    int result = s->transition_image ? (rgba && capacity < CAPTURE_SIZE ? -1 : 1) : 0;
    if (result == 1 && rgba) memcpy(rgba, s->transition_image, CAPTURE_SIZE);
    pthread_mutex_unlock(&s->lock); return result;
}
static int set_applet_state(botw_services *s, uint64_t user_id, int foreground, int focused, uint32_t operation, char *error, size_t cap) {
    if (!user_id || (foreground != 0 && foreground != 1) || (focused != 0 && focused != 1) || (!foreground && focused) || operation > 1)
        return problem(error, cap, "invalid host applet state user=0x%llx foreground=%d focused=%d operation=%u", (unsigned long long)user_id, foreground, focused, operation);
    if (s->applet_ready && s->applet_resource_user_id != user_id)
        return problem(error, cap, "host applet resource user ID cannot change during a run");
    unsigned focus = !foreground ? 3 : focused ? 1 : 2;
    uint32_t messages[3]; unsigned count = 0;
    /* Opening the virtual applet transitions Unknown -> measured host focus.
     * The 1.x SDK waits for this initial announcement before querying focus. */
    if (!s->applet_ready || s->applet_focus != focus) messages[count++] = 15;
    if (s->applet_ready && s->applet_operation != operation && s->applet_notify_operation) messages[count++] = 30;
    /* ISelfController cmd14 controls Resume(16), not process relaunch. Publish
     * only a real execution-policy suspension -> running lifecycle edge. */
    if (s->applet_ready && s->focus_policy_configured && s->applet_notify_resume &&
        focus_suspended(s->focus_mode, s->focus_suspend_out, s->applet_foreground, s->applet_focus) &&
        !focus_suspended(s->focus_mode, s->focus_suspend_out, (unsigned)foreground, focus)) messages[count++] = 16;
    if (count > 32 - s->applet_message_count) return problem(error, cap, "applet notification queue full");
    if (s->focus_policy_configured && (s->applet_foreground != (unsigned)foreground || s->applet_focus != focus) && apply_focus_policy(s, s->focus_mode, s->focus_suspend_out, (unsigned)foreground, focus, error, cap)) return -1;
    if (count && s->applet_event_write && s->kernel.signal_event(s->kernel.opaque, s->applet_event_write, error, cap)) return -1;
    for (unsigned i = 0; i < count; i++) s->applet_messages[(s->applet_message_head + s->applet_message_count++) % 32] = messages[i];
    s->applet_ready = 1; s->applet_resource_user_id = user_id;
    s->applet_foreground = (unsigned)foreground; s->applet_focus = focus; s->applet_operation = operation;
    if (!foreground) s->applet_foreground_rights = 0;
    if (s->input) {
        botw_input_set_active(s->input, foreground && focused);
        botw_input_sample(s->input, botw_input_monotonic_ticks());
        if (refresh_input(s, error, cap)) return -1;
    }
    return 0;
}
int botw_services_set_applet_state(botw_services *s, uint64_t user_id, int foreground, int focused, uint32_t operation, char *error, size_t cap) {
    if (!s) return problem(error, cap, "host applet state requires service context");
    pthread_mutex_lock(&s->lock);
    int result = set_applet_state(s, user_id, foreground, focused, operation, error, cap);
    pthread_mutex_unlock(&s->lock); return result;
}
void botw_services_destroy(botw_services *s) {
    if (!s) return;
    pthread_mutex_lock(&s->lock);
    if (s->trace_suppressed) emit(s, "ipc_trace_summary unique_commands=%u repeated_requests=%llu", s->trace_seen_count, (unsigned long long)s->trace_suppressed);
    for (unsigned i = 0; i < HANDLE_LIMIT; i++) { release_session(s->handles[i].session); free(s->handles[i].pointer_memory); }
    for (unsigned i = 0; i < 3; i++) free(s->capture_images[i]);
    free(s->transition_image);
    char error[128];
    if (s->applet_event_read) (void)s->kernel.close_handle(s->kernel.opaque, s->applet_event_read, error, sizeof error);
    if (s->applet_event_write) (void)s->kernel.close_handle(s->kernel.opaque, s->applet_event_write, error, sizeof error);
    if (s->aoc_event_read) (void)s->kernel.close_handle(s->kernel.opaque, s->aoc_event_read, error, sizeof error);
    if (s->aoc_event_write) (void)s->kernel.close_handle(s->kernel.opaque, s->aoc_event_write, error, sizeof error);
    if (s->parental_event_read) (void)s->kernel.close_handle(s->kernel.opaque, s->parental_event_read, error, sizeof error);
    if (s->parental_event_write) (void)s->kernel.close_handle(s->kernel.opaque, s->parental_event_write, error, sizeof error);
    for (unsigned index = 0; index < 10; index++) {
        if (s->hid_style_event_read[index]) (void)s->kernel.close_handle(s->kernel.opaque, s->hid_style_event_read[index], error, sizeof error);
        if (s->hid_style_event_write[index]) (void)s->kernel.close_handle(s->kernel.opaque, s->hid_style_event_write[index], error, sizeof error);
    }
    pthread_mutex_unlock(&s->lock); pthread_mutex_destroy(&s->lock); free(s);
}
static int services_connect(botw_services *s, const char *name, size_t length, uint32_t *handle, char *error, size_t cap) {
    if (!s || !name || !handle || length != 3 || memcmp(name, "sm:", 3))
        return problem(error, cap, "unsupported named port %.*s (only sm: is registered)", (int)(length < 32 ? length : 32), name ? name : "");
    Object *o = new_object(OBJECT_SM);
    if (!o) return problem(error, cap, "allocate sm: object failed");
    if (object_handle(s, o, handle, error, cap)) return -1;
    emit(s, "ipc_connect port=sm: handle=0x%x", *handle); return 0;
}
static int services_close(botw_services *s, uint32_t id, char *error, size_t cap) {
    Handle *handle = s ? find_handle(s, id) : NULL;
    if (!handle) return problem(error, cap, "CloseHandle invalid or closed IPC handle=0x%x", id);
    Session *session = handle->session; handle->id = 0; handle->session = NULL; release_session(session);
    free(handle->pointer_memory); handle->pointer_memory = NULL;
    emit(s, "ipc_close handle=0x%x", id); return 0;
}
static int checked_span(size_t offset, size_t length, size_t end) { return offset <= end && length <= end - offset; }
static int ranges_overlap(uint64_t first, size_t first_size, uint64_t second, size_t second_size) {
    /* Compare distances, not address+size endpoints that could overflow. */
    return first_size && second_size && (first <= second ? second - first < first_size : first - second < second_size);
}
static int input_descriptor(botw_services *s, Request *r, uint64_t address, uint64_t length, char *error, size_t cap) {
    if (!length && !address) return 0; /* inactive half of an auto-select buffer */
    if (!length || length > SIZE_MAX || address > UINT64_MAX - length ||
        !s->memory(s->opaque, address, (size_t)length, 0))
        return problem(error, cap, "IPC input descriptor is not checked guest memory address=0x%llx bytes=%llu", (unsigned long long)address, (unsigned long long)length);
    if (++r->input_count > 1) return problem(error, cap, "IPC multiple nonempty input buffers are unsupported");
    r->input_address = address; r->input_size = (size_t)length; return 0;
}
static int output_descriptor(botw_services *s, Request *r, uint64_t address, uint64_t length, char *error, size_t cap) {
    if (!length && !address) return 0;
    if (!length || length > SIZE_MAX || address > UINT64_MAX - length || !s->memory(s->opaque, address, (size_t)length, 1))
        return problem(error, cap, "IPC output descriptor is not checked guest writable memory address=0x%llx bytes=%llu", (unsigned long long)address, (unsigned long long)length);
    if (++r->output_count > 1) return problem(error, cap, "IPC multiple nonempty output buffers are unsupported");
    r->output_address = address; r->output_size = (size_t)length; return 0;
}
static int copy_from_guest(botw_services *s, uint64_t source, void *destination,
        size_t size, char *error, size_t cap) {
    if (!size) return 0;
    if (!source || size > UINT64_MAX - source || !s->memory(s->opaque, source, size, 0))
        return problem(error, cap, "IPC pointer source is not completely readable guest memory");
    if (s->read_guest) {
        if (!s->read_guest(s->copy_opaque, source, destination, size, error, cap)) return 0;
        if (!error || !cap || !error[0]) problem(error, cap, "protected IPC guest read failed");
        return -1;
    }
    memcpy(destination, (const void *)(uintptr_t)source, size); return 0;
}
static int copy_to_guest(botw_services *s, uint64_t destination, const void *source,
        size_t size, char *error, size_t cap) {
    if (!size) return 0;
    if (!destination || size > UINT64_MAX - destination || !s->memory(s->opaque, destination, size, 1))
        return problem(error, cap, "IPC pointer destination is not completely writable guest memory");
    if (s->write_guest) {
        if (!s->write_guest(s->copy_opaque, destination, source, size, error, cap)) return 0;
        if (!error || !cap || !error[0]) problem(error, cap, "protected IPC guest write failed");
        return -1;
    }
    memcpy((void *)(uintptr_t)destination, source, size); return 0;
}
static int parse_request(botw_services *s, Session *session, const uint8_t *ipc, size_t capacity, Request *r, char *error, size_t cap) {
    uint32_t a = load32(ipc), b = load32(ipc + 4);
    r->type = a & 0xffff;
    if (r->type == 2) {
        if (a != 2 || b) return problem(error, cap, "HIPC Close must not contain descriptors or payload");
        return 0;
    }
    if (r->type != 4 && r->type != 5 && r->type != 6 && r->type != 7)
        return problem(error, cap, "unsupported HIPC type=%u", r->type);
    unsigned statics = (a >> 16) & 15, sends = (a >> 20) & 15;
    unsigned recvs = (a >> 24) & 15, exchanges = a >> 28, recv_mode = (b >> 10) & 15;
    if (exchanges || recv_mode == 1 || recv_mode == 2 || (b & 0x7fffc000u))
        return problem(error, cap, "unsupported HIPC receive/exchange descriptors or reserved header bits (word0=0x%x word1=0x%x)", a, b);
    size_t offset = 8;
    if (b >> 31) {
        if (!checked_span(offset, 4, capacity)) return problem(error, cap, "truncated HIPC special header");
        uint32_t special = load32(ipc + offset); offset += 4;
        if ((special & ~0x1fu) || ((special >> 1) & 15) > 1) return problem(error, cap, "IPC move/multiple input handles or reserved special header unsupported (0x%x)", special);
        r->copy_count = (special >> 1) & 15;
        r->pid = special & 1;
        if (r->pid) {
            if (!checked_span(offset, 8, capacity)) return problem(error, cap, "truncated HIPC PID");
            offset += 8; /* Kernel supplies host process identity, never trusts placeholder. */
        }
        if (r->copy_count) {
            if (!checked_span(offset, 4, capacity)) return problem(error, cap, "truncated HIPC copy handle");
            r->copy_handle = load32(ipc + offset); offset += 4;
        }
    }
    if (!checked_span(offset, (size_t)statics * 8 + (size_t)(sends + recvs) * 12, capacity))
        return problem(error, cap, "HIPC descriptor list exceeds IPC buffer");
    for (unsigned i = 0; i < statics; i++, offset += 8) {
        uint32_t x = load32(ipc + offset), low = load32(ipc + offset + 4);
        uint64_t address = low | (uint64_t)((x >> 12) & 15) << 32 | (uint64_t)((x >> 6) & 63) << 36;
        if (input_descriptor(s, r, address, x >> 16, error, cap)) return -1;
        if (x >> 16) r->input_is_static = 1;
        r->pointer_input_bytes += ((size_t)(x >> 16) + 15) & ~(size_t)15;
        r->input_static_count++;
    }
    for (unsigned i = 0; i < sends + recvs; i++, offset += 12) {
        uint32_t size = load32(ipc + offset), low = load32(ipc + offset + 4), high = load32(ipc + offset + 8);
        if ((high & 3) == 2) return problem(error, cap, "HIPC buffer has invalid memory mode=2");
        uint64_t address = low | (uint64_t)(high >> 28) << 32 | (uint64_t)((high >> 2) & 0x3fffff) << 36;
        uint64_t length = size | (uint64_t)((high >> 24) & 15) << 32;
        if (i < sends ? input_descriptor(s, r, address, length, error, cap) : output_descriptor(s, r, address, length, error, cap)) return -1;
    }
    size_t raw_end = offset + (size_t)(b & 0x3ff) * 4;
    r->raw_start = offset; r->raw_end = raw_end;
    if (raw_end > capacity) return problem(error, cap, "HIPC raw data exceeds IPC buffer (end=%zu capacity=%zu)", raw_end, capacity);
    unsigned recv_statics = recv_mode >= 3 ? recv_mode - 2 : 0;
    r->buffer_descriptor_count = statics + sends + recvs + recv_statics;
    if (!checked_span(raw_end, recv_statics * 8, capacity)) return problem(error, cap, "HIPC receive static list exceeds buffer");
    for (unsigned i = 0; i < recv_statics; i++) {
        const uint8_t *entry = ipc + raw_end + i * 8;
        uint32_t high = load32(entry + 4);
        uint64_t address = load32(entry) | (uint64_t)(high & 0xffff) << 32;
        if (output_descriptor(s, r, address, high >> 16, error, cap)) return -1;
        if (high >> 16) r->output_is_static = 1;
        r->pointer_output_bytes += ((size_t)(high >> 16) + 15) & ~(size_t)15;
        r->output_static_count++;
    }
    offset = (offset + 15) & ~(size_t)15;
    if (session->domain && (r->type == 4 || r->type == 6)) {
        if (!checked_span(offset, 16, raw_end)) return problem(error, cap, "truncated CMIF domain header");
        unsigned type = ipc[offset], objects = ipc[offset + 1];
        unsigned data_size = ipc[offset + 2] | (unsigned)ipc[offset + 3] << 8;
        r->object_id = load32(ipc + offset + 4); r->domain = 1;
        /* CmifDomainInHeader +8 is padding; SDKs may leave an earlier reply
         * there when reusing TLS. +12 is a domain token, not an object count.
         * Only the byte at +1 declares input objects (libnx sf/cmif.h). */
        if (objects)
            return problem(error, cap, "CMIF domain input objects unsupported: type=%u objects=%u data_size=%u object=%u padding=0x%x command=0x%x",
                type, objects, data_size, r->object_id, load32(ipc + offset + 8),
                checked_span(offset + 16, 16, raw_end) ? load32(ipc + offset + 24) : UINT32_MAX);
        offset += 16;
        if (type == 2) {
            if (data_size || r->input_count || r->output_count || r->pid || r->copy_count) return problem(error, cap, "CMIF domain Close has unexpected data");
            r->domain_close = 1; return 0;
        }
        if (type != 1 || data_size < 16 || !checked_span(offset, data_size, raw_end))
            return problem(error, cap, "invalid CMIF domain type=%u data_size=%u", type, data_size);
        raw_end = offset + data_size;
    }
    if (!checked_span(offset, 16, raw_end)) return problem(error, cap, "truncated CMIF input header");
    uint32_t magic = load32(ipc + offset), version = load32(ipc + offset + 4);
    if (magic != SFCI || version > 1 || (version && r->type != 6 && r->type != 7))
        return problem(error, cap, "invalid CMIF header magic=0x%x version=%u", magic, version);
    r->cmif_offset = offset;
    r->command = load32(ipc + offset + 8); r->token = load32(ipc + offset + 12);
    r->payload = ipc + offset + 16; r->payload_size = raw_end - offset - 16; return 0;
}
static int stage_pointers(botw_services *s, Object *object, Request *r, const uint8_t *ipc,
        PointerTransfer *transfer, size_t ipc_capacity, char *error, size_t cap) {
    /* The single server receive arena grows from both ends. The SDK reserves
     * AlignUp(size,16) for every pointer; map-alias A/B buffers use no arena. */
    if (r->pointer_input_bytes > POINTER_CAPACITY ||
            r->pointer_output_bytes > POINTER_CAPACITY - r->pointer_input_bytes)
        return problem(error, cap, "IPC pointer arena aggregate budget exceeds capacity=%u", POINTER_CAPACITY);
    if (r->output_static_count) {
        /* This fixed SDK's ListAllUsers is a VARIABLE OutPointer, not a fixed
         * C array. Its u16 size table lies after the reserved alignment bytes,
         * outside domain data_size. Other output-pointer schemas need evidence. */
        if (object->kind != OBJECT_ACCOUNT || r->command != 2)
            return problem(error, cap, "unsupported IPC receive-static pointer schema service=%s command=%u", kind_name(object->kind), r->command);
        size_t table = r->raw_start + 16 + (r->domain ? 16 : 0) + 16;
        size_t raw_size = (16 + (r->domain ? 16 : 0) + 16 + 2 + 3) & ~(size_t)3;
        if (r->buffer_descriptor_count != 1 || r->output_static_count != 1 || r->output_count != 1 ||
                !r->output_is_static || r->output_size != 128 || r->input_count ||
                r->raw_end - r->raw_start != raw_size || !checked_span(table, 2, r->raw_end) ||
                (r->domain && r->payload_size) ||
                ((unsigned)ipc[table] | (unsigned)ipc[table + 1] << 8) != r->output_size)
            return problem(error, cap, "acc.ListAllUsers requires exact variable C128 pointer size table and no input parameters");
        if (ranges_overlap(r->output_address, r->output_size, transfer->ipc_address, ipc_capacity))
            return problem(error, cap, "IPC receive-static pointer overlaps the client IPC message");
        if (r->output_address >= (UINT64_C(1) << 42))
            return problem(error, cap, "IPC receive-static destination cannot be represented by the 42-bit reply pointer");
        transfer->output_address = r->output_address; transfer->output_size = r->output_size;
        r->output_address = (uint64_t)(uintptr_t)(transfer->arena + POINTER_CAPACITY - r->pointer_output_bytes);
        r->payload_size = 0;
        memset((void *)(uintptr_t)r->output_address, 0, r->output_size);
    }
    if (r->input_is_static) {
        if (copy_from_guest(s, r->input_address, transfer->arena, r->input_size, error, cap)) return -1;
        r->input_address = (uint64_t)(uintptr_t)transfer->arena;
    }
    r->transfer = transfer; return 0;
}
static void response(uint8_t *ipc, size_t capacity, const Request *r, const void *data, size_t data_size, uint32_t handle, uint32_t object_id) {
    memset(ipc, 0, capacity);
    size_t raw_start = handle ? 16 : 8;
    if (r->transfer && r->transfer->output_written) {
        uint64_t address = r->transfer->output_address;
        uint32_t first = (uint32_t)((address >> 36) & 63) << 6 |
            (uint32_t)((address >> 32) & 15) << 12 | (uint32_t)r->transfer->output_written << 16;
        store32(ipc, 1u << 16); store32(ipc + raw_start, first); store32(ipc + raw_start + 4, (uint32_t)address);
        raw_start += 8;
    }
    size_t aligned = (raw_start + 15) & ~(size_t)15;
    size_t payload = aligned + (r->domain ? 16 : 0);
    size_t end = payload + 16 + data_size + (object_id ? 4 : 0);
    store32(ipc + 4, (uint32_t)((end - raw_start + 3) / 4) | (handle ? 0x80000000u : 0));
    if (handle) { store32(ipc + 8, 1u << 5); store32(ipc + 12, handle); }
    if (r->domain) store32(ipc + aligned, object_id ? 1 : 0);
    store32(ipc + payload, SFCO); store32(ipc + payload + 12, r->token);
    if (data_size) memcpy(ipc + payload + 16, data, data_size);
    if (object_id) store32(ipc + payload + 16 + data_size, object_id);
}
static int result_response(uint8_t *ipc, size_t capacity, const Request *r, uint32_t result) {
    response(ipc, capacity, r, NULL, 0, 0, 0);
    store32(ipc + 16 + (r->domain ? 16 : 0) + 8, result); return 0;
}
static int exact_payload(const Request *r, size_t expected, const char *method, char *error, size_t cap) {
    /* cmifMakeRequest reserves 16 alignment bytes; an already aligned data
     * start (e.g. one X descriptor) leaves all 16 at the end. Domain data_size
     * is exact; neither path may hide unknown nonzero input in its padding. */
    if (r->payload_size < expected || (r->domain ? r->payload_size != expected : r->payload_size - expected > 16))
        return problem(error, cap, "%s requires %zu input bytes (got=%zu domain=%u)", method, expected, r->payload_size, r->domain);
    for (size_t i = expected; i < r->payload_size; i++)
        if (r->payload[i]) return problem(error, cap, "%s has unexpected input beyond %zu bytes", method, expected);
    return 0;
}
static Object *new_storage(botw_services *s, uint64_t size, char *error, size_t cap) {
    if (size > BUFFER_LIMIT || size > STORAGE_TOTAL_LIMIT - s->storage_bytes) {
        problem(error, cap, "CreateStorage size=%llu exceeds per-object/aggregate limit", (unsigned long long)size); return NULL;
    }
    StorageBacking *backing = calloc(1, sizeof *backing);
    Object *storage = new_object(OBJECT_APPLET_STORAGE);
    if (!backing || !storage) { free(backing); release_object(storage); problem(error, cap, "allocate applet storage metadata failed"); return NULL; }
    backing->bytes = calloc(1, size ? (size_t)size : 1);
    if (!backing->bytes) { free(backing); release_object(storage); problem(error, cap, "allocate applet storage bytes failed"); return NULL; }
    backing->owner = s; backing->refs = 1; backing->size = (size_t)size;
    s->storage_bytes += (size_t)size; storage->storage = backing; return storage;
}
static int return_object(botw_services *s, Session *session, Object *o, uint8_t *ipc, size_t capacity, const Request *r, int kernel_handle, char *error, size_t cap) {
    uint32_t handle = 0, object_id = 0;
    if (r->domain && !kernel_handle) {
        for (unsigned i = 0; i < DOMAIN_LIMIT; i++) if (!session->objects[i]) { session->objects[i] = o; object_id = i + 1; break; }
        if (!object_id) { release_object(o); return problem(error, cap, "CMIF domain object table exhausted"); }
    } else if (object_handle(s, o, &handle, error, cap)) return -1;
    response(ipc, capacity, r, NULL, 0, handle, object_id); return 0;
}
static int control(botw_services *s, Session *session, uint8_t *ipc, size_t capacity, const Request *r, char *error, size_t cap) {
    if (r->buffer_descriptor_count || r->input_count || r->output_count || r->pid || r->copy_count) return problem(error, cap, "CMIF control command=%u has unexpected buffers/PID", r->command);
    uint32_t handle = 0, object_id;
    switch (r->command) {
        case 0:
            if (session->domain || !session->root) return problem(error, cap, "CMIF ConvertCurrentObjectToDomain called on domain/empty session");
            session->domain = 1; session->objects[0] = session->root; object_id = 1;
            response(ipc, capacity, r, &object_id, 4, 0, 0); return 0;
        case 1:
            if (!session->domain || r->payload_size < 4) return problem(error, cap, "CMIF CopyFromCurrentDomain requires domain and object ID");
            object_id = load32(r->payload);
            if (!object_id || object_id > DOMAIN_LIMIT || !session->objects[object_id - 1]) return problem(error, cap, "CMIF CopyFromCurrentDomain invalid object=%u", object_id);
            session->objects[object_id - 1]->refs++;
            if (object_handle(s, session->objects[object_id - 1], &handle, error, cap)) return -1;
            response(ipc, capacity, r, NULL, 0, handle, 0); return 0;
        case 2: case 4: /* CloneCurrentObject / CloneCurrentObjectEx(tag). */
            if (r->command == 4 && r->payload_size < 4) return problem(error, cap, "CMIF CloneCurrentObjectEx missing tag");
            if (assign_handle(s, session, &handle, error, cap)) return -1;
            session->refs++; response(ipc, capacity, r, NULL, 0, handle, 0); return 0;
        case 3: {
            uint16_t size = POINTER_CAPACITY; response(ipc, capacity, r, &size, 2, 0, 0); return 0;
        }
        default: return problem(error, cap, "unsupported CMIF control command=%u", r->command);
    }
}
static int read_leb128(const uint8_t *bytes, size_t size, size_t *offset, uint64_t *value) {
    *value = 0;
    for (unsigned shift = 0; shift < 64 && *offset < size; shift += 7) {
        uint8_t byte = bytes[(*offset)++];
        if (shift == 63 && (byte & 0xfe)) return -1;
        *value |= (uint64_t)(byte & 0x7f) << shift;
        if (!(byte & 0x80)) return 0;
    }
    return -1;
}
static void emit_text(botw_services *s, const uint8_t *text, size_t length, unsigned severity) {
    /* Escape control bytes so guest text cannot forge host diagnostic records. */
    char chunk[1024]; size_t used = 0;
    for (size_t i = 0; i < length; i++) {
        uint8_t c = text[i];
        if (used > sizeof chunk - 6) { chunk[used] = 0; emit(s, "guest_log severity=%u text=%s", severity, chunk); used = 0; }
        if (c < 32 || c == 127) { chunk[used++] = '\\'; chunk[used++] = 'x'; const char *hex = "0123456789abcdef"; chunk[used++] = hex[c >> 4]; chunk[used++] = hex[c & 15]; }
        else chunk[used++] = (char)c;
    }
    chunk[used] = 0; emit(s, "guest_log severity=%u text=%s", severity, chunk);
}
static int logger_packet(botw_services *s, const Request *r, char *error, size_t cap) {
    if (r->input_count != 1 || r->input_size < 24 || r->input_size > LOG_LIMIT) return problem(error, cap, "ILogger.Log requires one checked buffer with 24-byte header");
    const uint8_t *packet = (const uint8_t *)(uintptr_t)r->input_address;
    uint32_t length = load32(packet + 20);
    if (length > r->input_size - 24 || (packet[16] & ~7u) || packet[17] || packet[18] > 4)
        return problem(error, cap, "invalid ILogger packet flags=0x%x severity=%u payload=%u capacity=%zu", packet[16], packet[18], length, r->input_size - 24);
    const uint8_t *chunks = packet + 24; size_t offset = 0;
    /* Validate every chunk before any logging side effect. */
    while (offset < length) {
        uint64_t key, size;
        if (read_leb128(chunks, length, &offset, &key) || read_leb128(chunks, length, &offset, &size) || size > length - offset)
            return problem(error, cap, "invalid ILogger LEB128/chunk bounds at offset=%zu", offset);
        offset += (size_t)size;
    }
    if (!s->log_destination) return 0;
    emit(s, "guest_log_packet pid=%llu tid=%llu flags=0x%x severity=%u verbosity=%u bytes=%u", (unsigned long long)load64(packet), (unsigned long long)load64(packet + 8), packet[16], packet[18], packet[19], length);
    offset = 0;
    while (offset < length) {
        uint64_t key = 0, size = 0;
        (void)read_leb128(chunks, length, &offset, &key); (void)read_leb128(chunks, length, &offset, &size);
        if (key == 2) emit_text(s, chunks + offset, (size_t)size, packet[18]);
        offset += (size_t)size;
    }
    return 0;
}
static int account_request(botw_services *s, Session *session, Object *o, uint8_t *ipc, size_t capacity, const Request *r, char *error, size_t cap) {
    if (!s->local_user_ready) return problem(error, cap, "acc:u0 requires explicit persistent host-local user configuration");
    if (local_user_mount(s, s->local_uid, error, cap)) return -1;
    if (o->kind == OBJECT_ACCOUNT_CONTEXT) {
        if (o->pid != s->host_process_id || memcmp(o->account_uid, s->local_uid, 16))
            return problem(error, cap, "account context lost its local process/user binding");
        /* cmd101 creates a lifetime-managed local user context for the old
         * SDK UserHandle. It does not supply BAAS credentials or linked state. */
        return problem(error, cap, "unsupported account context command=%u: Nintendo online/linked backend is not configured", r->command);
    }
    if (r->input_count || r->copy_count) return problem(error, cap, "acc:u0 command=%u unexpected input buffer/handles", r->command);
    if (r->command == 100) {
        if (!r->pid || r->output_count || o->initialized || exact_payload(r, 8, "acc.InitializeApplicationInfo", error, cap))
            return problem(error, cap, "acc.InitializeApplicationInfo requires PID, zero u64 placeholder and uninitialized client");
        if (load64(r->payload)) return problem(error, cap, "acc.InitializeApplicationInfo unknown nonzero PID payload");
        /* HIPC SendPid and this SDK's input u64 are zero placeholders. The
         * service context belongs to one host process running this guest; use
         * getpid captured at creation, never guest-supplied numeric authority. */
        o->initialized = 1; o->pid = s->host_process_id;
        emit(s, "account_initialized scope=host_local_offline pid_source=host_process pid=%llu", (unsigned long long)o->pid);
        response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (!o->initialized || o->pid != s->host_process_id || r->pid)
        return problem(error, cap, "acc:u0 requires process-bound initialization and no unexpected PID command=%u", r->command);
    if (r->command == 2) {
        if (exact_payload(r, 0, "acc.ListAllUsers", error, cap)) return -1;
        if (r->output_count != 1 || r->output_static_count != 1 || r->output_size != 128)
            return problem(error, cap, "acc.ListAllUsers requires one checked C receive-static buffer of exactly 128 bytes");
        uint8_t users[128] = {0}; memcpy(users, s->local_uid, 16);
        memcpy((void *)(uintptr_t)r->output_address, users, sizeof users);
        r->transfer->output_written = sizeof users;
        /* The original SDK scans eight UID entries for a zero terminator. No
         * response count scalar is present in this command's ABI. */
        response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (r->command == 101) {
        if (r->output_count) return problem(error, cap, "acc.GetBaasAccountManagerForApplication does not accept output buffers");
        if (exact_payload(r, 16, "acc.GetBaasAccountManagerForApplication", error, cap)) return -1;
        if (memcmp(r->payload, s->local_uid, 16)) return problem(error, cap, "account context UID does not match the published local user");
        Object *context = new_object(OBJECT_ACCOUNT_CONTEXT);
        if (!context) return problem(error, cap, "allocate local account context failed");
        context->pid = o->pid; memcpy(context->account_uid, s->local_uid, 16);
        return return_object(s, session, context, ipc, capacity, r, 0, error, cap);
    }
    return problem(error, cap, "unsupported IPC service=acc:u0 command=%u (host-local user path only)", r->command);
}
static int applet_request(botw_services *s, Session *session, Object *o, uint8_t *ipc, size_t capacity, const Request *r, char *error, size_t cap) {
    if (!s->applet_ready) return problem(error, cap, "appletOE requires measured host lifecycle state");
    if (r->input_count && o->kind != OBJECT_STORAGE_ACCESSOR) return problem(error, cap, "unsupported applet input buffer service=%s command=%u", kind_name(o->kind), r->command);
    if (o->kind == OBJECT_APPLET_OE && r->command == 0) {
        if (!r->pid || r->copy_count != 1 || r->payload_size < 8 || load64(r->payload) || !s->kernel.is_process_handle || !s->kernel.is_process_handle(s->kernel.opaque, r->copy_handle))
            return problem(error, cap, "appletOE.OpenApplicationProxy requires PID, reserved=0 and one current-process copy handle (got=0x%x)", r->copy_handle);
        Object *proxy = new_object(OBJECT_APPLET_PROXY);
        if (!proxy) return problem(error, cap, "allocate ApplicationProxy failed");
        return return_object(s, session, proxy, ipc, capacity, r, 0, error, cap);
    }
    if (r->pid || r->copy_count) return problem(error, cap, "unexpected applet PID/handle service=%s command=%u", kind_name(o->kind), r->command);
    if (o->kind == OBJECT_APPLET_PROXY) {
        enum ObjectKind child;
        switch (r->command) {
            case 0: child = OBJECT_COMMON_STATE; break;
            case 1: child = OBJECT_SELF_CONTROLLER; break;
            case 2: child = OBJECT_WINDOW_CONTROLLER; break;
            case 20: child = OBJECT_APPLICATION_FUNCTIONS; break;
            case 11: child = OBJECT_LIBRARY_APPLET_CREATOR; break;
            case 3: child = OBJECT_AUDIO_CONTROLLER; break;
            case 4: child = OBJECT_DISPLAY_CONTROLLER; break;
            case 1000: child = OBJECT_DEBUG_FUNCTIONS; break;
            default: return problem(error, cap, "unsupported ApplicationProxy interface command=%u", r->command);
        }
        Object *object = new_object(child);
        if (!object) return problem(error, cap, "allocate %s failed", kind_name(child));
        return return_object(s, session, object, ipc, capacity, r, 0, error, cap);
    }
    if (o->kind == OBJECT_DEBUG_FUNCTIONS && r->command == 20) {
        free(s->transition_image); s->transition_image = NULL; s->transition_generation++;
        response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (o->kind == OBJECT_DISPLAY_CONTROLLER && r->command == 21) {
        if (r->payload_size < 4 || r->output_count) return problem(error, cap, "ClearAppletTransitionBuffer requires u32 color and no output buffer");
        if (!s->transition_image) s->transition_image = malloc(CAPTURE_SIZE);
        if (!s->transition_image) return problem(error, cap, "allocate actual transition capture buffer failed");
        for (size_t offset = 0; offset < CAPTURE_SIZE; offset += 4) memcpy(s->transition_image + offset, r->payload, 4);
        s->transition_generation++; response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (o->kind == OBJECT_DISPLAY_CONTROLLER && r->command >= 5 && r->command <= 7) {
        if (r->output_count != 1) return problem(error, cap, "DisplayController capture query requires one checked output buffer");
        const uint8_t *snapshot = s->capture_images[r->command - 5]; uint8_t available = snapshot != NULL;
        if (snapshot) {
            if (r->output_size < CAPTURE_SIZE) return problem(error, cap, "DisplayController output cannot fit 1280x720 RGBA8 capture");
            memcpy((void *)(uintptr_t)r->output_address, snapshot, CAPTURE_SIZE);
        }
        response(ipc, capacity, r, &available, 1, 0, 0); return 0;
    }
    if (o->kind == OBJECT_AUDIO_CONTROLLER) {
        if (r->command == 1 || r->command == 2) {
            float gain = r->command == 1 ? s->audio_main_gain : s->audio_library_gain;
            response(ipc, capacity, r, &gain, 4, 0, 0); return 0;
        }
        if (r->command == 0 || r->command == 4) {
            float values[2] = {0}; size_t size = r->command == 0 ? 8 : 4;
            if (r->payload_size < size) return problem(error, cap, "IAudioController command=%u truncated gain input", r->command);
            memcpy(values, r->payload, size);
            if (!isfinite(values[0]) || values[0] < 0 || values[0] > 1 || (r->command == 0 && (!isfinite(values[1]) || values[1] < 0 || values[1] > 1)))
                return problem(error, cap, "IAudioController gains must be finite within [0,1]");
            if (r->command == 0) { s->audio_main_gain = values[0]; s->audio_library_gain = values[1]; }
            else s->audio_transparent_rate = values[0];
            emit(s, "applet_audio main_gain=%g library_gain=%g transparent_rate=%g", (double)s->audio_main_gain, (double)s->audio_library_gain, (double)s->audio_transparent_rate);
            response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
        }
    }
    if (o->kind == OBJECT_LIBRARY_APPLET_CREATOR) {
        if (r->command == 10) {
            if (r->payload_size < 8) return problem(error, cap, "CreateStorage requires signed size");
            Object *storage = new_storage(s, load64(r->payload), error, cap);
            if (!storage) return -1;
            return return_object(s, session, storage, ipc, capacity, r, 0, error, cap);
        }
        if (r->command == 0 && r->payload_size >= 8)
            return problem(error, cap, "CreateLibraryApplet requires host UI adapter applet_id=0x%x mode=%u", load32(r->payload), load32(r->payload + 4));
    } else if (o->kind == OBJECT_APPLET_STORAGE && r->command == 0) {
        Object *accessor = new_object(OBJECT_STORAGE_ACCESSOR);
        if (!accessor) return problem(error, cap, "allocate IStorageAccessor failed");
        accessor->storage = o->storage; accessor->storage->refs++;
        return return_object(s, session, accessor, ipc, capacity, r, 0, error, cap);
    } else if (o->kind == OBJECT_STORAGE_ACCESSOR) {
        if (r->command == 0) {
            if (r->input_count || r->output_count) return problem(error, cap, "IStorageAccessor.GetSize does not accept buffers");
            uint64_t size = o->storage->size; response(ipc, capacity, r, &size, 8, 0, 0); return 0;
        }
        if (r->command == 10 || r->command == 11) {
            int write = r->command == 10;
            if (r->payload_size < 8 || (write ? r->input_count != 1 || r->output_count : r->output_count != 1 || r->input_count))
                return problem(error, cap, "IStorageAccessor.%s requires offset and one %s buffer", write ? "Write" : "Read", write ? "input" : "output");
            uint64_t offset = load64(r->payload); size_t length = write ? r->input_size : r->output_size;
            if (offset > o->storage->size || length > o->storage->size - offset)
                return problem(error, cap, "IStorageAccessor range exceeds backing offset=%llu length=%zu size=%zu", (unsigned long long)offset, length, o->storage->size);
            if (write) memcpy(o->storage->bytes + (size_t)offset, (const void *)(uintptr_t)r->input_address, length);
            else memcpy((void *)(uintptr_t)r->output_address, o->storage->bytes + (size_t)offset, length);
            response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
        }
    }
    if (o->kind == OBJECT_COMMON_STATE) {
        if (r->command == 0) {
            if (!s->kernel.create_event || !s->kernel.duplicate_handle || !s->kernel.signal_event || !s->kernel.clear_event || !s->kernel.close_handle)
                return problem(error, cap, "ICommonStateGetter.GetEventHandle requires real kernel event API");
            if (!s->applet_event_read && s->kernel.create_event(s->kernel.opaque, s->applet_message_count != 0, &s->applet_event_write, &s->applet_event_read, error, cap)) return -1;
            uint32_t handle = 0;
            if (s->kernel.duplicate_handle(s->kernel.opaque, s->applet_event_read, &handle, error, cap)) return -1;
            response(ipc, capacity, r, NULL, 0, handle, 0);
            store32(ipc + 8, 1u << 1); /* HIPC copy, independent client read endpoint. */
            return 0;
        }
        if (r->command == 1) {
            if (!s->applet_message_count) {
                response(ipc, capacity, r, NULL, 0, 0, 0);
                store32(ipc + 16 + (r->domain ? 16 : 0) + 8, 0x680); /* AM: no messages */
                return 0;
            }
            if (s->applet_message_count == 1 && s->applet_event_read && s->kernel.clear_event(s->kernel.opaque, s->applet_event_read, error, cap)) return -1;
            uint32_t message = s->applet_messages[s->applet_message_head];
            s->applet_message_head = (s->applet_message_head + 1) % 32; s->applet_message_count--;
            response(ipc, capacity, r, &message, 4, 0, 0); return 0;
        }
        if (r->command == 5 || r->command == 9) {
            uint8_t value = (uint8_t)(r->command == 5 ? s->applet_operation : s->applet_focus);
            response(ipc, capacity, r, &value, 1, 0, 0); return 0;
        }
        if (r->command == 6) { response(ipc, capacity, r, &s->performance_mode, 4, 0, 0); return 0; }
    } else if (o->kind == OBJECT_SELF_CONTROLLER && (r->command == 13 || r->command == 16)) {
        uint32_t mode = s->focus_mode; unsigned suspend_out = s->focus_suspend_out;
        if (r->command == 13) {
            if (r->payload_size < 3) return problem(error, cap, "SetFocusHandlingMode requires three bools");
            if (r->payload[0] == 0 && r->payload[1] == 0 && r->payload[2] == 1) mode = 0;
            else if (r->payload[0] == 1 && r->payload[1] == 1 && r->payload[2] == 0) mode = 1;
            else if (r->payload[0] == 1 && r->payload[1] == 0 && r->payload[2] == 1) mode = 2;
            else return problem(error, cap, "unsupported SetFocusHandlingMode bool tuple=%u,%u,%u", r->payload[0], r->payload[1], r->payload[2]);
        } else {
            if (r->payload_size < 1 || r->payload[0] > 1) return problem(error, cap, "SetOutOfFocusSuspendingEnabled requires bool");
            suspend_out = r->payload[0];
        }
        if (apply_focus_policy(s, mode, suspend_out, s->applet_foreground, s->applet_focus, error, cap)) return -1;
        s->focus_mode = mode; s->focus_suspend_out = suspend_out; s->focus_policy_configured = 1;
        response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    } else if (o->kind == OBJECT_SELF_CONTROLLER && (r->command == 11 || r->command == 12 || r->command == 14)) {
        if (r->payload_size < 1 || r->payload[0] > 1) return problem(error, cap, "SelfController notification command=%u requires bool", r->command);
        if (r->command == 11) s->applet_notify_operation = r->payload[0];
        else if (r->command == 12) s->applet_notify_performance = r->payload[0];
        else s->applet_notify_resume = r->payload[0];
        response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    } else if (o->kind == OBJECT_WINDOW_CONTROLLER) {
        if (r->command == 1) { response(ipc, capacity, r, &s->applet_resource_user_id, 8, 0, 0); return 0; }
        if (r->command == 10) {
            if (!s->applet_foreground || s->applet_focus != 1) return problem(error, cap, "WindowController.AcquireForegroundRights requires active focused host app");
            s->applet_foreground_rights = 1; response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
        }
    } else if (o->kind == OBJECT_APPLICATION_FUNCTIONS && r->command == 1) {
        if (r->output_count) return problem(error, cap, "ApplicationFunctions.PopLaunchParameter does not accept output buffers");
        if (exact_payload(r, 4, "ApplicationFunctions.PopLaunchParameter", error, cap)) return -1;
        uint32_t kind = load32(r->payload);
        if (kind != 1 && kind != 2) return problem(error, cap, "PopLaunchParameter unsupported kind=%u", kind);
        /* No application-specific UserChannel (kind1) is supplied by this
         * launcher. Its empty slot is independent of the explicit kind2 slot. */
        if (kind == 1) return result_response(ipc, capacity, r, AM_NOT_AVAILABLE);
        if (!s->local_user_ready) return problem(error, cap, "preselected launch parameter requires configured host-local user");
        if (local_user_mount(s, s->local_uid, error, cap)) return -1;
        /* Original SDK TryPopFromApplicationParameterChannel (sdk+e9ec4)
         * compares Result's low 22 bits with 0x480 and returns false for that
         * specific empty-slot result. ReceiveMessage's 0x680 is distinct. */
        if (!s->preselected_pending) return result_response(ipc, capacity, r, AM_NOT_AVAILABLE);
        Object *storage = new_storage(s, PRESELECTED_STORAGE_SIZE, error, cap);
        if (!storage) return -1;
        store32(storage->storage->bytes, 0xc79497cau); storage->storage->bytes[4] = 1;
        memcpy(storage->storage->bytes + 8, s->local_uid, 16);
        int result = return_object(s, session, storage, ipc, capacity, r, 0, error, cap);
        if (!result) {
            s->preselected_pending = 0;
            emit(s, "local_user launch_parameter_kind=2 delivered=true bytes=%u scope=host_local_offline", PRESELECTED_STORAGE_SIZE);
        }
        return result;
    } else if (o->kind == OBJECT_APPLICATION_FUNCTIONS && r->command == 21) {
        /* IApplicationFunctions::GetDesiredLanguage: no semantic input,
         * one inline eight-byte LanguageCode, no buffer/handle transfer.
         * Original sdk e3c04 proxy and libnx appletGetDesiredLanguage agree. */
        if (r->buffer_descriptor_count)
            return problem(error, cap, "ApplicationFunctions.GetDesiredLanguage does not accept buffer descriptors");
        /* Domain data_size excludes framing padding. Without descriptors or
         * special headers a session request can retain exactly eight trailing
         * alignment bytes, whose contents are unspecified, not business data. */
        if (r->domain ? r->payload_size != 0 : (r->payload_size != 0 && r->payload_size != 8))
            return problem(error, cap, "ApplicationFunctions.GetDesiredLanguage requires no semantic input bytes");
        if (!s->application_language_ready)
            return problem(error, cap, "ApplicationFunctions.GetDesiredLanguage requires explicit application language configuration");
        response(ipc, capacity, r, s->application_language, sizeof s->application_language, 0, 0); return 0;
    } else if (o->kind == OBJECT_APPLICATION_FUNCTIONS && r->command == 22) {
        /* Switchbrew AM_services: SetTerminateResult takes one u32 Result.
         * Preserve the guest's original failure rather than hiding it behind
         * an unknown command or accepting an unimplemented termination path. */
        if (exact_payload(r, 4, "ApplicationFunctions.SetTerminateResult", error, cap)) return -1;
        return problem(error, cap, "guest requested ApplicationFunctions.SetTerminateResult result=0x%08x; termination delivery is not implemented",
            load32(r->payload));
    } else if (o->kind == OBJECT_APPLICATION_FUNCTIONS && r->command == 40) {
        if (!s->applet_foreground) return problem(error, cap, "ApplicationFunctions.NotifyRunning requires foreground host app");
        s->applet_running = 1; uint8_t active = 1;
        emit(s, "applet_running=true resource_user_id=0x%llx", (unsigned long long)s->applet_resource_user_id);
        response(ipc, capacity, r, &active, 1, 0, 0); return 0;
    }
    return problem(error, cap, "unsupported IPC service=%s command=%u", kind_name(o->kind), r->command);
}
static int fs_result_response(uint8_t *ipc, size_t capacity, const Request *r, uint32_t result) {
    return result_response(ipc, capacity, r, result);
}
static int fs_error_response(uint8_t *ipc, size_t capacity, const Request *r, const botw_romfs_error *backend, char *error, size_t cap) {
    if (backend->code == BOTW_ROMFS_ENOENT) {
        return fs_result_response(ipc, capacity, r, 0x202); /* FS PathNotFound */
    }
    return problem(error, cap, "RomFS backend error=%d: %s", backend->code, backend->message);
}
static int fs_no_input_payload(const Request *r, const char *method, char *error, size_t cap) {
    /* Nondomain HIPC may carry up to 15 trailing alignment bytes. A domain
     * request explicitly sizes its CMIF data, so no extra data is accepted. */
    if ((r->domain && r->payload_size) || r->payload_size > 15)
        return problem(error, cap, "fsp-srv.%s takes no input payload", method);
    for (size_t i = 0; i < r->payload_size; i++)
        if (r->payload[i]) return problem(error, cap, "fsp-srv.%s takes no input payload", method);
    return 0;
}
static int save_backend_response(botw_services *s, uint8_t *ipc, size_t capacity, const Request *r,
                                 const char *method, const botw_savefs_error *backend, uint64_t actual, char *error, size_t cap) {
    /* Only established FS results are translated. Unmapped host failures stop
     * with the real errno and partial byte count instead of fabricated success.
     * Numeric descriptions reference libvapours/results/fs_results.hpp. */
    uint32_t description = backend->code == ENOENT ? 1 : backend->code == EEXIST ? 2 :
        (backend->code == EDQUOT || backend->code == ENOSPC) ? 30 : 0;
    emit(s, "savefs_error method=%s errno=%d actual_bytes=%llu detail=%s", method, backend->code, (unsigned long long)actual, backend->message);
    if (description) return fs_result_response(ipc, capacity, r, 2u | description << 9);
    return problem(error, cap, "SaveFS %s errno=%d actual_bytes=%llu: %s", method, backend->code, (unsigned long long)actual, backend->message);
}
static int save_path_input(const Request *r, char path[0x301], char *error, size_t cap) {
    if (r->input_count != 1 || r->input_static_count != 1 || r->input_size > 0x301)
        return problem(error, cap, "SaveFS path requires one checked X pointer of at most 0x301 bytes");
    const char *input = (const char *)(uintptr_t)r->input_address;
    const char *end = memchr(input, 0, r->input_size);
    if (!end) return problem(error, cap, "SaveFS path is not NUL terminated");
    memcpy(path, input, (size_t)(end - input) + 1); return 0;
}
static int save_request(botw_services *s, Session *session, Object *o, uint8_t *ipc, size_t capacity, const Request *r, char *error, size_t cap) {
    if (!s->local_user_ready || !o->savefs) return problem(error, cap, "SaveFS object has no persistent local user/mount binding");
    /* An opened object pins its borrowed backend identity, independently of
     * later set_savefs calls. The owner keeps every borrowed mount alive until
     * service/object destruction. File objects do not depend on a live IFS. */
    if (local_user_savefs(o->savefs, s->local_uid, error, cap)) return -1;
    if (r->pid || r->copy_count) return problem(error, cap, "SaveFS command=%u unexpected PID/handles", r->command);
    botw_savefs_error backend = {0};
    if (o->kind == OBJECT_SAVE_FILESYSTEM) {
        if (r->output_count) return problem(error, cap, "SaveFS IFileSystem command=%u does not accept output buffers", r->command);
        if (r->command == 10) {
            if (r->input_count) return problem(error, cap, "SaveFS IFileSystem.Commit does not accept buffers");
            if (exact_payload(r, 0, "SaveFS IFileSystem.Commit", error, cap)) return -1;
            if (botw_savefs_commit(o->savefs, &backend)) return save_backend_response(s, ipc, capacity, r, "IFileSystem.Commit", &backend, 0, error, cap);
            response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
        }
        if (r->command != 0 && r->command != 2 && r->command != 7 && r->command != 8)
            return problem(error, cap, "unsupported SaveFS IFileSystem command=%u: delete/rename/directory/recursive/free-space operations are not connected", r->command);
        size_t payload_size = r->command == 0 ? 16 : r->command == 8 ? 4 : 0;
        if (exact_payload(r, payload_size, "SaveFS IFileSystem path command", error, cap)) return -1;
        char path[0x301]; if (save_path_input(r, path, error, cap)) return -1;
        if (r->command == 0) {
            uint64_t size = load64(r->payload + 8);
            /* +4 is alignment padding, not a second option field. BigFile=1
             * means a concatenation directory and has no backend here. */
            if (load32(r->payload) || size > INT64_MAX) return problem(error, cap, "SaveFS CreateFile requires option=0 and nonnegative signed size");
            if (botw_savefs_create(o->savefs, path, size, &backend)) return save_backend_response(s, ipc, capacity, r, "IFileSystem.CreateFile", &backend, 0, error, cap);
            response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
        }
        if (r->command == 2) {
            if (botw_savefs_mkdir(o->savefs, path, &backend)) return save_backend_response(s, ipc, capacity, r, "IFileSystem.CreateDirectory", &backend, 0, error, cap);
            response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
        }
        uint32_t mode = r->command == 8 ? load32(r->payload) : 0;
        if (r->command == 8 && ((mode & ~7u) || !(mode & 3u) || ((mode & 4u) && !(mode & 2u))))
            return problem(error, cap, "SaveFS OpenFile invalid/unsupported mode=0x%x (Read=1 Write=2 AllowAppend=4)", mode);
        botw_savefs_entry entry;
        if (botw_savefs_stat(o->savefs, path, &entry, &backend)) return save_backend_response(s, ipc, capacity, r, "IFileSystem.GetEntryType/OpenFile", &backend, 0, error, cap);
        if (r->command == 7) {
            uint32_t type = entry.is_directory ? 0 : 1;
            response(ipc, capacity, r, &type, 4, 0, 0); return 0;
        }
        if (entry.is_directory) return problem(error, cap, "SaveFS OpenFile path is a directory");
        /* Probe actual OS read/write permissions without changing bytes. The
         * path backend currently opens files for each checked operation; no
         * rename/delete commands are exposed that could retarget a live file. */
        size_t actual = 0;
        int failed = mode & 2u ? botw_savefs_pwrite(o->savefs, path, 0, NULL, 0, &actual, &backend) :
            botw_savefs_pread(o->savefs, path, 0, NULL, 0, &actual, &backend);
        if (failed) return save_backend_response(s, ipc, capacity, r, "IFileSystem.OpenFile", &backend, 0, error, cap);
        Object *file = new_object(OBJECT_SAVE_FILE);
        if (!file) return problem(error, cap, "allocate SaveFS file object failed");
        size_t length = strlen(path) + 1; file->file_path = malloc(length);
        if (!file->file_path) { release_object(file); return problem(error, cap, "allocate SaveFS file path failed"); }
        memcpy(file->file_path, path, length); file->savefs = o->savefs; file->file_mode = mode;
        return return_object(s, session, file, ipc, capacity, r, 0, error, cap);
    }
    if (r->command == 2 || r->command == 3 || r->command == 4) {
        if (r->input_count || r->output_count) return problem(error, cap, "SaveFS IFile command=%u does not accept buffers", r->command);
        if (exact_payload(r, r->command == 3 ? 8 : 0, "SaveFS IFile metadata command", error, cap)) return -1;
        if (r->command == 2) {
            /* The backend's durable primitive syncs the entire actual save
             * store; close itself never runs this operation. */
            if (botw_savefs_commit(o->savefs, &backend)) return save_backend_response(s, ipc, capacity, r, "IFile.Flush", &backend, 0, error, cap);
        } else if (r->command == 3) {
            if (!(o->file_mode & 2u)) return fs_result_response(ipc, capacity, r, 2u | (6203u << 9));
            uint64_t size = load64(r->payload);
            if (size > INT64_MAX) return problem(error, cap, "SaveFS SetSize requires nonnegative signed size");
            if (botw_savefs_truncate(o->savefs, o->file_path, size, &backend)) return save_backend_response(s, ipc, capacity, r, "IFile.SetSize", &backend, 0, error, cap);
        } else {
            botw_savefs_entry entry;
            if (botw_savefs_stat(o->savefs, o->file_path, &entry, &backend)) return save_backend_response(s, ipc, capacity, r, "IFile.GetSize", &backend, 0, error, cap);
            if (entry.is_directory) return problem(error, cap, "SaveFS GetSize file path became a directory");
            response(ipc, capacity, r, &entry.size, 8, 0, 0); return 0;
        }
        response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (r->command == 0 || r->command == 1) {
        int write = r->command == 1;
        if (exact_payload(r, 24, write ? "SaveFS IFile.Write" : "SaveFS IFile.Read", error, cap)) return -1;
        uint32_t option = load32(r->payload); uint64_t offset = load64(r->payload + 8), length = load64(r->payload + 16);
        if (option > (unsigned)write || offset > INT64_MAX || length > (uint64_t)INT64_MAX - offset || length > SIZE_MAX)
            return problem(error, cap, "SaveFS IFile.%s unsupported option/offset/length option=%u offset=%llu length=%llu", write ? "Write" : "Read", option, (unsigned long long)offset, (unsigned long long)length);
        if (write ? r->output_count || r->input_static_count || r->input_count != (length ? 1u : 0u) || length > r->input_size :
            r->input_count || r->output_static_count || r->output_count != (length ? 1u : 0u) || length > r->output_size)
            return problem(error, cap, "SaveFS IFile.%s requires a checked %s buffer covering the full requested span", write ? "Write" : "Read", write ? "A input" : "B output");
        if (!write && ranges_overlap(r->output_address, r->output_size, r->transfer->ipc_address, capacity))
            return problem(error, cap, "SaveFS IFile.Read output descriptor overlaps IPC message buffer");
        if (!(o->file_mode & (write ? 2u : 1u))) return fs_result_response(ipc, capacity, r, 2u | ((write ? 6203u : 6202u) << 9));
        if (write && length && !(o->file_mode & 4u)) {
            botw_savefs_entry entry;
            if (botw_savefs_stat(o->savefs, o->file_path, &entry, &backend)) return save_backend_response(s, ipc, capacity, r, "IFile.Write size check", &backend, 0, error, cap);
            if (offset > entry.size || length > entry.size - offset) return fs_result_response(ipc, capacity, r, 2u | (6201u << 9));
        }
        if (!write && prepare_host_output(s, r->output_address, (size_t)length,
                "SaveFS IFile.Read", error, cap)) return -1;
        size_t done = 0;
        while ((uint64_t)done < length) {
            size_t chunk = length - done > BUFFER_LIMIT ? BUFFER_LIMIT : (size_t)(length - done), actual = 0;
            int failed = write ? botw_savefs_pwrite(o->savefs, o->file_path, offset + done, (const void *)(uintptr_t)(r->input_address + done), chunk, &actual, &backend) :
                botw_savefs_pread(o->savefs, o->file_path, offset + done, (void *)(uintptr_t)(r->output_address + done), chunk, &actual, &backend);
            if (actual > chunk) return problem(error, cap, "SaveFS backend returned count beyond requested chunk");
            done += actual;
            if (failed) return save_backend_response(s, ipc, capacity, r, write ? "IFile.Write" : "IFile.Read", &backend, done, error, cap);
            if (actual < chunk) {
                if (write) return problem(error, cap, "SaveFS write returned short success actual_bytes=%zu", done);
                break; /* A real file EOF is a successful short read. */
            }
        }
        if (write && option && botw_savefs_commit(o->savefs, &backend)) return save_backend_response(s, ipc, capacity, r, "IFile.Write Flush", &backend, done, error, cap);
        uint64_t actual = done;
        response(ipc, capacity, r, write ? NULL : &actual, write ? 0 : 8, 0, 0); return 0;
    }
    return problem(error, cap, "unsupported SaveFS IFile command=%u", r->command);
}
static int fs_request(botw_services *s, Session *session, Object *o, uint8_t *ipc, size_t capacity, const Request *r, char *error, size_t cap) {
    if (o->kind == OBJECT_FSP) {
        if (r->input_count || r->output_count || r->copy_count) return problem(error, cap, "fsp-srv command=%u unexpected buffers/handles", r->command);
        if (r->command == 1) {
            if (!r->pid || r->payload_size < 8 || o->initialized) return problem(error, cap, "fsp-srv.Initialize requires PID descriptor and uninitialized session");
            o->initialized = 1; response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
        }
        if (!o->initialized || r->pid) return problem(error, cap, "fsp-srv requires initialized client and no unexpected PID");
        if (r->command == 51) {
            if (exact_payload(r, 0x48, "fsp-srv.OpenSaveDataFileSystem", error, cap)) return -1;
            if (!s->local_user_ready) return problem(error, cap, "OpenSaveDataFileSystem requires configured persistent local user");
            if (local_user_mount(s, s->local_uid, error, cap)) return -1;
            uint8_t expected[0x48] = {0}; expected[0] = 1; expected[40] = 1;
            memcpy(expected + 16, s->local_uid, 16);
            if (memcmp(r->payload, expected, sizeof expected))
                return problem(error, cap, "OpenSaveDataFileSystem requires User space, current title=0, matching local UID, Account type=1 and zero remaining attributes");
            Object *filesystem = new_object(OBJECT_SAVE_FILESYSTEM);
            if (!filesystem) return problem(error, cap, "allocate SaveFS filesystem object failed");
            filesystem->savefs = s->savefs;
            return return_object(s, session, filesystem, ipc, capacity, r, 0, error, cap);
        }
        if (r->command == 1005) {
            if (fs_no_input_payload(r, "GetGlobalAccessLogMode", error, cap)) return -1;
            /* No SDK Log/SD access-log sink has been installed, so this actual
             * policy stays None. Host diagnostic callbacks are independent.
             * SetGlobalAccessLogMode/OutputAccessLogToSdCard remain unsupported
             * until their requested output can really be delivered. */
            response(ipc, capacity, r, &s->fs_access_log_mode, 4, 0, 0); return 0;
        }
        if (r->command == 203) {
            if (fs_no_input_payload(r, "OpenPatchDataStorageByCurrentProcess", error, cap)) return -1;
            if (!s->patch_known_absent)
                return problem(error, cap, "fsp-srv.OpenPatchDataStorageByCurrentProcess installation state is unknown; installed patch storage backend is not configured");
            /* Public FS module=2, TargetNotFound description=1002:
             * libvapours/results/fs_results.hpp. The actual base SDK accepts
             * this missing-target result in its optional patch mount path. */
            emit(s, "patch_data_storage installed=false result=0x7d402 reason=verified_no_installed_update");
            return fs_result_response(ipc, capacity, r, 2u | (1002u << 9));
        }
        if (r->command == 2 || r->command == 200) {
            if (!s->romfs) return problem(error, cap, "fsp-srv command=%u requires mounted host RomFS backend", r->command);
            Object *child = new_object(r->command == 2 ? OBJECT_ROMFS_FILESYSTEM : OBJECT_ROMFS_STORAGE);
            if (!child) return problem(error, cap, "allocate RomFS service object failed");
            child->romfs = s->romfs;
            return return_object(s, session, child, ipc, capacity, r, 0, error, cap);
        }
    } else if (o->kind == OBJECT_ROMFS_FILESYSTEM) {
        if (r->pid || r->copy_count || r->output_count) return problem(error, cap, "RomFS filesystem unexpected PID/handle/output buffer");
        if (r->command == 7 || r->command == 8) {
            if (r->input_count != 1 || r->input_size > 0x301) return problem(error, cap, "RomFS path requires one input pointer of at most 0x301 bytes");
            const char *path = (const char *)(uintptr_t)r->input_address;
            const char *end = memchr(path, 0, r->input_size);
            if (!end) return problem(error, cap, "RomFS path is not NUL terminated");
            botw_romfs_entry entry; botw_romfs_error backend = {0};
            if (botw_romfs_stat(o->romfs, path, &entry, &backend)) return fs_error_response(ipc, capacity, r, &backend, error, cap);
            if (r->command == 7) {
                uint32_t type = entry.is_directory ? 0 : 1;
                response(ipc, capacity, r, &type, 4, 0, 0); return 0;
            }
            if (r->payload_size < 4 || load32(r->payload) != 1 || entry.is_directory)
                return problem(error, cap, "RomFS.OpenFile supports existing regular files in read-only mode=1");
            Object *file = new_object(OBJECT_ROMFS_FILE);
            if (!file) return problem(error, cap, "allocate RomFS file object failed");
            size_t length = (size_t)(end - path) + 1; file->file_path = malloc(length);
            if (!file->file_path) { release_object(file); return problem(error, cap, "allocate RomFS file path failed"); }
            memcpy(file->file_path, path, length); file->romfs = o->romfs;
            return return_object(s, session, file, ipc, capacity, r, 0, error, cap);
        }
    } else if (o->kind == OBJECT_ROMFS_STORAGE || o->kind == OBJECT_ROMFS_FILE) {
        if (r->pid || r->copy_count || r->input_count) return problem(error, cap, "read-only RomFS object does not accept PID/handles/input buffers");
        uint64_t size = 0;
        botw_romfs_error backend = {0};
        if (o->kind == OBJECT_ROMFS_STORAGE) size = botw_romfs_image_size(o->romfs);
        else {
            botw_romfs_entry entry;
            if (botw_romfs_stat(o->romfs, o->file_path, &entry, &backend)) return fs_error_response(ipc, capacity, r, &backend, error, cap);
            size = entry.size;
        }
        if (r->command == 4) {
            if (r->output_count) return problem(error, cap, "RomFS.GetSize does not accept output buffers");
            response(ipc, capacity, r, &size, 8, 0, 0); return 0;
        }
        if (r->command == 0) {
            unsigned file = o->kind == OBJECT_ROMFS_FILE;
            size_t input_offset = file ? 8 : 0;
            if (r->payload_size < input_offset + 16 || (file && (load32(r->payload) || load32(r->payload + 4))))
                return problem(error, cap, "RomFS.Read requires offset/length and supported option=0");
            uint64_t offset = load64(r->payload + input_offset), length = load64(r->payload + input_offset + 8);
            if (offset > INT64_MAX || length > SIZE_MAX || (length && (r->output_count != 1 || length > r->output_size)) || (!length && r->output_count > 1))
                return problem(error, cap, "RomFS.Read invalid offset/length/output descriptor offset=%llu length=%llu", (unsigned long long)offset, (unsigned long long)length);
            if (!file && (offset > size || length > size - offset)) return problem(error, cap, "RomFS IStorage.Read exceeds image bounds");
            uint64_t wanted = length;
            if (file && (offset >= size || wanted > size - offset)) wanted = offset >= size ? 0 : size - offset;
            if (prepare_host_output(s, r->output_address, (size_t)wanted,
                    "RomFS.Read", error, cap)) return -1;
            size_t done = 0;
            while ((uint64_t)done < wanted) {
                size_t chunk = wanted - done > BUFFER_LIMIT ? BUFFER_LIMIT : (size_t)(wanted - done), actual = 0;
                void *destination = (void *)(uintptr_t)(r->output_address + done);
                int failed = file ? botw_romfs_pread(o->romfs, o->file_path, offset + done, destination, chunk, &actual, &backend) :
                    botw_romfs_raw_pread(o->romfs, offset + done, destination, chunk, &actual, &backend);
                if (failed) return fs_error_response(ipc, capacity, r, &backend, error, cap);
                if (actual != chunk) return problem(error, cap, "RomFS immutable backend returned unexpected short read");
                done += actual;
            }
            uint64_t actual = done;
            response(ipc, capacity, r, file ? &actual : NULL, file ? 8 : 0, 0, 0);
            emit(s, "romfs_read object=%s offset=%llu bytes=%zu", file ? o->file_path : "raw-image", (unsigned long long)offset, done); return 0;
        }
    }
    return problem(error, cap, "unsupported read-only IPC service=%s command=%u", kind_name(o->kind), r->command);
}
static int hid_request(botw_services *s, Session *session, Object *o, uint8_t *ipc, size_t capacity, const Request *r, char *error, size_t cap) {
    if (!s->input || !s->input_shared_memory_handle || !s->applet_ready) return problem(error, cap, "hid requires real host input, shared-memory handle and applet resource identity");
    if (r->copy_count || r->output_count) return problem(error, cap, "hid unexpected copy handle/output buffer");
    if (o->kind == OBJECT_HID_APPLET_RESOURCE && r->command == 0) {
        if (r->pid || r->input_count || !s->kernel.duplicate_handle) return problem(error, cap, "hid.GetSharedMemoryHandle requires kernel duplicate API and no inputs");
        uint32_t handle = 0;
        if (s->kernel.duplicate_handle(s->kernel.opaque, s->input_shared_memory_handle, &handle, error, cap)) return -1;
        response(ipc, capacity, r, NULL, 0, handle, 0); store32(ipc + 8, 1u << 1); return 0;
    }
    if (o->kind != OBJECT_HID) return problem(error, cap, "unsupported hid applet resource command=%u", r->command);
    size_t user_offset = (r->command == 100 || r->command == 106 || r->command == 107 || r->command == 109) ? 8 : 0;
    if (!r->pid || r->payload_size < user_offset + 8 || load64(r->payload + user_offset) != s->applet_resource_user_id)
        return problem(error, cap, "hid command=%u requires PID and matching applet resource user ID", r->command);
    if (r->input_count && r->command != 102) return problem(error, cap, "unexpected hid input buffer command=%u", r->command);
    if (r->command == 0) {
        Object *resource = new_object(OBJECT_HID_APPLET_RESOURCE);
        if (!resource) return problem(error, cap, "allocate hid.IAppletResource failed");
        return return_object(s, session, resource, ipc, capacity, r, 0, error, cap);
    }
    if (r->command == 1) {
        /* Old SDK ActivateDebugPad: PID + one u64 ARUID, no buffers/handles
         * or output objects. Empty auto-select descriptors are still foreign
         * parameters for this method. Validate everything before publishing. */
        if (r->buffer_descriptor_count) return problem(error, cap, "hid.ActivateDebugPad does not accept buffer descriptors");
        /* PID-only raw prefix is 20 bytes. CMIF alignment consumes 12 of
         * the builder's 16 reserved bytes, leaving exactly four framing bytes.
         * The original SDK does not initialize that tail. Domain data_size
         * already removes framing padding, so its payload must be exact. */
        if (r->domain ? r->payload_size != 8 : (r->payload_size != 8 && r->payload_size != 12))
            return problem(error, cap, "hid.ActivateDebugPad requires u64 ARUID and at most four non-domain framing bytes");
        uint64_t ticks = botw_input_monotonic_ticks();
        if (!ticks) return problem(error, cap, "hid.ActivateDebugPad requires the actual host input clock");
        if (botw_input_activate_debug_pad(s->input, ticks)) return problem(error, cap, "host rejected DebugPad activation");
        response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (r->command == 100 || r->command == 102 || r->command == 103 || r->command == 104 || r->command == 109) {
        uint32_t styles = s->hid_supported_styles, ids[10]; size_t count = s->hid_npad_count;
        memcpy(ids, s->hid_npad_ids, sizeof ids); unsigned active = s->hid_npad_active;
        if (r->command == 100) { styles = load32(r->payload); if (styles & ~31u) return problem(error, cap, "hid invalid supported Npad style set=0x%x", styles); }
        if (r->command == 102) {
            if (r->input_size > sizeof ids || r->input_size % 4) return problem(error, cap, "hid Npad ID buffer must contain at most ten u32 IDs");
            count = r->input_size / 4;
            if (count) memcpy(ids, (const void *)(uintptr_t)r->input_address, r->input_size);
        }
        if (r->command == 103 || r->command == 109) active = 1;
        if (r->command == 109 && load32(r->payload) != 0) return problem(error, cap, "HID backend supports old SDK Npad revision=0, requested=%u", load32(r->payload));
        if (r->command == 104) active = 0;
        if (botw_input_configure(s->input, active ? styles : 0, ids, count)) return problem(error, cap, "host input rejected Npad styles/duplicate or invalid IDs");
        s->hid_supported_styles = styles; memcpy(s->hid_npad_ids, ids, sizeof ids); s->hid_npad_count = count; s->hid_npad_active = active;
        botw_input_sample(s->input, botw_input_monotonic_ticks());
        if (refresh_input(s, error, cap)) return -1;
        response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (r->command == 101) { response(ipc, capacity, r, &s->hid_supported_styles, 4, 0, 0); return 0; }
    if (r->command == 106) {
        if (r->payload_size < 24 || !s->kernel.create_event || !s->kernel.duplicate_handle || !s->kernel.signal_event || !s->kernel.close_handle)
            return problem(error, cap, "AcquireNpadStyleSetUpdateEventHandle requires Npad ID, reserved event pointer and kernel event API");
        int index = npad_index(load32(r->payload));
        if (index < 0) return problem(error, cap, "invalid Npad style event ID=0x%x", load32(r->payload));
        if (!s->hid_style_event_read[index]) {
            if (s->kernel.create_event(s->kernel.opaque, 1, &s->hid_style_event_write[index], &s->hid_style_event_read[index], error, cap)) return -1;
            s->hid_last_style[index] = botw_input_style(s->input, load32(r->payload));
        }
        uint32_t handle = 0;
        if (s->kernel.duplicate_handle(s->kernel.opaque, s->hid_style_event_read[index], &handle, error, cap)) return -1;
        response(ipc, capacity, r, NULL, 0, handle, 0); store32(ipc + 8, 1u << 1); return 0;
    }
    if (r->command == 107) {
        if (botw_input_disconnect(s->input, load32(r->payload))) return problem(error, cap, "DisconnectNpad invalid ID=0x%x", load32(r->payload));
        botw_input_sample(s->input, botw_input_monotonic_ticks());
        if (refresh_input(s, error, cap)) return -1;
        response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (r->command == 120) {
        if (r->payload_size < 16 || load64(r->payload + 8) > 1) return problem(error, cap, "NpadJoyHoldType requires Vertical=0 or Horizontal=1");
        s->hid_joy_hold = load64(r->payload + 8); response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (r->command == 121) { response(ipc, capacity, r, &s->hid_joy_hold, 8, 0, 0); return 0; }
    return problem(error, cap, "unsupported IPC service=hid command=%u", r->command);
}
static int aoc_request(botw_services *s, uint8_t *ipc, size_t capacity, const Request *r, char *error, size_t cap) {
    if (!s->aoc_ready) return problem(error, cap, "aoc:u requires actual installed content state from host");
    if (r->input_count || r->copy_count) return problem(error, cap, "aoc:u unexpected input buffer/handle command=%u", r->command);
    if (r->command == 8 || r->command == 10) {
        if (r->output_count || (r->command == 10 ? !r->pid || r->payload_size < 8 : r->pid)) return problem(error, cap, "invalid AOC list-changed event request");
        if (!s->kernel.create_event || !s->kernel.duplicate_handle || !s->kernel.signal_event || !s->kernel.close_handle)
            return problem(error, cap, "AOC list-changed event requires real kernel events");
        if (!s->aoc_event_read && s->kernel.create_event(s->kernel.opaque, 0, &s->aoc_event_write, &s->aoc_event_read, error, cap)) return -1;
        uint32_t handle = 0;
        if (s->kernel.duplicate_handle(s->kernel.opaque, s->aoc_event_read, &handle, error, cap)) return -1;
        response(ipc, capacity, r, NULL, 0, handle, 0); store32(ipc + 8, 1u << 1); return 0;
    }
    if (r->command <= 5) {
        unsigned list = r->command == 1 || r->command == 3, by_process = r->command == 2 || r->command == 3 || r->command == 5;
        size_t identity_offset = list ? 8 : 0;
        if (r->payload_size < identity_offset + 8 || (by_process ? !r->pid : r->pid || load64(r->payload + identity_offset) != s->aoc_application_id))
            return problem(error, cap, "aoc:u command=%u requires actual application identity or current-process PID descriptor", r->command);
        if (r->command == 4 || r->command == 5) {
            if (!s->aoc_base_id || r->output_count) return problem(error, cap, "AOC base ID unavailable: host must publish actual control metadata");
            response(ipc, capacity, r, &s->aoc_base_id, 8, 0, 0); return 0;
        }
        if (!list) {
            if (r->output_count) return problem(error, cap, "CountAddOnContent does not accept output buffers");
            uint32_t count = (uint32_t)s->aoc_installed_count;
            response(ipc, capacity, r, &count, 4, 0, 0); return 0;
        }
        uint32_t offset = load32(r->payload), requested = load32(r->payload + 4);
        if (offset > INT32_MAX || requested > INT32_MAX || (requested && (r->output_count != 1 || requested > r->output_size / 4)))
            return problem(error, cap, "ListAddOnContent invalid offset/count/output buffer");
        uint32_t available = offset >= s->aoc_installed_count ? 0 : (uint32_t)s->aoc_installed_count - offset;
        if (available > requested) available = requested;
        if (available) memcpy((void *)(uintptr_t)r->output_address, s->aoc_installed_ids + offset, available * 4);
        response(ipc, capacity, r, &available, 4, 0, 0); return 0;
    }
    return problem(error, cap, "unsupported aoc:u command=%u installed_count=%zu (no DLC entitlement inferred)", r->command, s->aoc_installed_count);
}
static int parental_request(botw_services *s, Session *session, Object *o, uint8_t *ipc, size_t capacity, const Request *r, char *error, size_t cap) {
    if (!s->parental_ready) return problem(error, cap, "pctl:a requires explicit user-controlled local parental policy");
    if (r->input_count || r->output_count || r->copy_count) return problem(error, cap, "pctl unexpected buffers/handles command=%u", r->command);
    if (o->kind == OBJECT_PCTL_FACTORY && (r->command == 0 || r->command == 1)) {
        if (!r->pid || r->payload_size < 8) return problem(error, cap, "CreateParentalControlService requires PID descriptor and u64 placeholder");
        Object *service = new_object(OBJECT_PCTL_SERVICE);
        if (!service) return problem(error, cap, "allocate parental control service failed");
        service->initialized = r->command == 0;
        return return_object(s, session, service, ipc, capacity, r, 0, error, cap);
    }
    if (o->kind != OBJECT_PCTL_SERVICE || r->pid) return problem(error, cap, "unsupported parental factory command=%u or unexpected PID", r->command);
    if (r->command == 1) {
        if (o->initialized) return problem(error, cap, "parental control service already initialized");
        o->initialized = 1; response(ipc, capacity, r, NULL, 0, 0, 0); return 0;
    }
    if (!o->initialized) return problem(error, cap, "parental control service is not initialized");
    if (r->command == 1001 || r->command == 1004 || r->command == 1013) {
        unsigned denied = r->command == 1001 ? s->parental_communication : r->command == 1004 ? s->parental_sns : s->parental_stereo;
        response(ipc, capacity, r, NULL, 0, 0, 0);
        if (denied) store32(ipc + 16 + (r->domain ? 16 : 0) + 8, 142u | ((r->command == 1013 ? 104u : 133u) << 9));
        return 0;
    }
    if (r->command == 1006 || r->command == 1031 || r->command == 1062 || r->command == 1065 || r->command == 1403 || r->command == 1018) {
        uint8_t value = 0;
        if (r->command == 1031) value = !!(s->parental_communication || s->parental_sns || s->parental_stereo);
        if (r->command == 1062) value = (uint8_t)s->parental_stereo;
        if (r->command == 1065) value = !s->parental_stereo;
        if (r->command == 1018) value = !s->parental_communication;
        /* No PIN unlock or Nintendo pairing is created by a local policy. */
        response(ipc, capacity, r, &value, 1, 0, 0); return 0;
    }
    if (r->command == 1432) {
        if (!s->kernel.create_event || !s->kernel.duplicate_handle || !s->kernel.signal_event || !s->kernel.close_handle)
            return problem(error, cap, "parental policy synchronization requires real kernel event API");
        if (!s->parental_event_read && s->kernel.create_event(s->kernel.opaque, 0, &s->parental_event_write, &s->parental_event_read, error, cap)) return -1;
        uint32_t handle = 0;
        if (s->kernel.duplicate_handle(s->kernel.opaque, s->parental_event_read, &handle, error, cap)) return -1;
        response(ipc, capacity, r, NULL, 0, handle, 0); store32(ipc + 8, 1u << 1); return 0;
    }
    return problem(error, cap, "unsupported pctl command=%u policy_source=%s (no external account/PIN authorization inferred)", r->command, s->parental_source);
}
static int services_send(botw_services *s, uint32_t handle_id, void *buffer, size_t capacity,
        PointerTransfer *transfer, char *error, size_t cap) {
    Handle *handle = s ? find_handle(s, handle_id) : NULL;
    if (!handle) return problem(error, cap, "SendSyncRequest invalid or closed IPC handle=0x%x", handle_id);
    if (!handle->session) return problem(error, cap, "SendSyncRequest server endpoint closed for IPC handle=0x%x", handle_id);
    uint8_t *ipc = buffer; Session *session = handle->session; Request r = {0};
    if (parse_request(s, session, ipc, capacity, &r, error, cap)) return -1;
    if ((r.type == 2 || r.type == 5 || r.type == 7) && s->write_guest &&
            prepare_host_output(s, transfer->ipc_address, capacity, "IPC reply preflight", error, cap)) return -1;
    if (r.type == 2) {
        handle->session = NULL; release_session(session);
        emit(s, "ipc_session_closed handle=0x%x client_handle_live=true", handle_id);
        return 0;
    }
    if (r.type == 5 || r.type == 7) return control(s, session, ipc, capacity, &r, error, cap);
    Object *o = session->root;
    if (r.domain) {
        if (!r.object_id || r.object_id > DOMAIN_LIMIT || !session->objects[r.object_id - 1])
            return problem(error, cap, "CMIF invalid or closed domain object=%u", r.object_id);
        o = session->objects[r.object_id - 1];
        if (r.domain_close) {
            if (s->write_guest && prepare_host_output(s, transfer->ipc_address, capacity, "IPC domain-close reply preflight", error, cap)) return -1;
            session->objects[r.object_id - 1] = NULL;
            if (session->root == o) session->root = NULL;
            release_object(o); memset(ipc, 0, capacity); return 0;
        }
    }
    if (!o) return problem(error, cap, "CMIF session has no live root object");
    if (stage_pointers(s, o, &r, ipc, transfer, capacity, error, cap)) return -1;
    if (r.output_count && o->kind != OBJECT_STORAGE_ACCESSOR && o->kind != OBJECT_DISPLAY_CONTROLLER && o->kind != OBJECT_ROMFS_STORAGE && o->kind != OBJECT_ROMFS_FILE && o->kind != OBJECT_AOC && o->kind != OBJECT_ACCOUNT && o->kind != OBJECT_SAVE_FILE) return problem(error, cap, "unexpected output buffer service=%s command=%u", kind_name(o->kind), r.command);
    if (r.copy_count && o->kind != OBJECT_APPLET_OE) return problem(error, cap, "unexpected IPC copy handle service=%s command=%u handle=0x%x", kind_name(o->kind), r.command, r.copy_handle);
    /* Protected VM writes cannot enter the CPU dirty-fault handler. Preflight
     * the real reply span before logging, consuming storage or host I/O. This
     * does not promise a transaction against concurrent guest unmapping. */
    if (s->write_guest && prepare_host_output(s, transfer->ipc_address, capacity, "IPC reply preflight", error, cap)) return -1;
    trace_request(s, o->kind, &r, handle_id);
    if (o->kind == OBJECT_SM) {
        if (r.input_count) return problem(error, cap, "sm: command=%u unexpected input buffer", r.command);
        if (r.command == 0) {
            if (!r.pid || r.payload_size < 8 || o->initialized) return problem(error, cap, "sm: Initialize requires PID descriptor, u64 placeholder and uninitialized session");
            o->initialized = 1; o->pid = 1; response(ipc, capacity, &r, NULL, 0, 0, 0); return 0;
        }
        if (r.command == 1) {
            if (!o->initialized || r.payload_size < 8) return problem(error, cap, "sm: GetService requires initialized client and 8-byte service name");
            char name[9]; memcpy(name, r.payload, 8); name[8] = 0;
            enum ObjectKind service_kind;
            if (!memcmp(name, "lm\0\0\0\0\0\0", 8)) service_kind = OBJECT_LM;
            else if (!memcmp(name, "apm\0\0\0\0\0", 8)) service_kind = OBJECT_APM;
            else if (!memcmp(name, "appletOE", 8)) service_kind = OBJECT_APPLET_OE;
            else if (!memcmp(name, "fsp-srv\0", 8)) service_kind = OBJECT_FSP;
            else if (!memcmp(name, "hid\0\0\0\0\0", 8)) service_kind = OBJECT_HID;
            else if (!memcmp(name, "aoc:u\0\0\0", 8)) service_kind = OBJECT_AOC;
            else if (!memcmp(name, "pctl:a\0\0", 8)) service_kind = OBJECT_PCTL_FACTORY;
            else if (!memcmp(name, "acc:u0\0\0", 8)) service_kind = OBJECT_ACCOUNT;
            else return problem(error, cap, "unsupported sm: GetService name=%s bytes=%02x%02x%02x%02x%02x%02x%02x%02x", name, r.payload[0], r.payload[1], r.payload[2], r.payload[3], r.payload[4], r.payload[5], r.payload[6], r.payload[7]);
            Object *service = new_object(service_kind); if (!service) return problem(error, cap, "allocate service=%s object failed", name);
            return return_object(s, session, service, ipc, capacity, &r, 1, error, cap);
        }
    } else if (o->kind == OBJECT_SAVE_FILESYSTEM || o->kind == OBJECT_SAVE_FILE) {
        return save_request(s, session, o, ipc, capacity, &r, error, cap);
    } else if (o->kind == OBJECT_ACCOUNT || o->kind == OBJECT_ACCOUNT_CONTEXT) {
        return account_request(s, session, o, ipc, capacity, &r, error, cap);
    } else if (o->kind == OBJECT_PCTL_FACTORY || o->kind == OBJECT_PCTL_SERVICE) {
        return parental_request(s, session, o, ipc, capacity, &r, error, cap);
    } else if (o->kind == OBJECT_AOC) {
        return aoc_request(s, ipc, capacity, &r, error, cap);
    } else if (o->kind == OBJECT_HID || o->kind == OBJECT_HID_APPLET_RESOURCE) {
        return hid_request(s, session, o, ipc, capacity, &r, error, cap);
    } else if (o->kind >= OBJECT_FSP && o->kind <= OBJECT_ROMFS_FILE) {
        return fs_request(s, session, o, ipc, capacity, &r, error, cap);
    } else if (o->kind >= OBJECT_APPLET_OE && o->kind <= OBJECT_DEBUG_FUNCTIONS) {
        return applet_request(s, session, o, ipc, capacity, &r, error, cap);
    } else if (o->kind == OBJECT_APM) {
        if (r.input_count || r.pid) return problem(error, cap, "apm manager command=%u unexpected buffers/PID", r.command);
        if (r.command == 0) {
            Object *apm_session = new_object(OBJECT_APM_SESSION);
            if (!apm_session) return problem(error, cap, "allocate apm.ISession failed");
            return return_object(s, session, apm_session, ipc, capacity, &r, 0, error, cap);
        }
        if (r.command == 1) {
            /* iPhone host exposes the handheld Normal mode. Dock/Boost cannot
             * be inferred from a desktop display or a charging connection. */
            response(ipc, capacity, &r, &s->performance_mode, 4, 0, 0); return 0;
        }
    } else if (o->kind == OBJECT_APM_SESSION) {
        if (r.input_count || r.pid) return problem(error, cap, "apm.ISession command=%u unexpected buffers/PID", r.command);
        if (r.command == 0 || r.command == 1) {
            if (r.payload_size < (r.command == 0 ? 8u : 4u)) return problem(error, cap, "apm.ISession command=%u truncated input", r.command);
            uint32_t mode = load32(r.payload);
            if (mode > 1) return problem(error, cap, "apm.ISession invalid performance mode=%u", mode);
            if (r.command == 0) {
                uint32_t configuration = load32(r.payload + 4);
                if (!s->performance_handler) return problem(error, cap, "apm.SetPerformanceConfiguration requires host performance handler mode=%u configuration=0x%x", mode, configuration);
                if (error && cap) error[0] = 0;
                if (s->performance_handler(s->performance_opaque, mode, configuration, mode == s->performance_mode, error, cap)) {
                    if (error && cap && error[0]) return -1;
                    return problem(error, cap, "host rejected apm performance mode=%u configuration=0x%x", mode, configuration);
                }
                s->performance_configuration[mode] = configuration; s->performance_configured[mode] = 1;
                emit(s, "apm_configuration mode=%u configuration=0x%x active=%s host_policy_accepted=true", mode, configuration, mode == s->performance_mode ? "true" : "false");
                response(ipc, capacity, &r, NULL, 0, 0, 0); return 0;
            }
            if (!s->performance_configured[mode]) return problem(error, cap, "apm.GetPerformanceConfiguration has no configured host policy mode=%u", mode);
            response(ipc, capacity, &r, &s->performance_configuration[mode], 4, 0, 0); return 0;
        }
    } else if (o->kind == OBJECT_LM && r.command == 0) {
        if (r.input_count || !r.pid || r.payload_size < 8) return problem(error, cap, "lm.OpenLogger requires PID descriptor and u64 placeholder");
        Object *logger = new_object(OBJECT_LOGGER); if (!logger) return problem(error, cap, "allocate ILogger failed");
        logger->pid = 1; return return_object(s, session, logger, ipc, capacity, &r, 0, error, cap);
    } else if (o->kind == OBJECT_LOGGER) {
        if (r.command == 0) {
            if (logger_packet(s, &r, error, cap)) return -1;
            response(ipc, capacity, &r, NULL, 0, 0, 0); return 0;
        }
        if (r.command == 1) {
            if (r.payload_size < 4 || r.input_count || (load32(r.payload) != 0xffff && (load32(r.payload) & ~7u))) return problem(error, cap, "ILogger.SetDestination requires a valid u32 destination mask");
            s->log_destination = load32(r.payload); emit(s, "guest_log_destination mask=0x%x host_sink=event", s->log_destination);
            response(ipc, capacity, &r, NULL, 0, 0, 0); return 0;
        }
    }
    return problem(error, cap, "unsupported IPC service=%s command=%u", kind_name(o->kind), r.command);
}

/* Service -> kernel is the sole lock order. Kernel callbacks must never call
 * services again. Context destruction requires callers to join guest threads. */
int botw_services_connect(botw_services *s, const char *name, size_t length, uint32_t *handle, char *error, size_t cap) {
    if (!s) return problem(error, cap, "ConnectToNamedPort requires service context");
    pthread_mutex_lock(&s->lock); int result = services_connect(s, name, length, handle, error, cap);
    pthread_mutex_unlock(&s->lock); return result;
}
int botw_services_close(botw_services *s, uint32_t handle, char *error, size_t cap) {
    if (!s) return problem(error, cap, "CloseHandle requires service context");
    pthread_mutex_lock(&s->lock); int result = services_close(s, handle, error, cap);
    pthread_mutex_unlock(&s->lock); return result;
}
int botw_services_send(botw_services *s, uint32_t handle, void *buffer, size_t capacity, char *error, size_t cap) {
    if (!s) return problem(error, cap, "SendSyncRequest requires service context");
    pthread_mutex_lock(&s->lock);
    int result = -1; Handle *entry = find_handle(s, handle);
    if (!buffer || capacity < 0x40 || capacity > IPC_LIMIT ||
            !s->memory(s->opaque, (uint64_t)(uintptr_t)buffer, capacity, 1)) {
        problem(error, cap, "SendSyncRequest IPC buffer is not checked guest writable memory or capacity is outside 0x40..0x100");
    } else if (!!s->read_guest != !!s->write_guest) {
        problem(error, cap, "IPC protected copies require both read and write callbacks");
    } else if (!entry || !entry->session) {
        problem(error, cap, "SendSyncRequest invalid or closed IPC handle=0x%x", handle);
    } else {
        uint8_t snapshot[IPC_LIMIT];
        PointerTransfer transfer = {.ipc_address=(uint64_t)(uintptr_t)buffer, .arena=entry->pointer_memory};
        if (!copy_from_guest(s, transfer.ipc_address, snapshot, capacity, error, cap)) {
            result = services_send(s, handle, snapshot, capacity, &transfer, error, cap);
            if (!result && transfer.output_written) {
                if (transfer.output_written > transfer.output_size ||
                        prepare_host_output(s, transfer.output_address, transfer.output_written, "IPC pointer copyback", error, cap)) result = -1;
            }
            if (!result && transfer.output_written) {
                uint8_t *output = transfer.arena + POINTER_CAPACITY - ((transfer.output_size + 15) & ~(size_t)15);
                result = copy_to_guest(s, transfer.output_address, output, transfer.output_written, error, cap);
            }
            if (!result) result = copy_to_guest(s, transfer.ipc_address, snapshot, capacity, error, cap);
        }
    }
    pthread_mutex_unlock(&s->lock); return result;
}
