/* SPDX-License-Identifier: MIT */
#include "whole_romfs.h"
#include <stdio.h>
#include <string.h>

static int fail(botw_romfs_error *error, int code, const char *message) {
    if (error) { error->code = code; snprintf(error->message, sizeof error->message, "%s", message); }
    return code;
}
int botw_whole_romfs_read(const botw_romfs *image, const botw_romfs_entry *entry,
                         uint64_t offset, void *buffer, size_t length,
                         size_t *actual, botw_romfs_error *error) {
    if (actual) *actual = 0;
    if (!image || !entry || !actual || (!buffer && length) || entry->is_directory)
        return fail(error, BOTW_ROMFS_EINVAL, "Invalid image file read");
    uint64_t image_size = botw_romfs_image_size(image);
    if (entry->offset > image_size || entry->size > image_size - entry->offset)
        return fail(error, BOTW_ROMFS_ERANGE, "Image file exceeds its mount bounds");
    if (error) { error->code = 0; error->message[0] = 0; }
    if (offset >= entry->size) return 0;
    uint64_t remaining = entry->size - offset;
    if ((uint64_t)length > remaining) length = (size_t)remaining;
    size_t done = 0;
    while (done < length) {
        size_t want = length - done, got = 0;
        if (want > 16u * 1024u * 1024u) want = 16u * 1024u * 1024u;
        int rc = botw_romfs_raw_pread(image, entry->offset + offset + done,
                                    (unsigned char *)buffer + done, want, &got, error);
        if (rc) return rc; /* A failed/short underlying read is never success. */
        if (got != want) return fail(error, BOTW_ROMFS_EIO, "Short image file read");
        done += got;
    }
    *actual = done; return 0;
}
typedef struct {
    unsigned mode;
    uint64_t skip, seen, maximum, emitted;
    botw_romfs_directory_callback emit;
    void *user;
    int error;
} directory_request;
static int directory_visit(void *opaque, const char *name, const botw_romfs_entry *entry) {
    directory_request *request = opaque;
    if (!(request->mode & (entry->is_directory ? 1u : 2u))) return 0;
    if (request->seen++ < request->skip) return 0;
    if (request->emitted == request->maximum) return 1;
    if (request->emit) {
        request->error = request->emit(request->user, name, entry);
        if (request->error) return 1;
    }
    request->emitted++;
    return request->emitted == request->maximum;
}
int botw_whole_romfs_directory(const botw_romfs *image, const char *path,
                              unsigned mode, uint64_t *cursor, uint64_t maximum,
                              botw_romfs_directory_callback emit, void *user,
                              uint64_t *actual, botw_romfs_error *error) {
    if (actual) *actual = 0;
    if (!cursor || !actual || !emit || !(mode & 3u))
        return fail(error, BOTW_ROMFS_EINVAL, "Invalid image directory read");
    directory_request request = {mode, *cursor, 0, maximum, 0, emit, user, 0};
    int rc = botw_romfs_iterate(image, path, directory_visit, &request, error);
    *cursor += request.emitted; *actual = request.emitted;
    if (rc) return rc;
    return request.error ? fail(error, request.error, "Image directory emitter failed") : 0;
}
int botw_whole_romfs_directory_count(const botw_romfs *image, const char *path,
                                    unsigned mode, uint64_t *actual,
                                    botw_romfs_error *error) {
    if (actual) *actual = 0;
    if (!actual || !(mode & 3u)) return fail(error, BOTW_ROMFS_EINVAL, "Invalid image directory count");
    directory_request request = {mode, 0, 0, UINT64_MAX, 0, NULL, NULL, 0};
    int rc = botw_romfs_iterate(image, path, directory_visit, &request, error);
    if (!rc) *actual = request.emitted;
    return rc;
}
