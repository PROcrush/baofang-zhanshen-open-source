"""Move pack: everything needed to set up this app on another Mac.

A folder with the lyric library backup (existing format, matched by audio
SHA256), plus live settings, tonight's set list, MIDI mapping and show logs.
No music, no API keys (keys stay in the Keychain), no studio project files.
"""
import json
import shutil
from pathlib import Path

from app.lyric_cache import atomic_bytes, checksum, encode_json

KIND = 'baofang-move-pack'
FILES = ('settings.json', 'setlist.json', 'midi.json')
MAX_FILE = 4_000_000


def export_pack(library, runtime, destination):
    target = Path(destination)
    if target.exists() and any(target.iterdir()):
        target = target / '包房战神搬家包'
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise ValueError('目标文件夹里已有搬家包，请换一个空文件夹')
    runtime = Path(runtime)
    lyrics = library.export_cache(target / '歌词库')
    entries = []
    for name in FILES:
        source = runtime / name
        if source.is_file() and source.stat().st_size <= MAX_FILE:
            data = source.read_bytes()
            atomic_bytes(target / '设置' / name, data)
            entries.append({'file': name, 'sha256': checksum(data)})
    logs = runtime / 'show-log'
    log_count = 0
    if logs.is_dir():
        (target / '演出记录').mkdir(exist_ok=True)
        for path in sorted(logs.glob('*.jsonl')):
            if path.stat().st_size <= MAX_FILE:
                shutil.copy2(path, target / '演出记录' / path.name)
                log_count += 1
    atomic_bytes(target / 'pack.json', encode_json({'schemaVersion': 1, 'kind': KIND, 'settings': entries,
                                                    'lyrics': lyrics.get('exported', 0), 'logs': log_count}))
    return {'directory': str(target), 'lyrics': lyrics.get('exported', 0), 'settings': len(entries), 'logs': log_count}


def read_pack(source):
    origin = Path(source)
    manifest = json.loads((origin / 'pack.json').read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('kind') != KIND or manifest.get('schemaVersion') != 1:
        raise ValueError('这不是包房战神的搬家包')
    settings = {}
    for entry in manifest.get('settings', []):
        name = entry.get('file') if isinstance(entry, dict) else None
        if name not in FILES:
            raise ValueError('搬家包里有未知设置文件')
        path = origin / '设置' / name
        if path.stat().st_size > MAX_FILE:
            raise ValueError('搬家包设置文件过大')
        data = path.read_bytes()
        if checksum(data) != entry.get('sha256'):
            raise ValueError('搬家包设置文件校验失败：' + name)
        settings[name] = json.loads(data.decode('utf-8'))
    return origin, settings


def import_logs(origin, runtime):
    folder = Path(origin) / '演出记录'
    target = Path(runtime) / 'show-log'
    count = 0
    if folder.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for path in folder.glob('*.jsonl'):
            if len(path.stem) == 10 and path.stat().st_size <= MAX_FILE and not (target / path.name).exists():
                shutil.copy2(path, target / path.name)
                count += 1
    return count
