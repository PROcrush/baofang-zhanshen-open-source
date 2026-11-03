/* Local read-only helper. The only permitted executable path is bound at build.
 * stdin: D\n, R <hex-address> <decimal-length>\n, Q\n. No remote writes or control.
 * One task send right; EOF, parent exit, protocol error and 300s deadline exit.
 */
#include <errno.h>
#include <ctype.h>
#include <fcntl.h>
#include <inttypes.h>
#include <libproc.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/task_info.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#ifndef REKORDBOX_TARGET_PATH
#error "A single permitted rekordbox executable path must be bound at build"
#endif

#define MAX_READ 65536U
#define MAX_ADDRESS UINT64_C(0x0000ffffffffffff)
static mach_port_t target_task = MACH_PORT_NULL;
static unsigned char *buffer;
static char *response;
static volatile sig_atomic_t stopping;
static pid_t owner_pid;
static double expires_at;

static void cleanup(void) {
    if (target_task != MACH_PORT_NULL) {
        mach_port_deallocate(mach_task_self(), target_task);
        target_task = MACH_PORT_NULL;
    }
    if (buffer) { memset(buffer, 0, MAX_READ); free(buffer); }
    free(response);
}

static void stop(int signal_number) { (void)signal_number; stopping = 1; }

static double monotonic_seconds(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return -1;
    return now.tv_sec + now.tv_nsec / 1000000000.0;
}

static int target_matches(pid_t pid) {
    char path[PROC_PIDPATHINFO_MAXSIZE] = {0};
    int length = proc_pidpath(pid, path, sizeof(path));
    return length > 0 && path[sizeof(path) - 1] == 0 &&
           strcmp(path, REKORDBOX_TARGET_PATH) == 0;
}

static int send_bytes(const char *bytes, size_t length) {
    while (length && !stopping) {
        if (expires_at && (getppid() != owner_pid || monotonic_seconds() >= expires_at)) return -1;
        ssize_t sent = write(STDOUT_FILENO, bytes, length);
        if (sent < 0 && errno == EINTR) continue;
        if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            struct pollfd output = {STDOUT_FILENO, POLLOUT, 0};
            if (poll(&output, 1, 1000) < 0 && errno != EINTR) return -1;
            continue;
        }
        if (sent <= 0) return -1;
        bytes += sent;
        length -= (size_t)sent;
    }
    return length ? -1 : 0;
}

static int error_response(const char *kind, int code) {
    char line[80];
    int length = snprintf(line, sizeof(line), "ERR %s %d\n", kind, code);
    return length > 0 && (size_t)length < sizeof(line) ?
        send_bytes(line, (size_t)length) : -1;
}

static int handle(const char *line, pid_t pid) {
    if (!strcmp(line, "Q")) return 1;
    if (!target_matches(pid)) { error_response("IDENTITY", 0); return -1; }
    if (!strcmp(line, "D")) {
        struct task_dyld_info info = {0};
        mach_msg_type_number_t count = TASK_DYLD_INFO_COUNT;
        kern_return_t code = task_info(target_task, TASK_DYLD_INFO,
                                      (task_info_t)&info, &count);
        if (code || count < TASK_DYLD_INFO_COUNT ||
            info.all_image_info_format != TASK_DYLD_ALL_IMAGE_INFO_64 ||
            info.all_image_info_size < 16 || info.all_image_info_addr < 0x1000 ||
            info.all_image_info_addr > MAX_ADDRESS || info.all_image_info_addr % 8)
            return error_response("DYLD", code);
        int length = snprintf(response, 128, "OK D 0x%" PRIx64 "\n",
                              (uint64_t)info.all_image_info_addr);
        return send_bytes(response, (size_t)length);
    }
    char *end = NULL;
    errno = 0;
    uint64_t address = line[0] == 'R' && line[1] == ' ' && isxdigit((unsigned char)line[2]) ?
        strtoull(line + 2, &end, 16) : 0;
    if (!address || errno || !end || *end != ' ' || !isdigit((unsigned char)end[1])) {
        error_response("PROTOCOL", 0);
        return -1;
    }
    uint64_t requested = strtoull(end + 1, &end, 10);
    if (errno || *end || !requested || requested > MAX_READ || address < 0x1000 ||
        address > MAX_ADDRESS - requested) {
        error_response("PROTOCOL", 0);
        return -1;
    }
    uint32_t length = (uint32_t)requested;
    memset(buffer, 0, MAX_READ);
    mach_vm_size_t copied = 0;
    kern_return_t code = mach_vm_read_overwrite(target_task, address, length,
                                               (mach_vm_address_t)buffer, &copied);
    if (code || copied != length) {
        memset(buffer, 0, MAX_READ);
        return error_response("READ", code);
    }
    int prefix = snprintf(response, 64, "OK R %u ", length);
    const char hex[] = "0123456789abcdef";
    for (uint32_t index = 0; index < length; ++index) {
        response[prefix + index * 2] = hex[buffer[index] >> 4];
        response[prefix + index * 2 + 1] = hex[buffer[index] & 15];
    }
    response[prefix + length * 2] = '\n';
    int result = send_bytes(response, (size_t)prefix + length * 2 + 1);
    memset(buffer, 0, MAX_READ);
    memset(response, 0, (size_t)prefix + length * 2 + 1);
    return result;
}

int main(int argc, char **argv) {
    if (argc != 2) return 64;
    errno = 0;
    char *end = NULL;
    long value = strtol(argv[1], &end, 10);
    if (errno || !*argv[1] || *end || value <= 0 || value > INT32_MAX) return 64;
    pid_t pid = (pid_t)value, parent = getppid();
    owner_pid = parent;
    int stdout_flags = fcntl(STDOUT_FILENO, F_GETFL);
    if (stdout_flags < 0 || fcntl(STDOUT_FILENO, F_SETFL, stdout_flags | O_NONBLOCK)) return 2;
    if (parent <= 1 || !target_matches(pid)) {
        error_response("IDENTITY", 0);
        return 65;
    }
    atexit(cleanup);
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    signal(SIGPIPE, SIG_IGN);
    kern_return_t code = task_for_pid(mach_task_self(), pid, &target_task);
    if (code || target_task == MACH_PORT_NULL) {
        error_response("TASK", code);
        return 2;
    }
    buffer = calloc(MAX_READ, 1);
    response = calloc(MAX_READ * 2 + 128, 1);
    if (!buffer || !response) { error_response("ALLOC", 0); return 2; }
    double started = monotonic_seconds();
    expires_at = started + 300;
    if (started < 0 || send_bytes("OK READY\n", 9)) return 2;
    char line[96] = {0};
    size_t used = 0;
    while (!stopping) {
        double now = monotonic_seconds();
        if (getppid() != parent || now < 0 || now - started >= 300) break;
        struct pollfd input = {STDIN_FILENO, POLLIN, 0};
        int ready = poll(&input, 1, 1000);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) return 2;
        if (!ready) continue;
        char character = 0;
        ssize_t got = read(STDIN_FILENO, &character, 1);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) break;
        if (character == '\n') {
            line[used] = 0;
            int result = handle(line, pid);
            memset(line, 0, sizeof(line));
            used = 0;
            if (result) return result > 0 ? 0 : 2;
        } else {
            if (character < 32 || character > 126 || used >= sizeof(line) - 1) {
                error_response("PROTOCOL", 0);
                return 2;
            }
            line[used++] = character;
        }
    }
    return 0;
}
