#pragma once

// Installs the host callback used to signal a bound VI vsync event.
// The callback runs on a host display-link or timer thread and must not enter guest code.
void botw_front_set_vsync_callback(void (*callback)(void));
