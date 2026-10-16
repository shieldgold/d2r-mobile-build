/* SPDX-License-Identifier: MIT */
#include "whole_ipc.h"
#include "whole_kernel.h"
#include "services.h"
#include <pthread.h>
#include <stdio.h>
#include <string.h>

static pthread_mutex_t binding_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t binding_idle = PTHREAD_COND_INITIALIZER;
static botw_whole_ipc_configuration binding;
static size_t in_flight;
static int closing;
static int problem(char *error, size_t capacity, const char *message) {
    if (error && capacity) snprintf(error, capacity, "%s", message);
    return -1;
}
int botw_whole_ipc_bind(const botw_whole_ipc_configuration *configuration,
                       char *error, size_t capacity) {
    if (!configuration || !configuration->services || !configuration->read_guest || !configuration->current_tlr)
        return problem(error, capacity, "host IPC requires complete real service and memory/TLR providers");
    pthread_mutex_lock(&binding_lock);
    if (binding.services || closing) {
        pthread_mutex_unlock(&binding_lock);
        return problem(error, capacity, "host IPC provider is already bound or retiring");
    }
    const botw_services_transport_api transport = {
        NULL, botw_whole_kernel_transport_allocate, botw_whole_kernel_transport_release
    };
    // Configuration precedes every SVC borrower. The canonical callbacks only
    // enter G; no callback can enter binding_lock or call services again.
    if (botw_services_set_transport_api(configuration->services, &transport, error, capacity)) {
        pthread_mutex_unlock(&binding_lock);
        return -1;
    }
    binding = *configuration;
    pthread_mutex_unlock(&binding_lock);
    return 0;
}
int botw_whole_ipc_unbind(botw_services *expected, char *error, size_t capacity) {
    pthread_mutex_lock(&binding_lock);
    if (!expected || binding.services != expected || closing) {
        pthread_mutex_unlock(&binding_lock);
        return problem(error, capacity, "host IPC provider does not match the live binding");
    }
    closing = 1;
    while (in_flight) pthread_cond_wait(&binding_idle, &binding_lock);
    memset(&binding, 0, sizeof binding);
    closing = 0;
    pthread_mutex_unlock(&binding_lock);
    return 0;
}
static int borrow(botw_whole_ipc_configuration *out, char *error, size_t capacity) {
    pthread_mutex_lock(&binding_lock);
    if (!binding.services || closing || in_flight == SIZE_MAX) {
        pthread_mutex_unlock(&binding_lock);
        return problem(error, capacity, "host IPC provider is unavailable or retiring");
    }
    *out = binding;
    ++in_flight;
    pthread_mutex_unlock(&binding_lock);
    return 0;
}
static void release_borrow(void) {
    pthread_mutex_lock(&binding_lock);
    --in_flight;
    if (!in_flight) pthread_cond_broadcast(&binding_idle);
    pthread_mutex_unlock(&binding_lock);
}
static int send_request(const botw_whole_ipc_configuration *provider, int number,
                        uint64_t *regs, char *error, size_t capacity) {
    uint32_t handle = (uint32_t)(number == 0x21 ? regs[0] : regs[2]);
    void *ipc = (void *)(uintptr_t)regs[0];
    size_t size = (size_t)regs[1];
    if (number == 0x21) {
        size_t tlr_size = 0;
        if (provider->current_tlr(provider->context, &ipc, &tlr_size, error, capacity)) return -1;
        if (!ipc || tlr_size < 0x100)
            return problem(error, capacity, "host IPC current TLR has no complete command buffer");
        size = 0x100;
    }
    if (!ipc || size < 0x40 || size > 0x100)
        return problem(error, capacity, "host IPC command buffer is outside complete service admission");
    return botw_services_send(provider->services, handle, ipc, size, error, capacity);
}
int botw_whole_ipc_dispatch(int number, uint64_t *regs, char *error, size_t capacity) {
    if (number != 0x16 && number != 0x1f && number != 0x21 && number != 0x22) return 0;
    if (!regs) return problem(error, capacity, "host IPC register frame is absent");
    if (error && capacity) error[0] = 0;
    if (number != 0x1f) {
        const uint32_t handle = (uint32_t)(number == 0x22 ? regs[2] : regs[0]);
        const enum botw_whole_handle_kind kind = botw_whole_kernel_handle_kind(handle);
        if (number == 0x16 && kind == BOTW_WHOLE_HANDLE_THREAD) return 0;
        if (number == 0x16 && (kind == BOTW_WHOLE_HANDLE_EVENT_READ || kind == BOTW_WHOLE_HANDLE_EVENT_WRITE)) {
            regs[0] = botw_whole_kernel_close_event(NULL, handle, error, capacity) ? 0xe401u : 0;
            return 1;
        }
        if (kind != BOTW_WHOLE_HANDLE_TRANSPORT) { regs[0] = 0xe401u; return 1; }
    }
    botw_whole_ipc_configuration provider;
    if (borrow(&provider, error, capacity)) return -1;
    int result = -1;
    uint32_t connected = 0;
    if (number == 0x1f) {
        char port[16];
        size_t length;
        for (length = 0; length < sizeof port; ++length) {
            if (regs[1] > UINT64_MAX - length) {
                problem(error, capacity, "host IPC named port range overflows");
                break;
            }
            if (provider.read_guest(provider.context, regs[1] + length, port + length, 1, error, capacity)) break;
            if (!port[length]) {
                result = botw_services_connect(provider.services, port, length, &connected, error, capacity);
                break;
            }
        }
        if (length == sizeof port) problem(error, capacity, "host IPC named port exceeds length limit");
    } else if (number == 0x16) {
        // G was released by kind lookup before entering services -> G release.
        result = botw_services_close(provider.services, (uint32_t)regs[0], error, capacity);
    } else result = send_request(&provider, number, regs, error, capacity);
    release_borrow();
    if (result) {
        if (error && capacity && !error[0]) problem(error, capacity, "host IPC provider rejected the normal request");
        return -1;
    }
    regs[0] = 0;
    if (number == 0x1f) regs[1] = connected;
    return 1;
}
