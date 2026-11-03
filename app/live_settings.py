"""Local live preferences, shared by the console and independent output."""
MAX_OFFSET_MS = 3000
MAX_OFFSET_TRACKS = 512

INPUT_MODES = ('replay', 'serato', 'rekordbox', 'rekordbox_ax')


def live_session(state):
    """Only operator choices survive a restart, never playback observations."""
    return {'inputMode': state['inputMode'], 'selectedSource': state['selectedSource'],
            'resumeSource': state['resumeSource']}


def load_live_session(runtime, input_mode=None):
    import json
    from pathlib import Path
    from app.lyric_cache import read_bytes
    session = {'inputMode': 'replay', 'selectedSource': 'auto', 'resumeSource': 'auto'}
    try:
        saved = json.loads(read_bytes(Path(runtime) / 'live-session.json', limit=4096))
        if (not isinstance(saved, dict) or saved.get('schemaVersion') != 1
                or saved.get('inputMode') not in INPUT_MODES
                or saved.get('selectedSource') not in ('auto', 'A', 'B', 'none')
                or saved.get('resumeSource') not in ('auto', 'A', 'B')):
            raise ValueError('现场状态记录无效')
        session = {key: saved[key] for key in session}
        if session['selectedSource'] != 'none':
            session['resumeSource'] = session['selectedSource']
    except (OSError, ValueError, TypeError, KeyError):
        pass
    if input_mode is not None:
        if input_mode not in INPUT_MODES:
            raise ValueError('现场输入模式无效')
        session['inputMode'] = input_mode
    if session['inputMode'] == 'rekordbox_ax':
        for key in ('selectedSource', 'resumeSource'):
            if session[key] == 'auto':
                session[key] = 'A'
    return session


def save_live_session(runtime, state):
    from pathlib import Path
    from app.lyric_cache import atomic_bytes, encode_json
    atomic_bytes(Path(runtime) / 'live-session.json',
                 encode_json({'schemaVersion': 1, **live_session(state)}))


def input_mode_state(mode):
    label = {'serato': 'Serato 只读联动', 'rekordbox': 'rekordbox 测试副本',
             'rekordbox_ax': 'rekordbox 原版跟随', 'replay': '回放演示'}[mode]
    notice = ('只读原版界面；先确认每盘实际歌曲文件，再指定 A / B。换歌后重新确认，不改签 rekordbox。' if mode == 'rekordbox_ax' else
              '只读 rekordbox 7.2.19 测试副本；按源位置跟随，播放操作留在 DJ 软件。' if mode == 'rekordbox' else
              '只读 Serato Video；新识别需审阅歌曲准备计划，不控制 DJ。' if mode == 'serato' else '回放数据，不播放声音；未连接真实 DJ。')
    status = ('等待原版 rekordbox 的界面位置' if mode == 'rekordbox_ax' else
              '等待 rekordbox 测试副本的播放位置' if mode == 'rekordbox' else
              '等待有效 Serato Video 状态' if mode == 'serato' else '正在启动回放桥')
    return dict(mode=mode, inputMode=mode, modeLabel=label, notice=notice, bridgeStatus=status)


def validate_offsets(value):
    if not isinstance(value, dict) or len(value) > MAX_OFFSET_TRACKS:
        raise ValueError('歌曲同步设置无效')
    for version, offset in value.items():
        if not isinstance(version, str) or not version.strip() or len(version) > 256:
            raise ValueError('歌曲同步设置缺少有效版本')
        if type(offset) is not int or not -MAX_OFFSET_MS <= offset <= MAX_OFFSET_MS:
            raise ValueError('歌词同步微调范围为前后 3 秒')
    return dict(value)


TAP_WINDOW_MS = 1500


def line_starts(timeline, traditional=False):
    """Start times of the lines the guest screen actually shows."""
    if not timeline or not timeline.get('segments'):
        return []
    segments = timeline['segments']
    if traditional:
        from app.karaoke import traditional_lines
        segments = traditional_lines([dict(row, characters=row.get('words', []) if timeline.get('quality') == 'word' else [])
                                      for row in segments])
    return sorted(float(row['startMs']) for row in segments)


def tap_offset(timeline, position_ms, current_offset, *, traditional=False, window_ms=TAP_WINDOW_MS):
    """One whole-song offset from a tap at the moment a line starts being sung.

    Picks the line start nearest to where the lyrics currently think we are,
    so a tap shifts the whole song; no line time is rewritten. Returns
    (offset_ms, line_index) or None when no line starts near the tap.
    """
    starts = line_starts(timeline, traditional)
    if not starts:
        return None
    shown = position_ms - current_offset
    index = min(range(len(starts)), key=lambda i: abs(starts[i] - shown))
    if abs(starts[index] - shown) > window_ms:
        return None
    offset = int(round((position_ms - starts[index]) / 10.0)) * 10
    if not -MAX_OFFSET_MS <= offset <= MAX_OFFSET_MS:
        return None
    return offset, index


def validate_output_target(value):
    if not isinstance(value, dict) or set(value) != {'screenName', 'fullScreen'}:
        raise ValueError('客屏显示设置无效')
    if not isinstance(value['screenName'], str) or len(value['screenName']) > 256:
        raise ValueError('客屏显示器名称无效')
    if type(value['fullScreen']) is not bool:
        raise ValueError('客屏全屏设置无效')
    return dict(value)


def screen_options():
    from PySide6.QtGui import QGuiApplication
    instance = QGuiApplication.instance()
    if not isinstance(instance, QGuiApplication):
        return []
    return [{'name': screen.name(),
             'label': f'显示器 {index + 1} · {screen.name() or "未命名"} · {screen.size().width()}×{screen.size().height()}'}
            for index, screen in enumerate(instance.screens())]


def place_output(window, target):
    """Apply an explicit target. A missing monitor returns to a normal window."""
    from PySide6.QtGui import QGuiApplication, QWindow
    screens = QGuiApplication.screens()
    requested = target['screenName']
    screen = next((item for item in screens if item.name() == requested), None) if requested else None
    missing = bool(requested and screen is None)
    if screen is None:
        screen = window.screen() if window.screen() in screens else QGuiApplication.primaryScreen()
    if screen is None:
        return
    if window.screen() != screen:
        window.setScreen(screen)
    if target['fullScreen'] and not missing:
        window.setGeometry(screen.geometry())
        window.showFullScreen()
    else:
        if window.visibility() == QWindow.Visibility.FullScreen:
            window.showNormal()
        area = screen.availableGeometry()
        width = min(1120, area.width())
        height = min(720, area.height())
        window.setGeometry(area.x() + (area.width() - width) // 2,
                           area.y() + (area.height() - height) // 2, width, height)
