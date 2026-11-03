/* Read-only AX transport for the unmodified, exact /Applications original.
 * stdin R\n -> bounded UI snapshot JSON; Q\n / EOF / parent death -> exit.
 * This file has no AX action/write, task-port, injection or signing calls.
 */
#import <Foundation/Foundation.h>
#import <ApplicationServices/ApplicationServices.h>
#include <libproc.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/proc_info.h>
#include <time.h>
#include <unistd.h>

#ifndef REKORDBOX_TARGET_PATH
#error "An exact original executable must be bound at build"
#endif

static volatile sig_atomic_t stopping;
static pid_t parentPID, targetPID;
static uint64_t birthSec, birthUsec;
static uint64_t ownerBirthSec, ownerBirthUsec;
static AXUIElementRef application;
static AXUIElementRef windowElement;
static NSMutableArray *cached;
static double lastDiscovery;

static void stop(int value) { (void)value; stopping = 1; }
static double seconds(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return -1;
    return now.tv_sec + now.tv_nsec / 1e9;
}
static BOOL identity(BOOL initial) {
    char path[PROC_PIDPATHINFO_MAXSIZE] = {0};
    struct proc_bsdinfo info = {0};
    if (proc_pidpath(targetPID, path, sizeof(path)) <= 0 ||
        strcmp(path, REKORDBOX_TARGET_PATH) ||
        proc_pidinfo(targetPID, PROC_PIDTBSDINFO, 0, &info, sizeof(info)) != sizeof(info)) return NO;
    if (initial) { birthSec = info.pbi_start_tvsec; birthUsec = info.pbi_start_tvusec; }
    return birthSec == info.pbi_start_tvsec && birthUsec == info.pbi_start_tvusec;
}
static BOOL ownerIdentity(BOOL initial) {
    struct proc_bsdinfo info = {0};
    if (proc_pidinfo(parentPID, PROC_PIDTBSDINFO, 0, &info, sizeof(info)) != sizeof(info)) return NO;
    if (initial) { ownerBirthSec = info.pbi_start_tvsec; ownerBirthUsec = info.pbi_start_tvusec; }
    return info.pbi_start_tvsec == ownerBirthSec && info.pbi_start_tvusec == ownerBirthUsec;
}
static id copied(AXUIElementRef element, CFStringRef attribute) {
    CFTypeRef value = NULL;
    AXError error = AXUIElementCopyAttributeValue(element, attribute, &value);
    return error == kAXErrorSuccess && value ? CFBridgingRelease(value) : nil;
}
static NSString *string(id value) {
    if ([value isKindOfClass:NSString.class]) return value;
    if ([value isKindOfClass:NSNumber.class]) return [value stringValue];
    return @"";
}
static NSArray *multiple(AXUIElementRef element, NSArray *attributes) {
    CFArrayRef values = NULL;
    if (AXUIElementCopyMultipleAttributeValues(element, (__bridge CFArrayRef)attributes,
            0, &values) != kAXErrorSuccess || !values) return nil;
    return CFBridgingRelease(values);
}
static NSDictionary *error(NSString *code) { return @{@"ok": @NO, @"code": code}; }
static NSDictionary *discover(void) {
    if (windowElement) { CFRelease(windowElement); windowElement = NULL; }
    cached = [NSMutableArray new];
    NSArray *windows = copied(application, kAXWindowsAttribute);
    if (![windows isKindOfClass:NSArray.class] || windows.count != 1) return error(@"window_not_unique");
    windowElement = (__bridge AXUIElementRef)windows[0]; CFRetain(windowElement);
    AXUIElementSetMessagingTimeout(windowElement, .04f);
    if ([copied(windowElement, kAXMinimizedAttribute) boolValue]) return error(@"window_minimized");
    NSMutableArray *queue = [NSMutableArray arrayWithObject:@{ @"element": windows[0], @"depth": @0 }];
    NSArray *keys = @[(id)kAXRoleAttribute, (id)kAXValueAttribute, (id)kAXTitleAttribute,
                     (id)kAXDescriptionAttribute, (id)kAXHelpAttribute, (id)kAXPositionAttribute,
                     (id)kAXSizeAttribute, (id)kAXChildrenAttribute];
    double deadline = seconds() + 1.5;
    NSUInteger visited = 0;
    for (NSUInteger index = 0; index < queue.count; index++) {
        if (seconds() > deadline || visited++ > 700 || queue.count > 1000) return error(@"layout_scan_timeout");
        NSDictionary *item = queue[index];
        AXUIElementRef element = (__bridge AXUIElementRef)item[@"element"];
        NSArray *values = multiple(element, keys);
        if (values.count != keys.count) continue;
        NSString *role = string(values[0]);
        CGPoint position = CGPointZero; CGSize size = CGSizeZero;
        if (CFGetTypeID((__bridge CFTypeRef)values[5]) == AXValueGetTypeID())
            AXValueGetValue((__bridge AXValueRef)values[5], kAXValueCGPointType, &position);
        if (CFGetTypeID((__bridge CFTypeRef)values[6]) == AXValueGetTypeID())
            AXValueGetValue((__bridge AXValueRef)values[6], kAXValueCGSizeType, &size);
        NSString *text = string(values[1]);
        if (!text.length) text = string(values[2]);
        if (!text.length) text = string(values[3]);
        BOOL layoutControl = [text containsString:@"2Deck"] || [text isEqualToString:@"PERFORMANCE"];
        if ([role isEqualToString:(id)kAXStaticTextRole] ||
            [role isEqualToString:(id)kAXImageRole] || layoutControl) {
            [cached addObject:@{@"element": item[@"element"], @"role": role,
                @"order": @(index), @"x": @(position.x), @"y": @(position.y),
                @"width": @(size.width), @"height": @(size.height)}];
        }
        /* Collection rows have no deck-clock data; do not enumerate songs. */
        if ([role isEqualToString:(id)kAXTableRole] || [role isEqualToString:(id)kAXOutlineRole] ||
            [role isEqualToString:(id)kAXListRole] || [role isEqualToString:(id)kAXMenuRole] ||
            [item[@"depth"] unsignedIntegerValue] >= 12) continue;
        id children = values[7];
        if ([children isKindOfClass:NSArray.class]) {
            for (id child in children) [queue addObject:@{@"element": child,
                @"depth": @([item[@"depth"] unsignedIntegerValue] + 1)}];
        }
    }
    if (!cached.count || cached.count > 200) return error(@"layout_scalar_bounds");
    lastDiscovery = seconds();
    return nil;
}
static NSArray *scalarSnapshot(void) {
    NSMutableArray *result = [NSMutableArray new];
    NSArray *keys = @[(id)kAXValueAttribute, (id)kAXTitleAttribute, (id)kAXDescriptionAttribute];
    for (NSDictionary *entry in cached) {
        if ([entry[@"role"] isEqualToString:(id)kAXImageRole]) {
            NSMutableDictionary *node = [entry mutableCopy];
            [node removeObjectForKey:@"element"]; node[@"text"] = @"";
            [result addObject:node]; continue;
        }
        AXUIElementRef element = (__bridge AXUIElementRef)entry[@"element"];
        NSArray *values = multiple(element, keys);
        if (values.count != keys.count) return nil;
        NSString *text = string(values[0]);
        if (!text.length) text = string(values[1]);
        if (!text.length) text = string(values[2]);
        if (text.length > 4096) return nil;
        NSMutableDictionary *node = [entry mutableCopy];
        [node removeObjectForKey:@"element"]; node[@"text"] = text;
        [result addObject:node];
    }
    return result;
}
static NSDictionary *snapshot(void) {
    if (!identity(NO)) return error(@"process_identity_changed");
    if (!AXIsProcessTrusted()) return error(@"accessibility_permission_required");
    BOOL discovery = !cached || !windowElement || seconds() - lastDiscovery > 2;
    if (discovery) { NSDictionary *failure = discover(); if (failure) return failure; }
    if ([copied(windowElement, kAXMinimizedAttribute) boolValue]) return error(@"window_minimized");
    NSArray *first = scalarSnapshot(), *second = first ? scalarSnapshot() : nil;
    if (!first || !second) { cached = nil; return error(@"ax_fields_unavailable"); }
    if (!identity(NO)) return error(@"process_identity_changed");
    return @{@"ok": @YES, @"first": first, @"second": second,
        @"discovery": @(discovery), @"process": @{@"pid": @(targetPID),
            @"birthSec": @(birthSec), @"birthUsec": @(birthUsec), @"executableVerified": @YES}};
}
static BOOL emitJSON(NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:NULL];
    if (!data || data.length >= 256 * 1024) return NO;
    /* A large blocking FIFO write can wait for space before the reader sees
     * readiness on macOS. LaunchServices also holds stdout read/write, so a
     * vanished host need not produce EPIPE. Bound both size and write time. */
    int flags = fcntl(STDOUT_FILENO, F_GETFL);
    if (flags < 0 || fcntl(STDOUT_FILENO, F_SETFL, flags | O_NONBLOCK) < 0) return NO;
    NSMutableData *line = [data mutableCopy];
    [line appendBytes:"\n" length:1];
    const uint8_t *bytes = line.bytes;
    NSUInteger offset = 0;
    double deadline = seconds() + .5;
    while (offset < line.length) {
        if (stopping || seconds() > deadline || (ownerBirthSec && !ownerIdentity(NO))) return NO;
        ssize_t written = write(STDOUT_FILENO, bytes + offset, MIN((NSUInteger)4096, line.length - offset));
        if (written > 0) { offset += (NSUInteger)written; continue; }
        if (written < 0 && errno != EAGAIN && errno != EINTR) return NO;
        struct pollfd output = {STDOUT_FILENO, POLLOUT, 0};
        (void)poll(&output, 1, 20);
    }
    return YES;
}
int main(int argc, char **argv) { @autoreleasepool {
    if (argc < 2) return 64;
    BOOL requestAccess = NO;
    char *end = NULL; long value = strtol(argv[1], &end, 10);
    if (!end || *end || value <= 0 || value > INT_MAX) return 64;
    targetPID = (pid_t)value; parentPID = getppid();
    for (int index = 2; index < argc; index++) {
        if (!strcmp(argv[index], "--request-access") && !requestAccess) requestAccess = YES;
        else if (!strcmp(argv[index], "--owner-pid") && index + 1 < argc) {
            end = NULL; long owner = strtol(argv[++index], &end, 10);
            if (!end || *end || owner <= 1 || owner > INT_MAX) return 64;
            parentPID = (pid_t)owner;
        } else return 64;
    }
    signal(SIGTERM, stop); signal(SIGINT, stop); signal(SIGPIPE, SIG_IGN);
    if (!identity(YES)) { emitJSON(error(@"process_identity_unavailable")); return 2; }
    if (!ownerIdentity(YES)) { emitJSON(error(@"owner_identity_unavailable")); return 2; }
    /* An explicit user-facing request may open the system permission prompt.
     * It never grants permission or changes TCC; ordinary reads never prompt. */
    if (requestAccess) {
        NSDictionary *options = @{(__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES};
        (void)AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)options);
    }
    application = AXUIElementCreateApplication(targetPID);
    AXUIElementSetMessagingTimeout(application, .04f);
    if (!emitJSON(@{@"ok": @YES, @"ready": @YES})) return 2;
    char line[16];
    while (!stopping && ownerIdentity(NO)) {
        struct pollfd item = {STDIN_FILENO, POLLIN, 0};
        int ready = poll(&item, 1, 250);
        if (ready < 0) continue;
        if (!ready) continue;
        if (!fgets(line, sizeof(line), stdin) || !strcmp(line, "Q\n")) break;
        if (strcmp(line, "R\n")) { emitJSON(error(@"invalid_request")); break; }
        @autoreleasepool { if (!emitJSON(snapshot())) break; }
    }
    if (windowElement) CFRelease(windowElement);
    if (application) CFRelease(application);
    return 0;
} }
