/* SPDX-License-Identifier: MIT
 * Complete Eden ABI2 renderer, original SDK IPC clients and one host kernel. */
#pragma once
#include "base_v0_audren_memory.h"
/* One-shot process provider; no SDK/Guest instructions run here. The main
 * owner calls prepare before original SDK initializers can use audren IPC. */
#ifdef __cplusplus
extern "C" {
#endif
int nxnative_base_v0_audren_prepare(void);
/* Actual UIKit session/focus producers may arrive before prepare. They retain
 * measured state; no SDK or audio worker calls are made until prepared. */
int nxnative_base_v0_audren_audio_available(int);
int nxnative_base_v0_audren_focus(int foreground,int focused);
/* Only after all normal callers are quiescent. Failed cleanup retains exact
 * owners for retry. The normal process does not unload live Guest threads. */
int nxnative_base_v0_audren_shutdown(void);
int nxnative_base_v0_audren_ready(void);

#ifdef __cplusplus
}
#endif
