#import "BOTWEventLog.h"
#include <dispatch/dispatch.h>

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
