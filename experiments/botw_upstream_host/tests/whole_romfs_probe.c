/* SPDX-License-Identifier: MIT
 * CPU-only service fixture. Includes the real FS unit and exercises its actual
 * HLE functions with owned paths, buffers and callbacks. No guest is loaded. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include "botw_fs.c"
struct dm_flags g_flag;
static _Atomic unsigned prepared;
void dm_guest_write_prepare(void *p, size_t n) { assert(p && n); prepared++; }
#ifdef BOTW_ROMFS_ADAPTED
static int fail_second_emit(void *opaque, const char *name, const botw_romfs_entry *entry) {
    unsigned *seen = opaque; assert(name && entry);
    return ++*seen == 2 ? BOTW_ROMFS_EIO : BOTW_ROMFS_OK;
}
static int accept_emit(void *opaque, const char *name, const botw_romfs_entry *entry) {
    unsigned *seen = opaque; assert(name && entry); (*seen)++; return 0;
}
static void *owned_read_worker(void *opaque) {
    uint64_t handle = *(const uint64_t *)opaque;
    for (unsigned i = 0; i < 1000; i++) {
        char bytes[9]; size_t actual = 0;
        Result result = botw_fs_ReadFile(&actual, handle, 0, bytes, sizeof bytes);
        if (result == RES_FS_INVALID_HANDLE) break;
        assert(result == RES_OK && actual == sizeof bytes && !memcmp(bytes, "boot data", sizeof bytes));
    }
    return NULL;
}
#endif
int main(int argc, char **argv) {
    assert(argc == 3);
    setenv("DM_ROMFS", argv[1], 1);
    unsigned char owned_cache[ROM_CACHE_SIZE] = {0};
    Result result = botw_fs_MountRom("content", owned_cache, sizeof owned_cache);
    if (result) { assert(mount_find("content", 7) < 0); fprintf(stderr, "FAIL: actual MountRom rejected owned image (Result=%u)\n", result); return 1; }
    int32_t type = -1;
    assert(!botw_fs_GetEntryType(&type, "content:/") && type == ENTRY_DIRECTORY);
#ifdef BOTW_ROMFS_ADAPTED
    if (!strcmp(argv[2], "files") || !strcmp(argv[2], "stale") || !strcmp(argv[2], "truncate")) {
        uint64_t file = 0;
        assert(!botw_fs_OpenFile(&file, "content:/./System/../Bootup.pack", OPEN_READ) && file);
        int64_t size = -1;
        assert(!botw_fs_GetFileSize(&size, file) && size == 9);
        char output[32] = {0}; size_t actual = 0;
        assert(!botw_fs_ReadFile(&actual, file, 0, output, sizeof output));
        assert(actual == 9 && !memcmp(output, "boot data", 9) && prepared);
        assert(!botw_fs_ReadFileOpt(&actual, file, 5, output, sizeof output, NULL));
        assert(actual == 4 && !memcmp(output, "data", 4));
        assert(!botw_fs_ReadFile(&actual, file, 100, output, 1) && !actual);
        assert(botw_fs_ReadFile(&actual, file, -1, output, 1) == RES_FS_INVALID_OFFSET);
        assert(botw_fs_GetFileSize(&size, 0) == RES_FS_INVALID_HANDLE);
        if (!strcmp(argv[2], "truncate")) {
            assert(!truncate(argv[1], 0x202));
            assert(botw_fs_ReadFile(&actual, file, 0, output, 9) == RES_FS_UNEXPECTED && !actual);
        }
        if (!strcmp(argv[2], "stale")) {
            uint64_t directory = 0;
            assert(!botw_fs_OpenDirectory(&directory, "content:/", 3));
            botw_fs_Unmount("content");
            assert(botw_fs_GetFileSize(&size, file) == RES_FS_INVALID_HANDLE);
            assert(botw_fs_GetDirectoryEntryCount(&size, directory) == RES_FS_INVALID_HANDLE);
            assert(!botw_fs_MountRom("content", owned_cache, sizeof owned_cache));
            uint64_t next = 0; assert(!botw_fs_OpenFile(&next, "content:/Bootup.pack", OPEN_READ));
            assert(next != file && botw_fs_GetFileSize(&size, file) == RES_FS_INVALID_HANDLE);
            botw_fs_CloseFile(next);
        } else {
            botw_fs_CloseFile(file);
            assert(botw_fs_GetFileSize(&size, file) == RES_FS_INVALID_HANDLE);
            uint64_t next = 0;
            assert(!botw_fs_OpenFile(&next, "content:/Bootup.pack", OPEN_READ) && next != file);
            botw_fs_CloseFile(next);
        }
    }
    if (!strcmp(argv[2], "paths")) {
        assert(botw_fs_GetEntryType(&type, "content:/missing") == RES_FS_PATH_NOT_FOUND);
        assert(botw_fs_GetEntryType(&type, "content:/../../escape") == RES_FS_INVALID_PATH_FORMAT);
        assert(botw_fs_GetEntryType(&type, "other:/Bootup.pack") == RES_FS_INVALID_MOUNT_NAME);
        assert(botw_fs_GetEntryType(&type, "content:/bad\\name") == RES_FS_INVALID_PATH_FORMAT);
        assert(!botw_fs_GetEntryType(&type, "content:/System/Actor.byml") && type == ENTRY_FILE);
        uint64_t handle = 5;
        assert(botw_fs_OpenFile(&handle, "content:/System", OPEN_READ) == RES_FS_PATH_NOT_FOUND && !handle);
        assert(botw_fs_OpenDirectory(&handle, "content:/Bootup.pack", 3) == RES_FS_PATH_NOT_FOUND && !handle);
        assert(botw_fs_OpenFile(&handle, "content:/Bootup.pack", 0) == RES_FS_INVALID_OPEN_MODE && !handle);
    }
    if (!strcmp(argv[2], "directory")) {
        uint64_t directory = 0; int64_t count = -1;
        assert(!botw_fs_OpenDirectory(&directory, "content:/", 3));
        assert(!botw_fs_GetDirectoryEntryCount(&count, directory) && count == 2);
        DirectoryEntry entries[2] = {{0}};
        assert(!botw_fs_ReadDirectory(&count, NULL, directory, 0) && count == 0);
        assert(!botw_fs_ReadDirectory(&count, entries, directory, 1) && count == 1);
        assert(!strcmp(entries[0].name, "Bootup.pack") && entries[0].type == ENTRY_FILE && entries[0].size == 9);
        assert(!botw_fs_GetDirectoryEntryCount(&count, directory) && count == 2);
        assert(!botw_fs_ReadDirectory(&count, entries, directory, 2) && count == 1);
        assert(!strcmp(entries[0].name, "System") && entries[0].type == ENTRY_DIRECTORY && !entries[0].size);
        assert(!botw_fs_ReadDirectory(&count, entries, directory, 2) && !count);
        int64_t size = 0; assert(botw_fs_GetFileSize(&size, directory) == RES_FS_INVALID_HANDLE);
        botw_fs_CloseDirectory(directory);
        assert(botw_fs_GetDirectoryEntryCount(&count, directory) == RES_FS_INVALID_HANDLE);
        assert(!botw_fs_OpenDirectory(&directory, "content:/", OPEN_DIR_FILE | OPEN_DIR_NO_FILE_SIZE));
        assert(!botw_fs_GetDirectoryEntryCount(&count, directory) && count == 1);
        assert(!botw_fs_ReadDirectory(&count, entries, directory, 2) && count == 1 && !entries[0].size);
        botw_fs_CloseDirectory(directory);
        assert(!botw_fs_OpenDirectory(&directory, "content:/System", OPEN_DIR_FILE));
        assert(!botw_fs_ReadDirectory(&count, entries, directory, 2) && count == 1);
        assert(!strcmp(entries[0].name, "Actor.byml") && entries[0].size == 10);
        botw_fs_CloseDirectory(directory);
    }
    if (!strcmp(argv[2], "readonly")) {
        uint64_t file = 0, rejected = 5;
        assert(!botw_fs_OpenFile(&file, "content:/Bootup.pack", OPEN_READ));
        assert(botw_fs_OpenFile(&rejected, "content:/Bootup.pack", OPEN_WRITE) == RES_FS_UNSUPPORTED_OPERATION && !rejected);
        assert(botw_fs_OpenFile(&rejected, "content:/Bootup.pack", OPEN_READ | OPEN_ALLOW_APPEND) == RES_FS_UNSUPPORTED_OPERATION && !rejected);
        assert(botw_fs_CreateFile("content:/new", 1) == RES_FS_UNSUPPORTED_OPERATION);
        assert(botw_fs_DeleteFile("content:/Bootup.pack") == RES_FS_UNSUPPORTED_OPERATION);
        assert(botw_fs_CreateDirectory("content:/new") == RES_FS_UNSUPPORTED_OPERATION);
        assert(botw_fs_DeleteDirectoryRecursively("content:/System") == RES_FS_UNSUPPORTED_OPERATION);
        assert(botw_fs_WriteFile(file, 0, "x", 1, NULL) != RES_OK);
        assert(botw_fs_SetFileSize(file, 1) != RES_OK);
        assert(botw_fs_CommitSaveData("content") == RES_FS_UNSUPPORTED_OPERATION);
        botw_fs_CloseFile(file);
    }
    if (!strcmp(argv[2], "large")) {
        uint64_t file = 0; assert(!botw_fs_OpenFile(&file, "content:/Large.bin", OPEN_READ));
        size_t length = 17u * 1024u * 1024u + 7u, actual = 0;
        unsigned char *buffer = malloc(length); assert(buffer);
        assert(!botw_fs_ReadFile(&actual, file, 0, buffer, length) && actual == length);
        for (size_t i = 0; i < length; i++) assert(buffer[i] == (unsigned char)'x');
        free(buffer); botw_fs_CloseFile(file);
    }
    if (!strcmp(argv[2], "emit-error")) {
        int mi = mount_find("content", 7); assert(mi >= 0 && g_mnt[mi].image);
        uint64_t cursor = 0, count = 0; unsigned emitted = 0; botw_romfs_error error;
        assert(botw_whole_romfs_directory(g_mnt[mi].image, "/", 3, &cursor, 10,
                                         fail_second_emit, &emitted, &count, &error) == BOTW_ROMFS_EIO);
        assert(cursor == 1 && count == 1 && error.code == BOTW_ROMFS_EIO);
        emitted = 0;
        assert(!botw_whole_romfs_directory(g_mnt[mi].image, "/", 3, &cursor, 10,
                                          accept_emit, &emitted, &count, &error));
        assert(cursor == 2 && count == 1 && emitted == 1);
        assert(botw_whole_romfs_directory_count(g_mnt[mi].image, "/missing", 3, &count, &error) == BOTW_ROMFS_ENOENT && count == 0);
    }
    if (!strcmp(argv[2], "concurrent")) {
        uint64_t file = 0; pthread_t worker;
        assert(!botw_fs_OpenFile(&file, "content:/Bootup.pack", OPEN_READ));
        assert(!pthread_create(&worker, NULL, owned_read_worker, &file));
        botw_fs_Unmount("content");
        assert(!pthread_join(worker, NULL));
        int64_t size = -1; assert(botw_fs_GetFileSize(&size, file) == RES_FS_INVALID_HANDLE);
    }
    if (!strcmp(argv[2], "slots")) {
        uint64_t files[FILE_SLOTS], directory = 0;
        for (int i = 0; i < FILE_SLOTS; i++) assert(!botw_fs_OpenFile(&files[i], "content:/Bootup.pack", OPEN_READ));
        uint64_t extra = 5;
        assert(botw_fs_OpenFile(&extra, "content:/Bootup.pack", OPEN_READ) == RES_FS_OPEN_COUNT_LIMIT && !extra);
        assert(!botw_fs_OpenDirectory(&directory, "content:/", 3));
        int64_t count = 0; assert(!botw_fs_GetDirectoryEntryCount(&count, directory) && count == 2);
        botw_fs_CloseFile(files[0]);
        assert(!botw_fs_OpenFile(&extra, "content:/Bootup.pack", OPEN_READ) && extra != files[0]);
        botw_fs_Unmount("content");
        assert(botw_fs_GetFileSize(&count, extra) == RES_FS_INVALID_HANDLE);
    }
#endif
    botw_fs_Unmount("content");
    puts("PASS: actual upstream MountRom accepted owned image");
    return 0;
}
