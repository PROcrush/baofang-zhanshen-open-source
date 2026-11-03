"""Own transparent lyric window follows an identified Serato output window."""
import ctypes
import json
import math
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QRegion

from app.clock import monotonic_ns


def window_rectangle(row):
    bounds = row.get('bounds', {})
    values = tuple(bounds.get(key) for key in ('x', 'y', 'width', 'height'))
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        return None
    x, y, width, height = values
    if width <= 1 or height <= 1 or max(abs(x), abs(y), width, height) > 32768:
        return None
    return tuple(round(value) for value in values)


def producer_roles(producers):
    """Reuse the reader's verified main/nested Video pair, never a window name alone."""
    if not isinstance(producers, list) or len(producers) != 2:
        return {}
    mains = [row for row in producers if isinstance(row, dict) and
             str(row.get('executable', '')).endswith('/Contents/MacOS/Serato DJ Pro')]
    if len(mains) != 1:
        return {}
    main = mains[0]
    executable = Path(main['executable'])
    if not executable.is_absolute() or executable.parents[2].suffix != '.app':
        return {}
    video_path = str(executable.parent / 'Serato Video.app/Contents/MacOS/Serato Video')
    videos = [row for row in producers if isinstance(row, dict) and row.get('executable') == video_path]
    if len(videos) != 1:
        return {}
    video = videos[0]
    if any(type(row.get('pid')) is not int or row['pid'] <= 1 for row in (main, video)) or main['pid'] == video['pid']:
        return {}
    return {main['pid']: ('com.serato.seratodj', main['executable']),
            video['pid']: ('com.serato.seratovideo', video_path)}


def select_host(snapshot, producers):
    if not snapshot.get('available'):
        return None, '无法读取 macOS 视频窗口信息'
    candidates = []
    roles = producer_roles(producers)
    if not roles:
        return None, '等待只读联动核验 Serato 进程身份'
    for row in snapshot.get('windows', []):
        role = roles.get(row.get('pid'))
        if not role:
            continue
        bundle, executable = role
        layers = (0, 3) if bundle == 'com.serato.seratovideo' else (0,)
        title = str(row.get('title', '')).strip().casefold()
        # CGWindow may omit titles/application metadata without screen capture
        # access. The pinned Video PID is sufficient for a unique video window;
        # the main process still needs its exact output title to exclude the DJ UI.
        title_matches = title in ('serato video output', 'video output')
        if (row.get('onScreen') is True and
                row.get('bundleID', bundle) == bundle and
                row.get('executablePath', executable) == executable and
                type(row.get('pid')) is int and row['pid'] > 1 and
                type(row.get('windowID')) is int and row['windowID'] > 0 and
                row.get('layer') in layers and window_rectangle(row) and
                (title_matches or (bundle == 'com.serato.seratovideo' and not title))):
            candidates.append(row)
    if len(candidates) == 1:
        return candidates[0], ''
    if len(candidates) > 1:
        return None, '检测到多个 Serato 视频输出窗口，暂不选择；请保留一个目标窗口'
    return None, '等待 Serato Video Output；请在 Serato 打开视频输出窗口'


def subtract_rectangle(region, cover):
    x, y, width, height = region
    a, b, w, h = cover
    left, top = max(x, a), max(y, b)
    right, bottom = min(x + width, a + w), min(y + height, b + h)
    if left >= right or top >= bottom:
        return [region]
    result = []
    for rectangle in ((x, y, width, top - y), (x, bottom, width, y + height - bottom),
                      (x, top, left - x, bottom - top), (right, top, x + width - right, bottom - top)):
        if rectangle[2] > 0 and rectangle[3] > 0:
            result.append(rectangle)
    return result


def needs_clipping(row, host, own_window_ids=()):
    layer = row.get('layer')
    output_level = 4 if host['layer'] == 3 else 3
    # Only clip windows that raising our caption panel could incorrectly paint
    # over. Higher layers already occlude both windows through macOS composition;
    # their bounding boxes may contain mostly transparent pixels (e.g. Dock).
    return (row.get('windowID') not in own_window_ids and row.get('onScreen') is True and
            row.get('alpha', 1) > 0 and type(layer) is int and host['layer'] <= layer <= output_level)


def visible_regions(windows, host, own_window_ids=()):
    bounds = window_rectangle(host)
    if not bounds:
        return []
    regions = [bounds]
    found = False
    for row in windows:
        if row.get('windowID') == host['windowID'] and row.get('pid') == host['pid']:
            found = True
            break
        if not needs_clipping(row, host, own_window_ids):
            continue
        cover = window_rectangle(row)
        if cover:
            regions = [piece for region in regions for piece in subtract_rectangle(region, cover)]
            if len(regions) > 256:
                return []
    return regions if found else []


class NativeOverlayBridge:
    def __init__(self, root):
        if sys.platform != 'darwin':
            raise RuntimeError('Serato 窗口字幕叠加目前支持 macOS')
        self.library = ctypes.CDLL(str(Path(root) / 'build/native/libbaofang_overlay.dylib'))
        self.library.bf_overlay_windows_json.argtypes = []
        self.library.bf_overlay_windows_json.restype = ctypes.c_void_p
        self.library.bf_overlay_free.argtypes = [ctypes.c_void_p]
        self.library.bf_overlay_free.restype = None
        for name in ('bf_overlay_configure', 'bf_overlay_raise'):
            function = getattr(self.library, name)
            function.argtypes = [ctypes.c_size_t]
            function.restype = ctypes.c_bool
        self.library.bf_overlay_set_host_layer.argtypes = [ctypes.c_size_t, ctypes.c_int]
        self.library.bf_overlay_set_host_layer.restype = ctypes.c_bool
        self.library.bf_overlay_window_state_json.argtypes = [ctypes.c_size_t]
        self.library.bf_overlay_window_state_json.restype = ctypes.c_void_p
        from probes.macos_ipc import MacReadOnlyIPC
        self.process_reader = MacReadOnlyIPC()

    def snapshot(self):
        pointer = self.library.bf_overlay_windows_json()
        if not pointer:
            raise RuntimeError('视频窗口信息读取失败')
        try:
            return json.loads(ctypes.string_at(pointer))
        finally:
            self.library.bf_overlay_free(pointer)

    def configure(self, window):
        if not self.library.bf_overlay_configure(int(window.winId())):
            raise RuntimeError('透明字幕窗口配置失败')

    def raise_window(self, window, host):
        if not (self.library.bf_overlay_set_host_layer(int(window.winId()), host['layer']) and
                self.library.bf_overlay_raise(int(window.winId()))):
            raise RuntimeError('Serato 字幕窗口显示失败')

    def window_state(self, window):
        pointer = self.library.bf_overlay_window_state_json(int(window.winId()))
        if not pointer:
            raise RuntimeError('字幕窗口状态读取失败')
        try:
            return json.loads(ctypes.string_at(pointer))
        finally:
            self.library.bf_overlay_free(pointer)

    def producer_is_current(self, host, producers):
        expected = next((row for row in producers if row.get('pid') == host['pid']), None)
        return bool(expected and self.process_reader.process(host['pid']) == expected)


class SeratoOverlay(QObject):
    def __init__(self, root, runtime, controller, window, bridge=None):
        super().__init__()
        self.controller, self.window = controller, window
        self.status_path = Path(runtime) / 'serato-overlay-status.json'
        self.bridge = bridge or NativeOverlayBridge(root)
        self.bridge.configure(window)
        self.configured = True
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.tick()

    def status(self, phase, detail, host=None, regions=()):
        value = {'schemaVersion': 1, 'rendererPid': os.getpid(), 'capturedNs': monotonic_ns(),
                 'phase': phase, 'detail': detail, 'visible': self.window.isVisible()}
        if host:
            value.update(windowID=host['windowID'], hostPid=host['pid'], hostTitle=host.get('title', ''),
                         bounds=window_rectangle(host), visibleRegions=len(regions))
        read_window = getattr(self.bridge, 'window_state', None)
        if read_window:
            value['nativeWindow'] = read_window(self.window)
        if phase == 'covered' and host:
            # Bounds-only diagnostics explain real occlusion without collecting
            # unrelated application names, titles or screen pixels.
            blockers = []
            bounds = window_rectangle(host)
            for row in getattr(self, 'last_snapshot', {}).get('windows', []):
                if row.get('windowID') == host['windowID'] and row.get('pid') == host['pid']:
                    break
                cover = window_rectangle(row)
                if (row.get('pid') != os.getpid() and needs_clipping(row, host) and
                        cover and subtract_rectangle(bounds, cover) != [bounds]):
                    blockers.append({key: row.get(key) for key in ('windowID', 'pid', 'layer', 'alpha', 'bounds')})
            value['occludingWindows'] = blockers[:32]
        temporary = self.status_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False))
        os.replace(temporary, self.status_path)

    def tick(self):
        try:
            started = monotonic_ns()
            snapshot = self.bridge.snapshot()
            self.last_snapshot = snapshot
            if monotonic_ns() - started > 350_000_000:
                self.window.hide()
                self.status('suspended', '视频窗口几何读取已过期，等待新状态')
                return
            state = self.controller.state
            producers = state.get('readerDiagnostics', {}).get('producers', [])
            host, error = select_host(snapshot, producers)
            if not host:
                self.window.hide()
                self.status('waiting', error)
                return
            if not self.bridge.producer_is_current(host, producers):
                self.window.hide()
                self.status('waiting', '视频窗口进程身份已变化，等待重新核验')
                return
            own = [row['windowID'] for row in snapshot.get('windows', []) if row.get('pid') == os.getpid()]
            regions = visible_regions(snapshot['windows'], host, own)
            usable = (not state.get('stale', True) and state.get('effectiveSource') in ('A', 'B') and
                      state.get('mode') == 'serato' and state.get('selectedSource') != 'none' and
                      not state.get('outputBlocked') and
                      state.get('deck' + state['effectiveSource'], {}).get('cacheState') == 'ready')
            if not usable:
                self.window.hide()
                self.status('suspended', '歌词来源未就绪或已隐藏，叠加暂停', host)
                return
            if not regions:
                self.window.hide()
                self.status('covered', 'Serato 视频窗口被遮挡，字幕随窗口隐藏', host)
                return
            x, y, width, height = window_rectangle(host)
            if self.window.geometry().getRect() != (x, y, width, height):
                self.window.setGeometry(x, y, width, height)
            mask = QRegion()
            for a, b, w, h in regions:
                mask = mask.united(QRegion(a - x, b - y, w, h))
            self.window.setMask(mask)
            self.window.show()
            self.bridge.raise_window(self.window, host)
            self.status('attached', '字幕已叠加到 Serato 视频窗口，样式修改实时生效', host, regions)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            self.window.hide()
            self.status('error', str(exc)[:300])

    def shutdown(self):
        self.timer.stop()
        self.window.hide()
        self.status('closed', 'Serato 字幕叠加已关闭')
