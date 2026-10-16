#include "botw_shader_observation.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct {
    char *data;
    size_t size, capacity;
} json_buffer;

static int fail(char *error, size_t error_size, const char *format, ...) {
    if (error && error_size) {
        va_list ap;
        va_start(ap, format);
        vsnprintf(error, error_size, format, ap);
        va_end(ap);
    }
    return -1;
}

static int exact_zero(const char *name) {
    const char *value = getenv(name);
    return value && !strcmp(value, "0");
}

int botw_shader_observation_environment(const char **path, const char **selector, char *error, size_t error_size) {
    const char *output = getenv("DM_SHADER_OBSERVATION");
    const char *sha = getenv("DM_SHADER_OBSERVATION_SHA256");
    if (path) *path = NULL;
    if (selector) *selector = NULL;
    if (error && error_size) error[0] = 0;
    if (sha && !*sha)
        return fail(error, error_size, "DM_SHADER_OBSERVATION_SHA256 must not be empty; unset it for first-draw observation");
    if (sha && *sha && (!output || !*output))
        return fail(error, error_size, "DM_SHADER_OBSERVATION_SHA256 requires the DM_SHADER_OBSERVATION output path");
    if (!output || !*output) return 0;
    if (output[0] != '/') return fail(error, error_size, "DM_SHADER_OBSERVATION must be an absolute path outside a repository");
    if (sha && *sha) {
        if (strpbrk(sha, ",;: \t"))
            return fail(error, error_size, "DM_SHADER_OBSERVATION_SHA256 accepts a single digest, not a list");
        size_t length = strlen(sha);
        if (length != 64)
            return fail(error, error_size, "DM_SHADER_OBSERVATION_SHA256 must be exactly 64 lowercase hex characters (got %zu)", length);
        for (size_t i = 0; i < length; ++i) {
            char c = sha[i];
            if (c >= 'A' && c <= 'F')
                return fail(error, error_size, "DM_SHADER_OBSERVATION_SHA256 must use lowercase hex characters");
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
                return fail(error, error_size, "DM_SHADER_OBSERVATION_SHA256 must use only lowercase hex characters");
        }
    } else if (sha) {
        return fail(error, error_size, "DM_SHADER_OBSERVATION_SHA256 must not be empty; unset it for first-draw observation");
    }
    static const char *required[] = {
        "DM_NVN_TBDR", "DM_NVN_ZSKIP", "DM_NVN_DEFER", "DM_NVN_BLOOM_Q", "DM_RT_AB",
    };
    for (size_t i = 0; i < sizeof required / sizeof *required; ++i)
        if (!exact_zero(required[i]))
            return fail(error, error_size, "%s=0 is required for first-draw observation", required[i]);
    if (path) *path = output;
    if (selector) *selector = sha && *sha ? sha : NULL;
    return 1;
}

static int reserve(json_buffer *buffer, size_t extra) {
    if (extra > SIZE_MAX - buffer->size - 1) return 0;
    size_t need = buffer->size + extra + 1;
    if (need <= buffer->capacity) return 1;
    size_t capacity = buffer->capacity ? buffer->capacity : 4096;
    while (capacity < need) {
        if (capacity > SIZE_MAX / 2) { capacity = need; break; }
        capacity *= 2;
    }
    char *data = realloc(buffer->data, capacity);
    if (!data) return 0;
    buffer->data = data;
    buffer->capacity = capacity;
    return 1;
}

static int append(json_buffer *buffer, const char *format, ...) {
    va_list ap, copy;
    va_start(ap, format);
    va_copy(copy, ap);
    int count = vsnprintf(NULL, 0, format, copy);
    va_end(copy);
    if (count < 0 || !reserve(buffer, (size_t)count)) { va_end(ap); return 0; }
    vsnprintf(buffer->data + buffer->size, buffer->capacity - buffer->size, format, ap);
    va_end(ap);
    buffer->size += (size_t)count;
    return 1;
}

static int hex_string(const char *value, size_t length) {
    if (!value || strlen(value) != length) return 0;
    for (size_t i = 0; i < length; ++i) {
        char c = value[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return 0;
    }
    return 1;
}

static int append_attachment(json_buffer *json, const botw_shader_observation_attachment *a) {
    return append(json,
        "{\"slot\":%u,\"storage\":{\"target\":%d,\"format\":%d,\"width\":%u,\"height\":%u,"
        "\"depth\":%u,\"levels\":%u,\"samples\":%u},\"view\":{\"present\":%s,\"set_target\":%s,"
        "\"set_format\":%s,\"set_levels\":%s,\"set_layers\":%s,\"target\":%d,\"format\":%d,"
        "\"base_level\":%d,\"level_count\":%d,\"min_layer\":%d,\"layer_count\":%d},"
        "\"effective\":{\"target\":%d,\"format\":%d}}",
        a->slot, a->storage_target, a->storage_format, a->width, a->height, a->depth, a->levels, a->samples,
        a->has_view ? "true" : "false", a->view_set_target ? "true" : "false",
        a->view_set_format ? "true" : "false", a->view_set_levels ? "true" : "false",
        a->view_set_layers ? "true" : "false", a->view_target, a->view_format,
        a->view_base_level, a->view_level_count, a->view_min_layer, a->view_layer_count,
        a->effective_target, a->effective_format);
}

static int append_binding(json_buffer *json, const botw_shader_observation_binding *b) {
    return append(json,
        "{\"kind\":\"%s\",\"stage\":%u,\"slot\":%u,\"texture_id\":%u,\"sampler_id\":%u,"
        "\"registered\":%s,\"storage_target\":%d,\"storage_format\":%d,\"view\":{\"present\":%s,"
        "\"set_target\":%s,\"set_format\":%s,\"set_levels\":%s,\"set_layers\":%s,\"target\":%d,"
        "\"format\":%d,\"base_level\":%d,\"level_count\":%d,\"min_layer\":%d,\"layer_count\":%d},"
        "\"effective_target\":%d,\"effective_format\":%d}",
        b->kind == BOTW_SHADER_OBSERVATION_IMAGE ? "image" : "texture", b->stage, b->slot,
        b->texture_id, b->sampler_id, b->registered ? "true" : "false", b->storage_target, b->storage_format,
        b->has_view ? "true" : "false", b->view_set_target ? "true" : "false",
        b->view_set_format ? "true" : "false", b->view_set_levels ? "true" : "false",
        b->view_set_layers ? "true" : "false", b->view_target, b->view_format,
        b->view_base_level, b->view_level_count, b->view_min_layer, b->view_layer_count,
        b->effective_target, b->effective_format);
}

static int build_json(const botw_shader_observation *o, json_buffer *json,
                      char *error, size_t error_size) {
    if (!o || !o->stage_count || o->stage_count > BOTW_SHADER_OBSERVATION_MAX_STAGES ||
        o->color_count > 8 || o->binding_count > BOTW_SHADER_OBSERVATION_MAX_BINDINGS ||
        o->attribute_count > BOTW_SHADER_OBSERVATION_MAX_VERTEX_SLOTS ||
        o->stream_count > BOTW_SHADER_OBSERVATION_MAX_VERTEX_SLOTS ||
        o->vertex_buffer_count > BOTW_SHADER_OBSERVATION_MAX_VERTEX_SLOTS)
        return fail(error, error_size, "invalid first-draw observation counts");
    if (o->has_viewport) for (unsigned i = 0; i < 4; ++i)
        if (!isfinite(o->viewport[i])) return fail(error, error_size, "non-finite viewport in draw observation");
    for (uint32_t i = 0; i < o->stage_count; ++i)
        if (!hex_string(o->stages[i].key, 16) || !hex_string(o->stages[i].sha256, 64) || o->stages[i].stage >= 6)
            return fail(error, error_size, "invalid shader identity in first-draw observation");

    int ok = append(json,
        "{\n  \"schema\": \"botw-shader-draw-observation-v1\",\n  \"kind\": \"graphics_draw\",\n"
        "  \"inference\": \"none\",\n  \"device_state\": {\"origin\": %u, \"depth_mode\": %u},\n"
        "  \"draw\": {\"indexed\": %s, \"primitive\": %u, \"first\": %u, \"count\": %u,"
        " \"index_type\": %u, \"base_vertex\": %d, \"base_instance\": %d, \"instances\": %u},\n"
        "  \"shaders\": [",
        o->origin_mode, o->depth_mode, o->indexed ? "true" : "false", o->primitive, o->first, o->count,
        o->index_type, o->base_vertex, o->base_instance, o->instances);
    for (uint32_t i = 0; ok && i < o->stage_count; ++i)
        ok = append(json, "%s{\"stage\":%u,\"key\":\"%s\",\"sha256\":\"%s\"}",
                    i ? "," : "", o->stages[i].stage, o->stages[i].key, o->stages[i].sha256);
    ok = ok && append(json, "],\n  \"render_targets\": {\"colors\":[");
    for (uint32_t i = 0; ok && i < o->color_count; ++i) {
        if (i) ok = append(json, ",");
        if (ok) ok = append_attachment(json, &o->colors[i]);
    }
    ok = ok && append(json, "],\"depth\":");
    if (ok) ok = o->has_depth ? append_attachment(json, &o->depth) : append(json, "null");
    ok = ok && append(json, "},\n  \"bindings\": [");
    for (uint32_t i = 0; ok && i < o->binding_count; ++i) {
        if (i) ok = append(json, ",");
        if (ok) ok = append_binding(json, &o->bindings[i]);
    }
    ok = ok && append(json, "],\n  \"vertex_state\": {\"attributes\":[");
    for (uint32_t i = 0; ok && i < o->attribute_count; ++i) {
        const botw_shader_observation_attribute *a = &o->attributes[i];
        ok = append(json, "%s{\"slot\":%u,\"format\":%u,\"offset\":%u,\"stream\":%u}",
                    i ? "," : "", a->slot, a->format, a->offset, a->stream);
    }
    ok = ok && append(json, "],\"streams\":[");
    for (uint32_t i = 0; ok && i < o->stream_count; ++i) {
        const botw_shader_observation_stream *s = &o->streams[i];
        ok = append(json, "%s{\"slot\":%u,\"stride\":%u,\"divisor\":%u}",
                    i ? "," : "", s->slot, s->stride, s->divisor);
    }
    ok = ok && append(json, "],\"buffers\":[");
    for (uint32_t i = 0; ok && i < o->vertex_buffer_count; ++i) {
        const botw_shader_observation_vertex_buffer *b = &o->vertex_buffers[i];
        ok = append(json, "%s{\"slot\":%u,\"bound\":%s,\"size\":%llu}", i ? "," : "", b->slot,
                    b->bound ? "true" : "false", (unsigned long long)b->size);
    }
    ok = ok && append(json,
        "],\"viewport\":%s", o->has_viewport ? "{" : "null");
    if (ok && o->has_viewport)
        ok = append(json, "\"x\":%.9g,\"y\":%.9g,\"width\":%.9g,\"height\":%.9g}",
                    o->viewport[0], o->viewport[1], o->viewport[2], o->viewport[3]);
    ok = ok && append(json, ",\"scissor\":%s", o->has_scissor ? "{" : "null");
    if (ok && o->has_scissor)
        ok = append(json, "\"x\":%d,\"y\":%d,\"width\":%d,\"height\":%d}",
                    o->scissor[0], o->scissor[1], o->scissor[2], o->scissor[3]);
    ok = ok && append(json, "}\n}\n");
    if (!ok) return fail(error, error_size, "first-draw observation JSON allocation failed");
    return 1;
}

static int outside_repository(const char *directory, char *error, size_t error_size) {
    char parent[PATH_MAX];
    if (!realpath(directory, parent)) return fail(error, error_size, "cannot resolve observation directory: %s", strerror(errno));
    for (;;) {
        char marker[PATH_MAX];
        struct stat st;
        int n = snprintf(marker, sizeof marker, "%s/.git", parent);
        if (n < 0 || (size_t)n >= sizeof marker) return fail(error, error_size, "observation directory path is too long");
        if (!lstat(marker, &st)) return fail(error, error_size, "shader observations must stay outside a repository");
        char *slash = strrchr(parent, '/');
        if (!slash || slash == parent) break;
        *slash = 0;
    }
    return 1;
}

static int write_all(int fd, const char *data, size_t size) {
    while (size) {
        ssize_t count = write(fd, data, size);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return 0;
        data += count;
        size -= (size_t)count;
    }
    return 1;
}

int botw_shader_observation_write(const char *path, const botw_shader_observation *observation,
                                  char *error, size_t error_size) {
    if (error && error_size) error[0] = 0;
    if (!path || path[0] != '/') return fail(error, error_size, "observation destination must be an absolute path outside a repository");
    const char *slash = strrchr(path, '/');
    if (!slash || !slash[1]) return fail(error, error_size, "observation destination must name a JSON file");
    size_t directory_length = slash == path ? 1 : (size_t)(slash - path);
    if (directory_length >= PATH_MAX || strlen(slash + 1) >= NAME_MAX)
        return fail(error, error_size, "observation destination path is too long");
    char directory[PATH_MAX];
    memcpy(directory, path, directory_length);
    directory[directory_length] = 0;
    if (outside_repository(directory, error, error_size) < 0) return -1;

    json_buffer json = {0};
    if (build_json(observation, &json, error, error_size) < 0) { free(json.data); return -1; }
    int dir = open(directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) { free(json.data); return fail(error, error_size, "cannot open observation directory: %s", strerror(errno)); }
    char temporary[NAME_MAX];
    int n = snprintf(temporary, sizeof temporary, ".%s.tmp.%ld", slash + 1, (long)getpid());
    if (n < 0 || (size_t)n >= sizeof temporary) { close(dir); free(json.data); return fail(error, error_size, "observation temporary name is too long"); }
    int fd = -1;
    for (unsigned attempt = 0; attempt < 100; ++attempt) {
        n = snprintf(temporary, sizeof temporary, ".%s.tmp.%ld.%u", slash + 1, (long)getpid(), attempt);
        if (n < 0 || (size_t)n >= sizeof temporary) break;
        fd = openat(dir, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd >= 0 || errno != EEXIST) break;
    }
    if (fd < 0) { close(dir); free(json.data); return fail(error, error_size, "cannot create observation temporary file: %s", strerror(errno)); }
    int ok = write_all(fd, json.data, json.size);
    if (ok && fsync(fd)) ok = 0;
    int saved = errno;
    if (close(fd) && ok) { ok = 0; saved = errno; }
    if (!ok) {
        unlinkat(dir, temporary, 0);
        close(dir);
        free(json.data);
        return fail(error, error_size, "cannot finish observation temporary file: %s", strerror(saved));
    }
    if (linkat(dir, temporary, dir, slash + 1, 0)) {
        saved = errno;
        unlinkat(dir, temporary, 0);
        close(dir);
        free(json.data);
        return fail(error, error_size, saved == EEXIST ? "observation destination already exists" : "cannot publish observation: %s", strerror(saved));
    }
    unlinkat(dir, temporary, 0);
    fsync(dir);
    close(dir);
    free(json.data);
    return 1;
}
