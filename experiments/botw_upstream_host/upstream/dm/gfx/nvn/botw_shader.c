#include "botw_shader.h"
#include <CommonCrypto/CommonDigest.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define SHADER_MAGIC UINT32_C(0x98761234)
/* Parsed control fields end at 0x714, so 0x718 bytes are the minimum every
 * supported control must supply. */
#define CONTROL_HEADER_SIZE 0x718u
/* Opaque capture ceiling; an explicit resource policy, not a format bound. */
#define CONTROL_LIMIT (1024u * 1024u)
/* The extended-descriptor override is compared against control[0x768..0x77f]. */
#define CONTROL_EXT_OFFSET 0x768u
#define CONTROL_EXT_SIZE 24u
#define CODE_LIMIT (16u * 1024u * 1024u)
#define CACHE_LIMIT (512u * 1024u * 1024u)
#define BUCKETS 4096u

typedef struct shader_pool {
    const void *owner;
    uintptr_t cpu;
    uint64_t gpu;
    size_t size;
    struct shader_pool *next;
} shader_pool;

typedef struct shader_snapshot {
    botw_shader_info info;
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    uint8_t *control, *code;
    struct shader_snapshot *hash_next, *pointer_next;
} shader_snapshot;

static pthread_mutex_t shader_lock = PTHREAD_MUTEX_INITIALIZER;
static shader_pool *pools;
static shader_snapshot *hashes[BUCKETS], *pointers[BUCKETS];
static size_t cache_bytes;

static int fail(char *error, size_t n, const char *format, ...) {
    if (error && n) {
        va_list ap;
        va_start(ap, format);
        vsnprintf(error, n, format, ap);
        va_end(ap);
    }
    return 0;
}

static uint32_t u32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void le32(uint8_t *p, uint32_t v) {
    for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (8 * i));
}

static int read_safe(void *out, const void *in, size_t n) {
    if (!in || n > UINTPTR_MAX - (uintptr_t)in) return 0;
    mach_vm_size_t copied = 0;
    return mach_vm_read_overwrite(mach_task_self(), (mach_vm_address_t)in,
                                  n, (mach_vm_address_t)out, &copied) == KERN_SUCCESS && copied == n;
}

static void hex_digest(const uint8_t *digest, char out[65]) {
    static const char hex[] = "0123456789abcdef";
    for (unsigned i = 0; i < CC_SHA256_DIGEST_LENGTH; ++i) {
        out[2 * i] = hex[digest[i] >> 4];
        out[2 * i + 1] = hex[digest[i] & 15];
    }
    out[64] = 0;
}

static unsigned pointer_bucket(const void *p) {
    uintptr_t v = (uintptr_t)p;
    return (unsigned)((v >> 4) ^ (v >> 17)) & (BUCKETS - 1);
}

int botw_shader_pool_add(const void *owner, const void *cpu, uint64_t gpu,
                         size_t size, char *error, size_t error_size) {
    if (!owner || !cpu || !size || size > UINTPTR_MAX - (uintptr_t)cpu || size > UINT64_MAX - gpu)
        return fail(error, error_size, "invalid or overflowing shader memory pool range");
    shader_pool *fresh = malloc(sizeof *fresh);
    if (!fresh) return fail(error, error_size, "shader memory pool allocation failed");
    *fresh = (shader_pool){owner, (uintptr_t)cpu, gpu, size, NULL};
    pthread_mutex_lock(&shader_lock);
    shader_pool **at = &pools;
    while (*at && (*at)->owner != owner) at = &(*at)->next;
    if (*at) {
        shader_pool *old = *at;
        *at = old->next;
        free(old);
    }
    fresh->next = pools;
    pools = fresh;
    pthread_mutex_unlock(&shader_lock);
    return 1;
}

void botw_shader_pool_remove(const void *owner) {
    pthread_mutex_lock(&shader_lock);
    shader_pool **at = &pools;
    while (*at && (*at)->owner != owner) at = &(*at)->next;
    if (*at) {
        shader_pool *old = *at;
        *at = old->next;
        free(old);
    }
    pthread_mutex_unlock(&shader_lock);
}

/* Existing captures are verified, never overwritten. Symlinks and non-regular
 * files are rejected. JSON is written last and marks a complete capture. */
static int write_exact(int dir, const char *name, const void *data, size_t size,
                        char *error, size_t error_size) {
    int fd = openat(dir, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0 && errno == EEXIST) {
        fd = openat(dir, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
        if (fd < 0) return fail(error, error_size, "cannot verify existing shader capture %s: %s", name, strerror(errno));
        struct stat st;
        int ok = fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_size >= 0 && (uint64_t)st.st_size == size;
        uint8_t buf[4096];
        size_t off = 0;
        while (ok && off < size) {
            size_t want = size - off < sizeof buf ? size - off : sizeof buf;
            ssize_t got = read(fd, buf, want);
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0 || memcmp(buf, (const uint8_t *)data + off, (size_t)got)) { ok = 0; break; }
            off += (size_t)got;
        }
        close(fd);
        return ok ? 1 : fail(error, error_size, "existing shader capture differs: %s", name);
    }
    if (fd < 0) return fail(error, error_size, "cannot create shader capture %s: %s", name, strerror(errno));
    size_t off = 0;
    int ok = 1;
    while (off < size) {
        ssize_t n = write(fd, (const uint8_t *)data + off, size - off);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { ok = 0; break; }
        off += (size_t)n;
    }
    if (ok && fsync(fd)) ok = 0;
    if (close(fd)) ok = 0;
    if (!ok) {
        int saved = errno;
        unlinkat(dir, name, 0);
        return fail(error, error_size, "cannot finish shader capture %s: %s", name, strerror(saved));
    }
    return 1;
}

static int capture(const shader_snapshot *s, char *error, size_t error_size) {
    const char *path = getenv("DM_SHADER_CAPTURE");
    if (!path || !*path) return 1;
    if (path[0] != '/') return fail(error, error_size, "DM_SHADER_CAPTURE must be an absolute directory outside a repository");
    if (mkdir(path, 0700) && errno != EEXIST)
        return fail(error, error_size, "cannot create shader capture directory: %s", strerror(errno));
    char resolved[PATH_MAX];
    if (!realpath(path, resolved)) return fail(error, error_size, "cannot resolve shader capture directory: %s", strerror(errno));
    char parent[PATH_MAX];
    memcpy(parent, resolved, strlen(resolved) + 1);
    for (;;) {
        char marker[PATH_MAX];
        struct stat st;
        int n = snprintf(marker, sizeof marker, "%s/.git", parent);
        if (n < 0 || (size_t)n >= sizeof marker) return fail(error, error_size, "shader capture directory path is too long");
        if (!lstat(marker, &st)) return fail(error, error_size, "shader captures must stay outside a repository");
        char *slash = strrchr(parent, '/');
        if (!slash || slash == parent) break;
        *slash = 0;
    }
    int dir = open(resolved, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) return fail(error, error_size, "cannot open shader capture directory: %s", strerror(errno));
    const botw_shader_info *i = &s->info;
    char name[96], json[1536], ctl_hash[65], code_hash[65];
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(s->control, i->control_size, digest); hex_digest(digest, ctl_hash);
    CC_SHA256(s->code, i->code_size, digest); hex_digest(digest, code_hash);
    int n = snprintf(json, sizeof json,
        "{\n  \"schema\": \"botw-shader-v1\",\n  \"key\": \"%s\",\n  \"sha256\": \"%s\",\n"
        "  \"control_sha256\": \"%s\",\n  \"code_sha256\": \"%s\",\n"
        "  \"version\": %u,\n  \"stage\": %u,\n  \"control_size\": %u,\n  \"code_size\": %u,\n"
        "  \"code_offset\": %u,\n  \"c1_offset\": %u,\n  \"c1_size\": %u,\n"
        "  \"hash_metadata_le32\": [%u, %u, %u, %u, %u, %u, %u],\n"
        "  \"control_file\": \"%s.control\",\n  \"code_file\": \"%s.code\"\n}\n",
        i->key, i->sha256, ctl_hash, code_hash, i->version, i->stage, i->control_size, i->code_size,
        i->code_offset, i->c1_offset, i->c1_size, i->version, i->stage, i->control_size, i->code_size,
        i->code_offset, i->c1_offset, i->c1_size, i->key, i->key);
    int ok = n > 0 && (size_t)n < sizeof json;
    if (!ok) fail(error, error_size, "shader capture metadata overflow");
    snprintf(name, sizeof name, "%s.control", i->key);
    if (ok) ok = write_exact(dir, name, s->control, i->control_size, error, error_size);
    snprintf(name, sizeof name, "%s.code", i->key);
    if (ok) ok = write_exact(dir, name, s->code, i->code_size, error, error_size);
    snprintf(name, sizeof name, "%s.json", i->key);
    if (ok) ok = write_exact(dir, name, json, (size_t)n, error, error_size);
    close(dir);
    return ok;
}

static int snapshot(const NVNshaderData *input, const void *extra, NVNshaderData *output,
                         char *error, size_t error_size) {
    if (error && error_size) error[0] = 0;
    if (!output) return fail(error, error_size, "missing shader snapshot output");
    NVNshaderData sd;
    uint8_t prefix[0x24];
    if (!read_safe(&sd, input, sizeof sd) || !read_safe(prefix, sd.control, sizeof prefix))
        return fail(error, error_size, "unreadable shader descriptor or control header");
    uint32_t version = u32(prefix + 8), size = u32(prefix + 0x20);
    if (u32(prefix) != SHADER_MAGIC || u32(prefix + 4) != 1 || (version != 9 && version != 14))
        return fail(error, error_size, "unsupported shader control magic or version");
    /* Preserve opaque tails instead of requiring the lengths of earlier samples.
     * These bounds cover our reads and limit capture allocation; they do not
     * establish that every accepted length is a valid native format. */
    if (size < CONTROL_HEADER_SIZE || size > CONTROL_LIMIT)
        return fail(error, error_size, "shader control size %#x outside supported range [%#x, %#x]",
                    size, (unsigned)CONTROL_HEADER_SIZE, (unsigned)CONTROL_LIMIT);
    shader_snapshot *s = calloc(1, sizeof *s);
    if (!s) return fail(error, error_size, "shader snapshot allocation failed");
    s->control = malloc(size);
    int ok = 0;
    if (!s->control || !read_safe(s->control, sd.control, size)) {
        fail(error, error_size, "unreadable shader control body"); goto done;
    }
    if (memcmp(prefix, s->control, sizeof prefix)) {
        fail(error, error_size, "shader control header changed during capture"); goto done;
    }
    if (extra) {
        /* The comparison below reads control[0x768..0x77f]; a shorter
         * control cannot back that access and is rejected before it. */
        if (size < CONTROL_EXT_OFFSET + CONTROL_EXT_SIZE) {
            fail(error, error_size, "extended shader metadata needs control size >= %#x, got %#x",
                 (unsigned)(CONTROL_EXT_OFFSET + CONTROL_EXT_SIZE), size); goto done;
        }
        uint8_t override[CONTROL_EXT_SIZE];
        if (!read_safe(override, extra, sizeof override)) {
            fail(error, error_size, "unreadable extended shader metadata"); goto done;
        }
        if (memcmp(override, s->control + CONTROL_EXT_OFFSET, sizeof override)) {
            fail(error, error_size, "extended shader metadata differs from control; override semantics are unsupported"); goto done;
        }
    }
    botw_shader_info *i = &s->info;
    i->version = version; i->control_size = size;
    i->code_size = u32(s->control + 0x704);
    i->code_offset = u32(s->control + 0x708);
    i->c1_offset = u32(s->control + 0x700);
    i->stage = u32(s->control + 0x714);
    if (i->stage > 5 || !i->code_size || i->code_size > CODE_LIMIT || (i->code_size & 7) ||
        i->c1_offset > i->code_size || i->code_offset > i->c1_offset || (i->c1_offset & 7) ||
        (i->stage == 5 ? i->code_offset != 0 || i->c1_offset < 8 :
                        i->code_offset != 0x30 || i->c1_offset < 0x88)) {
        fail(error, error_size, "invalid shader stage, code size, header offset, or C1 bounds"); goto done;
    }
    i->c1_size = i->code_size - i->c1_offset;
    /* Preserve the full opaque tail in identity and capture. A raw capture
     * alone does not establish a runnable shader. */
    s->code = malloc(i->code_size);
    if (!s->code) { fail(error, error_size, "shader code allocation failed"); goto done; }
    pthread_mutex_lock(&shader_lock);
    const void *cpu = NULL;
    for (shader_pool *p = pools; p; p = p->next) {
        if (sd.data >= p->gpu && sd.data - p->gpu <= p->size && i->code_size <= p->size - (sd.data - p->gpu)) {
            cpu = (const void *)(p->cpu + (size_t)(sd.data - p->gpu));
            break;
        }
    }
    if (!cpu || !read_safe(s->code, cpu, i->code_size)) {
        pthread_mutex_unlock(&shader_lock);
        fail(error, error_size, "shader code is unreadable or outside a live memory pool"); goto done;
    }
    pthread_mutex_unlock(&shader_lock);
    if (i->stage != 5) {
        static const uint8_t sph_stage[5] = {1, 5, 4, 2, 3};
        if (u32(s->code) != UINT32_C(0x12345678) || ((u32(s->code + i->code_offset) >> 10) & 15) != sph_stage[i->stage]) {
            fail(error, error_size, "shader code wrapper or SPH stage does not match control"); goto done;
        }
    }
    /* SHA256(domain including NUL || seven LE32 fields || control || code).
     * The full digest is retained; the 16-character backend key is checked for
     * collisions before interning or writing capture files. */
    static const char domain[] = "botw-shader-v1";
    uint32_t values[7] = {i->version, i->stage, i->control_size, i->code_size,
                          i->code_offset, i->c1_offset, i->c1_size};
    uint8_t metadata[28];
    for (unsigned j = 0; j < 7; ++j) le32(metadata + 4 * j, values[j]);
    CC_SHA256_CTX hash;
    CC_SHA256_Init(&hash);
    CC_SHA256_Update(&hash, domain, sizeof domain);
    CC_SHA256_Update(&hash, metadata, sizeof metadata);
    CC_SHA256_Update(&hash, s->control, i->control_size);
    CC_SHA256_Update(&hash, s->code, i->code_size);
    CC_SHA256_Final(s->digest, &hash);
    hex_digest(s->digest, i->sha256);
    memcpy(i->key, i->sha256, 16); i->key[16] = 0;
    unsigned bucket = u32(s->digest) & (BUCKETS - 1);
    pthread_mutex_lock(&shader_lock);
    shader_snapshot *existing = hashes[bucket];
    while (existing && strcmp(existing->info.key, i->key)) existing = existing->hash_next;
    if (existing && memcmp(existing->digest, s->digest, sizeof s->digest)) {
        fail(error, error_size, "shader key collision for %s", i->key);
    } else if (!existing && sizeof *s + (size_t)i->control_size + i->code_size > CACHE_LIMIT - cache_bytes) {
        fail(error, error_size, "shader snapshot cache limit reached");
    } else if (!capture(existing ? existing : s, error, error_size)) {
        /* Capture failure is visible to the caller; it must not claim success. */
    } else if (existing) {
        *output = (NVNshaderData){(NVNbufferAddress)existing->code, existing->control};
        ok = 1;
    } else {
        cache_bytes += sizeof *s + (size_t)i->control_size + i->code_size;
        s->hash_next = hashes[bucket]; hashes[bucket] = s;
        unsigned pb = pointer_bucket(s->control);
        s->pointer_next = pointers[pb]; pointers[pb] = s;
        *output = (NVNshaderData){(NVNbufferAddress)s->code, s->control};
        s = NULL;
        ok = 1;
    }
    pthread_mutex_unlock(&shader_lock);
done:
    if (s) { free(s->control); free(s->code); free(s); }
    return ok;
}

int botw_shader_get_info(const NVNshaderData *snapshot, botw_shader_info *info) {
    if (!snapshot || !info) return 0;
    pthread_mutex_lock(&shader_lock);
    shader_snapshot *s = pointers[pointer_bucket(snapshot->control)];
    while (s && (s->control != snapshot->control || (NVNbufferAddress)s->code != snapshot->data)) s = s->pointer_next;
    if (s) *info = s->info;
    pthread_mutex_unlock(&shader_lock);
    return s != NULL;
}

void botw_shader_unsupported(const char *reason, const char *key) {
    fprintf(stderr, "UNIMPL BotW shader%s%s: %s\n", key ? " " : "", key ? key : "", reason ? reason : "unsupported operation");
    const char *stop = getenv("DM_STOP_ON_UNIMPL");
    fflush(stderr);
    if (stop && *stop && strcmp(stop, "0")) _exit(3);
    abort();
}

int botw_shader_snapshot(const NVNshaderData *input, NVNshaderData *output,
                         char *error, size_t error_size) {
    return snapshot(input, NULL, output, error, error_size);
}
int botw_shader_snapshot_ext(const NVNshaderDataExt *input, NVNshaderData *output,
                             char *error, size_t error_size) {
    _Static_assert(sizeof(NVNshaderDataExt) == 24 && offsetof(NVNshaderDataExt, extra) == 16,
                   "BotW extended shader descriptor ABI");
    NVNshaderDataExt ext;
    if (!read_safe(&ext, input, sizeof ext)) return fail(error, error_size, "unreadable extended shader descriptor");
    NVNshaderData base = {ext.data, ext.control};
    return snapshot(&base, ext.extra, output, error, error_size);
}
