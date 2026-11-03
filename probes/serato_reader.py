"""Independent Serato Video read-only state reader and JSON-lines bridge.

The observed dynamic marker path is used without modifying either application.
Producer identity means a pinned local process/session and verified IPC writer
relationship, not official Serato API compatibility or an Apple trust claim.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import sys
import time
import uuid

from .fader_parser import parse_fader_snapshot
from .macos_ipc import MacReadOnlyIPC, NativeReadError
from .movie_parser import BUFFER_SIZE, MovieParseError, parse_movie_snapshot
from .serato_probe import PS_COMMAND, _process_inventory, _run, _signature


FRAME_TTL_NS = 350_000_000
MAX_CAPTURE_NS = 100_000_000


def _clock_ns() -> int:
    # Apple Python 3.9's monotonic_ns has a per-process origin. CLOCK_MONOTONIC
    # provides the system-wide basis needed by the Qt/native consumer.
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


class ReaderError(RuntimeError):
    def __init__(self, code: str, detail: str, *, terminal: bool = False, status: str = "waiting"):
        self.code, self.detail, self.terminal, self.status = code, detail, terminal, status
        super().__init__(detail)


def _file_stamp(path: Path) -> tuple:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ReaderError("artifact_type", "Serato 身份文件不是普通文件", terminal=True, status="error")
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def _no_symlinks(path: Path) -> None:
    if not path.is_absolute():
        raise ReaderError("executable_path", "Serato 可执行路径必须是绝对路径", terminal=True, status="error")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ReaderError("artifact_symlink", "Serato 身份路径含符号链接，当前读取策略无法核验", terminal=True, status="error")


def _pin_artifact(executable: str, role: str) -> dict:
    path = Path(executable)
    _no_symlinks(path)
    if path.name != ("Serato DJ Pro" if role == "main" else "Serato Video") or path.parent.name != "MacOS" or path.parent.parent.name != "Contents":
        raise ReaderError("bundle_path", "Serato 可执行路径不符合已核验 bundle 结构", terminal=True, status="error")
    bundle = path.parent.parent.parent
    plist = bundle / "Contents" / "Info.plist"
    _no_symlinks(plist)
    plist_stamp = _file_stamp(plist)
    with plist.open("rb") as stream:
        info = plistlib.load(stream)
    expected_id = "com.serato.seratodj" if role == "main" else "com.serato.seratovideo"
    if not isinstance(info, dict) or info.get("CFBundleIdentifier") != expected_id:
        raise ReaderError("bundle_identifier", "Serato bundle identifier 与进程角色不符", terminal=True, status="error")
    version, build = info.get("CFBundleShortVersionString"), info.get("CFBundleVersion")
    if not isinstance(version, str) or not isinstance(build, str) or not version or not build:
        raise ReaderError("bundle_version", "Serato 版本身份缺失", terminal=True, status="error")
    stamp = _file_stamp(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        opened_stamp = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        if opened_stamp != stamp:
            raise ReaderError("artifact_changed", "核验期间 Serato 可执行文件发生变化", terminal=True, status="error")
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
        final = os.fstat(stream.fileno())
        if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns) != stamp:
            raise ReaderError("artifact_changed", "核验期间 Serato 可执行文件发生变化", terminal=True, status="error")
    signature = _signature(executable)
    if not signature["strict"]["passed"] or signature["metadata_status"] != "ok":
        raise ReaderError("signature_integrity", "Serato 磁盘签名自身一致性未通过或未核验", terminal=True, status="error")
    if _file_stamp(path) != stamp or _file_stamp(plist) != plist_stamp:
        raise ReaderError("artifact_changed", "核验期间 Serato 身份文件发生变化", terminal=True, status="error")
    return {"path": executable, "bundle": str(bundle), "version": version, "build": build, "sha256": digest.hexdigest(), "signature": signature, "files": [(str(path), stamp), (str(plist), plist_stamp)]}


def _marker(path: Path, uid: int) -> tuple:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid:
        raise ReaderError("marker_identity", "IPC 标记必须是当前用户拥有的普通文件", terminal=True, status="error")
    return info.st_dev, info.st_ino, info.st_uid


def _discover_processes() -> dict:
    return _process_inventory(_run(PS_COMMAND))


class SeratoReader:
    """Pinned readonly attachment. Use as a context manager or call close().

    read_frame raises ReaderError on any failed verification. It never returns
    a previous accepted frame. The injectable boundary supports isolated tests.
    """

    def __init__(self, *, native=None, process_inventory=None, artifact_inspector=None, marker_path=None):
        self.native = native if native is not None else MacReadOnlyIPC()
        self.uid = os.getuid()
        if self.uid != os.geteuid():
            raise ReaderError("uid_mismatch", "reader 不支持有效用户与实际用户不同的执行环境", terminal=True, status="error")
        self.address = None
        self.invalidated = False
        self.sequence = 0
        inventory = (process_inventory or _discover_processes)()
        if inventory["status"] != "ok":
            raise ReaderError("process_unavailable", "进程列表未核验：" + (inventory.get("error") or "读取失败"))
        if not inventory["main"] or not inventory["video"]:
            raise ReaderError("producer_missing", "等待 Serato DJ Pro 与 Serato Video 同时运行")
        if len(inventory["main"]) != 1 or len(inventory["video"]) != 1:
            raise ReaderError("producer_ambiguous", "检测到多组 Serato 进程，无法唯一绑定", status="error")
        self.processes = [self.native.process(item["pid"]) for item in (inventory["main"][0], inventory["video"][0])]
        for item in self.processes:
            if item["uid"] != self.uid or item["real_uid"] != self.uid or item["started_seconds"] <= 0 or not 0 <= item["started_microseconds"] < 1_000_000:
                raise ReaderError("process_identity", "Serato 进程用户或出生身份不符", terminal=True, status="error")
        inspect = artifact_inspector or _pin_artifact
        self.artifacts = [inspect(item["executable"], role) for item, role in zip(self.processes, ("main", "video"))]
        main, video = self.artifacts
        expected_video = Path(main["bundle"]) / "Contents" / "MacOS" / "Serato Video.app"
        if Path(video["bundle"]) != expected_video or (main["version"], main["build"]) != (video["version"], video["build"]):
            raise ReaderError("producer_pair", "主程序与 Video 的 bundle 或版本身份不匹配", terminal=True, status="error")
        self.marker_path = Path(marker_path or f"/private/tmp/sslv_sharing{self.uid}")
        self.marker_stamp = _marker(self.marker_path, self.uid)
        self.key = self.native.marker_key(str(self.marker_path))
        self.shm_id, self.sem_id = self.native.ids(self.key)
        self.shm = self.native.shm_info(self.shm_id)
        self.sem = self.native.sem_info(self.sem_id)
        self._validate_ipc_metadata()
        # Process identity is checked again after hashing/signature commands.
        self._validate_lifecycle()
        self._idle()
        try:
            self.address = self.native.attach(self.shm_id)
            self._validate_lifecycle()
            self._idle()
        except BaseException:
            self.close()
            raise
        identity_digest = hashlib.sha256(json.dumps(self.processes, sort_keys=True).encode()).hexdigest()[:16]
        self.epoch = identity_digest + ":" + str(uuid.uuid4())

    def _validate_ipc_metadata(self) -> None:
        for item in (self.shm, self.sem):
            permission = item["permission"]
            if permission["uid"] != self.uid or permission["creator_uid"] != self.uid or permission["key"] != self.key:
                raise ReaderError("ipc_owner", "共享段或信号量的用户/key 与已绑定生产者不符", terminal=True, status="error")
        if self.shm["size"] != BUFFER_SIZE or self.sem["count"] != 1:
            raise ReaderError("ipc_shape", "共享段必须是 128000 bytes，信号量集合必须恰好一个成员", terminal=True, status="error")

    def _validate_lifecycle(self) -> None:
        try:
            if _marker(self.marker_path, self.uid) != self.marker_stamp or self.native.marker_key(str(self.marker_path)) != self.key:
                raise ReaderError("marker_changed", "IPC 标记身份已变化，需要重新附接", terminal=True)
            if self.native.ids(self.key) != (self.shm_id, self.sem_id):
                raise ReaderError("ipc_replaced", "IPC 对象已被替换，需要重新附接", terminal=True)
            if self.native.shm_info(self.shm_id) != self.shm or self.native.sem_info(self.sem_id) != self.sem:
                raise ReaderError("ipc_lifecycle", "共享段或信号量生命周期已变化，需要重新附接", terminal=True)
            for process in self.processes:
                if self.native.process(process["pid"]) != process:
                    raise ReaderError("process_restarted", "Serato 进程出生身份/路径已变化，需要重新附接", terminal=True)
            for artifact in self.artifacts:
                for filename, stamp in artifact["files"]:
                    _no_symlinks(Path(filename))
                    if _file_stamp(Path(filename)) != tuple(stamp):
                        raise ReaderError("artifact_changed", "Serato 磁盘身份已变化，需要重新核验", terminal=True, status="error")
        except OSError as exc:
            raise ReaderError("identity_unavailable", "进程或 IPC 身份读取失败：" + str(exc), terminal=True) from exc

    def _idle(self) -> None:
        value, last_pid = self.native.semaphore(self.sem_id)
        if value != 1:
            raise ReaderError("producer_busy", "生产者正在更新共享状态，等待下一份稳定快照")
        if last_pid not in {item["pid"] for item in self.processes}:
            raise ReaderError("semaphore_writer", f"信号量最近操作 PID {last_pid} 不属于当前 Serato 进程", terminal=True, status="error")

    def read_frame(self) -> dict:
        if self.address is None or self.invalidated:
            raise ReaderError("reader_closed", "reader 已关闭或身份失效，需要重新附接", terminal=True)
        try:
            self._validate_lifecycle()
            self._idle()
            started_ns = _clock_ns()
            first, second = self.native.snapshots(self.address)
            captured_ns = _clock_ns()
            self._idle()
            movies_first, movies_second = parse_movie_snapshot(first), parse_movie_snapshot(second)
            fader_first, fader_second = parse_fader_snapshot(first), parse_fader_snapshot(second)
            if not movies_first["complete"] or not movies_second["complete"]:
                raise ReaderError("incomplete_movies", "等待两盘全部 Movie 字段就绪")
            if movies_first != movies_second or fader_first != fader_second:
                raise ReaderError("unstable_snapshot", "两份快照的 Movie/推子状态不一致，等待下一帧")
            # Also reject changes in unknown active payload, rather than assume
            # an unrecognized field has no relationship to these records.
            active = movies_first["active_bytes"]
            if first[:active] != second[:active]:
                raise ReaderError("unstable_payload", "两份快照的有效数据仍在变化，等待下一帧")
            self._validate_lifecycle()
            self._idle()
            if _clock_ns() - started_ns > MAX_CAPTURE_NS:
                raise ReaderError("capture_slow", "快照采集与验证超过 100ms，等待下一帧")
            self.sequence += 1
            decks = [{name: movie[name] for name in ("deck_index", "source_identity", "audio_path", "position_seconds", "velocity")} for movie in movies_first["movies"]]
            # The parser withholds empty/at-capacity paths as None. The bridge
            # contract uses an empty string for an unavailable audio source;
            # preserve the reason below without rejecting the other deck.
            for deck in decks:
                if deck["audio_path"] is None:
                    deck["audio_path"] = ""
            paths = {str(movie["deck_index"]): movie["audio_path_info"]["status"] for movie in movies_first["movies"]}
            fader = {name: fader_first[name] for name in ("normalized", "left_deck_index", "right_deck_index", "source_identity")}
            return {"type": "serato_frame", "protocolVersion": 1, "mode": "serato", "epoch": self.epoch, "sequence": self.sequence, "capturedNs": captured_ns, "validUntilNs": captured_ns + FRAME_TTL_NS, "producerVerified": True, "decks": decks, "fader": fader, "diagnostics": {"captured_at": dt.datetime.now(dt.timezone.utc).isoformat(), "clock": "CLOCK_MONOTONIC", "audio_path_status": paths, "producers": [dict(process) for process in self.processes], "ipc": {"key": self.key, "shm_id": self.shm_id, "sem_id": self.sem_id, "read_only": True}, "identity_policy": "pinned-local-process-disk-and-ipc-session", "official_compatibility_verified": False}}
        except MovieParseError as exc:
            raise ReaderError("snapshot_schema", f"共享状态格式未通过校验：{exc.code}（byte {exc.offset}）") from exc
        except OSError as exc:
            self.invalidated = True
            self.close()
            raise ReaderError("native_unavailable", "只读状态读取失败：" + str(exc), terminal=True) from exc
        except ReaderError as exc:
            if exc.terminal:
                self.invalidated = True
                self.close()
            raise

    def identity_report(self) -> dict:
        return {"processes": self.processes, "artifacts": [{k: v for k, v in item.items() if k != "files"} for item in self.artifacts], "marker": str(self.marker_path), "key": self.key, "shm_id": self.shm_id, "shm": self.shm, "sem_id": self.sem_id, "sem": self.sem, "read_only": True, "official_compatibility_verified": False}

    def close(self) -> None:
        if self.address is not None:
            address, self.address = self.address, None
            self.native.detach(address)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def status_message(error: Exception) -> dict:
    return {"type": "serato_status", "protocolVersion": 1, "mode": "serato", "status": getattr(error, "status", "waiting"), "code": getattr(error, "code", "native_unavailable"), "detail": getattr(error, "detail", "只读接入未就绪：" + str(error)), "producerVerified": False, "capturedNs": _clock_ns()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serato Video 只读状态 JSONL bridge；不会写共享段/信号量或控制播放")
    parser.add_argument("--once", action="store_true", help="输出一次帧或明确失败状态后退出")
    parser.add_argument("--output", type=Path, help="保存最近一次输出；成功帧附身份报告（不保存原始共享段）")
    parser.add_argument("--interval", type=float, default=.05, help="输出间隔秒数，默认 20Hz；范围 .02..5")
    args = parser.parse_args(argv)
    if not .02 <= args.interval <= 5:
        parser.error("--interval 必须在 .02..5 秒内")
    reader = None
    next_attach = 0.
    try:
        while True:
            tick = time.monotonic()
            output = None
            try:
                if reader is None:
                    if tick < next_attach:
                        time.sleep(min(args.interval, next_attach - tick))
                        continue
                    reader = SeratoReader()
                output = reader.read_frame()
            except (ReaderError, OSError, ValueError) as exc:
                output = status_message(exc)
                if reader is not None and (not isinstance(exc, ReaderError) or exc.terminal):
                    reader.close()
                    reader = None
                if reader is None:
                    next_attach = time.monotonic() + 2.
            print(json.dumps(output, ensure_ascii=False, allow_nan=False), flush=True)
            if args.output:
                saved = dict(output)
                if reader is not None and output["type"] == "serato_frame":
                    saved["identity"] = reader.identity_report()
                args.output.parent.mkdir(parents=True, exist_ok=True)
                temporary = args.output.with_name(args.output.name + ".tmp")
                temporary.write_text(json.dumps(saved, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
                temporary.replace(args.output)
            if args.once:
                return 0 if output["type"] == "serato_frame" else 2
            time.sleep(max(0., args.interval - (time.monotonic() - tick)))
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    finally:
        if reader is not None:
            reader.close()


if __name__ == "__main__":
    sys.exit(main())
