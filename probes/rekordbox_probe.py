"""Read-only rekordbox preflight. Acquiring a task port does not verify decks.

No process launch/termination, memory writes, signing or playback commands.
Reports unavailable values explicitly; never substitutes simulated A/B data.
"""
import argparse
import ctypes
from datetime import datetime
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
from zoneinfo import ZoneInfo


DEFAULT_APP = Path('/Applications/rekordbox 7/rekordbox.app')


def embedded_entitlements(binary):
    """Read XML entitlements from each signed Mach-O slice, including fat apps."""
    with Path(binary).open('rb') as stream:
        head = stream.read(8)
        magic, count = struct.unpack('>II', head)
        if magic == 0xCAFEBABE:
            if count > 16:
                raise ValueError('unexpected Mach-O slice count')
            slices = [struct.unpack('>IIIII', stream.read(20)) for _ in range(count)]
        else:
            slices = [(None, None, 0, None, None)]
        results = []
        for cpu, _, offset, _, _ in slices:
            stream.seek(offset)
            header = struct.unpack('<IiiIIIII', stream.read(32))
            if header[0] != 0xFEEDFACF or header[5] > 16 * 1024 * 1024:
                raise ValueError('unsupported Mach-O header')
            commands = stream.read(header[5])
            cursor = 0
            found = None
            for _ in range(header[4]):
                command, size = struct.unpack_from('<II', commands, cursor)
                if size < 8 or cursor + size > len(commands):
                    raise ValueError('invalid Mach-O load command')
                if command == 0x1D:
                    data_offset, data_size = struct.unpack_from('<II', commands, cursor + 8)
                    if data_size > 32 * 1024 * 1024:
                        raise ValueError('unexpected signature size')
                    stream.seek(offset + data_offset)
                    signature = stream.read(data_size)
                    sig_magic, length, slots = struct.unpack_from('>III', signature)
                    if sig_magic != 0xFADE0CC0 or length > len(signature) or slots > 64:
                        raise ValueError('invalid signature container')
                    for index in range(slots):
                        kind, start = struct.unpack_from('>II', signature, 12 + index * 8)
                        if kind == 5:
                            blob_magic, blob_length = struct.unpack_from('>II', signature, start)
                            if blob_magic != 0xFADE7171 or start + blob_length > length:
                                raise ValueError('invalid XML entitlements')
                            found = plistlib.loads(signature[start + 8:start + blob_length])
                cursor += size
            if not isinstance(found, dict):
                raise ValueError('XML entitlements unavailable; do not discard original rights')
            results.append({'cpu': cpu or header[1], 'entitlements': found})
        return results


def task_port_check(pid):
    library = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    library.mach_task_self.restype = ctypes.c_uint
    library.task_for_pid.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    library.task_for_pid.restype = ctypes.c_int
    library.mach_error_string.argtypes = [ctypes.c_int]
    library.mach_error_string.restype = ctypes.c_char_p
    task = ctypes.c_uint(0)
    owner = library.mach_task_self()
    result = library.task_for_pid(owner, pid, ctypes.byref(task))
    if result == 0 and task.value:
        library.mach_port_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint]
        library.mach_port_deallocate(owner, task.value)
    return {'code': result, 'detail': library.mach_error_string(result).decode(),
            'taskPortAcquired': result == 0 and bool(task.value)}


def executable_path(pid):
    library = ctypes.CDLL('/usr/lib/libproc.dylib')
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    library.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    if library.proc_pidpath(pid, buffer, len(buffer)) <= 0:
        raise OSError('process executable identity unavailable')
    return Path(buffer.value.decode()).resolve(strict=True)


def inspect_rekordbox(app=DEFAULT_APP, *, run=subprocess.run,
                     path_for_pid=executable_path, check_port=task_port_check):
    report = {'type': 'rekordbox_preflight', 'protocolVersion': 1,
              'capturedAt': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
              'liveConnectionVerified': False, 'memoryBytesRead': 0,
              'decks': [{'deck': side, 'status': 'unavailable', 'track': None,
                         'positionMs': None, 'speed': None, 'playing': None} for side in 'AB'],
              'checks': [], 'applicationModified': False}
    if sys.platform != 'darwin':
        report['checks'].append({'status': 'unavailable', 'code': 'unsupported_platform'})
        return report
    app = Path(app).resolve(strict=True)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != 'com.pioneerdj.rekordboxdj':
        raise ValueError('selected app is not rekordbox')
    binary = (app / 'Contents/MacOS' / info['CFBundleExecutable']).resolve(strict=True)
    with binary.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    report['application'] = {'path': str(app), 'version': info.get('CFBundleShortVersionString'),
                             'binarySha256': digest}
    result = run(['/usr/bin/pgrep', '-x', 'rekordbox'], capture_output=True, text=True, timeout=3)
    if result.returncode:
        report['checks'].append({'status': 'unavailable',
                                 'code': 'process_query_failed' if result.stderr.strip() else 'not_running',
                                 'detail': result.stderr.strip()[:500]})
        return report
    matches = []
    for token in result.stdout.split():
        if not token.isdecimal():
            continue
        pid = int(token)
        try:
            if path_for_pid(pid) == binary:
                matches.append(pid)
        except OSError:
            report['checks'].append({'status': 'unavailable', 'code': 'process_identity_unavailable', 'pid': pid})
    if len(matches) != 1:
        report['checks'].append({'status': 'unavailable', 'code': 'process_identity_not_unique', 'matches': matches})
        return report
    pid = matches[0]
    permission = check_port(pid)
    report['process'] = {'pid': pid, 'executableVerified': True}
    report['checks'].append({'status': 'ok' if permission['taskPortAcquired'] else 'unavailable',
                             'code': 'task_port_acquired' if permission['taskPortAcquired'] else 'task_port_denied',
                             'kernelCode': permission['code'], 'detail': permission['detail'],
                             'taskPortAcquired': permission['taskPortAcquired']})
    # A port alone provides no evidence for the deck layout, units or freshness.
    report['checks'].append({'status': 'unavailable', 'code': 'deck_mapping_not_live_validated'})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=DEFAULT_APP)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = inspect_rekordbox(args.app)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        report = {'type': 'rekordbox_preflight', 'liveConnectionVerified': False,
                  'memoryBytesRead': 0, 'error': str(error)}
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n')
    print(encoded)
    return 0 if report.get('liveConnectionVerified') else 2


if __name__ == '__main__':
    raise SystemExit(main())
