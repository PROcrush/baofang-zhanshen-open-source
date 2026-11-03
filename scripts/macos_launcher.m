#import <AppKit/AppKit.h>
#include <sys/stat.h>
#include <unistd.h>

// Resolve every executable from this bundle, never the build machine's path.
int main(int argc, char **argv) {
    @autoreleasepool {
        NSString *resources = [[NSBundle mainBundle] resourcePath];
        NSString *pythonHome = [resources stringByAppendingPathComponent:@"python"];
        NSString *python = [pythonHome stringByAppendingPathComponent:@"bin/python3.12"];
        NSString *source = [resources stringByAppendingPathComponent:@"app"];
        if (access([python fileSystemRepresentation], X_OK) != 0 ||
            chdir([source fileSystemRepresentation]) != 0) {
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"包房战神安装包不完整";
            alert.informativeText = @"请重新从安装包拖入完整的包房战神.app。";
            [alert runModal];
            return 1;
        }
        setenv("PYTHONHOME", [pythonHome fileSystemRepresentation], 1);
        setenv("PYTHONPATH", [source fileSystemRepresentation], 1);
        setenv("PYTHONNOUSERSITE", "1", 1);
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
        setenv("PYTHONUTF8", "1", 1);
        NSString *path = [[resources stringByAppendingPathComponent:@"bin"]
            stringByAppendingString:@":/usr/bin:/bin:/usr/sbin:/sbin"];
        setenv("PATH", [path fileSystemRepresentation], 1);
        // Finder has no visible stderr. The log contains application diagnostics,
        // not command arguments or credentials. CLI smoke tests retain stdout.
        if (argc == 1) {
            NSString *logs = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/包房战神"];
            [[NSFileManager defaultManager] createDirectoryAtPath:logs
                withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:nil];
            NSString *log = [logs stringByAppendingPathComponent:@"application.log"];
            FILE *out = freopen([log fileSystemRepresentation], "a", stderr);
            if (out) chmod([log fileSystemRepresentation], 0600);
            freopen("/dev/null", "w", stdout);
        }
        char **arguments = calloc((size_t)argc + 5, sizeof(char *));
        arguments[0] = (char *)[python fileSystemRepresentation];
        arguments[1] = "-B";
        arguments[2] = "-m";
        arguments[3] = "app.main";
        if (argc == 1) arguments[4] = "--studio";
        else for (int i = 1; i < argc; i++) arguments[i + 3] = argv[i];
        execv(arguments[0], arguments);
        perror("启动包房战神失败");
        free(arguments);
        return 1;
    }
}
