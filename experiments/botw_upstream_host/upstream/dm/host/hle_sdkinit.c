// Service layer initialization called by nninitInitializeSdkModule: on the real device each connects to a Horizon system service (IPC), the host has no such service processes.
// Native takeover: create no session, return directly; each service's later interfaces are taken over one by one as host implementations when they come into use (see hle_*.c in the same directory).
// nn::os::InitializeVirtualAddressMemory belongs to the SDK's os layer and is not taken over; fs::SetLocalApplicationAccessLog /
// SetResultHandledByApplication only write SDK-internal switches and are not taken over.
#include "hle.h"

static void once(const char *what) { LOG("init takeover: %s (no-op)", what); }

HLE_FORCE("nndiagStartup", "connects the lm log service; diagnostic logging is emitted by the host directly instead", void, h_nndiagStartup, void) { once("nndiagStartup"); }
HLE_FORCE("_ZN2nn2oe10InitializeEv", "connects the am/oe operating-environment service; environment queries become host defaults one by one", void, h_oe_Initialize, void) { once("oe::Initialize"); }
HLE_FORCE("_ZN2nn3aoc10InitializeEv", "add-on content service: host enumeration and filesystem adapters replace the Horizon session", void, h_aoc_Initialize, void) { once("aoc::Initialize"); }
HLE_FORCE("_ZN2nn3err26InitializeContextWriterApiEv", "connects the err error-context service; the host does not report", void, h_err_InitCtx, void) { once("err::InitializeContextWriterApi"); }
HLE_FORCE("_ZN2nn2fs24InitializeForApplicationEv", "connects the fsp-srv file service; file interfaces go to the host file system + RomFS", void, h_fs_InitApp, void) { once("fs::InitializeForApplication"); }
HLE_FORCE("_ZN2nn4pctl10InitializeEv", "connects the pctl parental-control service; offline play has no parental controls", void, h_pctl_Initialize, void) { once("pctl::Initialize"); }
HLE_FORCE("_ZN2nn4diag34InitializeApplicationAbortObserverEv", "registers the abort observer with the system; host crashes are printed by the signal handler", void, h_diag_AbortObs, void) { once("diag::InitializeApplicationAbortObserver"); }
