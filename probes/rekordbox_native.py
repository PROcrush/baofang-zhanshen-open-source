"""Debugger-entitled, target-bound native backend for the experimental reader.

Only the already approved project-local test copy can be targeted. The helper
is built/signed in ignored runtime/, never changes rekordbox or global debugger
settings, and exposes only task_info and bounded mach_vm_read_overwrite calls.
Disk code-profile verification is not official compatibility or a runtime hash.
"""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import selectors
import subprocess
import sys
import tempfile
import time

from probes.rekordbox_reader import MAX_POINTER, MAX_READ, RUNTIME_ROOT, ReaderError, inspect_profile


DEFAULT_TEST_APP = RUNTIME_ROOT / "rekordbox-ab-20261021/rekordbox-test.app"
SOURCE = Path(__file__).resolve().parent / "native/rekordbox_memory.c"
ENTITLEMENTS = {"com.apple.security.cs.debugger": True}
MAX_RESPONSE = MAX_READ * 2 + 64
_BUILD_VERSION = "2-runtime"


def _target(app_path):
    app = Path(app_path).resolve()
    # Keep the literal approved location: resolving both sides would silently
    # permit replacing the test bundle with a symlink to the original app.
    if app != DEFAULT_TEST_APP.absolute() or RUNTIME_ROOT.resolve() != RUNTIME_ROOT.absolute():
        raise ReaderError("native_target_not_allowed", "only the approved runtime test copy is permitted")
    return app / "Contents/MacOS/rekordbox"


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _run(arguments, timeout=30):
    try:
        return subprocess.run(arguments, capture_output=True, check=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", b"") or str(error).encode()
        raise ReaderError("native_helper_build_or_signature_failed", detail.decode(errors="replace")[:500]) from None


def _signature_valid(helper):
    try:
        _run(["/usr/bin/codesign", "--verify", "--strict", str(helper)], timeout=10)
        result = _run(["/usr/bin/codesign", "-d", "--verbose=2", "--entitlements", ":-", str(helper)], timeout=10)
        flags = re.search(rb"flags=0x([0-9a-fA-F]+)", result.stderr)
        if not flags or int(flags.group(1), 16) & 0x10002 != 0x10002:
            return False
        data = result.stdout
        start = data.find(b"<?xml")
        if start < 0:
            return False
        return plistlib.loads(data[start:]) == ENTITLEMENTS
    except (ReaderError, ValueError, plistlib.InvalidFileException):
        return False


def _cache_valid(helper, manifest_path, build_key):
    try:
        if helper.is_symlink() or manifest_path.is_symlink():
            return False
        manifest = json.loads(manifest_path.read_text())
        return (manifest.get("buildKey") == build_key
                and manifest.get("helperSha256") == _digest(helper)
                and _signature_valid(helper))
    except (OSError, ValueError):
        return False


def prepare_helper(app_path=DEFAULT_TEST_APP):
    """Compile and ad-hoc sign only our local helper; verify cache before use."""
    target = _target(app_path)
    if sys.platform != "darwin":
        raise ReaderError("unsupported_platform")
    profile = inspect_profile(Path(app_path))
    if profile["binary"] != target:
        raise ReaderError("native_target_not_allowed", "test executable path changed")
    source_hash = _digest(SOURCE)
    build_key = hashlib.sha256((source_hash + "\n" + str(target) + "\n" + _BUILD_VERSION).encode()).hexdigest()
    folder = RUNTIME_ROOT / "rekordbox-ab-20261021/native-helper" / build_key[:20]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    helper, manifest_path = folder / "rekordbox-memory", folder / "manifest.json"
    with (folder / "build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not _cache_valid(helper, manifest_path, build_key):
            with tempfile.TemporaryDirectory(prefix="build-", dir=folder) as temporary:
                temp = Path(temporary)
                candidate, rights = temp / "rekordbox-memory", temp / "entitlements.plist"
                rights.write_bytes(plistlib.dumps(ENTITLEMENTS))
                define = "-DREKORDBOX_TARGET_PATH=" + json.dumps(str(target), ensure_ascii=False)
                _run(["/usr/bin/clang", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                      define, str(SOURCE), "-o", str(candidate)])
                _run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none",
                      "--options", "runtime", "--entitlements", str(rights), str(candidate)])
                if not _signature_valid(candidate) or _digest(SOURCE) != source_hash:
                    raise ReaderError("native_helper_source_or_signature_changed")
                candidate.chmod(0o700)
                manifest = {"buildKey": build_key, "sourceSha256": source_hash,
                            "targetExecutable": str(target), "helperSha256": _digest(candidate),
                            "entitlements": ENTITLEMENTS}
                temporary_manifest = temp / "manifest.json"
                temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
                os.replace(candidate, helper)
                os.replace(temporary_manifest, manifest_path)
        if not _cache_valid(helper, manifest_path, build_key):
            raise ReaderError("native_helper_cache_invalid")
    return helper


class NativeReadOnlyTask:
    """MachReadOnlyTask-compatible API; owns and stops only its own helper."""
    def __init__(self, pid, *, appPath=DEFAULT_TEST_APP, timeout=2.0,
                 _helper_builder=prepare_helper, _spawn=subprocess.Popen):
        _target(appPath)
        if type(pid) is not int or not 0 < pid <= 0x7FFFFFFF:
            raise ReaderError("invalid_pid")
        if not math.isfinite(timeout) or not 0.05 <= timeout <= 10:
            raise ReaderError("invalid_helper_timeout")
        self.pid, self.app_path, self.timeout = pid, appPath, timeout
        self.builder, self.spawn = _helper_builder, _spawn
        self.process, self.selector, self.started = None, None, None
        self.bytes_read = 0
        self.pending = bytearray()

    def __enter__(self):
        if self.process is not None:
            raise ReaderError("native_helper_already_open")
        helper = self.builder(self.app_path)
        self.started = time.monotonic()
        try:
            self.process = self.spawn([str(helper), str(self.pid)], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
            if self._line() != b"OK READY":
                raise ReaderError("native_helper_handshake_invalid")
            return self
        except Exception:
            self.close()
            raise

    def _line(self):
        deadline = time.monotonic() + self.timeout
        while True:
            newline = self.pending.find(b"\n")
            if newline >= 0:
                result = bytes(self.pending[:newline])
                del self.pending[:newline + 1]
                if self.pending:
                    raise ReaderError("native_helper_unsolicited_output")
                if result.startswith(b"ERR "):
                    try:
                        prefix, kind, code = result.decode("ascii").split(" ")
                        int(code)
                    except (UnicodeDecodeError, ValueError):
                        raise ReaderError("native_helper_response_invalid") from None
                    codes = {"TASK": "task_port_denied", "READ": "memory_unavailable",
                             "DYLD": "dyld_info_unavailable", "IDENTITY": "process_identity_changed"}
                    raise ReaderError(codes.get(kind, "native_helper_error"), f"{kind}; kernel code {code}")
                return result
            if len(self.pending) > MAX_RESPONSE:
                raise ReaderError("native_helper_response_too_large")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise ReaderError("native_helper_timeout")
            block = os.read(self.process.stdout.fileno(), min(8192, MAX_RESPONSE + 1 - len(self.pending)))
            if not block:
                raise ReaderError("native_helper_eof")
            self.pending.extend(block)

    def _request(self, request):
        if self.process is None or self.started is None:
            raise ReaderError("native_helper_not_open")
        if time.monotonic() - self.started >= 300:
            self.close()
            raise ReaderError("native_helper_lifetime_expired")
        try:
            self.process.stdin.write(request)
            self.process.stdin.flush()
            return self._line()
        except ReaderError as error:
            # A genuine target read failure permits the reader to inspect B.
            # Broken transport/identity never leaves a reusable stale channel.
            if error.code != "memory_unavailable":
                self.close()
            raise
        except (OSError, ValueError):
            self.close()
            raise ReaderError("native_helper_transport_failed") from None

    def dyld_address(self):
        line = self._request(b"D\n")
        try:
            prefix, kind, value = line.decode("ascii").split(" ")
            address = int(value, 16)
            if prefix != "OK" or kind != "D" or not value.startswith("0x") or address % 8:
                raise ValueError
            if not 0x1000 <= address <= MAX_POINTER:
                raise ValueError
            return address
        except (UnicodeDecodeError, ValueError):
            self.close()
            raise ReaderError("native_helper_response_invalid") from None

    def read(self, address, size):
        if (type(address) is not int or type(size) is not int or not 0 < size <= MAX_READ
                or not 0x1000 <= address <= MAX_POINTER - size):
            raise ReaderError("read_bounds")
        line = self._request(f"R {address:x} {size}\n".encode("ascii"))
        try:
            prefix, kind, length, encoded = line.split(b" ")
            if prefix != b"OK" or kind != b"R" or int(length) != size or len(encoded) != size * 2:
                raise ValueError
            data = bytes.fromhex(encoded.decode("ascii"))
            if len(data) != size:
                raise ValueError
        except (UnicodeDecodeError, ValueError):
            self.close()
            raise ReaderError("native_helper_response_invalid") from None
        self.bytes_read += size
        return data

    def close(self):
        process, self.process = self.process, None
        if self.selector is not None:
            self.selector.close()
            self.selector = None
        if process is not None:
            try:
                if process.poll() is None:
                    try:
                        process.stdin.write(b"Q\n")
                        process.stdin.flush()
                    except (OSError, ValueError):
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1)
            finally:
                if process.stdin:
                    process.stdin.close()
                if process.stdout:
                    process.stdout.close()
        self.pending.clear()

    def __exit__(self, *_):
        self.close()
