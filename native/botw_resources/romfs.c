/* Read-only Switch RomFS. Layout: SciresM/hactool ivfc.h (RomFS structs).
 * Deliberately independent of Foundation, JSON, NCA crypto and guest code.
 */
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#include "romfs.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define EMPTY UINT32_MAX
#define MAX_IMAGE (UINT64_C(64) * 1024 * 1024 * 1024)
#define MAX_METADATA (UINT64_C(64) * 1024 * 1024)
#define MAX_INDEX (UINT64_C(64) * 1024 * 1024)
#define MAX_ENTRIES 1000000u
#define MAX_PATH 768u
#define MAX_READ (16u * 1024u * 1024u)

struct indexed_entry {
    char *path;
    botw_romfs_entry value;
};
struct botw_romfs {
    int fd;
    uint64_t image_size;
    struct indexed_entry *entries;
    size_t count, capacity;
    uint64_t index_bytes;
};
struct table { unsigned char *bytes; uint64_t offset; size_t size; };
struct raw_entry {
    uint32_t position, parent, sibling, child, file, hash, name_size;
    const unsigned char *name;
    uint64_t offset, size;
    unsigned char hash_seen, tree_seen;
};
struct visit { uint32_t position, parent; size_t parent_index; };

static int error_set(botw_romfs_error *error, int code, const char *message) {
    if (error) {
        error->code = code;
        (void)snprintf(error->message, sizeof(error->message), "%s", message);
    }
    return code;
}
static void error_clear(botw_romfs_error *error) {
    if (error) { error->code = 0; error->message[0] = '\0'; }
}
static uint32_t u32(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint64_t u64(const unsigned char *p) {
    return (uint64_t)u32(p) | ((uint64_t)u32(p + 4) << 32);
}
static int read_exact(const botw_romfs *fs, void *buffer, size_t length,
                      uint64_t offset, botw_romfs_error *error) {
    unsigned char *out = buffer;
    size_t done = 0;
    if (offset > fs->image_size || (uint64_t)length > fs->image_size - offset)
        return error_set(error, BOTW_ROMFS_EINVAL, "RomFS read exceeds image bounds");
    while (done < length) {
        ssize_t n = pread(fs->fd, out + done, length - done, (off_t)(offset + done));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return error_set(error, BOTW_ROMFS_EIO, "Truncated or unreadable RomFS image");
        done += (size_t)n;
    }
    return 0;
}
static int valid_name(const unsigned char *name, size_t length, int allow_empty) {
    size_t i = 0;
    if (!length) return allow_empty;
    if (length > 255 || (length == 1 && name[0] == '.') ||
        (length == 2 && name[0] == '.' && name[1] == '.')) return 0;
    while (i < length) {
        unsigned char c = name[i++];
        size_t continuation;
        if (c < 0x80) {
            if (c < 32 || c == 127 || c == '/' || c == '\\' || c == ':') return 0;
            continue;
        }
        if (c >= 0xc2 && c <= 0xdf) continuation = 1;
        else if (c >= 0xe0 && c <= 0xef) continuation = 2;
        else if (c >= 0xf0 && c <= 0xf4) continuation = 3;
        else return 0;
        if (continuation > length - i) return 0;
        if ((c == 0xe0 && name[i] < 0xa0) || (c == 0xed && name[i] >= 0xa0) ||
            (c == 0xf0 && name[i] < 0x90) || (c == 0xf4 && name[i] >= 0x90)) return 0;
        while (continuation--) if ((name[i++] & 0xc0) != 0x80) return 0;
    }
    return 1;
}
static int valid_path(const char *path) {
    size_t length, begin;
    if (!path || path[0] != '/') return 0;
    length = strnlen(path, MAX_PATH + 1u);
    if (!length || length > MAX_PATH) return 0;
    if (length == 1) return 1;
    begin = 1;
    for (size_t i = 1; i <= length; ++i) {
        if (i == length || path[i] == '/') {
            if (!valid_name((const unsigned char *)path + begin, i - begin, 0)) return 0;
            begin = i + 1;
        }
    }
    return 1;
}
static int parse_entries(const struct table *table, int directory,
                          struct raw_entry **result, size_t *result_count,
                          botw_romfs_error *error) {
    size_t position = 0, count = 0, fixed = directory ? 24u : 32u;
    struct raw_entry *entries;
    while (position < table->size) {
        uint32_t name_size;
        size_t record_size;
        if (table->size - position < fixed || count >= MAX_ENTRIES)
            return error_set(error, BOTW_ROMFS_EINVAL, "RomFS metadata is truncated or has too many entries");
        name_size = u32(table->bytes + position + fixed - 4);
        if (!valid_name(table->bytes + position + fixed,
                        name_size <= table->size - position - fixed ? name_size : 256u,
                        directory && position == 0))
            return error_set(error, BOTW_ROMFS_EINVAL, "Invalid UTF-8 or unsafe RomFS entry name");
        record_size = (fixed + name_size + 3u) & ~(size_t)3;
        if (record_size > table->size - position)
            return error_set(error, BOTW_ROMFS_EINVAL, "RomFS name exceeds metadata bounds");
        position += record_size; ++count;
    }
    entries = calloc(count ? count : 1, sizeof(*entries));
    if (!entries) return error_set(error, BOTW_ROMFS_ENOMEM, "Cannot allocate RomFS metadata index");
    position = 0;
    for (size_t i = 0; i < count; ++i) {
        const unsigned char *p = table->bytes + position;
        struct raw_entry *entry = &entries[i];
        entry->position = (uint32_t)position; entry->parent = u32(p); entry->sibling = u32(p + 4);
        if (directory) { entry->child = u32(p + 8); entry->file = u32(p + 12); }
        else { entry->offset = u64(p + 8); entry->size = u64(p + 16); }
        entry->hash = u32(p + fixed - 8); entry->name_size = u32(p + fixed - 4);
        entry->name = p + fixed;
        position += (fixed + entry->name_size + 3u) & ~(size_t)3;
    }
    *result = entries; *result_count = count;
    return 0;
}
static struct raw_entry *find_raw(struct raw_entry *entries, size_t count, uint32_t position) {
    size_t left = 0, right = count;
    while (left < right) {
        size_t mid = left + (right - left) / 2;
        if (entries[mid].position < position) left = mid + 1;
        else right = mid;
    }
    return left < count && entries[left].position == position ? &entries[left] : NULL;
}
static int validate_hashes(const struct table *table, struct raw_entry *entries,
                           size_t count, botw_romfs_error *error) {
    size_t seen = 0;
    for (size_t i = 0; i < table->size; i += 4) {
        uint32_t position = u32(table->bytes + i);
        while (position != EMPTY) {
            struct raw_entry *entry = find_raw(entries, count, position);
            if (!entry || entry->hash_seen)
                return error_set(error, BOTW_ROMFS_EINVAL, "Invalid or cyclic RomFS hash chain");
            entry->hash_seen = 1; ++seen; position = entry->hash;
        }
    }
    if (seen != count) return error_set(error, BOTW_ROMFS_EINVAL, "Unindexed RomFS metadata entry");
    return 0;
}
static int append_entry(botw_romfs *fs, const char *parent, const struct raw_entry *raw,
                         int directory, uint64_t offset, size_t *index,
                         botw_romfs_error *error) {
    size_t prefix = parent && strcmp(parent, "/") != 0 ? strlen(parent) : 0;
    size_t length = parent ? prefix + 1 + raw->name_size : 1;
    char *path;
    if (length > MAX_PATH || fs->count >= fs->capacity ||
        (uint64_t)length + 1 > MAX_INDEX - fs->index_bytes)
        return error_set(error, BOTW_ROMFS_ERANGE, "RomFS path or index size limit exceeded");
    path = malloc(length + 1);
    if (!path) return error_set(error, BOTW_ROMFS_ENOMEM, "Cannot allocate RomFS path");
    if (prefix) memcpy(path, parent, prefix);
    path[prefix] = '/';
    if (parent) memcpy(path + prefix + 1, raw->name, raw->name_size);
    path[length] = '\0';
    *index = fs->count;
    fs->entries[fs->count].path = path;
    fs->entries[fs->count].value = (botw_romfs_entry){offset, directory ? 0 : raw->size, directory};
    ++fs->count; fs->index_bytes += (uint64_t)length + 1;
    return 0;
}
static int compare_path(const void *a, const void *b) {
    return strcmp(((const struct indexed_entry *)a)->path, ((const struct indexed_entry *)b)->path);
}
static int compare_offset(const void *a, const void *b) {
    uint64_t x = ((const struct raw_entry *)a)->offset, y = ((const struct raw_entry *)b)->offset;
    return x < y ? -1 : x > y ? 1 : 0;
}

botw_romfs *botw_romfs_open(const char *image_path, botw_romfs_error *error) {
    botw_romfs *fs = NULL;
    struct table tables[4] = {{0}};
    struct raw_entry *dirs = NULL, *files = NULL;
    struct visit *pending = NULL;
    size_t directory_count = 0, file_count = 0, queued = 0, seen_dirs = 0, seen_files = 0;
    uint64_t metadata_bytes = 0, data_offset = 0;
    unsigned char header[80];
    struct stat info;
    error_clear(error);
    if (!image_path) { error_set(error, BOTW_ROMFS_EINVAL, "Missing RomFS image path"); return NULL; }
    fs = calloc(1, sizeof(*fs));
    if (!fs) { error_set(error, BOTW_ROMFS_ENOMEM, "Cannot allocate RomFS mount"); return NULL; }
    fs->fd = open(image_path, O_RDONLY | O_CLOEXEC);
    if (fs->fd < 0 || fstat(fs->fd, &info) || !S_ISREG(info.st_mode) || info.st_size < 80 ||
        (uint64_t)info.st_size > MAX_IMAGE) {
        error_set(error, BOTW_ROMFS_EIO, "RomFS image is unreadable or exceeds size bounds"); goto failed;
    }
    fs->image_size = (uint64_t)info.st_size;
    if (read_exact(fs, header, sizeof(header), 0, error)) goto failed;
    data_offset = u64(header + 72);
    if (u64(header) != 80 || data_offset < 80 || data_offset > fs->image_size) {
        error_set(error, BOTW_ROMFS_EINVAL, "Invalid RomFS header"); goto failed;
    }
    for (size_t i = 0; i < 4; ++i) {
        uint64_t length = u64(header + 16 + i * 16);
        tables[i].offset = u64(header + 8 + i * 16);
        if (tables[i].offset < 80 || tables[i].offset % 4 || length % 4 ||
            tables[i].offset > fs->image_size || length > fs->image_size - tables[i].offset ||
            length > MAX_METADATA - metadata_bytes || ((i == 0 || i == 2) && !length)) {
            error_set(error, BOTW_ROMFS_EINVAL, "RomFS metadata exceeds bounded image limits"); goto failed;
        }
        tables[i].size = (size_t)length; metadata_bytes += length;
        if (length && data_offset >= tables[i].offset && data_offset < tables[i].offset + length) {
            error_set(error, BOTW_ROMFS_EINVAL, "RomFS data starts inside metadata"); goto failed;
        }
        for (size_t j = 0; j < i; ++j) {
            if (length && tables[j].size && tables[i].offset < tables[j].offset + tables[j].size &&
                tables[i].offset + length > tables[j].offset) {
                error_set(error, BOTW_ROMFS_EINVAL, "Overlapping RomFS metadata tables"); goto failed;
            }
        }
        tables[i].bytes = malloc(tables[i].size ? tables[i].size : 1);
        if (!tables[i].bytes) { error_set(error, BOTW_ROMFS_ENOMEM, "Cannot allocate bounded RomFS metadata"); goto failed; }
        if (read_exact(fs, tables[i].bytes, tables[i].size, tables[i].offset, error)) goto failed;
    }
    if (parse_entries(&tables[1], 1, &dirs, &directory_count, error) ||
        parse_entries(&tables[3], 0, &files, &file_count, error)) goto failed;
    if (!directory_count || directory_count + file_count > MAX_ENTRIES ||
        dirs[0].name_size || (dirs[0].parent != 0 && dirs[0].parent != EMPTY) || dirs[0].sibling != EMPTY) {
        error_set(error, BOTW_ROMFS_EINVAL, "Invalid RomFS root or entry count"); goto failed;
    }
    if (validate_hashes(&tables[0], dirs, directory_count, error) ||
        validate_hashes(&tables[2], files, file_count, error)) goto failed;
    fs->capacity = directory_count + file_count;
    fs->index_bytes = fs->capacity * sizeof(*fs->entries);
    if (fs->index_bytes > MAX_INDEX) { error_set(error, BOTW_ROMFS_ERANGE, "RomFS index exceeds size limit"); goto failed; }
    fs->entries = calloc(fs->capacity, sizeof(*fs->entries));
    pending = calloc(directory_count, sizeof(*pending));
    if (!fs->entries || !pending) { error_set(error, BOTW_ROMFS_ENOMEM, "Cannot allocate bounded RomFS tree index"); goto failed; }
    pending[queued++] = (struct visit){0, EMPTY, SIZE_MAX};
    while (queued) {
        struct visit visit = pending[--queued];
        struct raw_entry *dir = find_raw(dirs, directory_count, visit.position);
        const char *parent_path = visit.parent_index == SIZE_MAX ? NULL : fs->entries[visit.parent_index].path;
        size_t index;
        uint32_t position;
        if (!dir || dir->tree_seen || (parent_path && dir->parent != visit.parent)) {
            error_set(error, BOTW_ROMFS_EINVAL, "Invalid, cyclic or mismatched RomFS directory link"); goto failed;
        }
        dir->tree_seen = 1; ++seen_dirs;
        if (append_entry(fs, parent_path, dir, 1, 0, &index, error)) goto failed;
        if (dir->sibling != EMPTY) {
            if (queued >= directory_count) { error_set(error, BOTW_ROMFS_EINVAL, "Cyclic RomFS directory queue"); goto failed; }
            pending[queued++] = (struct visit){dir->sibling, visit.parent, visit.parent_index};
        }
        if (dir->child != EMPTY) {
            if (queued >= directory_count) { error_set(error, BOTW_ROMFS_EINVAL, "Cyclic RomFS directory queue"); goto failed; }
            pending[queued++] = (struct visit){dir->child, dir->position, index};
        }
        position = dir->file;
        while (position != EMPTY) {
            struct raw_entry *file = find_raw(files, file_count, position);
            uint64_t absolute, end;
            size_t ignored;
            if (!file || file->tree_seen || file->parent != dir->position) {
                error_set(error, BOTW_ROMFS_EINVAL, "Invalid, cyclic or mismatched RomFS file link"); goto failed;
            }
            file->tree_seen = 1; ++seen_files;
            if (file->offset > fs->image_size - data_offset) {
                error_set(error, BOTW_ROMFS_EINVAL, "RomFS file offset exceeds image bounds"); goto failed;
            }
            absolute = data_offset + file->offset;
            if (file->size > fs->image_size - absolute) {
                error_set(error, BOTW_ROMFS_EINVAL, "RomFS file size exceeds image bounds"); goto failed;
            }
            end = absolute + file->size;
            if (file->size) for (size_t i = 0; i < 4; ++i) {
                if (tables[i].size && absolute < tables[i].offset + tables[i].size && end > tables[i].offset) {
                    error_set(error, BOTW_ROMFS_EINVAL, "RomFS file overlaps metadata"); goto failed;
                }
            }
            if (append_entry(fs, fs->entries[index].path, file, 0, absolute, &ignored, error)) goto failed;
            position = file->sibling;
        }
    }
    if (seen_dirs != directory_count || seen_files != file_count) {
        error_set(error, BOTW_ROMFS_EINVAL, "Unreachable RomFS metadata entry"); goto failed;
    }
    qsort(fs->entries, fs->count, sizeof(*fs->entries), compare_path);
    for (size_t i = 1; i < fs->count; ++i) {
        if (!strcmp(fs->entries[i - 1].path, fs->entries[i].path)) {
            error_set(error, BOTW_ROMFS_EINVAL, "Duplicate RomFS path"); goto failed;
        }
    }
    qsort(files, file_count, sizeof(*files), compare_offset);
    {
        uint64_t previous_end = 0;
        for (size_t i = 0; i < file_count; ++i) if (files[i].size) {
            if (files[i].offset < previous_end) {
                error_set(error, BOTW_ROMFS_EINVAL, "Overlapping RomFS file payloads"); goto failed;
            }
            previous_end = files[i].offset + files[i].size;
        }
    }
    for (size_t i = 0; i < 4; ++i) free(tables[i].bytes);
    free(dirs); free(files); free(pending);
    return fs;
failed:
    for (size_t i = 0; i < 4; ++i) free(tables[i].bytes);
    free(dirs); free(files); free(pending); botw_romfs_close(fs);
    return NULL;
}
void botw_romfs_close(botw_romfs *fs) {
    if (!fs) return;
    if (fs->fd >= 0) (void)close(fs->fd);
    for (size_t i = 0; i < fs->count; ++i) free(fs->entries[i].path);
    free(fs->entries); free(fs);
}
uint64_t botw_romfs_image_size(const botw_romfs *fs) {
    return fs ? fs->image_size : 0;
}
int botw_romfs_raw_pread(const botw_romfs *fs, uint64_t offset, void *buffer,
                         size_t length, size_t *actual, botw_romfs_error *error) {
    uint64_t available;
    int result;
    botw_romfs_error local_error;
    if (!error) error = &local_error;
    error_clear(error);
    if (actual) *actual = 0;
    if (!fs || !actual || (!buffer && length) || length > MAX_READ)
        return error_set(error, BOTW_ROMFS_EINVAL, "Invalid or oversized bounded raw RomFS read");
    if (offset >= fs->image_size || !length) return 0;
    available = fs->image_size - offset;
    if ((uint64_t)length > available) length = (size_t)available;
    result = read_exact(fs, buffer, length, offset, error);
    if (!result) *actual = length;
    return result;
}
static const struct indexed_entry *find_path(const botw_romfs *fs, const char *path,
                                             botw_romfs_error *error) {
    size_t left = 0, right;
    if (!fs || !valid_path(path)) { error_set(error, BOTW_ROMFS_EINVAL, "Invalid absolute RomFS path"); return NULL; }
    right = fs->count;
    while (left < right) {
        size_t mid = left + (right - left) / 2;
        int order = strcmp(fs->entries[mid].path, path);
        if (order < 0) left = mid + 1;
        else right = mid;
    }
    if (left < fs->count && !strcmp(fs->entries[left].path, path)) return &fs->entries[left];
    error_set(error, BOTW_ROMFS_ENOENT, "RomFS path does not exist");
    return NULL;
}
int botw_romfs_stat(const botw_romfs *fs, const char *path, botw_romfs_entry *entry,
                    botw_romfs_error *error) {
    const struct indexed_entry *found;
    botw_romfs_error local_error;
    if (!error) error = &local_error;
    error_clear(error);
    if (!entry) return error_set(error, BOTW_ROMFS_EINVAL, "Missing RomFS stat output");
    memset(entry, 0, sizeof(*entry));
    found = find_path(fs, path, error);
    if (!found) return error && error->code ? error->code : BOTW_ROMFS_ENOENT;
    *entry = found->value;
    return 0;
}
int botw_romfs_pread(const botw_romfs *fs, const char *path, uint64_t offset,
                     void *buffer, size_t length, size_t *actual,
                     botw_romfs_error *error) {
    const struct indexed_entry *found;
    uint64_t available;
    int result;
    botw_romfs_error local_error;
    if (!error) error = &local_error;
    error_clear(error);
    if (actual) *actual = 0;
    if (!actual || (!buffer && length) || length > MAX_READ)
        return error_set(error, BOTW_ROMFS_EINVAL, "Invalid or oversized bounded RomFS read");
    found = find_path(fs, path, error);
    if (!found) return error && error->code ? error->code : BOTW_ROMFS_ENOENT;
    if (found->value.is_directory) return error_set(error, BOTW_ROMFS_EISDIR, "Cannot read a RomFS directory");
    if (offset >= found->value.size || !length) return 0;
    available = found->value.size - offset;
    if ((uint64_t)length > available) length = (size_t)available;
    result = read_exact(fs, buffer, length, found->value.offset + offset, error);
    if (!result) *actual = length;
    return result;
}
int botw_romfs_iterate(const botw_romfs *fs, const char *path,
                       botw_romfs_directory_callback callback, void *user,
                       botw_romfs_error *error) {
    const struct indexed_entry *found;
    size_t length;
    botw_romfs_error local_error;
    if (!error) error = &local_error;
    error_clear(error);
    if (!callback) return error_set(error, BOTW_ROMFS_EINVAL, "Missing RomFS directory callback");
    found = find_path(fs, path, error);
    if (!found) return error && error->code ? error->code : BOTW_ROMFS_ENOENT;
    if (!found->value.is_directory) return error_set(error, BOTW_ROMFS_ENOTDIR, "RomFS path is not a directory");
    length = strlen(path);
    for (size_t i = 0; i < fs->count; ++i) {
        const char *candidate = fs->entries[i].path, *name;
        if (length == 1) name = candidate + 1;
        else {
            if (strncmp(candidate, path, length) || candidate[length] != '/') continue;
            name = candidate + length + 1;
        }
        if (*name && !strchr(name, '/') && callback(user, name, &fs->entries[i].value)) break;
    }
    return 0;
}
