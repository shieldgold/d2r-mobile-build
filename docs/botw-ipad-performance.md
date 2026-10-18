# BOTW 1.9 iPad resolution experiment

The iPad mini (A17 Pro) running build 1018.3 already has nxnative's 60 Hz
frame-interval cuts and VFR time-step correction. Existing handheld gameplay
logs show frame p50 about 30–36 ms and GPU p50 about 27–31 ms. These were
uncontrolled user gameplay samples, not a fixed-scene comparison. The 60 fps
budget is 16.67 ms; changing the cap alone does not provide that performance.

`scripts/botw_resolution.py` prepares a separate unsigned app copy. It pins the
source app version, NSO build ID `cd57b23fa4bbad65803d9788c01821ee` and the
entire rewritten main-text SHA256. It refuses a modified source, mismatched
instruction, truncated executable, ambiguous section or existing destination.
Only eight MOVZ/MOVK immediates in the handheld cases of main+`0x1266074`
change. The fixed and dynamic size cases and crop rectangle change together.
The docked case and the original VFR/60 Hz code remain unchanged.

The function was located using [Electry's build-specific 1080p patch](https://gist.github.com/Electry/386ddf94942dc1e973e23d6642ea4cc6),
then checked against the player's matching original NSO by disassembly.
The script changes the neighboring handheld cases; it does not apply that
docked patch. Fixed sizes are 1280x720 (identity), 960x540, 768x432 and 640x360.
The runtime must use `DM_OPMODE=0`; `DM_3D_SCALE` is D2R-specific and does not
change this BOTW build.

Example, with all game data kept in the existing game work directory:

```sh
python3 scripts/botw_resolution.py /path/to/verified-1018.3/DmIOS.app \
  /path/to/game-work/performance/540p/Payload/DmIOS.app \
  --height 540 --build 1018.4 \
  --receipt /path/to/game-work/performance/540p-build.json
```

The output needs fresh development signing. Keep the original signed app,
read-back save backup and original `Documents/env.txt` before installation.
Updating the same bundle preserves the resource container. Do not uninstall
the game as part of the comparison.

Acceptance requires device evidence: verify actual crop and render-target
dimensions, inspect the complete game scene and HUD, and compare frame/GPU
timings after the initial shader pipeline compilation. Use the same loaded
save and camera for a controlled comparison. A prepared app or passing unit
tests does not establish lower rendering cost or stable 60 fps. Actual
presentation intervals matter as well as frame-time percentiles.

Rollback is an in-place installation of the preserved signed build 1018.3,
with the original environment file. Save backups are recovery material, not
automatically restored over newer progress.

## First device result: rejected

Build 1018.4 installed and loaded the Shrine of Resurrection save. The actual
crop changed to 960x540. Frame 2400's render-pass dump also contained 960x540,
480x270 and 120x67 attachments, establishing that this was a rendering-size
change rather than a changed cap label. All 45,459 shader/table/font/environment
resource files remained byte-identical to build 1018.3.

Scene windows ending at frames 1500/1800/2100 had frame p50
34.45/34.40/34.57 ms and GPU p50 29.80/29.76/29.91 ms. They did not establish
a useful improvement over the earlier native-size observations. Menu windows
had 16.67 ms presentation, but that is not 60 fps gameplay acceptance. Frame
2400's diagnostic readback incurred a long frame and is excluded from timing
comparisons. The user rejected the visible loss in image quality.

The preserved signed 1018.3 app was subsequently reinstalled in place to
restore the original image quality. Do not ship 1018.4 as a performance fix.
Further work must address the render backend's fixed pass/submission costs
and validate image equivalence. The backend's opt-in TBDR batch of changes
has known incomplete inference of bindless texture dependencies; enabling it
wholesale is not a verified way to preserve image correctness.

## Native-quality follow-up and final device state

After the rollback, crop logging confirmed 1280x720. Native-quality windows
ending at frames 1800/2100/2400 had frame p50 34.05/34.31/34.62 ms and GPU
p50 29.75/29.25/29.90 ms. A second runtime-only test used `DM_NVN_CUT=256`
instead of the upstream default 64, without changing the app or resolution.
Its first gameplay window ended at frame 1200 with frame p50 39.78 ms and GPU
p50 34.04 ms; the app then entered the background. This is not a complete
controlled A/B comparison and establishes no performance improvement.
The earlier 540p screenshot also has a different camera orientation from the
native screenshot, so no exact percentage gain/loss is claimed between them.

The experiment was ended by a clean launch of signed build 1018.3 without
`DM_NVN_CUT`, `DM_KEYS`, or screenshot overrides. `Documents/env.txt` is
byte-identical to its pre-test backup. Read-back hashes confirm that the main
game save, caption and tracker files are unchanged in both `save` and
`save.work`; only `option.sav` differs. Both pre-test and post-test versions
were backed up, and no newer settings were overwritten. This work did not
achieve stable 60 fps at native quality.
