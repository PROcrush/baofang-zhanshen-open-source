#include "baofang_overlay.h"

#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>

#include <cmath>
#include <cstdlib>
#include <cstring>

namespace {

char *copyJson(NSDictionary *payload) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:payload options:0 error:nil];
    if (data == nil) {
        return nullptr;
    }
    char *result = static_cast<char *>(std::malloc(data.length + 1));
    if (result != nullptr) {
        std::memcpy(result, data.bytes, data.length);
        result[data.length] = '\0';
    }
    return result;
}

char *unavailableJson() {
    return copyJson(@{@"schemaVersion": @1, @"available": @NO,
                      @"namesAvailable": @NO, @"windows": @[],
                      @"error": @"window_list_unavailable"});
}

bool isSerato(NSRunningApplication *application) {
    NSString *identifier = application.bundleIdentifier;
    NSURL *bundleURL = application.bundleURL;
    NSURL *executableURL = application.executableURL;
    if (bundleURL == nil || executableURL == nil) {
        return false;
    }
    NSString *name = nil;
    if ([identifier isEqualToString:@"com.serato.seratodj"]) {
        name = @"Serato DJ Pro";
    } else if ([identifier isEqualToString:@"com.serato.seratovideo"]) {
        name = @"Serato Video";
    } else {
        return false;
    }
    NSString *expectedExecutable = [[bundleURL URLByAppendingPathComponent:
        [@"Contents/MacOS" stringByAppendingPathComponent:name]] path];
    if (![executableURL.path isEqualToString:expectedExecutable]) {
        return false;
    }
    if ([identifier isEqualToString:@"com.serato.seratodj"]) {
        return true;
    }
    // This is the verified nested Serato Video component layout used by our
    // readonly reader. The Python caller also pins its accepted producer PID
    // and path before attaching; metadata alone never selects a host window.
    NSURL *mainURL = [[[bundleURL URLByDeletingLastPathComponent]
        URLByDeletingLastPathComponent] URLByDeletingLastPathComponent];
    NSURL *expectedVideo = [mainURL URLByAppendingPathComponent:@"Contents/MacOS/Serato Video.app"];
    if (![bundleURL.path isEqualToString:expectedVideo.path]) {
        return false;
    }
    NSBundle *mainBundle = [NSBundle bundleWithURL:mainURL];
    NSBundle *videoBundle = [NSBundle bundleWithURL:bundleURL];
    if (![[mainBundle objectForInfoDictionaryKey:@"CFBundleIdentifier"]
            isEqualToString:@"com.serato.seratodj"]) {
        return false;
    }
    for (NSString *key in @[@"CFBundleShortVersionString", @"CFBundleVersion"]) {
        NSString *mainValue = [mainBundle objectForInfoDictionaryKey:key];
        NSString *videoValue = [videoBundle objectForInfoDictionaryKey:key];
        if (![mainValue isKindOfClass:[NSString class]] || mainValue.length == 0 ||
            ![videoValue isEqualToString:mainValue]) {
            return false;
        }
    }
    return true;
}

NSWindow *ownedWindow(uintptr_t handle) {
    if (handle == 0 || ![NSThread isMainThread] || NSApp == nil) {
        return nil;
    }
    for (NSWindow *window in NSApp.windows) {
        if (window.contentView == nil) {
            continue;
        }
        NSMutableArray<NSView *> *pending = [NSMutableArray arrayWithObject:window.contentView];
        while (pending.count != 0) {
            NSView *view = pending.lastObject;
            [pending removeLastObject];
            if (reinterpret_cast<uintptr_t>((__bridge void *)view) == handle) {
                return window;
            }
            [pending addObjectsFromArray:view.subviews];
        }
    }
    return nil;
}

} // namespace

extern "C" char *bf_overlay_windows_json(void) {
    @autoreleasepool {
        @try {
            CFArrayRef windows = CGWindowListCopyWindowInfo(kCGWindowListOptionAll,
                                                           kCGNullWindowID);
            if (windows == nullptr) {
                return unavailableJson();
            }
            NSArray<NSDictionary *> *allWindows = CFBridgingRelease(windows);
            CFArrayRef visibleWindows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly,
                                                                  kCGNullWindowID);
            if (visibleWindows == nullptr) {
                return unavailableJson();
            }
            // Only the on-screen query promises front-to-back order in the
            // SDK. Preserve that order for occlusion, then append hidden
            // windows for explicit host availability diagnostics.
            NSArray<NSDictionary *> *frontToBack = CFBridgingRelease(visibleWindows);
            NSMutableArray<NSDictionary *> *rawWindows = [frontToBack mutableCopy];
            NSMutableSet<NSNumber *> *visibleIDs = [NSMutableSet set];
            for (NSDictionary *raw in frontToBack) {
                NSNumber *number = raw[(__bridge NSString *)kCGWindowNumber];
                if ([number isKindOfClass:[NSNumber class]]) {
                    [visibleIDs addObject:number];
                }
            }
            for (NSDictionary *raw in allWindows) {
                NSNumber *number = raw[(__bridge NSString *)kCGWindowNumber];
                if ([number isKindOfClass:[NSNumber class]] && ![visibleIDs containsObject:number]) {
                    [rawWindows addObject:raw];
                }
            }
            NSMutableArray<NSDictionary *> *rows = [NSMutableArray array];
            NSMutableDictionary<NSNumber *, NSRunningApplication *> *applications =
                [NSMutableDictionary dictionary];
            NSMutableDictionary<NSNumber *, NSNumber *> *seratoIdentities =
                [NSMutableDictionary dictionary];
            bool namesAvailable = false;
            for (NSDictionary *raw in rawWindows) {
                NSDictionary *boundsInfo = raw[(__bridge NSString *)kCGWindowBounds];
                CGRect bounds;
                if (![boundsInfo isKindOfClass:[NSDictionary class]] ||
                    !CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)boundsInfo,
                                                             &bounds) ||
                    !std::isfinite(bounds.origin.x) || !std::isfinite(bounds.origin.y) ||
                    !std::isfinite(bounds.size.width) || !std::isfinite(bounds.size.height)) {
                    continue;
                }
                NSNumber *pid = raw[(__bridge NSString *)kCGWindowOwnerPID];
                NSNumber *windowID = raw[(__bridge NSString *)kCGWindowNumber];
                NSNumber *layer = raw[(__bridge NSString *)kCGWindowLayer];
                NSNumber *alpha = raw[(__bridge NSString *)kCGWindowAlpha];
                if (![pid isKindOfClass:[NSNumber class]] ||
                    ![windowID isKindOfClass:[NSNumber class]] ||
                    ![layer isKindOfClass:[NSNumber class]] ||
                    ![alpha isKindOfClass:[NSNumber class]] || !std::isfinite(alpha.doubleValue)) {
                    continue;
                }
                NSRunningApplication *application = nil;
                NSNumber *knownSerato = seratoIdentities[pid];
                if (knownSerato == nil) {
                    // Other windows only need geometry for composition. Querying
                    // LaunchServices metadata for every desktop process on every
                    // frame can exceed the geometry freshness deadline.
                    NSString *owner = raw[(__bridge NSString *)kCGWindowOwnerName];
                    if ([owner isEqualToString:@"Serato DJ Pro"] || [owner isEqualToString:@"Serato Video"]) {
                        application = [NSRunningApplication runningApplicationWithProcessIdentifier:pid.intValue];
                        if (application != nil) {
                            applications[pid] = application;
                        }
                    }
                    knownSerato = @(isSerato(application));
                    seratoIdentities[pid] = knownSerato;
                } else {
                    application = applications[pid];
                }
                const bool serato = knownSerato.boolValue;
                NSMutableDictionary *row = [@{
                    @"windowID": windowID, @"pid": pid, @"layer": layer,
                    @"alpha": alpha, @"onScreen": @([visibleIDs containsObject:windowID]),
                    @"bounds": @{@"x": @(bounds.origin.x), @"y": @(bounds.origin.y),
                                  @"width": @(bounds.size.width), @"height": @(bounds.size.height)},
                    @"serato": @(serato)
                } mutableCopy];
                if (serato) {
                    NSString *name = raw[(__bridge NSString *)kCGWindowName];
                    const bool hasName = [name isKindOfClass:[NSString class]] && name.length != 0;
                    row[@"nameAvailable"] = @(hasName);
                    row[@"title"] = hasName ? name : @"";
                    row[@"bundleID"] = application.bundleIdentifier;
                    row[@"executablePath"] = application.executableURL.path;
                    namesAvailable = namesAvailable || hasName;
                }
                [rows addObject:row];
            }
            return copyJson(@{@"schemaVersion": @1, @"available": @YES,
                              @"namesAvailable": @(namesAvailable), @"windows": rows});
        } @catch (NSException *) {
            return unavailableJson();
        }
    }
}

extern "C" void bf_overlay_free(void *allocation) {
    std::free(allocation);
}

extern "C" bool bf_overlay_configure(uintptr_t qt_view_handle) {
    @autoreleasepool {
        @try {
            NSWindow *window = ownedWindow(qt_view_handle);
            if (window == nil) {
                return false;
            }
            window.opaque = NO;
            window.backgroundColor = [NSColor clearColor];
            window.hasShadow = NO;
            window.ignoresMouseEvents = YES;
            window.hidesOnDeactivate = NO;
            window.level = NSFloatingWindowLevel;
            window.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
                                        NSWindowCollectionBehaviorStationary |
                                        NSWindowCollectionBehaviorIgnoresCycle |
                                        NSWindowCollectionBehaviorFullScreenAuxiliary;
            window.animationBehavior = NSWindowAnimationBehaviorNone;
            window.tabbingMode = NSWindowTabbingModeDisallowed;
            if ([window isKindOfClass:[NSPanel class]]) {
                window.styleMask |= NSWindowStyleMaskNonactivatingPanel;
                static_cast<NSPanel *>(window).becomesKeyOnlyIfNeeded = YES;
            }
            return true;
        } @catch (NSException *) {
            return false;
        }
    }
}

extern "C" bool bf_overlay_raise(uintptr_t qt_view_handle) {
    @autoreleasepool {
        @try {
            NSWindow *window = ownedWindow(qt_view_handle);
            if (window == nil) {
                return false;
            }
            [window orderFrontRegardless];
            return true;
        } @catch (NSException *) {
            return false;
        }
    }
}

extern "C" bool bf_overlay_set_host_layer(uintptr_t qt_view_handle, int host_layer) {
    @autoreleasepool {
        @try {
            if (host_layer != 0 && host_layer != 3) {
                return false;
            }
            NSWindow *window = ownedWindow(qt_view_handle);
            if (window == nil) {
                return false;
            }
            window.level = host_layer == 3 ? 4 : NSFloatingWindowLevel;
            return true;
        } @catch (NSException *) {
            return false;
        }
    }
}

extern "C" char *bf_overlay_window_state_json(uintptr_t qt_view_handle) {
    @autoreleasepool {
        @try {
            NSWindow *window = ownedWindow(qt_view_handle);
            if (window == nil) {
                return copyJson(@{@"schemaVersion": @1, @"available": @NO,
                                  @"error": @"invalid_owned_view"});
            }
            const NSRect frame = window.frame;
            return copyJson(@{
                @"schemaVersion": @1, @"available": @YES,
                @"opaque": @(window.opaque),
                @"backgroundAlpha": @(window.backgroundColor.alphaComponent),
                @"ignoresMouseEvents": @(window.ignoresMouseEvents),
                @"canBecomeKey": @(window.canBecomeKeyWindow),
                @"canBecomeMain": @(window.canBecomeMainWindow),
                @"level": @(window.level), @"windowID": @(window.windowNumber),
                @"visible": @(window.visible), @"isKeyWindow": @(window.keyWindow),
                @"hasShadow": @(window.hasShadow),
                @"hidesOnDeactivate": @(window.hidesOnDeactivate),
                @"collectionBehavior": @(window.collectionBehavior),
                @"coordinateSystem": @"appkit_bottom_left",
                @"frame": @{@"x": @(frame.origin.x), @"y": @(frame.origin.y),
                             @"width": @(frame.size.width), @"height": @(frame.size.height)}
            });
        } @catch (NSException *) {
            return copyJson(@{@"schemaVersion": @1, @"available": @NO,
                              @"error": @"window_state_unavailable"});
        }
    }
}
