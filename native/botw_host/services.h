/* Horizon HIPC/CMIF host services. No game data is included.
 * Host integration derived from jhaizhou-ops/d2r-mobile (MIT), commit
 * e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8. See THIRD_PARTY_LICENSE.txt. */
#ifndef BOTW_HOST_SERVICES_H
#define BOTW_HOST_SERVICES_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct botw_services botw_services;
typedef void (*botw_services_log_fn)(void *opaque, const char *message);
/* Return nonzero only when the entire guest range has the required access. */
typedef int (*botw_services_memory_fn)(void *opaque, uint64_t address, size_t length, int writable);
botw_services *botw_services_create(botw_services_log_fn log, botw_services_memory_fn memory, void *opaque);
void botw_services_destroy(botw_services *services);
/* 0 means executed. -1 means unsupported/invalid and error contains a concrete
 * diagnostic; callers must stop guest execution instead of forging success. */
int botw_services_connect(botw_services *services, const char *name, size_t name_length,
                          uint32_t *handle, char *error, size_t error_capacity);
int botw_services_close(botw_services *services, uint32_t handle, char *error, size_t error_capacity);
int botw_services_send(botw_services *services, uint32_t handle, void *ipc, size_t ipc_capacity,
                      char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
#endif
