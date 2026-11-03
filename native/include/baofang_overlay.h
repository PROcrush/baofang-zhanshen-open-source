#ifndef BAOFANG_OVERLAY_H
#define BAOFANG_OVERLAY_H

#include <stdbool.h>
#include <stdint.h>

#define BF_OVERLAY_API __attribute__((visibility("default")))

#ifdef __cplusplus
extern "C" {
#endif

/* Read-only CoreGraphics window metadata, in front-to-back order. The caller
 * owns the malloc-allocated UTF-8 JSON and must release it with bf_overlay_free.
 * Titles and application identities are included only for verified Serato
 * processes. Missing titles are explicitly unavailable; no OS permission is
 * requested. A null result indicates an allocation failure.
 */
BF_OVERLAY_API char *bf_overlay_windows_json(void);
BF_OVERLAY_API void bf_overlay_free(void *allocation);

/* qt_view_handle is QWindow::winId() on macOS, an NSView address. These calls
 * must run on the application's main thread and accept only a live view found
 * in this application's existing window hierarchy. Arbitrary or expired
 * addresses are compared, never dereferenced. The Qt caller must create a
 * frameless Tool window with WindowDoesNotAcceptFocus and TransparentForInput.
 * Neither call makes a window key nor activates its application.
 */
BF_OVERLAY_API bool bf_overlay_configure(uintptr_t qt_view_handle);
BF_OVERLAY_API bool bf_overlay_raise(uintptr_t qt_view_handle);

/* Only the verified Serato host layers 0 and 3 are supported. They map to
 * overlay levels 3 and 4 respectively. Unsupported layers never mutate a
 * window. Geometry and focus are unchanged.
 */
BF_OVERLAY_API bool bf_overlay_set_host_layer(uintptr_t qt_view_handle, int host_layer);

/* Diagnostic state of this application's own window only; same allocation
 * ownership as windows_json. frame uses AppKit's bottom-left screen space.
 * Invalid handles or off-main-thread calls return available=false.
 */
BF_OVERLAY_API char *bf_overlay_window_state_json(uintptr_t qt_view_handle);

#ifdef __cplusplus
}
#endif

#endif
