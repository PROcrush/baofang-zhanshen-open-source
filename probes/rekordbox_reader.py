"""Finite, read-only experiment for the local rekordbox 7.2.19.0342 arm64 build.

NOT a controller protocol: every frame retains layoutRuntimeValidated=False and
liveConnectionVerified=False, even when candidate values can be observed. There
is no TTL or playing/speed assertion. No target process launch, writes,
injection, ptrace, suspension, re-signing, or playback control is implemented.
The explicit native-helper option builds/signs only our target-bound helper.

Static evidence (preferred Mach-O base 0x100000000, original arm64 UUID below):
DjEngineIF::singletonHolder 0x1056c7a60; SingletonHolder::get 0x10001b524
loads holder+0x40. getPlayingTime 0x1009130e4 loads engine+0xc8;
DjUnitAudioGraph::getPlayerControl 0x1027cd580 loads array+0x458/count+0x464.
Array entries are DjPlayerStreamingEventSender wrappers, not IDjPlayerControl:
refDjPlayerState 0x102adad48 forwards through wrapper+0x40 (constructor
0x102ad8c44 stores it at 0x102ad8cbc). This extra hop was confirmed live.
DjPlayerUnit ctor 0x102b8c838 sets IDjPlayerControl at unit+0x180 and state
at unit+0x278; refDjPlayerState 0x102b987c0 and thunk 0x102b98978 confirm
control+0xf8. DjPlayerUnitState ctor 0x102ba7d2c stores rawState at +8.
getElapsedTimeFloatMs(false) 0x102ba9ee8 reads signed int32 rawState+0xc
and multiplies by 1000/44100 (constant at 0x1053b2178).
getPlayMode 0x102ba99c4 reads rawState+8; this is not a complete playing state.
getTempoRatioDiffPercent 0x102bac73c reads *(rawState+0x70)+0xe4;
getOutputBpm ordinary branch 0x102bac688 computes plainBpm*(1+delta).
getAudioSourceInfo 0x102ba8988 / 0x102b9c660 loads unitState+0x18 -> +0x70.
getFilePath 0x100913200 treats sourceInfo+0x18 as juce::String;
toRawUTF8 0x1024bb748 loads its first pointer. These are static inferences,
not a live-validated layout. Disk __text hash binds this exact code profile;
the mapped image UUID is checked, but runtime code bytes are not hashed.
"""

import argparse
from contextlib import ExitStack
import ctypes
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

from probes.rekordbox_probe import DEFAULT_APP, executable_path


VERSION = "7.2.19.0342"
ARM64_UUID = "D827BD18-6918-357A-9F57-C506CF428700"
TEXT_SHA256 = "1416f9eaa826e9aba138a176e997e9016a8fe360b0a06cf42b8ea4d5d9287445"
RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "runtime"
MAX_READ = 65536
MAX_PATH = 4096
MAX_POINTER = 0x0000FFFFFFFFFFFF


class ReaderError(Exception):
    def __init__(self, code, detail=""):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _stamp(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _layout(header, commands):
    if len(header) != 32:
        raise ReaderError("invalid_macho", "short header")
    magic, cpu, _, filetype, count, size, _, _ = struct.unpack("<IiiIIIII", header)
    if magic != 0xFEEDFACF or cpu != 0x100000C or filetype != 2:
        raise ReaderError("wrong_architecture", "arm64 MH_EXECUTE required")
    if not 0 < count <= 4096 or size != len(commands) or size > MAX_READ:
        raise ReaderError("invalid_macho", "load command bounds")
    cursor, result = 0, {}
    for _ in range(count):
        if cursor + 8 > size:
            raise ReaderError("invalid_macho", "short load command")
        command, length = struct.unpack_from("<II", commands, cursor)
        if length < 8 or cursor + length > size:
            raise ReaderError("invalid_macho", "load command length")
        if command == 0x1B and length == 24:
            result["uuid"] = str(uuid.UUID(bytes=commands[cursor + 8:cursor + 24])).upper()
        if command == 0x19 and length >= 72:
            segment = commands[cursor + 8:cursor + 24].split(b"\0")[0]
            if segment == b"__TEXT":
                result["preferredBase"] = struct.unpack_from("<Q", commands, cursor + 24)[0]
            sections = struct.unpack_from("<I", commands, cursor + 64)[0]
            if 72 + sections * 80 > length:
                raise ReaderError("invalid_macho", "section bounds")
            for index in range(sections):
                start = cursor + 72 + index * 80
                name = commands[start:start + 16].split(b"\0")[0]
                if segment == b"__TEXT" and name == b"__text":
                    address, section_size, offset = struct.unpack_from("<QQI", commands, start + 32)
                    result.update(textAddress=address, textSize=section_size, textOffset=offset)
        cursor += length
    if cursor != size or set(result) != {"uuid", "preferredBase", "textAddress", "textSize", "textOffset"}:
        raise ReaderError("invalid_macho", "missing or trailing layout information")
    if not 0 < result["textSize"] <= 128 * 1024 * 1024:
        raise ReaderError("invalid_macho", "text size bounds")
    return result


def inspect_profile(app):
    """Read only the selected bundle and hash its arm64 code section in chunks."""
    app = Path(app).resolve(strict=True)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if (info.get("CFBundleIdentifier") != "com.pioneerdj.rekordboxdj"
            or info.get("CFBundleExecutable") != "rekordbox"
            or info.get("CFBundleShortVersionString") != VERSION
            or info.get("CFBundleVersion") != VERSION):
        raise ReaderError("unsupported_application", "exact rekordbox bundle and version required")
    binary = (app / "Contents/MacOS/rekordbox").resolve(strict=True)
    if not binary.is_relative_to(app):
        raise ReaderError("unsupported_application", "executable escaped selected bundle")
    stamp = _stamp(binary)
    with binary.open("rb") as stream:
        magic, count = struct.unpack(">II", stream.read(8))
        if magic == 0xCAFEBABE:
            if not 0 < count <= 16:
                raise ReaderError("invalid_macho", "slice count")
            slices = [struct.unpack(">IIIII", stream.read(20)) for _ in range(count)]
            offsets = [entry[2] for entry in slices if entry[0] == 0x100000C]
            if len(offsets) != 1:
                raise ReaderError("wrong_architecture", "one arm64 slice required")
            slice_offset = offsets[0]
        else:
            slice_offset = 0
        stream.seek(slice_offset)
        header = stream.read(32)
        if len(header) != 32:
            raise ReaderError("invalid_macho", "short slice")
        size = struct.unpack_from("<I", header, 20)[0]
        if size > MAX_READ:
            raise ReaderError("invalid_macho", "load commands too large")
        layout = _layout(header, stream.read(size))
        if layout["uuid"] != ARM64_UUID:
            raise ReaderError("uuid_mismatch")
        stream.seek(slice_offset + layout["textOffset"])
        digest, remaining = hashlib.sha256(), layout["textSize"]
        while remaining:
            block = stream.read(min(MAX_READ, remaining))
            if not block:
                raise ReaderError("invalid_macho", "short code section")
            digest.update(block)
            remaining -= len(block)
    if digest.hexdigest() != TEXT_SHA256:
        raise ReaderError("text_hash_mismatch")
    if _stamp(binary) != stamp:
        raise ReaderError("artifact_changed")
    return {"app": app, "binary": binary, "version": VERSION, "layout": layout,
            "textSha256": digest.hexdigest(), "stamp": stamp}


class _DyldInfo(ctypes.Structure):
    _fields_ = [("address", ctypes.c_uint64), ("size", ctypes.c_uint64), ("format", ctypes.c_int)]


class MachReadOnlyTask:
    """Own one task send right; only mach_vm_read_overwrite reads its memory."""
    def __init__(self, pid, *, library=None):
        if library is None and sys.platform != "darwin":
            raise ReaderError("unsupported_platform")
        self.library = library or ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        self.pid, self.task, self.owner, self.bytes_read = pid, 0, 0, 0
        lib = self.library
        lib.mach_task_self.restype = ctypes.c_uint
        lib.task_for_pid.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
        lib.task_for_pid.restype = ctypes.c_int
        lib.mach_port_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint]
        lib.mach_port_deallocate.restype = ctypes.c_int
        lib.mach_error_string.argtypes = [ctypes.c_int]
        lib.mach_error_string.restype = ctypes.c_char_p
        lib.task_info.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.task_info.restype = ctypes.c_int
        lib.mach_vm_read_overwrite.argtypes = [ctypes.c_uint, ctypes.c_uint64, ctypes.c_uint64,
                                              ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64)]
        lib.mach_vm_read_overwrite.restype = ctypes.c_int

    def __enter__(self):
        port = ctypes.c_uint(0)
        self.owner = self.library.mach_task_self()
        code = self.library.task_for_pid(self.owner, self.pid, ctypes.byref(port))
        self.task = port.value
        if code or not self.task:
            self.close()
            raise ReaderError("task_port_denied", f"kernel code {code}")
        return self

    def close(self):
        if self.task:
            self.library.mach_port_deallocate(self.owner, self.task)
            self.task = 0

    def __exit__(self, *_):
        self.close()

    def dyld_address(self):
        info = _DyldInfo()
        count = ctypes.c_uint(ctypes.sizeof(info) // ctypes.sizeof(ctypes.c_uint))
        code = self.library.task_info(self.task, 17, ctypes.byref(info), ctypes.byref(count))
        if code or count.value < 5 or info.format != 2 or info.size < 16:
            raise ReaderError("dyld_info_unavailable", f"kernel code {code}")
        return info.address

    def read(self, address, size):
        if not self.task or not 0 < size <= MAX_READ or not 0x1000 <= address <= MAX_POINTER - size:
            raise ReaderError("read_bounds")
        buffer = ctypes.create_string_buffer(size)
        copied = ctypes.c_uint64(0)
        code = self.library.mach_vm_read_overwrite(self.task, address, size,
                                                 ctypes.addressof(buffer), ctypes.byref(copied))
        if code or copied.value != size:
            raise ReaderError("memory_unavailable", f"kernel code {code}; incomplete read")
        self.bytes_read += size
        return buffer.raw


def _find_pid(binary, path_for_pid):
    result = subprocess.run(["/usr/bin/pgrep", "-x", "rekordbox"], capture_output=True,
                            text=True, timeout=3)
    if result.returncode:
        raise ReaderError("process_query_failed" if result.stderr.strip() else "not_running")
    matches = []
    for token in result.stdout.split():
        if token.isdecimal():
            pid = int(token)
            try:
                if path_for_pid(pid) == binary:
                    matches.append(pid)
            except OSError:
                raise ReaderError("process_identity_unavailable") from None
    if len(matches) != 1:
        raise ReaderError("process_identity_not_unique")
    return matches[0]


def _empty_deck(side, status="unavailable", error=None):
    return {"deck": side, "status": status, "trackPath": None, "positionSamples": None,
            "positionSeconds": None, "playRequested": None, "playing": None,
            "tempoDelta": None, "normalRateCandidate": None, "speed": None, "error": error}


def unavailable_frame(error):
    return {"type": "rekordbox_experimental_frame", "protocolVersion": 1,
            "capturedAt": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "layoutRuntimeValidated": False, "liveConnectionVerified": False,
            "controllerCompatible": False, "runtimeCodeHashVerified": False,
            "status": "unavailable", "memoryBytesRead": 0,
            "decks": [_empty_deck(side) for side in "AB"],
            "error": {"code": error.code, "detail": error.detail}}


class RekordboxExperimentalReader:
    def __init__(self, app=DEFAULT_APP, *, native_factory=MachReadOnlyTask,
                 path_for_pid=executable_path, find_pid=_find_pid):
        self.app, self.native_factory = app, native_factory
        self.path_for_pid, self.find_pid = path_for_pid, find_pid
        self.stack, self.native, self.profile = ExitStack(), None, None
        self.sequence = 0

    def __enter__(self):
        try:
            self.profile = inspect_profile(self.app)
            self.pid = self.find_pid(self.profile["binary"], self.path_for_pid)
            self._check_process()
            self.native = self.stack.enter_context(self.native_factory(self.pid))
            self.base = self._main_image()
            self._check_process()
            return self
        except Exception:
            self.stack.close()
            raise

    def __exit__(self, *_):
        self.stack.close()

    def _check_process(self):
        if self.path_for_pid(self.pid) != self.profile["binary"]:
            raise ReaderError("process_identity_changed")
        if _stamp(self.profile["binary"]) != self.profile["stamp"]:
            raise ReaderError("artifact_changed")

    def _pointer(self, address, *, nullable=False):
        value = struct.unpack("<Q", self.native.read(address, 8))[0]
        if nullable and value == 0:
            return 0
        if not 0x1000 <= value <= MAX_POINTER or value % 8:
            raise ReaderError("invalid_pointer", f"at 0x{address:x}; value 0x{value:x}")
        return value

    def _cstring(self, address):
        if not 0x1000 <= address <= MAX_POINTER - MAX_PATH:
            raise ReaderError("invalid_path_pointer")
        result = bytearray()
        while len(result) < MAX_PATH:
            cursor = address + len(result)
            length = min(256, MAX_PATH - len(result), 4096 - cursor % 4096)
            block = self.native.read(cursor, length)
            if b"\0" in block:
                result.extend(block.split(b"\0", 1)[0])
                try:
                    return result.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    raise ReaderError("invalid_path_encoding") from None
            result.extend(block)
        raise ReaderError("path_not_terminated")

    def _main_image(self):
        # Only inspect dyld's first image, and require it to be the exact
        # selected MH_EXECUTE. A changed/unexpected array fails closed.
        info_address = self.native.dyld_address()
        info = self.native.read(info_address, 16)
        version, count, array = struct.unpack("<IIQ", info)
        if not version or not 0 < count <= 4096 or not array:
            raise ReaderError("dyld_info_unavailable")
        record = self.native.read(array, 24)
        base, path, _ = struct.unpack("<QQQ", record)
        if base % 4096 or Path(self._cstring(path)).resolve(strict=True) != self.profile["binary"]:
            raise ReaderError("mapped_image_identity_mismatch")
        header = self.native.read(base, 32)
        command_size = struct.unpack_from("<I", header, 20)[0]
        if command_size > MAX_READ:
            raise ReaderError("invalid_macho", "mapped load commands too large")
        mapped = _layout(header, self.native.read(base + 32, command_size))
        if mapped != self.profile["layout"]:
            raise ReaderError("mapped_image_layout_mismatch")
        if info != self.native.read(info_address, 16) or record != self.native.read(array, 24):
            raise ReaderError("dyld_image_changed")
        return base

    def _roots(self):
        engine = self._pointer(self.base + 0x56C7AA0)
        graph = self._pointer(engine + 0xC8)
        count = struct.unpack("<I", self.native.read(graph + 0x464, 4))[0]
        if not 2 <= count <= 8:
            raise ReaderError("player_array_unavailable")
        return engine, graph, self._pointer(graph + 0x458), count

    def _identity(self, controls, index):
        wrapper = self._pointer(controls + index * 8)
        control = self._pointer(wrapper + 0x40)
        state = self._pointer(control + 0xF8)
        raw = self._pointer(state + 8)
        loader = self._pointer(state + 0x18)
        source = self._pointer(loader + 0x70, nullable=True)
        path_pointer = self._pointer(source + 0x18) if source else 0
        path = self._cstring(path_pointer).replace("\\", "/") if path_pointer else None
        return control, state, raw, loader, source, path_pointer, wrapper, path

    def _deck(self, controls, index):
        side = "AB"[index]
        before = self._identity(controls, index)
        if not before[4]:
            result = _empty_deck(side, "empty_unvalidated")
        else:
            path = before[-1]
            if not path or not Path(path).is_absolute() or "\n" in path or "\r" in path:
                raise ReaderError("local_track_path_unavailable")
            raw = before[2]
            play = self.native.read(raw + 8, 1)[0]
            samples = struct.unpack("<i", self.native.read(raw + 0xC, 4))[0]
            stretch = self._pointer(raw + 0x70)
            delta = struct.unpack("<f", self.native.read(stretch + 0xE4, 4))[0]
            if play not in (0, 1) or not math.isfinite(delta) or not -1 <= delta <= 4:
                raise ReaderError("candidate_value_out_of_range")
            result = _empty_deck(side, "observed_unvalidated")
            result.update(trackPath=path, positionSamples=samples, positionSeconds=samples / 44100.0,
                          playRequested=bool(play), tempoDelta=delta, normalRateCandidate=1.0 + delta)
        if before != self._identity(controls, index):
            raise ReaderError("track_identity_changed")
        return result, before

    def read_frame(self):
        self._check_process()
        roots = self._roots()
        decks, identities = [], []
        for index in range(2):
            try:
                deck, identity = self._deck(roots[2], index)
                decks.append(deck)
                identities.append(identity)
            except ReaderError as error:
                decks.append(_empty_deck("AB"[index], error={"code": error.code, "detail": error.detail}))
                identities.append(None)
        # A can change while B is being copied. Recheck both before publishing
        # any candidate values; no cross-deck atomicity or freshness is claimed.
        for index, identity in enumerate(identities):
            if identity is None:
                continue
            try:
                if identity != self._identity(roots[2], index):
                    raise ReaderError("track_identity_changed")
            except ReaderError as error:
                decks[index] = _empty_deck("AB"[index], error={"code": error.code, "detail": error.detail})
        if roots != self._roots():
            raise ReaderError("player_array_changed")
        self._check_process()
        self.sequence += 1
        frame = unavailable_frame(ReaderError("layout_not_live_validated"))
        status = "experimental_observation" if any(d["status"] != "unavailable" for d in decks) else "unavailable"
        frame.update(sequence=self.sequence, status=status, decks=decks,
                     memoryBytesRead=self.native.bytes_read,
                     application={"path": str(self.profile["app"]), "version": VERSION,
                                  "arm64Uuid": ARM64_UUID, "arm64TextSha256": TEXT_SHA256},
                     process={"pid": self.pid, "executablePathVerified": True})
        return frame


def _output_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(RUNTIME_ROOT.resolve()):
        raise ReaderError("output_outside_runtime", "output must remain under this project's runtime/")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--native-helper", action="store_true",
                        help="use the locally built debugger-entitled helper, locked to the approved test copy")
    parser.add_argument("--duration", type=float, default=0, help="0: once; otherwise at most 300 seconds")
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--output", type=Path, default=RUNTIME_ROOT / "rekordbox-ab-20261021/experimental-frames.jsonl")
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration) or not 0 <= args.duration <= 300:
        parser.error("duration must be finite and between 0 and 300 seconds")
    if not math.isfinite(args.interval) or not 0.05 <= args.interval <= 5:
        parser.error("interval must be finite and between 0.05 and 5 seconds")
    output = _output_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    success = False
    with output.open("a", encoding="utf-8") as stream:
        def emit(frame):
            encoded = json.dumps(frame, ensure_ascii=False, allow_nan=False)
            stream.write(encoded + "\n")
            stream.flush()
            print(encoded, flush=True)
        if args.native_helper:
            from probes.rekordbox_native import NativeReadOnlyTask
            reader = RekordboxExperimentalReader(args.app, native_factory=lambda pid:
                                                NativeReadOnlyTask(pid, appPath=args.app))
        else:
            reader = RekordboxExperimentalReader(args.app)
        try:
            with reader:
                deadline = time.monotonic() + args.duration
                while True:
                    frame = reader.read_frame()
                    emit(frame)
                    success = success or any(deck["status"] == "observed_unvalidated"
                                             for deck in frame["decks"])
                    remaining = deadline - time.monotonic()
                    if args.duration == 0 or remaining <= 0:
                        break
                    time.sleep(min(args.interval, remaining))
        except (ReaderError, OSError, ValueError, struct.error, subprocess.TimeoutExpired) as error:
            if not isinstance(error, ReaderError):
                error = ReaderError("observation_unavailable", str(error))
            frame = unavailable_frame(error)
            frame["memoryBytesRead"] = getattr(reader.native, "bytes_read", 0)
            emit(frame)
            success = False
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
