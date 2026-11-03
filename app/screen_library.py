"""Personal phrases, local sent history and a narrow portable DJ profile."""
import copy
import hashlib
import io
import json
import re
import tempfile
import uuid
import zipfile
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.lyric_cache import atomic_bytes, encode_json, read_bytes
from app.screen_layers import clean_layer_text, default_layers, import_logo, load_layers, logo_url, validate_layers

KIND = 'baofang-dj-screen-profile'
MAX_PHRASES, MAX_HISTORY = 20, 10
ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def default_library():
    return {'phrases': [
        {'id': 'welcome', 'label': '欢迎', 'text': '欢迎来到今晚的派对！'},
        {'id': 'birthday', 'label': '生日', 'text': '祝{姓名}生日快乐！'},
        {'id': 'thanks', 'label': '感谢', 'text': '感谢今晚每一位朋友！'},
        {'id': 'encore', 'label': '返场', 'text': '精彩继续，一起唱！'}], 'history': []}


def validate_phrases(rows):
    if not isinstance(rows, list) or len(rows) > MAX_PHRASES:
        raise ValueError('最多保存 20 条常用语')
    result, ids = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'label', 'text'}:
            raise ValueError('常用语记录无效')
        key = row['id']
        if not isinstance(key, str) or not ID.fullmatch(key) or key in ids:
            raise ValueError('常用语编号重复或无效')
        label, text = clean_layer_text(row['label']), clean_layer_text(row['text'])
        if not label or len(label) > 16 or '\n' in label or not text:
            raise ValueError('常用语标题须为 1–16 字，正文不能为空')
        ids.add(key)
        result.append({'id': key, 'label': label, 'text': text})
    return result


def validate_library(value):
    if not isinstance(value, dict) or set(value) != {'phrases', 'history'}:
        raise ValueError('常用语和历史记录无效')
    result = {'phrases': validate_phrases(value['phrases']), 'history': []}
    rows = value['history']
    if not isinstance(rows, list) or len(rows) > MAX_HISTORY:
        raise ValueError('发送历史超出范围')
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'at', 'row', 'seconds', 'source'}:
            raise ValueError('发送历史字段无效')
        key, at = row['id'], row['at']
        if not isinstance(key, str) or not ID.fullmatch(key) or key in ids:
            raise ValueError('发送历史编号无效')
        if not isinstance(at, str) or len(at) > 40 or datetime.fromisoformat(at).tzinfo is None:
            raise ValueError('发送历史时间无效')
        if type(row['seconds']) is not int or not 0 <= row['seconds'] <= 3600 or row['source'] not in ('dj', 'guest'):
            raise ValueError('发送历史来源或时长无效')
        candidate = default_layers()
        candidate['text'] = row['row']
        text = validate_layers(candidate)['text']
        if not text['text']:
            raise ValueError('发送历史文字不能为空')
        text['visible'] = False
        ids.add(key)
        result['history'].append(dict(row, row=text))
    return result


def load_library(runtime):
    try:
        saved = json.loads(read_bytes(Path(runtime) / 'screen-layers.json', limit=200_000))
        if saved.get('schemaVersion') != 2:
            return default_library()
        return validate_library(saved['library'])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return default_library()


def save_profile(runtime, layers, library):
    """One atomic file lets profile application commit layout and phrases together."""
    payload = {'schemaVersion': 2, 'layers': validate_layers(layers), 'library': validate_library(library)}
    atomic_bytes(Path(runtime) / 'screen-layers.json', encode_json(payload))


def save_library(runtime, library, layers=None):
    save_profile(runtime, load_layers(runtime) if layers is None else layers, library)


def with_history(library, row, seconds, source='dj'):
    result = copy.deepcopy(library)
    result['history'].insert(0, {'id': uuid.uuid4().hex, 'at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
                               'row': dict(row, visible=False), 'seconds': seconds, 'source': source})
    result['history'] = result['history'][:MAX_HISTORY]
    return validate_library(result)


def portable_layers(layers):
    result = validate_layers(layers)
    result['text'].update(text='', visible=False)
    result['logo']['visible'] = False
    return result


def export_profile(runtime, layers, phrases, destination):
    layers = portable_layers(layers)
    manifest = {'kind': KIND, 'schemaVersion': 1, 'layers': layers, 'phrases': validate_phrases(phrases)}
    logo = None
    if layers['logo']['image']:
        if not logo_url(runtime, layers):
            raise ValueError('Logo 不可用，请重新选择')
        logo = read_bytes(Path(runtime) / 'screen-assets' / layers['logo']['image'], limit=20_000_000)
        if hashlib.sha256(logo).hexdigest() + '.png' != layers['logo']['image']:
            raise ValueError('Logo 副本已变化，请重新导入')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('profile.json', encode_json(manifest))
        if logo is not None:
            archive.writestr('logo.png', logo)
    atomic_bytes(Path(destination), output.getvalue())


def prepare_profile(runtime, source):
    data = read_bytes(Path(source), limit=24_000_000)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) not in ({'profile.json'}, {'profile.json', 'logo.png'}):
                raise ValueError('配置包包含未知文件或重复记录')
            for info in archive.infolist():
                limit = 100_000 if info.filename == 'profile.json' else 20_000_000
                if info.file_size > limit or info.flag_bits & 1:
                    raise ValueError('配置包文件超限或加密')
            manifest = json.loads(archive.read('profile.json'))
            if not isinstance(manifest, dict) or set(manifest) != {'kind', 'schemaVersion', 'layers', 'phrases'} or manifest['kind'] != KIND or manifest['schemaVersion'] != 1:
                raise ValueError('这不是 DJ 客屏配置包')
            layers = portable_layers(manifest['layers'])
            phrases = validate_phrases(manifest['phrases'])
            if bool(layers['logo']['image']) != ('logo.png' in names):
                raise ValueError('配置包 Logo 缺失或记录不一致')
            if layers['logo']['image']:
                logo = archive.read('logo.png')
                if hashlib.sha256(logo).hexdigest() + '.png' != layers['logo']['image']:
                    raise ValueError('配置包 Logo 校验失败')
                # Decode/validate through the same bounded PNG pipeline; never extract ZIP paths.
                with tempfile.TemporaryDirectory(prefix='dj-profile-') as temp:
                    path = Path(temp) / 'logo.png'
                    path.write_bytes(logo)
                    image, _ = import_logo(runtime, path)
                layers['logo']['image'] = image
            return {'layers': layers, 'phrases': phrases, 'logoImageUrl': logo_url(runtime, layers), 'fileName': Path(source).name}
    except (zipfile.BadZipFile, RuntimeError, zlib.error, EOFError) as exc:
        raise ValueError('配置包损坏或无法读取') from exc
