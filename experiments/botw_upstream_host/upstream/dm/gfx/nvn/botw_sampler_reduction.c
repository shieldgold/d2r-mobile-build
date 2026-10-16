// NVN sampler reduction-filter adapters.
//
// The existing Metal sampler path uses ordinary weighted filtering. Apple's Metal headers specify
// MTLSamplerReductionModeWeightedAverage as the descriptor default, so NVN AVERAGE needs no added
// object state or backend mutation. MIN and MAX require separately verified backend support.
#include "../../host/dm.h"
#include "nvn_protos.h"
#include "nvn_obj.h"
#include <unistd.h>

_Static_assert(NVN_SAMPLER_REDUCTION_AVERAGE == 0 && NVN_SAMPLER_REDUCTION_MIN == 1 &&
               NVN_SAMPLER_REDUCTION_MAX == 2, "NVN sampler reduction enum values");

typedef struct { const char *name; void *fn; } sampler_reduction_entry;
#define API(name, ret, ...) \
    static ret impl_##name(__VA_ARGS__); \
    static const NVNT_##name check_##name __attribute__((unused)) = impl_##name; \
    __attribute__((used, section("__DATA,swnvn"))) static const sampler_reduction_entry entry_##name = {#name, (void *)impl_##name}; \
    static ret impl_##name(__VA_ARGS__)

static void reduction_unsupported(const char *reason) {
    LOG("UNIMPL NVN sampler reduction filter: %s", reason);
    const char *stop = getenv("DM_STOP_ON_UNIMPL");
    if (stop && *stop && strcmp(stop, "0")) _exit(3);
    abort();
}

API(nvnSamplerBuilderSetReductionFilter, void, NVNsamplerBuilder *builder, NVNsamplerReduction mode) {
    if (!builder) reduction_unsupported("null sampler builder");
    if (mode != NVN_SAMPLER_REDUCTION_AVERAGE)
        reduction_unsupported(mode == NVN_SAMPLER_REDUCTION_MIN ? "minimum reduction is unsupported" :
                              mode == NVN_SAMPLER_REDUCTION_MAX ? "maximum reduction is unsupported" :
                              "invalid reduction mode");
}

API(nvnSamplerBuilderGetReductionFilter, NVNsamplerReduction, const NVNsamplerBuilder *builder) {
    if (!builder) reduction_unsupported("null sampler builder");
    return NVN_SAMPLER_REDUCTION_AVERAGE;
}

API(nvnSamplerGetReductionFilter, NVNsamplerReduction, const NVNsampler *sampler) {
    if (!sampler || ((const samp_t *)sampler)->magic != MAGIC('S', 'M', 'P', 0))
        reduction_unsupported("sampler is not initialized");
    return NVN_SAMPLER_REDUCTION_AVERAGE;
}
