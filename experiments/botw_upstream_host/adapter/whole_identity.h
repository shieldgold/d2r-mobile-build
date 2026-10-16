/* SPDX-License-Identifier: MIT */
#pragma once
#include <stddef.h>

/* Bind the existing private UID and its existing save directory once, before
 * host preload. No profile, lock, UID or save directory is created/repaired. */
int whole_identity_bind_documents(const char *documents, char *error, size_t capacity);
/* NULL until bound; the returned path is immutable for the process lifetime. */
const char *whole_identity_save_directory(void);
