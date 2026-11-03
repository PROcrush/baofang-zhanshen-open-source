"""Small macOS-only read-only IPC and process metadata boundary.

No create flags, semop, SETVAL, IPC_SET, IPC_RMID, task_for_pid, or process-memory
API is exposed. shmat uses SHM_RDONLY; shmdt only releases our own attachment.
ABI layouts were checked against the local SDK with sizeof/offsetof on arm64.
"""

from __future__ import annotations

import ctypes as C
import os
import platform

from .movie_parser import BUFFER_SIZE


class NativeReadError(OSError):
    pass


class IpcPerm(C.Structure):
    _pack_ = 4
    _fields_ = [("uid", C.c_uint32), ("gid", C.c_uint32), ("cuid", C.c_uint32), ("cgid", C.c_uint32), ("mode", C.c_uint16), ("seq", C.c_uint16), ("key", C.c_int32)]


class ShmInfo(C.Structure):
    _pack_ = 4
    _fields_ = [("perm", IpcPerm), ("size", C.c_uint64), ("last_pid", C.c_int32), ("creator_pid", C.c_int32), ("attachments", C.c_uint16), ("atime", C.c_int64), ("dtime", C.c_int64), ("ctime", C.c_int64), ("internal", C.c_void_p)]


class SemInfo(C.Structure):
    _pack_ = 4
    _fields_ = [("perm", IpcPerm), ("base", C.c_int32), ("count", C.c_uint16), ("otime", C.c_int64), ("pad1", C.c_int32), ("ctime", C.c_int64), ("pad2", C.c_int32), ("pad3", C.c_int32 * 4)]


class BsdInfo(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")]
    _fields_ += [("comm", C.c_char * 16), ("name", C.c_char * 32)]
    _fields_ += [(name, C.c_uint32) for name in ("nfiles", "pgid", "pjobc", "tdev", "tpgid")]
    _fields_ += [("nice", C.c_int32), ("start_seconds", C.c_uint64), ("start_microseconds", C.c_uint64)]


def _error(operation: str) -> NativeReadError:
    number = C.get_errno()
    return NativeReadError(number, f"{operation}: {os.strerror(number)}")


def _permission(info: IpcPerm) -> dict:
    return {"uid": info.uid, "gid": info.gid, "creator_uid": info.cuid, "creator_gid": info.cgid, "mode": info.mode, "sequence": info.seq, "key": info.key & 0xFFFFFFFF}


class MacReadOnlyIPC:
    """Native boundary; every control command is a metadata GET/STAT."""

    def __init__(self):
        if platform.system() != "Darwin" or C.sizeof(C.c_void_p) != 8:
            raise NativeReadError("只读 Serato IPC reader 需要 64 位 macOS")
        if (C.sizeof(IpcPerm), C.sizeof(ShmInfo), ShmInfo.ctime.offset, C.sizeof(SemInfo), SemInfo.ctime.offset, C.sizeof(BsdInfo), BsdInfo.start_seconds.offset) != (24, 76, 60, 72, 44, 136, 120):
            raise NativeReadError("macOS IPC ABI 与已核验布局不同")
        self.lib = C.CDLL(None, use_errno=True)
        signatures = {
            "ftok": ([C.c_char_p, C.c_int], C.c_int32),
            "shmget": ([C.c_int32, C.c_size_t, C.c_int], C.c_int),
            "semget": ([C.c_int32, C.c_int, C.c_int], C.c_int),
            "shmctl": ([C.c_int, C.c_int, C.POINTER(ShmInfo)], C.c_int),
            "shmat": ([C.c_int, C.c_void_p, C.c_int], C.c_void_p),
            "shmdt": ([C.c_void_p], C.c_int),
            "proc_pidinfo": ([C.c_int, C.c_int, C.c_uint64, C.c_void_p, C.c_int], C.c_int),
            "proc_pidpath": ([C.c_int, C.c_void_p, C.c_uint32], C.c_int),
            "OSMemoryBarrier": ([], None),
        }
        for name, (args, result) in signatures.items():
            call = getattr(self.lib, name)
            call.argtypes, call.restype = args, result
        # semctl is variadic. Only the first three fixed parameters are typed;
        # a pointer passed to IPC_STAT is the value of union semun.buf.
        self.lib.semctl.argtypes = [C.c_int, C.c_int, C.c_int]
        self.lib.semctl.restype = C.c_int

    def process(self, pid: int) -> dict:
        info = BsdInfo()
        size = self.lib.proc_pidinfo(pid, 3, 0, C.byref(info), C.sizeof(info))
        if size != C.sizeof(info):
            raise _error("读取进程出生身份")
        path_buffer = C.create_string_buffer(4096)
        if self.lib.proc_pidpath(pid, path_buffer, len(path_buffer)) <= 0:
            raise _error("读取进程可执行路径")
        return {"pid": info.pid, "uid": info.uid, "real_uid": info.ruid, "started_seconds": info.start_seconds, "started_microseconds": info.start_microseconds, "executable": os.fsdecode(path_buffer.value)}

    def marker_key(self, path: str) -> int:
        value = self.lib.ftok(os.fsencode(path), 0x52)
        if value == -1:
            raise _error("读取 IPC 标记 key")
        return value & 0xFFFFFFFF

    def ids(self, key: int) -> tuple[int, int]:
        # IPC_R only, no IPC_CREAT or IPC_EXCL. Size one looks up an existing
        # segment; the full actual size is verified with IPC_STAT before shmat.
        shm = self.lib.shmget(key, 1, 0x100)
        if shm == -1:
            raise _error("查找已有共享段")
        sem = self.lib.semget(key, 1, 0x100)
        if sem == -1:
            raise _error("查找已有信号量")
        return shm, sem

    def shm_info(self, identifier: int) -> dict:
        info = ShmInfo()
        if self.lib.shmctl(identifier, 2, C.byref(info)) == -1:
            raise _error("读取共享段元数据")
        return {"permission": _permission(info.perm), "size": info.size, "creator_pid": info.creator_pid, "ctime": info.ctime}

    def sem_info(self, identifier: int) -> dict:
        info = SemInfo()
        if self.lib.semctl(identifier, 0, 2, C.byref(info)) == -1:
            raise _error("读取信号量元数据")
        return {"permission": _permission(info.perm), "count": info.count, "ctime": info.ctime}

    def semaphore(self, identifier: int) -> tuple[int, int]:
        value = self.lib.semctl(identifier, 0, 5)
        if value == -1:
            raise _error("读取信号量值")
        pid = self.lib.semctl(identifier, 0, 4)
        if pid == -1:
            raise _error("读取信号量最近操作 PID")
        return value, pid

    def attach(self, identifier: int) -> int:
        address = self.lib.shmat(identifier, None, 0x1000)  # SHM_RDONLY
        if address in (None, C.c_void_p(-1).value):
            raise _error("只读附接共享段")
        return address

    def snapshots(self, address: int) -> tuple[bytes, bytes]:
        # Explicit native full fences surround the two copies, rather than
        # assuming the Python interpreter supplies a shared-memory barrier.
        self.lib.OSMemoryBarrier()
        first = C.string_at(address, BUFFER_SIZE)
        self.lib.OSMemoryBarrier()
        second = C.string_at(address, BUFFER_SIZE)
        self.lib.OSMemoryBarrier()
        return first, second

    def detach(self, address: int) -> None:
        if self.lib.shmdt(address) == -1:
            raise _error("释放本 reader 的只读附接")
