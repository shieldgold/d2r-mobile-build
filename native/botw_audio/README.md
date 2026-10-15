# AudioOut reuse and fixed SDK adapter

The SPSC pending/released queues, coalesced completion notifier, PCM renderer,
5.1 Lo/Ro downmix and AudioUnit stream/callback architecture are adapted as one
module from `dm/host/hle_audio.c` in
[botw-mobile](https://github.com/TommyLau/botw-mobile), commit
`5f673ae2ea4f90ad46403aa6fbe13537fb557271`. The same core is present in
[d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile), commit `9de307e`.
The upstream MIT copyright and license are preserved in `LICENSE`.

`upstream_audio.inc` contains that adapted module. `audio.c` supplies protected
guest copies, the fixed base-1.0 ABI, context ownership and lifecycle controls.
The module never interprets a guest AudioOut as an upstream private struct.

The fixed profile covers all AudioOut imports of base-1.0 `subsdk3` as a batch,
plus their related open/getter variants (18 exports). `GetSampleByteSize` remains
the original pure SDK implementation. Resolution requires the original exact
SDK name, canonical module index 5, symbol value, size and text bounds. Original
binary checks establish:

- SDK `1b1f24/c`: parameter initializer writes `{0, 0xcafe0000}`. Channel count
  zero means the default stereo output; reserved `0xcafe` is preserved.
- SDK `1b1f3c/c`: buffer pointer, capacity and byte count are at `+8/+16/+24`.
  The `+0` intrusive-list header remains untouched.
- SDK open variants `1c6604/28`, `1c662c/198`, `1c67c4/f0`, `1c68b4/f0` return
  a 32-bit Result. `subsdk3` start call sites `2afb50` and `2bda10` test `w0`;
  stop/close callers do not consume a return value.

PCM16 at 48 kHz supports mono, stereo and the upstream explicit
`FL FR C LFE RL RR` 5.1 downmix. This layout was measured for D2R upstream;
BOTW audio has not yet been measured. Unsupported rates/channels are rejected.
Each append copies checked guest PCM into host ownership. Total pending plus
released nodes is bounded to 64 per output, and owned PCM to 64 MiB per context.
A released buffer returns the original guest descriptor token only after its
last PCM byte was consumed. Guest memory is never accessed by an AudioUnit
callback or notifier. The notifier only signals the independent kernel writer.

Compared with upstream, production removes the silent timer sink, `DM_AUDIO`
selection and realtime file dumps. AudioUnit creation/start/resume failures are
explicit errors. Inactive playback retains PCM and completion state; it does
not consume buffers to keep a synthetic clock moving. Destruction stops and
disposes the AudioUnit before joining the notifier and releasing PCM/handles.
A failed stop/dispose/handle close retains backing for a shutdown retry.

The runtime owns an audio context and the callback opaque pointer. It must stop
guest callers before shutdown, keep kernel/memory callbacks alive until shutdown
succeeds, and clear its context pointer after success. A successfully attached
readable event belongs to the guest; AudioOut owns only its writer. Failed event
attachment retains both owned endpoints until they can be closed.

`tests/test_botw_audio.py` distinguishes deterministic simulated-transport tests
from a public-ABI actual CoreAudio integration test with zero PCM. The latter
proves AudioUnit callbacks, completions and pause/resume on an Apple host. It
does not prove BOTW game audio. Non-Apple product context creation fails; there
is no fallback success backend. The test-only transport is compiled only with
`BOTW_AUDIO_TEST_BACKEND` and is absent from product builds.

The original main module imports 75 AudioRenderer graph/device functions. They
remain outside this resolver. The upstream BOTW branch's REV15 CPU/manual probe
is documented but has no public renderer implementation; the base-1.0 SDK uses
REV1 and original HIPC managers. AudioOut reuse does not implement that mixer.
