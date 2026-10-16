/* SPDX-License-Identifier: MIT
 * Audren service/CAPI binding to the complete host's single kernel. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "../../../native/botw_host/services.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct botw_whole_audren botw_whole_audren;
typedef struct {
    uint32_t abi_version, struct_size;
    uint64_t process_owner, applet_resource_user_id;
    /* The normal process/applet provider must confirm these same explicit
     * identities. Nonzero values alone are not evidence of configuration.
     * This startup callback must not reenter this binding/services/CAPI. */
    void *identity_context;
    int (*validate_identity)(void *, uint64_t, uint64_t, char *, size_t);
    /* Canonical CAPI configuration. failed is mandatory: it must latch a real
     * terminal failure and wake/dispatch the host; never reenter CAPI or join
     * the calling worker. Diagnostic options must be zero. All contexts must
     * outlive checked destruction. A missing ordinary-memory provider remains
     * a real NotFound on a normal pool miss, never a fabricated registration. */
    botw_audren_configuration host;
} botw_whole_audren_configuration;
#define BOTW_WHOLE_AUDREN_ABI 1u
int botw_whole_audren_create(const botw_whole_audren_configuration *,
    botw_whole_audren **out, char *, size_t);
/* Configure once before admitting service callers. This installs the complete
 * typed backend only; whole_ipc installs the single kernel transport namespace.
 * No backend callback enters CAPI while holding services/G/binding locks. */
int botw_whole_audren_attach(botw_whole_audren *, botw_services *, char *, size_t);
/* Caller resumes PCM/AVAudioSession before active=1. For pause, obtain this
 * real engine round/worker ACK before stopping the platform PCM gate/session. */
botw_audren_result botw_whole_audren_set_active(botw_whole_audren *, int,
    char *, size_t);
/* Caller must first whole_ipc_unbind and quiesce every other service/binding
 * caller. Destruction obtains the actual services_destroy_checked ACK before
 * retiring the stored API context. Pass the exact attached services owner;
 * its pointer clears only after that ACK. A later CAPI/kernel cleanup failure
 * preserves *binding for retry. If never attached, services may be NULL. */
int botw_whole_audren_destroy(botw_whole_audren **binding,
    botw_services **services, char *, size_t);
#ifdef __cplusplus
}
#endif
