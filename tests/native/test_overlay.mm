#include "baofang_overlay.h"

#import <AppKit/AppKit.h>

#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <thread>

namespace {
int checks = 0;

void check(bool condition, const char *name) {
    ++checks;
    if (!condition) {
        std::cerr << "FAIL: " << name << '\n';
        std::exit(1);
    }
}

NSDictionary *readWindows() {
    char *json = bf_overlay_windows_json();
    check(json != nullptr, "owned JSON allocation returned");
    NSData *data = [[NSString stringWithUTF8String:json] dataUsingEncoding:NSUTF8StringEncoding];
    bf_overlay_free(json);
    NSDictionary *payload = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    check([payload isKindOfClass:[NSDictionary class]], "window enumeration is valid UTF-8 JSON");
    return payload;
}
} // namespace

int main() {
    @autoreleasepool {
        NSDictionary *payload = readWindows();
        check([payload[@"schemaVersion"] intValue] == 1, "metadata schema version");
        check([payload[@"windows"] isKindOfClass:[NSArray class]], "window list present");
        check([payload[@"available"] isKindOfClass:[NSNumber class]], "availability explicit");
        check([payload[@"namesAvailable"] isKindOfClass:[NSNumber class]], "missing names explicit");
        bool reachedHiddenWindow = false;
        NSMutableSet *seenIDs = [NSMutableSet set];
        for (NSDictionary *window in payload[@"windows"]) {
            check([window[@"windowID"] isKindOfClass:[NSNumber class]], "window ID is numeric");
            check(![seenIDs containsObject:window[@"windowID"]], "window metadata is not duplicated");
            [seenIDs addObject:window[@"windowID"]];
            const bool onScreen = [window[@"onScreen"] boolValue];
            check(!onScreen || !reachedHiddenWindow, "visible front-to-back list precedes hidden metadata");
            reachedHiddenWindow = reachedHiddenWindow || !onScreen;
            check([window[@"pid"] isKindOfClass:[NSNumber class]], "window PID is numeric");
            check([window[@"layer"] isKindOfClass:[NSNumber class]], "window layer is numeric");
            check(std::isfinite([window[@"alpha"] doubleValue]), "alpha finite");
            NSDictionary *bounds = window[@"bounds"];
            check([bounds isKindOfClass:[NSDictionary class]] && bounds.count == 4,
                  "geometric bounds present");
            for (NSString *key in @[@"x", @"y", @"width", @"height"]) {
                check(std::isfinite([bounds[key] doubleValue]), "bound finite");
            }
            if (![window[@"serato"] boolValue]) {
                check(window[@"title"] == nil && window[@"bundleID"] == nil &&
                          window[@"executablePath"] == nil,
                      "other application title and identity are never returned");
            } else {
                check([window[@"bundleID"] isEqualToString:@"com.serato.seratodj"] ||
                          [window[@"bundleID"] isEqualToString:@"com.serato.seratovideo"],
                      "Serato identity exact allowlist");
                check([window[@"nameAvailable"] isKindOfClass:[NSNumber class]],
                      "Serato title availability explicit");
            }
        }
        for (uintptr_t handle : {uintptr_t(0), uintptr_t(1), uintptr_t(0x1234),
                                 std::numeric_limits<uintptr_t>::max()}) {
            check(!bf_overlay_configure(handle), "invalid handle cannot be configured");
            check(!bf_overlay_raise(handle), "invalid handle cannot be raised");
            check(!bf_overlay_set_host_layer(handle, 3), "invalid handle cannot change level");
            char *stateJson = bf_overlay_window_state_json(handle);
            check(stateJson != nullptr, "invalid view diagnostics returned");
            NSData *stateData = [[NSString stringWithUTF8String:stateJson]
                dataUsingEncoding:NSUTF8StringEncoding];
            bf_overlay_free(stateJson);
            NSDictionary *state = [NSJSONSerialization JSONObjectWithData:stateData options:0 error:nil];
            check([state isKindOfClass:[NSDictionary class]] && ![state[@"available"] boolValue],
                  "invalid view diagnostic unavailable");
        }
        bf_overlay_free(nullptr);
        if ([payload[@"available"] boolValue]) {
            // Our own hidden test panel exercises address validation after an
            // NSApplication and real view hierarchy exist. It never becomes
            // visible, key, or active and cannot affect another application.
            [NSApplication sharedApplication];
            [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];
            NSPanel *panel = [[NSPanel alloc]
                initWithContentRect:NSMakeRect(20, 20, 40, 40)
                styleMask:NSWindowStyleMaskBorderless
                backing:NSBackingStoreBuffered defer:NO];
            panel.releasedWhenClosed = NO;
            const uintptr_t view = reinterpret_cast<uintptr_t>((__bridge void *)panel.contentView);
            const NSRect originalFrame = panel.frame;
            check(bf_overlay_configure(view), "owned live native view accepted");
            check(!panel.opaque && panel.backgroundColor.alphaComponent == 0,
                  "panel remains transparent");
            check(!panel.hasShadow && panel.ignoresMouseEvents && !panel.hidesOnDeactivate,
                  "pass-through panel does not hide on deactivation");
            check(panel.level == NSFloatingWindowLevel, "floating overlay level");
            check(bf_overlay_set_host_layer(view, 3) && panel.level == 4,
                  "overlay rises above layer 3 Serato host");
            check(!bf_overlay_set_host_layer(view, 4) && panel.level == 4,
                  "unsupported host layer rejected without mutation");
            check(bf_overlay_set_host_layer(view, 0) && panel.level == NSFloatingWindowLevel,
                  "layer 0 Serato host uses floating overlay");
            check(panel.collectionBehavior == (NSWindowCollectionBehaviorCanJoinAllSpaces |
                        NSWindowCollectionBehaviorStationary | NSWindowCollectionBehaviorIgnoresCycle |
                        NSWindowCollectionBehaviorFullScreenAuxiliary),
                  "space and fullscreen collection behavior");
            check((panel.styleMask & NSWindowStyleMaskNonactivatingPanel) != 0,
                  "panel cannot activate its application");
            check(!panel.visible && !panel.keyWindow && NSEqualRects(panel.frame, originalFrame),
                  "configuration neither presents nor moves the panel");
            char *stateJson = bf_overlay_window_state_json(view);
            check(stateJson != nullptr, "own window state allocation");
            NSData *stateData = [[NSString stringWithUTF8String:stateJson]
                dataUsingEncoding:NSUTF8StringEncoding];
            bf_overlay_free(stateJson);
            NSDictionary *state = [NSJSONSerialization JSONObjectWithData:stateData options:0 error:nil];
            check([state[@"available"] boolValue] && ![state[@"opaque"] boolValue] &&
                      [state[@"backgroundAlpha"] doubleValue] == 0 &&
                      [state[@"ignoresMouseEvents"] boolValue],
                  "own window diagnostics report transparency and input pass-through");
            check([state[@"level"] integerValue] == panel.level &&
                      [state[@"windowID"] integerValue] == panel.windowNumber &&
                      [state[@"coordinateSystem"] isEqualToString:@"appkit_bottom_left"],
                  "own window diagnostics preserve native identity and coordinates");
            for (uintptr_t handle : {uintptr_t(0), uintptr_t(1), uintptr_t(0x1234),
                                     std::numeric_limits<uintptr_t>::max()}) {
                check(!bf_overlay_configure(handle), "invalid pointer rejected with live NSApplication");
                check(!bf_overlay_raise(handle), "invalid raise rejected with live NSApplication");
                check(!bf_overlay_set_host_layer(handle, 3), "invalid level pointer rejected with live app");
            }
            bool configuredOffThread = true;
            bool raisedOffThread = true;
            std::thread background([&] {
                configuredOffThread = bf_overlay_configure(view);
                raisedOffThread = bf_overlay_raise(view);
            });
            background.join();
            check(!configuredOffThread && !raisedOffThread, "window mutation requires the main thread");
            [panel close];
        }
        for (int iteration = 0; iteration != 10; ++iteration) {
            readWindows();
        }
        std::cout << "PASS: " << checks << " native overlay checks; "
                  << [payload[@"windows"] count] << " current windows enumerated\n";
    }
    return 0;
}
