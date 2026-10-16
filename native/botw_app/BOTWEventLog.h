/* Bounded UI history; full file logging remains the caller's responsibility. */
#import <Foundation/Foundation.h>

/* Finite host-only diagnostics, independent of the UI logger below. Both
 * functions are main-thread-only. Enable only on explicit opt-in before the
 * first serialized graphics call. A failed pre-start enable stays disabled;
 * calls after Start return 0 without changing queued observation. Neither
 * function reports an NVN error. Start is process-once and schedules at most four reads
 * on its own serial GCD queue; it never drives display/input or takes UI locks. */
int botw_frontend_enable_graphics_lock_observation(void);
void botw_frontend_start_graphics_lock_observations(void);

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
