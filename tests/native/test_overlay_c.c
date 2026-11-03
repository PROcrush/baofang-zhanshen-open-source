#include "baofang_overlay.h"

#include <stdio.h>

int main(void) {
    char *windows;
    char *state;
    if (sizeof(uintptr_t) != sizeof(void *) || sizeof(bool) != 1 ||
        bf_overlay_configure(0) || bf_overlay_raise(0) ||
        bf_overlay_set_host_layer(0, 3)) {
        fputs("FAIL: native overlay C99 ABI\n", stderr);
        return 1;
    }
    windows = bf_overlay_windows_json();
    state = bf_overlay_window_state_json(0);
    if (windows == NULL || state == NULL) {
        bf_overlay_free(windows);
        bf_overlay_free(state);
        fputs("FAIL: C99 owned JSON allocation\n", stderr);
        return 1;
    }
    bf_overlay_free(windows);
    bf_overlay_free(state);
    bf_overlay_free(NULL);
    puts("PASS: native overlay C99 ABI and allocation ownership");
    return 0;
}
