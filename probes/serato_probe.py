"""Inspect Serato on macOS without launching, patching, or attaching to it.

This is an independent diagnostic inventory, not a Serato state reader.  A
marker, a running process, and a valid signature do not prove that a usable
playback channel exists.  No shared memory is attached and no semaphore is
changed.  Only the standard library is required.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import stat
import subprocess
import sys
from typing import Any

try:
    import pwd
except ImportError:  # Windows can still display the unsupported-platform report.
    pwd = None


APP_SEARCH_ROOTS = (Path("/Applications"), Path.home() / "Applications")
IPC_ROOT = Path("/private/tmp")
COMMAND_TIMEOUT_SECONDS = 3.0
PS_COMMAND = ["/bin/ps", "-axo", "pid=,uid=,comm="]
SHM_COMMAND = ["/usr/bin/ipcs", "-m", "-p"]
SEM_COMMAND = ["/usr/bin/ipcs", "-s"]
PENDING_READER = "本环境清单探针不读取实时状态；曲目、位置和推子由独立 serato_reader 只读核验。"


def _run(command: list[str]) -> dict[str, Any]:
    """Keep command denial and stderr distinct from a valid empty result."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr[:4096],
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "stderr": "", "error": "命令读取超时"}
    except OSError as exc:
        return {"returncode": None, "stdout": "", "stderr": "", "error": str(exc)[:4096]}


def _command_problem(result: dict[str, Any]) -> str | None:
    if result.get("error"):
        return str(result["error"])
    if result.get("returncode") != 0:
        message = result.get("stderr", "").strip() or "命令未成功完成"
        return f"{message}（退出码 {result.get('returncode')}）"
    return None


def _check(label: str, status: str, detail: str) -> dict[str, str]:
    return {"label": label, "status": status, "detail": detail}


def _process_inventory(result: dict[str, Any]) -> dict[str, Any]:
    problem = _command_problem(result)
    if problem:
        return {"status": "unavailable", "main": [], "video": [], "error": problem}
    main, video = [], []
    malformed = 0
    for line in result["stdout"].splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*(\d+)\s+(\d+)\s+(\S.*?)\s*", line)
        if not match:
            malformed += 1
            continue
        pid, uid, executable = match.groups()
        name = Path(executable).name
        # Exact executable names exclude Qt helpers, crash reporters, and this
        # probe itself. comm has no command-line arguments or music filenames.
        if name not in {"Serato DJ Pro", "Serato Video"}:
            continue
        item = {
            "pid": int(pid),
            "uid": int(uid),
            "executable": executable,
            "absolute_path": executable.startswith("/"),
            "identity_verified": False,
        }
        (main if name == "Serato DJ Pro" else video).append(item)
    return {
        "status": "ok" if malformed == 0 else "unavailable",
        "main": main,
        "video": video,
        "error": None if malformed == 0 else f"进程列表有 {malformed} 行无法解析，不能确认完整列表。",
    }


def _app_root(executable: str) -> Path | None:
    if not executable.startswith("/") or ".app/" not in executable:
        return None
    return Path(executable.split(".app/", 1)[0] + ".app")


def _read_bundle(bundle: Path, role: str) -> dict[str, Any]:
    record: dict[str, Any] = {"path": str(bundle), "role": role}
    try:
        info_path = bundle / "Contents" / "Info.plist"
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
        if not isinstance(info, dict):
            raise ValueError("Info.plist 根节点不是字典")
        record.update(
            identifier=info.get("CFBundleIdentifier"),
            version=info.get("CFBundleShortVersionString"),
            build=info.get("CFBundleVersion"),
        )
        executable_name = info.get("CFBundleExecutable")
        if not isinstance(executable_name, str) or Path(executable_name).name != executable_name:
            record.update(status="warning", error="Info.plist 缺少有效的 CFBundleExecutable；版本元数据已读取。")
            return record
        if executable_name in {".", ".."} or not executable_name:
            raise ValueError("CFBundleExecutable 无效")
        executable = bundle / "Contents" / "MacOS" / executable_name
        executable_stat = executable.stat()
        if not stat.S_ISREG(executable_stat.st_mode):
            raise ValueError("可执行路径不是普通文件")
        record.update(
            status="ok",
            executable=str(executable),
            executable_size=executable_stat.st_size,
        )
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        record.update(status="unavailable", error=str(exc)[:4096])
    return record


def _find_applications(processes: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: set[Path] = set()
    errors = []
    for root in APP_SEARCH_ROOTS:
        try:
            # pathlib.glob may suppress scandir permission errors; preserve a
            # denied inventory as unavailable instead of a false absence.
            with os.scandir(root) as entries:
                candidates.update(
                    Path(entry.path) for entry in entries
                    if entry.name.startswith("Serato DJ Pro") and entry.name.endswith(".app")
                )
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append(f"{root}: {exc}")
    for process in processes["main"] + processes["video"]:
        bundle = _app_root(process["executable"])
        if bundle is not None:
            candidates.add(bundle)
    records = []
    for bundle in sorted(candidates, key=str):
        records.append(_read_bundle(bundle, "main"))
        # This known location is an inventory hint, not a parser profile or an
        # assumption that the nested executable is currently running.
        nested = bundle / "Contents" / "MacOS" / "Serato Video.app"
        try:
            nested.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"{nested}: {exc}")
            continue
        records.append(_read_bundle(nested, "video"))
    return records, errors


def _signature(executable: str) -> dict[str, Any]:
    metadata = _run(["/usr/bin/codesign", "--display", "--verbose=4", executable])
    strict = _run(["/usr/bin/codesign", "--verify", "--strict", executable])
    anchor = _run([
        "/usr/bin/codesign", "--verify", "--strict", "-R", "=anchor apple generic", executable
    ])
    values: dict[str, Any] = {}
    for line in (metadata["stdout"] + "\n" + metadata["stderr"]).splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"Identifier", "Signature", "TeamIdentifier", "Authority"}:
            if key == "Authority":
                values.setdefault("Authority", []).append(value.strip())
            else:
                values[key] = value.strip()
    metadata_error = _command_problem(metadata)
    if metadata_error is None and not values.get("Identifier"):
        metadata_error = "签名元数据没有有效的 Identifier，结果未核验。"
    return {
        "path": executable,
        "metadata_status": "ok" if metadata_error is None else "unavailable",
        "metadata_error": metadata_error,
        "metadata": values,
        "strict": {"passed": _command_problem(strict) is None, "error": _command_problem(strict)},
        "apple_generic_anchor": {
            "passed": _command_problem(anchor) is None,
            "error": _command_problem(anchor),
        },
        "official_compatibility_verified": False,
    }


def _signature_check(record: dict[str, Any]) -> dict[str, str]:
    label = "磁盘签名 · " + Path(record["path"]).name
    if record["metadata_status"] != "ok":
        return _check(label, "unavailable", record["metadata_error"] or "签名元数据未核验")
    values = record["metadata"]
    team = values.get("TeamIdentifier", "未报告")
    signature = values.get("Signature", "由证书签署或未报告类型")
    strict = "通过" if record["strict"]["passed"] else "未通过"
    anchor = "通过" if record["apple_generic_anchor"]["passed"] else "未通过"
    detail = (
        f"Signature={signature}；TeamIdentifier={team}；磁盘自身一致性 {strict}；"
        f"Apple 通用签名要求 {anchor}。这些检查不证明官方兼容性，也不验证运行进程身份。"
    )
    status = "ok" if record["strict"]["passed"] and record["apple_generic_anchor"]["passed"] else "warning"
    return _check(label, status, detail)


def _ftok_key(path: Path) -> tuple[int | None, str | None]:
    """Calculate an existing marker's key; never create or attach IPC objects."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        ftok = libc.ftok
        ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]
        ftok.restype = ctypes.c_int32
        key = ftok(os.fsencode(path), 0x52)
        if key == -1:
            return None, os.strerror(ctypes.get_errno())
        return key & 0xFFFFFFFF, None
    except (AttributeError, OSError) as exc:
        return None, str(exc)


def _ipc_listing(result: dict[str, Any], kind: str) -> dict[str, Any]:
    problem = _command_problem(result)
    if problem:
        return {"status": "unavailable", "entries": [], "error": problem}
    entries = []
    malformed = 0
    for line in result["stdout"].splitlines():
        fields = line.split()
        if not fields or fields[0] != kind:
            continue
        try:
            if len(fields) < 6:
                raise ValueError("IPC 行字段不足")
            item = {
                "id": int(fields[1]),
                "key": int(fields[2], 0) & 0xFFFFFFFF,
                "mode": fields[3],
                "owner": fields[4],
                "group": fields[5],
            }
            if kind == "m" and len(fields) >= 8:
                item["creator_pid"] = int(fields[6]) if fields[6].isdigit() else None
                item["last_operator_pid"] = int(fields[7]) if fields[7].isdigit() else None
            entries.append(item)
        except ValueError:
            malformed += 1
    return {
        "status": "ok" if malformed == 0 else "unavailable",
        "entries": entries,
        "error": None if malformed == 0 else f"有 {malformed} 行 IPC 数据无法解析。",
    }


def _ipc_inventory(processes: dict[str, Any]) -> dict[str, Any]:
    shared = _ipc_listing(_run(SHM_COMMAND), "m")
    semaphores = _ipc_listing(_run(SEM_COMMAND), "s")
    uids = {os.getuid()}
    uids.update(process["uid"] for process in processes["main"] + processes["video"])
    markers = []
    for uid in sorted(uids):
        marker = IPC_ROOT / f"sslv_sharing{uid}"
        record: dict[str, Any] = {"path": str(marker), "expected_uid": uid}
        try:
            marker_stat = marker.lstat()
            record.update(
                exists=True,
                owner_uid=marker_stat.st_uid,
                owner_matches=marker_stat.st_uid == uid,
                regular_file=stat.S_ISREG(marker_stat.st_mode),
                size=marker_stat.st_size,
                inode=marker_stat.st_ino,
                device=marker_stat.st_dev,
                modified_at=dt.datetime.fromtimestamp(marker_stat.st_mtime, dt.timezone.utc).isoformat(),
            )
            if not record["regular_file"] or not record["owner_matches"]:
                record.update(status="error", error="IPC 标记必须是对应用户拥有的普通文件。")
            else:
                key, error = _ftok_key(marker)
                record.update(status="ok" if error is None else "unavailable", key=key, error=error)
                record["shared_memory"] = [item for item in shared["entries"] if item["key"] == key]
                record["semaphores"] = [item for item in semaphores["entries"] if item["key"] == key]
                try:
                    owner_name = pwd.getpwuid(uid).pw_name if pwd is not None else str(uid)
                except KeyError:
                    owner_name = str(uid)
                record["ipc_owner_matches"] = all(
                    item["owner"] in {owner_name, str(uid)}
                    for item in record["shared_memory"] + record["semaphores"]
                )
                record["video_pids"] = [item["pid"] for item in processes["video"] if item["uid"] == uid]
                record["creator_matches_current_video"] = (
                    any(item.get("creator_pid") in record["video_pids"] for item in record["shared_memory"])
                    if record["shared_memory"] and record["video_pids"] else None
                )
        except FileNotFoundError:
            record.update(status="warning", exists=False, error=None)
        except OSError as exc:
            record.update(status="unavailable", exists=None, error=str(exc))
        markers.append(record)
    video_pids = {item["pid"] for item in processes["video"]}
    return {
        "markers": markers,
        "shared_memory_listing": shared,
        "semaphore_listing": semaphores,
        "shared_memory_attached": False,
        "producer_identity_verified": False,
        "connection_verified": False,
        "segments_with_current_video_creator_pid": [
            item for item in shared["entries"] if item.get("creator_pid") in video_pids
        ],
    }


def inspect_serato() -> dict[str, Any]:
    """Return a bounded, JSON-serializable diagnostic report for the GUI."""
    system = platform.system()
    report: dict[str, Any] = {
        "schema_version": 1,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "read_only": True,
        "machine": {"system": system, "architecture": platform.machine(), "macos_version": platform.mac_ver()[0]},
        "summary": "",
        "checks": [],
        "applications": [],
        "signatures": [],
        "processes": {"status": "unavailable", "main": [], "video": [], "error": "未核验"},
        "ipc": {"shared_memory_attached": False, "connection_verified": False},
        "capabilities": {
            name: {"status": "unavailable", "reason": PENDING_READER}
            for name in ("audio_path", "position_seconds", "velocity", "video_fader")
        },
    }
    checks = report["checks"]
    if system != "Darwin":
        report["summary"] = "当前平台尚未实现 Serato 诊断；实时状态未连接。"
        checks.append(_check("运行平台", "unavailable", f"当前为 {system}；首个诊断探针面向 macOS。"))
        checks.append(_check("实时播放字段", "unavailable", PENDING_READER))
        return report
    checks.append(_check("运行平台", "ok", f"macOS {report['machine']['macos_version']} · {report['machine']['architecture']}"))
    processes = _process_inventory(_run(PS_COMMAND))
    report["processes"] = processes
    for role, label in (("main", "Serato 主进程"), ("video", "Serato Video 进程")):
        if processes["status"] != "ok":
            checks.append(_check(label, "unavailable", processes["error"] or "进程列表未核验"))
        elif processes[role]:
            pids = ", ".join(str(item["pid"]) for item in processes[role])
            checks.append(_check(label, "ok", f"进程列表发现 PID {pids}；仅为进程清单，出生时间和运行身份尚未验证。"))
        else:
            checks.append(_check(label, "warning", "已成功读取进程列表，该时刻未发现对应主可执行进程。"))
    applications, app_errors = _find_applications(processes)
    report["applications"] = applications
    if not applications:
        checks.append(_check("安装副本", "unavailable" if app_errors else "warning", "；".join(app_errors) or "默认目录及已发现进程路径中未找到 Serato DJ Pro 副本。"))
    for app in applications:
        label = "安装副本 · " + Path(app["path"]).name
        if app["status"] == "warning":
            checks.append(_check(label, "warning", f"{app['version'] or '版本未报告'} / {app['build'] or 'build 未报告'}；{app['path']}；{app['error']}"))
        elif app["status"] != "ok":
            checks.append(_check(label, "unavailable", app["error"]))
        else:
            checks.append(_check(label, "ok", f"{app['version'] or '版本未报告'} / {app['build'] or 'build 未报告'}；{app['path']}。磁盘版本不等同于当前运行版本。"))
    for error in app_errors:
        checks.append(_check("安装目录读取", "unavailable", error))
    executables = {app["executable"] for app in applications if app["status"] == "ok"}
    executables.update(item["executable"] for item in processes["main"] + processes["video"] if item["absolute_path"])
    for executable in sorted(executables):
        signature = _signature(executable)
        report["signatures"].append(signature)
        checks.append(_signature_check(signature))
    ipc = _ipc_inventory(processes)
    report["ipc"] = ipc
    for marker in ipc["markers"]:
        label = "IPC 标记 · " + Path(marker["path"]).name
        if marker["status"] == "ok":
            checks.append(_check(label, "ok", f"普通文件，UID {marker['owner_uid']}，key=0x{marker['key']:08x}。标记存在不证明生产者或实时连接有效。"))
        elif marker.get("exists") is False:
            checks.append(_check(label, "warning", "该时刻未发现标记；本探针不会创建标记或 IPC 对象。"))
        else:
            checks.append(_check(label, marker["status"], marker["error"] or "标记未核验"))
    for key, label in (("shared_memory_listing", "共享段清单"), ("semaphore_listing", "信号量清单")):
        listing = ipc[key]
        if listing["status"] != "ok":
            checks.append(_check(label, "unavailable", listing["error"] or "IPC 列表未核验"))
        else:
            matched = sum(len(marker.get("shared_memory" if key == "shared_memory_listing" else "semaphores", [])) for marker in ipc["markers"])
            checks.append(_check(label, "ok" if matched else "warning", f"成功读取清单，匹配标记 key 的对象 {matched} 个；未附接、未解析、未验证生产者。"))
    for marker in ipc["markers"]:
        if marker.get("ipc_owner_matches") is False:
            checks.append(_check("IPC 对象所有者", "error", f"{marker['path']} 对应 IPC 对象的 owner 与用户不一致。"))
        if marker.get("creator_matches_current_video") is False:
            creators = ", ".join(str(item.get("creator_pid")) for item in marker["shared_memory"])
            videos = ", ".join(str(pid) for pid in marker["video_pids"])
            checks.append(_check("IPC 生产者关系", "warning", f"标记 key 对应共享段的 CPID 为 {creators}，当前 Video PID 为 {videos}；创建 PID 不同。不能据此证明当前生产者正在更新，也不能仅凭 CPID 认定共享段失效。"))
    checks.append(_check("实时播放字段", "unavailable", PENDING_READER))
    if processes["status"] != "ok":
        report["summary"] = "进程或 IPC 条件未完整核验；实时状态尚未连接。"
    elif processes["main"] and processes["video"]:
        report["summary"] = "已发现 Serato 与 Video 进程；本清单不证明实时连接，请查看独立 reader 状态。"
    elif processes["main"]:
        report["summary"] = "已发现 Serato，当前进程清单未发现 Video；实时状态尚未连接。"
    else:
        report["summary"] = "当前进程清单未发现 Serato 主进程；实时状态尚未连接。"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读核验 Serato/macOS 安装、进程、签名与 IPC 条件")
    parser.add_argument("--output", type=Path, help="另存 JSON 报告到指定路径")
    args = parser.parse_args(argv)
    encoded = json.dumps(inspect_serato(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
        except OSError as exc:
            print(f"报告保存失败：{exc}", file=sys.stderr)
            return 1
    sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
