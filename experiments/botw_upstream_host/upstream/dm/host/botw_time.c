// BotW nn::time adapter: steady time follows the guest counter, the user clock
// follows host realtime, and calendar conversion uses the host time zone.
// Contracts were verified against the BotW 1.9.0 SDK wrappers; nntimeStartup is
// owned by title_botw.c and is not changed here.
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include <errno.h>
#include <limits.h>
#include <stdatomic.h>
#include <stddef.h>
#include <time.h>

typedef struct { int64_t seconds; uint8_t source_id[16]; } botw_steady_point;
typedef struct { int16_t year; int8_t month, day, hour, minute, second; uint8_t pad; } botw_calendar_time;
typedef struct {
    uint32_t day_of_week, day_of_year;
    char zone_name[8];
    uint8_t is_dst, pad[3];
    int32_t utc_offset;
} botw_calendar_info;
_Static_assert(sizeof(botw_steady_point) == 24 && offsetof(botw_steady_point, source_id) == 8, "SteadyClockTimePoint layout");
_Static_assert(sizeof(botw_calendar_time) == 8 && offsetof(botw_calendar_time, month) == 2 &&
               offsetof(botw_calendar_time, second) == 6 && offsetof(botw_calendar_time, pad) == 7, "CalendarTime layout");
_Static_assert(sizeof(botw_calendar_info) == 24 && offsetof(botw_calendar_info, day_of_year) == 4 &&
               offsetof(botw_calendar_info, zone_name) == 8 && offsetof(botw_calendar_info, is_dst) == 16 &&
               offsetof(botw_calendar_info, utc_offset) == 20, "CalendarAdditionalInfo layout");
_Static_assert(sizeof(time_t) == sizeof(int64_t) && (time_t)-1 < 0, "host time_t must be signed 64-bit");

#define TIME_NOT_INITIALIZED 0x74u
#define TIME_NETWORK_UNAVAILABLE 0xcc74u
#define TIME_SPAN_NULL_OUTPUT 0x70a74u
#define TIME_SPAN_SOURCE_MISMATCH 0x19074u
#define TIME_SPAN_OVERFLOW 0x19274u
#define TIME_NS_PER_SECOND 1000000000ull
_Static_assert(UINT64_MAX / TIME_NS_PER_SECOND <= (uint64_t)INT64_MAX, "steady seconds always fit int64");

static int host_realtime(struct timespec *ts) { return clock_gettime(CLOCK_REALTIME, ts); }
// Test seams: unit tests replace these before the first initialization.
static int (*time_realtime)(struct timespec *) = host_realtime;
static void (*time_entropy)(void *, size_t) = arc4random_buf;

static pthread_once_t time_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t time_lock = PTHREAD_MUTEX_INITIALIZER;
static _Atomic uint32_t time_refs;
static uint8_t time_source_id[16];   // Written once per process; stable across Finalize/Initialize
static int64_t time_fixed_wall = -1; // DM_FIXED_TIME seconds, or -1 to follow host realtime
static atomic_flag time_network_logged = ATOMIC_FLAG_INIT;
static atomic_flag time_calendar_logged = ATOMIC_FLAG_INIT;

// DM_FIXED_TIME keeps its existing meaning: a base-0 (decimal, 0x hex or 0 octal)
// non-negative int64 that replaces the user clock. Signs, whitespace and trailing
// characters are rejected rather than ignored.
static int time_parse_fixed(const char *s, int64_t *out) {
    if (*s < '0' || *s > '9') return 0;
    char *end; errno = 0;
    long long v = strtoll(s, &end, 0);
    if (errno || *end || v < 0) return 0;
    *out = v;
    return 1;
}
static void time_process_init(void) {
    static const uint8_t zero[16];
    int attempts = 0;
    do {
        if (attempts++ == 4) die("BotW time: host entropy produced an all-zero steady clock source ID");
        time_entropy(time_source_id, sizeof time_source_id);
    } while (!memcmp(time_source_id, zero, sizeof zero));
    const char *fixed = getenv("DM_FIXED_TIME");
    if (fixed && !time_parse_fixed(fixed, &time_fixed_wall))
        die("BotW time: DM_FIXED_TIME=\"%s\" is not a non-negative int64 (decimal, 0x hex or 0 octal)", fixed);
    tzset();
    if (time_fixed_wall >= 0)
        LOG("BotW time: steady clock follows the guest counter; user clock fixed at %lld (DM_FIXED_TIME)", (long long)time_fixed_wall);
    else
        LOG("BotW time: steady clock follows the guest counter; user clock follows host realtime");
}
static int time_ready(void) { return atomic_load(&time_refs) != 0; }

HLE_FORCE("_ZN2nn4time10InitializeEv", "BotW: host time adapter over the guest counter and host clocks", Result,
          botw_time_initialize, void) {
    pthread_once(&time_once, time_process_init);
    pthread_mutex_lock(&time_lock);
    uint32_t refs = atomic_load(&time_refs);
    if (refs == UINT32_MAX) die("BotW time: initialization reference count overflow");
    atomic_store(&time_refs, refs + 1);
    pthread_mutex_unlock(&time_lock);
    return RES_OK;
}
// The SDK returns 0 on every path, including finalization without initialization.
HLE_FORCE("_ZN2nn4time8FinalizeEv", "BotW: release one host time adapter reference", Result,
          botw_time_finalize, void) {
    pthread_mutex_lock(&time_lock);
    uint32_t refs = atomic_load(&time_refs);
    if (refs) atomic_store(&time_refs, refs - 1);
    pthread_mutex_unlock(&time_lock);
    return RES_OK;
}
HLE_FORCE("_ZN2nn4time13IsInitializedEv", "BotW: report host time adapter state", int,
          botw_time_is_initialized, void) {
    pthread_mutex_lock(&time_lock);
    int on = atomic_load(&time_refs) != 0;
    pthread_mutex_unlock(&time_lock);
    return on;
}

// The SDK reports whole seconds plus a 16-byte source ID; the ID is never zero here,
// so the zero-ID Result the SDK can return after writing the output does not occur.
HLE_FORCE("_ZN2nn4time19StandardSteadyClock19GetCurrentTimePointEPNS0_20SteadyClockTimePointE",
          "BotW: steady clock in whole seconds of the guest counter", Result,
          botw_time_steady_now, botw_steady_point *out) {
    if (!time_ready()) return TIME_NOT_INITIALIZED;
    if (!out) die("BotW time: null steady clock output");
    botw_steady_point point = {.seconds = (int64_t)(guest_ns() / TIME_NS_PER_SECOND)};
    memcpy(point.source_id, time_source_id, sizeof point.source_id);
    memcpy(out, &point, sizeof point);
    return RES_OK;
}

// Pure operation: valid with or without initialization. Failure leaves *out untouched.
HLE_FORCE("_ZN2nn4time14GetSpanBetweenEPlRKNS0_20SteadyClockTimePointES4_",
          "BotW: seconds between steady clock points of one source", Result,
          botw_time_span, int64_t *out, const botw_steady_point *first, const botw_steady_point *second) {
    if (!out) return TIME_SPAN_NULL_OUTPUT;
    if (!first || !second) die("BotW time: null steady clock point in span");
    if (memcmp(first->source_id, second->source_id, sizeof first->source_id)) return TIME_SPAN_SOURCE_MISMATCH;
    int64_t span;
    if (__builtin_sub_overflow(second->seconds, first->seconds, &span)) return TIME_SPAN_OVERFLOW;
    *out = span;
    return RES_OK;
}

HLE_FORCE("_ZN2nn4time23StandardUserSystemClock14GetCurrentTimeEPNS0_9PosixTimeE",
          "BotW: user clock from host realtime or DM_FIXED_TIME", Result,
          botw_time_user_now, int64_t *out) {
    if (!time_ready()) return TIME_NOT_INITIALIZED;
    if (!out) die("BotW time: null user clock output");
    int64_t now = time_fixed_wall;
    if (now < 0) {
        struct timespec ts;
        if (time_realtime(&ts)) die("BotW time: host realtime clock failed (errno %d)", errno);
        now = (int64_t)ts.tv_sec;
    }
    *out = now;
    return RES_OK;
}

// The host has no verified network-synchronized clock; report it unavailable and
// leave the output untouched rather than presenting host realtime as network time.
HLE_FORCE("_ZN2nn4time26StandardNetworkSystemClock14GetCurrentTimeEPNS0_9PosixTimeE",
          "BotW: report the network clock unavailable", Result,
          botw_time_network_now, int64_t *out) {
    (void)out;
    if (!time_ready()) return TIME_NOT_INITIALIZED;
    if (!atomic_flag_test_and_set(&time_network_logged))
        LOG("BotW time: network clock unavailable (Result %#x)", TIME_NETWORK_UNAVAILABLE);
    return TIME_NETWORK_UNAVAILABLE;
}

// Conversion failures the SDK would report through an unrecovered service Result
// are fatal here; no output is written unless the whole conversion is representable.
HLE_FORCE("_ZN2nn4time14ToCalendarTimeEPNS0_12CalendarTimeEPNS0_22CalendarAdditionalInfoERKNS0_9PosixTimeE",
          "BotW: calendar conversion in the host time zone", Result,
          botw_time_to_calendar, botw_calendar_time *out, botw_calendar_info *info, const int64_t *posix) {
    if (!time_ready()) return TIME_NOT_INITIALIZED;
    if (!out || !posix) die("BotW time: null calendar output or input");
    time_t t = (time_t)*posix;
    struct tm tm;
    if (!localtime_r(&t, &tm)) die("BotW time: host cannot convert %lld to local time (errno %d)", (long long)*posix, errno);
    int year;
    if (__builtin_add_overflow(tm.tm_year, 1900, &year) || year < INT16_MIN || year > INT16_MAX)
        die("BotW time: calendar year for %lld is outside the signed 16-bit range", (long long)*posix);
    if (tm.tm_mon < 0 || tm.tm_mon > 11 || tm.tm_mday < 1 || tm.tm_mday > 31 || tm.tm_hour < 0 || tm.tm_hour > 23 ||
        tm.tm_min < 0 || tm.tm_min > 59 || tm.tm_sec < 0 || tm.tm_sec > 60 || tm.tm_wday < 0 || tm.tm_wday > 6 ||
        tm.tm_yday < 0 || tm.tm_yday > 365 || tm.tm_isdst < 0)
        die("BotW time: host returned an out-of-range local time for %lld", (long long)*posix);
    botw_calendar_time cal = {(int16_t)year, (int8_t)(tm.tm_mon + 1), (int8_t)tm.tm_mday,
                              (int8_t)tm.tm_hour, (int8_t)tm.tm_min, (int8_t)tm.tm_sec, 0};
    botw_calendar_info extra = {0};
    if (info) {
        if (tm.tm_gmtoff < INT32_MIN || tm.tm_gmtoff > INT32_MAX)
            die("BotW time: host UTC offset %ld does not fit 32 bits", (long)tm.tm_gmtoff);
        extra.day_of_week = (uint32_t)tm.tm_wday;
        extra.day_of_year = (uint32_t)tm.tm_yday;
        // Host policy: zero-pad short abbreviations and truncate long ones to
        // the SDK's eight-byte field. A full field need not contain a terminator.
        if (tm.tm_zone) memcpy(extra.zone_name, tm.tm_zone, strnlen(tm.tm_zone, sizeof extra.zone_name));
        extra.is_dst = tm.tm_isdst > 0;
        extra.utc_offset = (int32_t)tm.tm_gmtoff;
    }
    if (!atomic_flag_test_and_set(&time_calendar_logged))
        LOG("BotW time: calendar conversion uses host zone %s (UTC offset %ld s)",
            tm.tm_zone ? tm.tm_zone : "(unknown)", (long)tm.tm_gmtoff);
    memcpy(out, &cal, sizeof cal);
    if (info) memcpy(info, &extra, sizeof extra);
    return RES_OK;
}
