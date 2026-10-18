# BOTW 1.9 fullscreen presentation

The pinned nxnative revision uses a centered 16:9 Metal view. The user requested the complete image stretched horizontally to fill the iPhone display.

`patches/nxnative-ios-stretch.patch` adds the opt-in `DM_IOS_STRETCH=1` setting. It changes only the Metal view frame; game resolution, renderer, shader translation, audio, gamepad bounds and save paths remain upstream. The default, missing or zero, preserves the original aspect fit. The runtime bundles the setting; `Documents/env.txt` can override it with `DM_IOS_STRETCH=0`.

The frontend workflow reuses the verified shader libraries and effective chain header from successful run 39047604473, chain `df19b235128f`. It recompiles the host with the pinned upstream source and this patch, without translating the shaders again. Private rewritten modules remain in encrypted temporary transport; no keys or game files are committed.

Device acceptance still requires a new installation, full-width screenshot, gameplay input and save/reopen checks. Build success alone does not establish these.
