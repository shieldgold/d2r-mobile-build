/* SPDX-License-Identifier: MIT */
#include "base_v0_force.h"
#include <CommonCrypto/CommonDigest.h>
#include <limits.h>
#include <string.h>

/* Source identities independently matched profiles/botw-switch-base-v0.json;
 * final image identities independently matched the private Guest report and
 * actual signed sections. Section order is distinct from this loader order. */
static const base_v0_force_module modules[] = {
    {"main",
     "fa2541333fdb5a868a286ea138774d82d13a1b0b97988d7af6c60ed82d2b1c81",
     "2094f152836683a3632c6153fd84ab8000000000000000000000000000000000",
     "cba191507c56577c3b81d61f7c32c3873a3bcfd980537a43b806bf6ab38458f7",
     "18573e8f26a19d4b1db0d30c754f81fda1558b46bb7676718402f936f5cd00ef",
     37723336u, 37732352u, {23280432u, 23281664u, 33980416u, 37289984u, 37240832u, 16384u, {0, 1, 2, 3, 4, 5, 6, 7}}},
    {"subsdk0",
     "58b6791aff6e5cb509c5347c1dc41d01a32c0a298c7f5da0c88ea56fc8a6eb17",
     "9f7e69a5c5e8aa0e61199ac33dd0d2d13717c190000000000000000000000000",
     "b216ef41e793c1ed4260edef1be06c1af6e7ce8166ae4412daecf109c699c053",
     "dd70aa9abdabd195d5bd666a4d69e429c0e648da129ad090a06e253bea0ff3ac",
     2136232u, 2146304u, {971244u, 974848u, 1736704u, 2097152u, 2048000u, 16384u, {0, 1, 2, 3, 4, 5, 6, 7}}},
    {"subsdk1",
     "a1b399f06db89bac005e8c7d8ce3475f931a9cdff6b7fc3509b9427e0a47be41",
     "fc3d9aae8b6769e2d69961c005e239f536805f19000000000000000000000000",
     "b60daf719e32258648aaf61ac37d4e59b500e0e6c64482e68dcb394200285bb9",
     "c1839386d53e8b2487dcec19818ac1a3f7ca6ccbf29286d92a674864b5be0195",
     198040u, 212992u, {65496u, 65536u, 163840u, 196608u, 180224u, 16384u, {0, 1, 2, 3, 4, 5, 6, 7}}},
    {"subsdk2",
     "e56eec1a58d1fa53779469e6eea36a795c0deac90bbf25b54b97c44ed195bd65",
     "2ba5fcc00ba28cdda26c792e934949cbb5cbc513000000000000000000000000",
     "99d8de01c8025e4659c87bdd31db4613f57c722824e312273f000aeab9bef272",
     "1c7764f71fb9b8d6006d89212b21248d9d3e665942beb5e859956b784510a343",
     332480u, 344064u, {176956u, 180224u, 274432u, 327680u, 294912u, 16384u, {0, 1, 2, 3, 4, 5, 6, 7}}},
    {"subsdk3",
     "2f344d84badf826937317e033b47f32bb2e1e8b7856553ce7356616268b5e238",
     "aa72f973a902c24747af4522ee67ea9a7994c3fb000000000000000000000000",
     "29ee0b0ee3a4265a894a00f66f219f51d5321982a4dd40cf10bc34d22a39f713",
     "121243a0cb0a2bce3c075942850d807d496ac925340e319991efe392bb53a3be",
     7096792u, 7110656u, {2986888u, 2990080u, 5353472u, 6815744u, 6782976u, 16384u, {0, 1, 2, 3, 4, 5, 6, 7}}},
    {"sdk",
     "0abff9d26d975cb17c24ddfecca488a4900e20ffd7ab9bb642e6df667dde9f31",
     "99d55fb2a2ce9e14bd5d56428ec0397be46e48eb000000000000000000000000",
     "3fb01f501844191dc266da6dc4b96b8663570e208cd37257c87bd7a5d68d21e0",
     "2f4afd02e29761b7639f2e8ad79b76eb0a6213bc06d4ccaf95c447fb103c74ff",
     8194752u, 8208384u, {3473812u, 3477504u, 6922240u, 7995392u, 7962624u, 16384u, {0, 1, 2, 3, 4, 5, 6, 7}}},
};
/* This is a selection/verification manifest, not another HLE registry. Function
 * implementations and dispatch remain exclusively in the upstream swforce
 * registry. Mask bits count exactly one successful force_lookup per module.
 * Zero means no by-name relocation in this exact image; direct intra-SDK
 * execution is not asserted to be intercepted or unreachable. */
typedef struct {
    const char *name, *why;
    unsigned titles, candidate, reference_mask;
} force_rule;
static const force_rule rules[] = {
    {"nndiagStartup", "connects the lm log service; diagnostic logging is emitted by the host directly instead", 2u, 1u, 0u},
    {"_ZN2nn2oe10InitializeEv", "BotW requires measured host applet state before SDK environment use", 2u, 1u, 1u},
    {"_ZN2nn3aoc10InitializeEv", "add-on content service: host enumeration and filesystem adapters replace the Horizon session", 2u, 1u, 1u},
    {"_ZN2nn3err26InitializeContextWriterApiEv", "connects the err error-context service; the host does not report", 2u, 1u, 0u},
    {"_ZN2nn2fs24InitializeForApplicationEv", "connects the fsp-srv file service; file interfaces go to the host file system + RomFS", 2u, 1u, 0u},
    {"_ZN2nn4pctl10InitializeEv", "connects the pctl parental-control service; offline play has no parental controls", 2u, 1u, 1u},
    {"_ZN2nn4diag34InitializeApplicationAbortObserverEv", "registers the abort observer with the system; host crashes are printed by the signal handler", 2u, 1u, 1u},
    {"_ZN2nn3mem17StandardAllocator8AllocateEmm", "allocates exactly as the original; only reports when returning null (original callers often skip the null check)", 2u, 0u, 0u},
    {"_ZN2nn7account10InitializeEv", "BotW: initialize one host-local user", 4u, 1u, 1u},
    {"_ZN2nn7account22TryOpenPreselectedUserEPNS0_10UserHandleE", "BotW: preselect the local user", 4u, 1u, 0u},
    {"_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE", "BotW: resolve a local user handle", 4u, 1u, 33u},
    {"_ZN2nn7account12PopOpenUsersEPiPNS0_10UserHandleEi", "BotW: transfer the local startup user", 4u, 1u, 0u},
    {"_ZN2nn7account12ListAllUsersEPiPNS0_3UidEi", "BotW: enumerate the local profile", 4u, 1u, 33u},
    {"_ZN2nn5audio3dsp20CommandListProcessor15DispatchCommandEPKNS0_13CommandHeaderE", "BotW CPU audio: enabled DeviceSink commands feed the host PCM sink; others stay native", 4u, 1u, 0u},
    {"_ZN2nn2os14FlushDataCacheEPKvm", "BotW CPU audio: fence tracked CPU audio buffers only", 4u, 1u, 34u},
    {"_ZN2nn5audio30GetAudioRendererWorkBufferSizeERKNS0_22AudioRendererParameterE", "BotW CPU audio: size the CPU/manual renderer", 4u, 1u, 1u},
    {"_ZN2nn5audio36GetAudioRendererConfigWorkBufferSizeERKNS0_22AudioRendererParameterE", "BotW CPU audio: size the CPU/manual config", 4u, 1u, 1u},
    {"_ZN2nn5audio29InitializeAudioRendererConfigEPNS0_19AudioRendererConfigERKNS0_22AudioRendererParameterEPvm", "BotW CPU audio: CPU/manual config with tracked work memory", 4u, 1u, 1u},
    {"_ZN2nn5audio17OpenAudioRendererEPNS0_19AudioRendererHandleEPNS_2os11SystemEventERKNS0_22AudioRendererParameterEPvm", "BotW CPU audio: open the native CPU/manual renderer and its SDK worker", 4u, 1u, 1u},
    {"_ZN2nn5audio26RequestUpdateAudioRendererENS0_19AudioRendererHandleEPKNS0_19AudioRendererConfigE", "BotW CPU audio: serialize native updates with rendering", 4u, 1u, 1u},
    {"_ZN2nn5audio18StartAudioRendererENS0_19AudioRendererHandleE", "BotW CPU audio: start rendering cadence", 4u, 1u, 1u},
    {"_ZN2nn5audio17StopAudioRendererENS0_19AudioRendererHandleE", "BotW CPU audio: park rendering cadence", 4u, 1u, 1u},
    {"_ZN2nn5audio34SetAudioRendererVoiceDropParameterENS0_19AudioRendererHandleEf", "BotW CPU audio: serialize native voice-drop changes with rendering", 4u, 1u, 0u},
    {"_ZN2nn5audio18CloseAudioRendererENS0_19AudioRendererHandleE", "BotW CPU audio: join the worker before native Close", 4u, 1u, 1u},
    {"_ZN2nn5audio17AcquireMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeEPvm", "BotW CPU audio: track memory-pool CPU ranges", 4u, 1u, 1u},
    {"_ZN2nn5audio17ReleaseMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeE", "BotW CPU audio: retire memory-pool CPU ranges", 4u, 1u, 1u},
    {"_ZN2nn5audio19ListAudioDeviceNameEPNS0_15AudioDeviceNameEi", "BotW: host logical output routes", 4u, 1u, 1u},
    {"_ZN2nn5audio26SetAudioDeviceOutputVolumeEPKNS0_15AudioDeviceNameEf", "BotW: per-route host PCM gain", 4u, 1u, 1u},
    {"_ZN2nn5audio41GetActiveAudioDeviceChannelCountForOutputEv", "BotW: fixed stereo host output", 4u, 1u, 0u},
    {"_ZN2nn5audio39AcquireAudioDeviceNotificationForOutputEPNS_2os11SystemEventE", "BotW: fixed-route local notification event", 4u, 1u, 0u},
    {"_ZN2nn3nfp10InitializeEv", "BotW: initialize NFP without NFC hardware", 4u, 1u, 1u},
    {"_ZN2nn3nfp8FinalizeEv", "BotW: finalize the host NFP client", 4u, 1u, 1u},
    {"_ZN2nn3nfp8GetStateEv", "BotW: report host NFP client state", 4u, 1u, 1u},
    {"_ZN2nn3nfp11ListDevicesEPNS0_12DeviceHandleEPii", "BotW: report no NFC readers", 4u, 1u, 1u},
    {"_ZN2nn2fs22QueryMountRomCacheSizeEPm", "BotW: RomFS is a host directory, only a token cache is requested", 4u, 1u, 1u},
    {"_ZN2nn2fs8MountRomEPKcPvm", "BotW: RomFS -> $DM_ROMFS host directory, read-only", 4u, 1u, 1u},
    {"_ZN2nn3aoc16ListAddOnContentEPiii", "BotW: enumerate configured add-on content", 4u, 1u, 0u},
    {"_ZN2nn2fs31QueryMountAddOnContentCacheSizeEPmi", "BotW: add-on content is a host directory, only a token cache is requested", 4u, 1u, 0u},
    {"_ZN2nn2fs17MountAddOnContentEPKciPvm", "BotW: add-on content index N -> $DM_DLC_N host directory, read-only", 4u, 1u, 0u},
    {"_ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE", "BotW: save data -> $DM_SAVE host directory, transactional", 4u, 1u, 1u},
    {"_ZN2nn2fs14CommitSaveDataEPKc", "BotW: flush the working copy to disk and swap it in atomically", 4u, 1u, 33u},
    {"_ZN2nn2fs7UnmountEPKc", "BotW: drop the mount; an uncommitted save transaction is discarded", 4u, 1u, 35u},
    {"_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci", "BotW: open a file inside a mount, confined walk", 4u, 1u, 51u},
    {"_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm", "BotW: positional read, short at end of file", 4u, 1u, 51u},
    {"_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvmRKNS0_10ReadOptionE", "BotW: positional read with ReadOption (no flags are defined)", 4u, 1u, 33u},
    {"_ZN2nn2fs9WriteFileENS0_10FileHandleElPKvmRKNS0_11WriteOptionE", "BotW: positional write inside the save working copy", 4u, 1u, 35u},
    {"_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE", "BotW: fstat on the host fd", 4u, 1u, 51u},
    {"_ZN2nn2fs11SetFileSizeENS0_10FileHandleEl", "BotW: ftruncate inside the save working copy", 4u, 1u, 35u},
    {"_ZN2nn2fs9FlushFileENS0_10FileHandleE", "BotW: fsync the host fd", 4u, 1u, 35u},
    {"_ZN2nn2fs9CloseFileENS0_10FileHandleE", "BotW: close the host fd and retire the handle", 4u, 1u, 51u},
    {"_ZN2nn2fs10CreateFileEPKcl", "BotW: create a file of the given size inside the save working copy", 4u, 1u, 35u},
    {"_ZN2nn2fs10DeleteFileEPKc", "BotW: unlink inside the save working copy", 4u, 1u, 35u},
    {"_ZN2nn2fs15CreateDirectoryEPKc", "BotW: mkdir inside the save working copy", 4u, 1u, 35u},
    {"_ZN2nn2fs26DeleteDirectoryRecursivelyEPKc", "BotW: confined recursive delete inside the save working copy", 4u, 1u, 1u},
    {"_ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc", "BotW: lstat inside a mount", 4u, 1u, 35u},
    {"_ZN2nn2fs13OpenDirectoryEPNS0_15DirectoryHandleEPKci", "BotW: opendir inside a mount, confined walk", 4u, 1u, 33u},
    {"_ZN2nn2fs13ReadDirectoryEPlPNS0_14DirectoryEntryENS0_15DirectoryHandleEl", "BotW: readdir into DirectoryEntry records", 4u, 1u, 33u},
    {"_ZN2nn2fs14CloseDirectoryENS0_15DirectoryHandleE", "BotW: closedir and retire the handle", 4u, 1u, 33u},
    {"_ZN2nn2fs22GetDirectoryEntryCountEPlNS0_15DirectoryHandleE", "BotW: count the same mounted directory without advancing it", 4u, 1u, 32u},
    {"_ZN2nn2oe27SetPerformanceConfigurationENS0_15PerformanceModeEi", "BotW: macOS manages CPU/GPU clocks", 4u, 1u, 1u},
    {"_ZN2nn2oe15SetCpuBoostModeENS0_12CpuBoostModeE", "BotW: host-managed CPU boost hint", 4u, 1u, 0u},
    {"_ZN2nn2oe28SetResumeNotificationEnabledEb", "BotW: host resume notification preference", 4u, 1u, 1u},
    {"_ZN2nn2oe42SetOperationModeChangedNotificationEnabledEb", "BotW: host operation-mode notification preference", 4u, 1u, 1u},
    {"_ZN2nn2oe44SetPerformanceModeChangedNotificationEnabledEb", "BotW: host performance-mode notification preference", 4u, 1u, 1u},
    {"_ZN2nn2oe38SetUserInactivityDetectionTimeExtendedEb", "BotW: host display sleep is not changed", 4u, 1u, 1u},
    {"_ZN2nn2oe24SetExpectedVolumeBalanceEff", "BotW: retain expected volume balance", 4u, 1u, 1u},
    {"_ZN2nn2oe24GetExpectedVolumeBalanceEPfS1_", "BotW: report the retained expected volume balance", 4u, 1u, 1u},
    {"_ZN2nn2oe20SetFocusHandlingModeENS0_17FocusHandlingModeE", "BotW: apply a real host execution policy or fail", 4u, 1u, 0u},
    {"_ZN2nn2oe16GetOperationModeEv", "BotW: measured host operation mode", 4u, 1u, 1u},
    {"_ZN2nn2oe18GetPerformanceModeEv", "BotW: measured host performance mode", 4u, 1u, 1u},
    {"_ZN2nn2oe25TryPopNotificationMessageEPj", "BotW: consume actual host applet notifications", 4u, 1u, 1u},
    {"_ZN2nn2oe18GetDesiredLanguageEv", "BotW: host-selected application language", 4u, 1u, 33u},
    {"_ZN2nn6applet23GetAppletResourceUserIdEv", "BotW: host application-instance identity", 4u, 1u, 38u},
    {"_ZN2nn2am23GetAppletResourceUserIdEv", "BotW: shared host application-instance identity", 4u, 1u, 32u},
    {"_ZN2nn2oe21TryPopLaunchParameterEPmPvm", "BotW: no queued application launch parameters", 4u, 1u, 0u},
    {"_ZN2nn2pl9EndVrModeEv", "BotW: desktop display remains outside VR mode", 4u, 1u, 0u},
    {"_ZN2nn2pl8IsVrModeEv", "BotW: desktop VR mode is inactive", 4u, 1u, 0u},
    {"_ZN2nn2pl11BeginVrModeEv", "BotW: VR display mode is unavailable", 4u, 1u, 0u},
    {"_ZN2nn2oe15EnableRecordingEv", "BotW: system recording permission hint", 4u, 1u, 0u},
    {"_ZN2nn2oe17FinishStartupLogoEv", "BotW: acknowledge startup overlay completion", 4u, 1u, 1u},
    {"_ZN2nn5prepo10PlayReport4SaveERKNS_7account3UidE", "BotW: play-report storage unavailable", 4u, 1u, 1u},
    {"_ZN2nn5prepo10PlayReport4SaveEv", "BotW: play-report storage unavailable", 4u, 1u, 1u},
    {"_ZN2nn4time10InitializeEv", "BotW: host time adapter over the guest counter and host clocks", 4u, 1u, 1u},
    {"_ZN2nn4time8FinalizeEv", "BotW: release one host time adapter reference", 4u, 1u, 1u},
    {"_ZN2nn4time13IsInitializedEv", "BotW: report host time adapter state", 4u, 1u, 1u},
    {"_ZN2nn4time19StandardSteadyClock19GetCurrentTimePointEPNS0_20SteadyClockTimePointE", "BotW: steady clock in whole seconds of the guest counter", 4u, 1u, 1u},
    {"_ZN2nn4time14GetSpanBetweenEPlRKNS0_20SteadyClockTimePointES4_", "BotW: seconds between steady clock points of one source", 4u, 1u, 1u},
    {"_ZN2nn4time23StandardUserSystemClock14GetCurrentTimeEPNS0_9PosixTimeE", "BotW: user clock from host realtime or DM_FIXED_TIME", 4u, 1u, 1u},
    {"_ZN2nn4time26StandardNetworkSystemClock14GetCurrentTimeEPNS0_9PosixTimeE", "BotW: report the network clock unavailable", 4u, 1u, 0u},
    {"_ZN2nn4time14ToCalendarTimeEPNS0_12CalendarTimeEPNS0_22CalendarAdditionalInfoERKNS0_9PosixTimeE", "BotW: calendar conversion in the host time zone", 4u, 1u, 1u},
    {"_ZN2nv20SetGraphicsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_", "BotW: record graphics allocator callbacks", 4u, 1u, 1u},
    {"_ZN2nv28SetGraphicsDevtoolsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_", "BotW: record graphics diagnostic allocator callbacks", 4u, 1u, 1u},
    {"_ZN2nv18InitializeGraphicsEPvm", "BotW: native Metal replaces nvdrv IPC", 4u, 1u, 1u},
    {"nvnBootstrapLoader", "BotW: resolve native NVN entry points", 4u, 1u, 1u},
    {"_ZN2nn2vi10InitializeEv", "BotW: native host display", 4u, 1u, 5u},
    {"_ZN2nn2vi18OpenDefaultDisplayEPPNS0_7DisplayE", "BotW: default host display", 4u, 1u, 1u},
    {"_ZN2nn2vi11CreateLayerEPPNS0_5LayerEPNS0_7DisplayE", "BotW: native Metal layer", 4u, 1u, 5u},
    {"_ZN2nn2vi15GetNativeWindowEPPvPNS0_5LayerE", "BotW: NVN window token", 4u, 1u, 1u},
    {"_ZN2nn2vi20GetDisplayVsyncEventEPNS_2os15SystemEventTypeEPNS0_7DisplayE", "BotW: host display refresh event", 4u, 1u, 1u},
    {"_ZN2nn3hid14InitializeNpadEv", "BotW: Npad backed by the host keyboard / mouse / GameController", 4u, 1u, 1u},
    {"_ZN2nn3hid24SetSupportedNpadStyleSetENS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE", "BotW: supported styles decide where the host virtual controller appears", 4u, 1u, 1u},
    {"_ZN2nn3hid22SetSupportedNpadIdTypeEPKjm", "BotW: supported ids decide where the host virtual controller appears", 4u, 1u, 1u},
    {"_ZN2nn3hid15GetNpadStyleSetERKj", "BotW: style of the host virtual controller", 4u, 1u, 1u},
    {"_ZN2nn3hid18SetNpadJoyHoldTypeENS0_15NpadJoyHoldTypeE", "BotW: Joy-Con hold type is recorded only", 4u, 1u, 1u},
    {"_ZN2nn3hid28SetNpadJoyAssignmentModeDualERKj", "BotW: Joy-Con assignment has no host Joy-Con", 4u, 1u, 1u},
    {"_ZN2nn3hid13GetNpadStatesEPNS0_16NpadFullKeyStateEiRKj", "BotW: Pro Controller states from the host virtual controller", 4u, 1u, 0u},
    {"_ZN2nn3hid13GetNpadStatesEPNS0_17NpadHandheldStateEiRKj", "BotW: handheld states from the host virtual controller", 4u, 1u, 0u},
    {"_ZN2nn3hid13GetNpadStatesEPNS0_16NpadJoyDualStateEiRKj", "BotW: no dual Joy-Con on the host; 0 states", 4u, 1u, 0u},
    {"_ZN2nn3hid13GetNpadStatesEPNS0_16NpadJoyLeftStateEiRKj", "BotW: no left Joy-Con on the host; 0 states", 4u, 1u, 0u},
    {"_ZN2nn3hid13GetNpadStatesEPNS0_17NpadJoyRightStateEiRKj", "BotW: no right Joy-Con on the host; 0 states", 4u, 1u, 0u},
    {"_ZN2nn3hid12GetNpadStateEPNS0_16NpadFullKeyStateERKj", "base-v0 single Npad state uses the complete native pad producer", 4u, 1u, 1u},
    {"_ZN2nn3hid12GetNpadStateEPNS0_17NpadHandheldStateERKj", "base-v0 single Npad state uses the complete native pad producer", 4u, 1u, 1u},
    {"_ZN2nn3hid12GetNpadStateEPNS0_16NpadJoyDualStateERKj", "base-v0 single Npad state uses the complete native pad producer", 4u, 1u, 1u},
    {"_ZN2nn3hid12GetNpadStateEPNS0_16NpadJoyLeftStateERKj", "base-v0 single Npad state uses the complete native pad producer", 4u, 1u, 1u},
    {"_ZN2nn3hid12GetNpadStateEPNS0_17NpadJoyRightStateERKj", "base-v0 single Npad state uses the complete native pad producer", 4u, 1u, 1u},
    {"_ZN2nn3hid23GetSixAxisSensorHandlesEPNS0_19SixAxisSensorHandleEiRKjNS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE", "BotW: host motion sensors unsupported; 0 handles", 4u, 1u, 1u},
    {"_ZN2nn3hid18StartSixAxisSensorERKNS0_19SixAxisSensorHandleE", "BotW: no six-axis handle is ever issued", 4u, 1u, 1u},
    {"_ZN2nn3hid25SetGyroscopeZeroDriftModeERKNS0_19SixAxisSensorHandleERKNS0_22GyroscopeZeroDriftModeE", "BotW: no six-axis handle is ever issued", 4u, 1u, 1u},
    {"_ZN2nn3hid22GetSixAxisSensorStatesEPNS0_18SixAxisSensorStateEiRKNS0_19SixAxisSensorHandleE", "BotW: no six-axis handle is ever issued; 0 states", 4u, 1u, 1u},
    {"_ZN2nn3hid28InitializeSevenSixAxisSensorEPvm", "BotW: VR sensor buffer accepted, host motion unsupported", 4u, 1u, 0u},
    {"_ZN2nn3hid26FinalizeSevenSixAxisSensorEv", "BotW: VR sensor has no host backend", 4u, 1u, 0u},
    {"_ZN2nn3hid23StartSevenSixAxisSensorEv", "BotW: VR sensor has no host backend", 4u, 1u, 0u},
    {"_ZN2nn3hid22StopSevenSixAxisSensorEv", "BotW: VR sensor has no host backend", 4u, 1u, 0u},
    {"_ZN2nn3hid32ResetSevenSixAxisSensorTimestampEv", "BotW: VR sensor has no host backend", 4u, 1u, 0u},
    {"_ZN2nn3hid27GetSevenSixAxisSensorStatesEPNS0_23SevenSixAxisSensorStateEi", "BotW: VR sensor has no host backend; 0 states", 4u, 1u, 0u},
    {"_ZN2nn3hid25GetVibrationDeviceHandlesEPNS0_21VibrationDeviceHandleEiRKjNS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE", "BotW: host rumble unsupported; 0 handles", 4u, 1u, 1u},
    {"_ZN2nn3hid25InitializeVibrationDeviceERKNS0_21VibrationDeviceHandleE", "BotW: no vibration handle is ever issued", 4u, 1u, 1u},
    {"_ZN2nn3hid18SendVibrationValueERKNS0_21VibrationDeviceHandleERKNS0_14VibrationValueE", "BotW: no vibration handle is ever issued", 4u, 1u, 1u},
    {"_ZN2nn3hid20ControllerSupportArg10SetDefaultEv", "BotW: SDK default arguments, verified at SDK+0x53e414", 4u, 1u, 0u},
    {"_ZN2nn3hid21ShowControllerSupportERKNS0_20ControllerSupportArgE", "BotW: controller applet answered by the host virtual controller", 4u, 1u, 1u},
    {"nntimeStartup", "BotW: retain the SDK counter callback with host time APIs", 4u, 1u, 0u},
    {"nvnBootstrapLoader", "The whole NVN graphics interface is replaced by a host null device (later Metal); the SDK's NVN driver does not run", 2u, 0u, 0u},
};
enum { MODULE_COUNT = sizeof modules / sizeof *modules,
       RULE_COUNT = sizeof rules / sizeof *rules };
enum { EMPTY, IMAGES, REGISTRY, REFERENCES, COMPLETE, FAILED };
static struct {
    unsigned phase, images, entries, last_reference_bit;
    uint8_t registry_seen[RULE_COUNT];
    unsigned references[RULE_COUNT];
} state;
static int fail(void) { state.phase = FAILED; return -1; }
const base_v0_force_module *base_v0_force_manifest(size_t *count) {
    if (count) *count = MODULE_COUNT;
    return modules;
}
int base_v0_force_begin(const char *const *names, size_t count) {
    if (state.phase != EMPTY || !names || count != MODULE_COUNT) return fail();
    for (size_t i = 0; i < count; i++)
        if (!names[i] || strcmp(names[i], modules[i].name)) return fail();
    state.phase = IMAGES;
    return 0;
}
static int same_layout(const base_v0_force_layout *a, const base_v0_force_layout *b) {
    if (!a || a->text_end != b->text_end || a->ro_off != b->ro_off ||
        a->data_off != b->data_off || a->patch_off != b->patch_off ||
        a->key_page_off != b->key_page_off || a->key_page_size != b->key_page_size) return 0;
    for (unsigned i = 0; i < 8; i++) if (a->key_idx[i] != b->key_idx[i]) return 0;
    return 1;
}
static int accept_identity(const char *name, const base_v0_force_layout *layout,
                           size_t bytes, const char *digest, int padded) {
    if (state.phase != IMAGES || state.images >= MODULE_COUNT || !name || !digest ||
        (padded != 0 && padded != 1)) return fail();
    const base_v0_force_module *m = &modules[state.images];
    if (strcmp(name, m->name) || !same_layout(layout, &m->layout) ||
        bytes != (padded ? m->padded_bytes : m->image_bytes) ||
        strcmp(digest, padded ? m->padded_sha256 : m->image_sha256)) return fail();
    state.images++;
    return 0;
}
int base_v0_force_image(const char *name, const base_v0_force_layout *layout,
    const void *const *parts, const size_t *lengths, size_t count, int padded) {
    if (state.phase != IMAGES || state.images >= MODULE_COUNT || !name || !parts || !lengths ||
        (padded != 0 && padded != 1) || count != (padded ? 3u : 1u)) return fail();
    const base_v0_force_module *m = &modules[state.images];
    /* Validate all lengths/layout before touching any byte stream. */
    if (strcmp(name, m->name) || !same_layout(layout, &m->layout)) return fail();
    size_t total = 0;
    for (size_t i = 0; i < count; i++) {
        if (!parts[i] || !lengths[i] || lengths[i] > UINT_MAX || lengths[i] > SIZE_MAX - total)
            return fail();
        total += lengths[i];
    }
    if (total != (padded ? m->padded_bytes : m->image_bytes)) return fail();
    if (padded) {
        size_t text = (size_t)((m->layout.text_end + 0x3fffu) & ~UINT64_C(0x3fff));
        if (lengths[0] != text || lengths[1] != m->layout.patch_off - text ||
            lengths[2] != m->padded_bytes - m->layout.patch_off) return fail();
    }
    CC_SHA256_CTX hash;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    if (!CC_SHA256_Init(&hash)) return fail();
    for (size_t i = 0; i < count; i++)
        if (!CC_SHA256_Update(&hash, parts[i], (CC_LONG)lengths[i])) return fail();
    if (!CC_SHA256_Final(digest, &hash)) return fail();
    char hex[CC_SHA256_DIGEST_LENGTH * 2 + 1];
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < sizeof digest; i++) {
        hex[i * 2] = digits[digest[i] >> 4]; hex[i * 2 + 1] = digits[digest[i] & 15];
    }
    hex[sizeof hex - 1] = 0;
    return accept_identity(name, layout, total, hex, padded);
}
int base_v0_force_registry_begin(void) {
    if (state.phase != IMAGES || state.images != MODULE_COUNT) return fail();
    state.phase = REGISTRY;
    return 0;
}
int base_v0_force_registry_entry(const char *name, unsigned titles,
    const char *why, int candidate, int implementation_present, uint8_t *selected) {
    if (state.phase != REGISTRY || !name || !why || !selected || !implementation_present ||
        (candidate != 0 && candidate != 1)) return fail();
    for (unsigned i = 0; i < RULE_COUNT; i++) {
        const force_rule *r = &rules[i];
        if (titles != r->titles || strcmp(name, r->name) || strcmp(why, r->why)) continue;
        if (state.registry_seen[i] || (unsigned)candidate != r->candidate) return fail();
        state.registry_seen[i] = 1; state.entries++;
        *selected = r->reference_mask != 0;
        return 0;
    }
    return fail();
}
int base_v0_force_registry_end(void) {
    if (state.phase != REGISTRY || state.entries != RULE_COUNT) return fail();
    state.phase = REFERENCES;
    return 0;
}
int base_v0_force_reference(const char *module, const char *name) {
    if (state.phase != REFERENCES || !module || !name) return fail();
    unsigned bit = 0;
    for (unsigned i = 0; i < MODULE_COUNT; i++) if (!strcmp(module, modules[i].name)) bit = 1u << i;
    if (!bit || bit < state.last_reference_bit) return fail();
    for (unsigned i = 0; i < RULE_COUNT; i++) {
        const force_rule *r = &rules[i];
        if (!r->candidate || strcmp(name, r->name)) continue;
        if (!(r->reference_mask & bit) || (state.references[i] & bit)) return fail();
        state.references[i] |= bit;
        state.last_reference_bit = bit;
        return 0;
    }
    return fail();
}
int base_v0_force_references_end(void) {
    if (state.phase != REFERENCES) return fail();
    for (unsigned i = 0; i < RULE_COUNT; i++)
        if (state.references[i] != rules[i].reference_mask) return fail();
    state.phase = COMPLETE;
    return 0;
}
