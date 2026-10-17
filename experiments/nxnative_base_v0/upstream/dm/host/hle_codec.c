// nn::codec::HardwareOpusDecoder: the real device decodes in hardware via the hwopus service (the game's audio and cutscene tracks are all Opus; this is not an online feature).
// Host: the constructor instead constructs the SDK's software OpusDecoder in the same memory (the sdk bundles libopus, native ARM).
// Basis: both are implementations of nn::codec::IOpusDecoder, with the 10 vtable slots identical one by one in order and signature (destructor×2, GetWorkBufferSize, IsInitialized,
// Initialize, Finalize, GetSampleRate, GetChannelCount, Reset, DecodeInterleaved); the game allocates 0x40 bytes and the software object is 0x30 bytes.
#include "hle.h"

HLE_FORCE("_ZN2nn5codec19HardwareOpusDecoderC1Ei", "hwopus 服务；换成 SDK 软件 OpusDecoder（同一 IOpusDecoder 虚接口）",
          void, h_codec_HwOpusCtor, void *self, int option) {
    (void)option;
    static void (*soft_ctor)(void *);
    if (!soft_ctor && !(soft_ctor = guest_sym("_ZN2nn5codec11OpusDecoderC1Ev"))) die("缺 OpusDecoder 构造");
    soft_ctor(self);
}
