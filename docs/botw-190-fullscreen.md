# BOTW 1.9 fullscreen presentation

The pinned nxnative revision uses a centered 16:9 Metal view. The user requested the complete image stretched horizontally to fill the iPhone display.

`patches/nxnative-ios-stretch.patch` adds the opt-in `DM_IOS_STRETCH=1` setting. It changes the Metal view frame and final presentation viewport. The game resolution, texture crop/UV coordinates, shader translation, audio, gamepad bounds and save paths remain upstream. The default, missing or zero, preserves the original aspect fit. The runtime bundles the setting; `Documents/env.txt` can override it with `DM_IOS_STRETCH=0`.

The frontend workflow reuses the verified shader libraries and effective chain header from successful run 39047604473, chain `df19b235128f`. It recompiles the host with the pinned upstream source and this patch, without translating the shaders again. Private rewritten modules remain in encrypted temporary transport; no keys or game files are committed.

Device acceptance completed on an iPhone 15 Pro with runtime 1.9.0, build 1018.3. The full-width world screenshot is 2556x1179; both former 20-pixel side bands changed from 100% black to 0% black. The game recognizes DLC indices 1 and 2 (DLC version 3.0). Audio was confirmed on the device after silent mode was disabled. Movement, camera controls, A/B and D-pad menu input were exercised; a manual save was read back, backed up, retained byte-for-byte across the runtime update and loaded into the Shrine of Resurrection. This is basic scene/input/save acceptance; later regions and long sessions were not tested.

The default console mode exited during the opening after a logged footprint near 3.2 GiB; no cause is conclusively assigned. The accepted run uses upstream's documented `DM_OPMODE=0` handheld setting to lower load. This is independent of full-width presentation.

The real Metal presentation regression first failed with 64 unwanted black pixels, then passed all 40 checks after the viewport change. It covers full-image stretch, explicit zero restoring aspect fit, texture crop boundaries, raw sRGB bytes and queued crop snapshots. The test target also links the existing host big-stack helper; its guest-address classifier is stubbed because the GPU test has no guest address space. Touch and controller mapping tests pass 33 checks.

Final build: https://github.com/shieldgold/d2r-mobile-build/actions/runs/39100743357 (source commit `8a41653dd8411448b81308bd71956827373bb170`). Signed runtime SHA256: `f1af963e4ceabca9684de66712c46833a3ca25f837ce42af03e8749edd13c8c6`. All 25,846 Metal libraries match the previously verified runtime byte-for-byte.
