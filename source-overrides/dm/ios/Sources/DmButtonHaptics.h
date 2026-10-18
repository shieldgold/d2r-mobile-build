#pragma once

// Call once for an accepted touch-down on a visible game control, on the main thread.
// Pass the monotonic UIKit event time; movement, repeats and cancellation do not call this.
void dm_button_haptic(double now);
