#import <UIKit/UIKit.h>
#include "input.h"
NS_ASSUME_NONNULL_BEGIN
/* Retain this view for the lifetime of input. Call invalidate before input destroy.
 * Attach on the main thread above the Metal view; unused screen regions pass through.
 * Reads already-connected GameController devices, without pairing/discovery changes. */
@interface BOTWInputOverlay : UIView
+ (instancetype)attachToView:(UIView *)view input:(botw_input *)input;
- (void)invalidate;
@end
NS_ASSUME_NONNULL_END
