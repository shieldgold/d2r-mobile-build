#pragma once
#include <stdint.h>
#include <time.h>

// BotW 1.9.0 NVN device descriptors specify 625 ns per 384 raw GPU ticks.
// Keep host counter records and both conversion APIs in this 614.4 MHz domain.
// Split arithmetic avoids intermediate overflow.
static inline uint64_t nvn_clock_ticks_from_ns(uint64_t ns) {
    return (ns / 625) * 384 + (ns % 625) * 384 / 625;
}
static inline uint64_t nvn_clock_ns_from_ticks(uint64_t ticks) {
    return (ticks / 384) * 625 + (ticks % 384) * 625 / 384;
}
static inline uint64_t nvn_clock_now_ticks(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return nvn_clock_ticks_from_ns((uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec);
}
