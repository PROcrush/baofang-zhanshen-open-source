"""Local text and DJ logo layers, independent from lyric routing."""
import copy
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path

from app.lyric_cache import atomic_bytes, read_bytes, plain_directory

POSITIONS = ('top-left', 'top-center', 'top-right', 'center-left', 'center', 'center-right',
             'bottom-left', 'bottom-center', 'bottom-right')
MAX_TEXT = 300
ASSET = re.compile(r'^[0-9a-f]{64}\.png$')
STYLES = {'text': ('classic', 'midnight', 'champagne', 'record'),
          'logo': ('plain', 'matte', 'foil')}


def default_layers():
    return {'text': {'text': '', 'visible': False, 'position': 'center', 'fontSize': 48,
                     'opacity': 1.0, 'color': '#FFFFFF', 'style': 'classic'},
            'logo': {'image': '', 'fileName': '', 'visible': False, 'position': 'top-left',
                     'size': 18, 'opacity': 1.0, 'style': 'plain'}}


def clean_layer_text(value):
    if not isinstance(value, str):
        raise ValueError('请输入客屏文字')
    value = value.replace('\r\n', '\n').replace('\r', '\n').replace('\t', ' ')
    value = ''.join(c for c in value if c == '\n' or unicodedata.category(c)[0] != 'C').strip()
    if len(value) > MAX_TEXT:
        raise ValueError('客屏文字最多 300 字')
    return value


def validate_layers(value):
    if not isinstance(value, dict) or set(value) != {'text', 'logo'}:
        raise ValueError('客屏图层设置无效')
    result = copy.deepcopy(value)
    defaults = default_layers()
    for name, row in result.items():
        if not isinstance(row, dict) or set(row) | {'style'} != set(defaults[name]):
            raise ValueError('客屏图层字段无效')
        row.setdefault('style', defaults[name]['style'])
        if row['style'] not in STYLES[name]:
            raise ValueError('客屏图层模板无效')
        if type(row['visible']) is not bool or row['position'] not in POSITIONS:
            raise ValueError('客屏图层位置或显示状态无效')
        ranges = (('opacity', .1, 1), ('fontSize', 24, 120)) if name == 'text' else (('opacity', .1, 1), ('size', 5, 40))
        for key, low, high in ranges:
            number = row[key]
            if type(number) not in (int, float) or not low <= number <= high or not math.isfinite(number):
                raise ValueError('客屏图层大小或透明度超出范围')
        if name == 'text':
            row['text'] = clean_layer_text(row['text'])
            if row['visible'] and not row['text']:
                raise ValueError('先输入文字，再上屏')
            if not isinstance(row['color'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', row['color']):
                raise ValueError('文字颜色请填写 #RRGGBB')
            row['color'] = row['color'].upper()
        else:
            if not isinstance(row['image'], str) or (row['image'] and not ASSET.fullmatch(row['image'])):
                raise ValueError('DJ Logo 图片记录无效')
            if not isinstance(row['fileName'], str) or len(row['fileName']) > 255:
                raise ValueError('DJ Logo 文件名称无效')
            if row['visible'] and not row['image']:
                raise ValueError('先导入 DJ Logo，再显示')
    return result


def load_layers(runtime):
    try:
        saved = json.loads(read_bytes(Path(runtime) / 'screen-layers.json', limit=200_000))
        if not isinstance(saved, dict) or saved.get('schemaVersion') not in (1, 2):
            raise ValueError('客屏图层版本无效')
        return validate_layers(saved['layers'])
    except (OSError, ValueError, TypeError, KeyError):
        return default_layers()


def save_layers(runtime, value):
    from app.screen_library import load_library, save_profile
    save_profile(runtime, value, load_library(runtime))


def logo_url(runtime, value):
    name = value['logo']['image']
    if not name or not ASSET.fullmatch(name):
        return ''
    folder = Path(runtime) / 'screen-assets'
    path = folder / name
    if folder.is_symlink() or path.is_symlink() or not path.is_file():
        return ''
    return path.resolve().as_uri()


def import_logo(runtime, source):
    """Keep an owned PNG copy; moving the user's original cannot break output."""
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImageReader
    source = Path(source)
    data = read_bytes(source, limit=20_000_000)
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    size = reader.size()
    if (bytes(reader.format()).lower() not in (b'png', b'jpeg', b'jpg', b'webp') or not size.isValid()
            or max(size.width(), size.height()) > 8192 or size.width() * size.height() > 16_000_000):
        raise ValueError('请选择 PNG、JPG 或 WebP 图片，最多 1600 万像素、20MB')
    image = reader.read()
    if image.isNull():
        raise ValueError('DJ Logo 图片无法读取，请换一张图片')
    output = QByteArray()
    target = QBuffer(output)
    target.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(target, 'PNG'):
        raise ValueError('DJ Logo 图片未能保存')
    encoded = bytes(output)
    folder = plain_directory(Path(runtime) / 'screen-assets', create=True)
    name = hashlib.sha256(encoded).hexdigest() + '.png'
    atomic_bytes(folder / name, encoded)
    return name, source.name[:255]
