/* Bounded UI history; full file logging remains the caller's responsibility. */
#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
typedef void (^BOTWEventLogHandler)(NSString *text, BOOL reveal);
@interface BOTWEventLog : NSObject
/* At most 512 events and characterLimit UTF-16 units are retained. A burst
 * schedules one main-queue flush per 50 ms; the handler receives a complete
 * replacement snapshot, outside the lock and in its own autorelease pool. */
- (nullable instancetype)initWithCharacterLimit:(NSUInteger)characterLimit
                                       handler:(nullable BOTWEventLogHandler)handler;
- (void)appendMessage:(nullable NSString *)message reveal:(BOOL)reveal;
- (instancetype)init NS_UNAVAILABLE;
@end
NS_ASSUME_NONNULL_END
