#import "BOTWEventLog.h"
#include <dispatch/dispatch.h>
#include <stdio.h>
#include "graphics.h"

static BOOL observe_graphics_lock;
static BOOL lock_observation_started;
static unsigned lock_observations_emitted; /* Dedicated serial observer queue only. */
static dispatch_queue_t lock_observation_queue;

/* Both public entry points are called on the frontend main thread. Once
 * queued, enabled is immutable and only this serial queue writes the cap. */
int botw_frontend_enable_graphics_lock_observation(void) {
    if (lock_observation_started) return 0;
    observe_graphics_lock = botw_graphics_enable_lock_observation() != 0;
    return observe_graphics_lock;
}

static const char *lock_observation_status_name(botw_graphics_lock_observation_status status) {
    switch (status) {
        case BOTW_GRAPHICS_LOCK_OBSERVATION_DISABLED: return "disabled";
        case BOTW_GRAPHICS_LOCK_OBSERVATION_IDLE: return "idle";
        case BOTW_GRAPHICS_LOCK_OBSERVATION_HELD: return "held";
        case BOTW_GRAPHICS_LOCK_OBSERVATION_UNSTABLE: return "unstable";
        default: return "unavailable";
    }
}
static void log_host_lock_observation(void) {
    if (!observe_graphics_lock || lock_observations_emitted >= 4) return;
    unsigned sample = ++lock_observations_emitted;
    botw_graphics_lock_observation value = {0};
    botw_graphics_lock_observation_status status = botw_graphics_read_lock_observation(&value);
    BOOL payload = status == BOTW_GRAPHICS_LOCK_OBSERVATION_IDLE || status == BOTW_GRAPHICS_LOCK_OBSERVATION_HELD;
    if (status == BOTW_GRAPHICS_LOCK_OBSERVATION_HELD && value.sampled_ns < value.acquired_ns) {
        status = BOTW_GRAPHICS_LOCK_OBSERVATION_UNAVAILABLE; payload = NO;
    }
    fprintf(stderr, "host_graphics_lock_observation sample=%u status=%s", sample, lock_observation_status_name(status));
    if (payload) {
        uint64_t held = status == BOTW_GRAPHICS_LOCK_OBSERVATION_HELD ? value.sampled_ns - value.acquired_ns : 0;
        fprintf(stderr, " lifecycle=%llu generation=%llu owner=%llu acquired_ns=%llu sampled_ns=%llu held_ns=%llu outer=%.*s active=%.*s",
            (unsigned long long)value.lifecycle, (unsigned long long)value.generation,
            (unsigned long long)value.owner_thread_id, (unsigned long long)value.acquired_ns,
            (unsigned long long)value.sampled_ns, (unsigned long long)held,
            (int)sizeof value.outer_entry, value.outer_entry, (int)sizeof value.active_entry, value.active_entry);
    }
    fprintf(stderr, " gameplay_verified=false\n"); fflush(stderr);
}
void botw_frontend_start_graphics_lock_observations(void) {
    if (lock_observation_started) return;
    lock_observation_started = YES;
    if (!observe_graphics_lock) return;
    lock_observation_queue = dispatch_queue_create("local.botw.host-lock-observation", DISPATCH_QUEUE_SERIAL);
    /* Finite host diagnostics only. This queue never drives display, VI or input. */
    const int64_t delays[] = {10, 30, 90, 180};
    for (unsigned i = 0; i < sizeof delays / sizeof delays[0]; ++i) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, delays[i] * NSEC_PER_SEC), lock_observation_queue, ^{
            log_host_lock_observation();
        });
    }
}

static NSString *messageTail(NSString *message, NSUInteger maximum) {
    if (!maximum) return @"";
    NSUInteger start = message.length > maximum ? message.length - maximum : 0;
    if (start && start < message.length) {
        NSRange composed = [message rangeOfComposedCharacterSequenceAtIndex:start];
        if (composed.location < start) start = NSMaxRange(composed);
    }
    return [message substringFromIndex:start];
}

@implementation BOTWEventLog {
    NSLock *_lock;
    NSMutableArray<NSString *> *_messages;
    BOTWEventLogHandler _handler;
    NSUInteger _limit, _characters;
    BOOL _scheduled, _reveal;
}
- (instancetype)initWithCharacterLimit:(NSUInteger)limit handler:(BOTWEventLogHandler)handler {
    if (!limit || limit > 1024u * 1024u || !handler) return nil;
    self = [super init];
    if (self) {
        _limit = limit; _handler = [handler copy];
        _lock = [NSLock new]; _messages = [NSMutableArray new];
    }
    return self;
}
- (void)appendMessage:(NSString *)message reveal:(BOOL)reveal {
    if (!message) return;
    @autoreleasepool {
        /* This new bounded string owns its characters, including for mutable
         * or oversized input. No queued block retains an individual event. */
        NSString *line = [messageTail(message, _limit - 1) stringByAppendingString:@"\n"];
        [_lock lock];
        while (_messages.count && (_messages.count >= 512 || _characters > _limit - line.length)) {
            _characters -= _messages.firstObject.length;
            [_messages removeObjectAtIndex:0];
        }
        [_messages addObject:line]; _characters += line.length; _reveal |= reveal;
        BOOL schedule = !_scheduled; _scheduled = YES;
        [_lock unlock];
        if (schedule) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 50 * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
                [self flush];
            });
        }
    }
}
- (void)flush {
    NSAssert(NSThread.isMainThread, @"UI log flush must run on the main thread");
    @autoreleasepool {
        [_lock lock];
        NSString *snapshot = [_messages componentsJoinedByString:@""];
        BOOL reveal = _reveal; _reveal = NO; _scheduled = NO;
        [_lock unlock];
        _handler(snapshot, reveal);
    }
}
@end
