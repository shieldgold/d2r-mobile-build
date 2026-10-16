/* SPDX-License-Identifier: MIT
 * Stateless port to the existing complete read-only RomFS reader. Mount and
 * handle ownership, locking and generations remain in upstream botw_fs.c. */
#pragma once
#include "romfs.h"

int botw_whole_romfs_read(const botw_romfs *image, const botw_romfs_entry *entry,
                         uint64_t offset, void *buffer, size_t length,
                         size_t *actual, botw_romfs_error *error);
/* mode bits are nn::fs ReadDirectoryMode (1 directories, 2 files). Cursor is
 * over filtered entries. An emitter returns a reader error code on failure.
 * A cursor advances only after an entry has been emitted successfully. */
int botw_whole_romfs_directory(const botw_romfs *image, const char *path,
                              unsigned mode, uint64_t *cursor, uint64_t maximum,
                              botw_romfs_directory_callback emit, void *user,
                              uint64_t *actual, botw_romfs_error *error);
int botw_whole_romfs_directory_count(const botw_romfs *image, const char *path,
                                    unsigned mode, uint64_t *actual,
                                    botw_romfs_error *error);
