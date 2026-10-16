/* SPDX-License-Identifier: MIT
 * Normal SVC bridge to the complete service module and one original kernel. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct botw_services botw_services;
typedef struct {
    botw_services *services;
    void *context;
    /* Normal logical permission check AND protected copy into host-owned output.
     * 0 succeeds; -1 fails with a fixed ordinary diagnostic, never a dump.
     * The complete services instance must also have its real memory validator,
     * checked write preparation and protected read/write copiers configured. */
    int (*read_guest)(void *, uint64_t source, void *output, size_t length, char *, size_t);
    /* Current calling thread's Horizon TLR, not ELF TLS. It remains live for
     * this synchronous SVC; services validates/copies its first 0x100 bytes. */
    int (*current_tlr)(void *, void **base, size_t *size, char *, size_t);
} botw_whole_ipc_configuration;
/* Caller supplies a fresh real services context. Binding installs canonical
 * single-g_h transport allocation before publication; no standalone namespace
 * or default/mock provider is allowed. Failed bind never publishes a binding.
 * Callbacks must not reenter bind/unbind and must outlive services destruction. */
int botw_whole_ipc_bind(const botw_whole_ipc_configuration *, char *, size_t);
/* Retire admission and wait for actual borrowed SVC calls. No service/G lock is
 * held while waiting. Caller then quiesces all other service users and calls
 * botw_services_destroy_checked; preserve context/providers until its ACK.
 * This does not destroy services, close its transports, or free provider state. */
int botw_whole_ipc_unbind(botw_services *expected, char *, size_t);
/* 0: unrelated SVC or original thread Close. 1: handled (x0 actual Result).
 * -1: real host/provider/service failure; every register remains unchanged.
 * Invalid handles return ResultInvalidHandle=0xe401 without invoking providers.
 * A successful 0x1f writes x0/x1; successful sends/Close only write x0. */
int botw_whole_ipc_dispatch(int number, uint64_t *registers, char *, size_t);
#ifdef __cplusplus
}
#endif
