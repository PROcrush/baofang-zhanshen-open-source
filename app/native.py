import ctypes
import sys
from pathlib import Path


class Observation(ctypes.Structure):
    _fields_ = [('position_ms', ctypes.c_double), ('speed_ratio', ctypes.c_double),
                ('duration_ms', ctypes.c_double), ('captured_ns', ctypes.c_int64),
                ('valid_until_ns', ctypes.c_int64), ('play_state', ctypes.c_int32)]


class Estimate(ctypes.Structure):
    _fields_ = [('position_ms', ctypes.c_double), ('status', ctypes.c_int32)]


class NativeClock:
    def __init__(self, root):
        name = 'libbaofang_sync.dylib' if sys.platform == 'darwin' else 'libbaofang_sync.so'
        path = Path(root) / 'build/native' / name
        self.library = ctypes.CDLL(str(path))
        self.library.bf_estimate_position.argtypes = [ctypes.POINTER(Observation), ctypes.c_int64, ctypes.POINTER(Estimate)]
        self.library.bf_estimate_position.restype = ctypes.c_int

    def estimate(self, deck, now_ns):
        result = Estimate()
        observation = Observation(deck['positionMs'], deck['speed'], deck['durationMs'],
                                  deck['capturedNs'], deck['validUntilNs'],
                                  1 if deck['playing'] and deck.get('clockMode') != 'observed' else 2)
        status = self.library.bf_estimate_position(ctypes.byref(observation), now_ns, ctypes.byref(result))
        return (result.position_ms, result.status) if status == 0 else (0.0, 2)
