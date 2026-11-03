"""Map spare buttons on a DJ controller to live actions (MIDI learn).

Uses mido with the python-rtmidi backend. Only note-on and control-change
presses are read; nothing is ever sent to the controller. The mapping is kept
in runtime/midi.json.
"""
import json
import os
import threading
from collections import deque
from pathlib import Path

ACTIONS = {'toggleLyrics': '隐藏 / 恢复歌词', 'tap': '打拍对齐', 'sourceA': '指定 A 盘',
           'sourceB': '指定 B 盘', 'earlier': '歌词提前 0.1 秒', 'later': '歌词延后 0.1 秒',
           'clearMessage': '清除客屏插字'}


def message_key(message):
    """Stable key for a press, or None for releases and other messages."""
    kind = getattr(message, 'type', '')
    if kind == 'note_on' and message.velocity > 0:
        return f'note:{message.channel}:{message.note}'
    if kind == 'control_change' and message.value > 0:
        return f'cc:{message.channel}:{message.control}'
    return None


def describe(key):
    if not key:
        return '未设置'
    kind, channel, number = key.split(':')
    return f'{"音符" if kind == "note" else "旋钮/推子"} {number} · 通道 {int(channel) + 1}'


def validate_mapping(value):
    if not isinstance(value, dict):
        raise ValueError('MIDI 设置无效')
    result = {}
    for action, key in value.items():
        if action not in ACTIONS or not isinstance(key, str) or len(key) > 32:
            raise ValueError('MIDI 设置无效')
        parts = key.split(':')
        if len(parts) != 3 or parts[0] not in ('note', 'cc') or not all(p.isdigit() for p in parts[1:]):
            raise ValueError('MIDI 设置无效')
        result[action] = key
    return result


def available():
    try:
        import mido
        mido.set_backend('mido.backends.rtmidi')
        return True
    except Exception:
        return False


def input_names():
    try:
        import mido
        mido.set_backend('mido.backends.rtmidi')
        return list(dict.fromkeys(mido.get_input_names()))
    except Exception:
        return []


class MidiControl:
    def __init__(self, path):
        self.path = Path(path)
        self.mapping = {}
        self.port_name = ''
        self.port = None
        self.learning = ''
        self.events = deque(maxlen=64)
        self.lock = threading.Lock()
        self.error = ''
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.mapping = validate_mapping(data.get('mapping', {}))
            self.port_name = data.get('port', '') if isinstance(data.get('port', ''), str) else ''
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'schemaVersion': 1, 'port': self.port_name, 'mapping': self.mapping},
                                        ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.path)

    def _callback(self, message):
        key = message_key(message)
        if key:
            with self.lock:
                self.events.append(key)

    def open(self, name):
        self.close()
        self.port_name = name or ''
        self.error = ''
        if not name:
            self.save()
            return False
        try:
            import mido
            mido.set_backend('mido.backends.rtmidi')
            self.port = mido.open_input(name, callback=self._callback)
        except Exception as exc:
            self.port, self.error = None, f'打不开 {name}：{exc}'[:200]
        self.save()
        return self.port is not None

    def close(self):
        port, self.port = self.port, None
        if port:
            try:
                port.close()
            except Exception:
                pass

    def learn(self, action):
        if action and action not in ACTIONS:
            raise ValueError('未知操作')
        self.learning = action or ''

    def clear(self, action):
        if action in self.mapping:
            mapping = dict(self.mapping)
            mapping.pop(action)
            self.mapping = mapping
            self.save()

    def poll(self):
        """Return actions pressed since last poll; a press during learn binds instead."""
        with self.lock:
            keys = list(self.events)
            self.events.clear()
        actions = []
        for key in keys:
            if self.learning:
                mapping = {a: k for a, k in self.mapping.items() if k != key}
                mapping[self.learning] = key
                self.mapping = validate_mapping(mapping)
                self.learning = ''
                self.save()
                continue
            actions.extend(action for action, bound in self.mapping.items() if bound == key)
        return actions

    def view(self, ports=()):
        return {'available': available(), 'ports': list(ports), 'port': self.port_name,
                'connected': self.port is not None, 'error': self.error, 'learning': self.learning,
                'rows': [{'action': a, 'label': l, 'key': self.mapping.get(a, ''), 'binding': describe(self.mapping.get(a))}
                         for a, l in ACTIONS.items()]}
