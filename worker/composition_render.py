"""Bounded, seekable local media sampler and original transition algorithms."""

import hashlib
import math
import os
import re
import subprocess
from collections import OrderedDict
from pathlib import Path

from worker.style_catalog import EFFECTS, LAYOUTS, TEXT_LAYOUTS, TRANSITIONS, find_style


def _number(value, label, minimum=0, maximum=float('inf')):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{label} 超出有效范围')
    return float(value)


def check_source_identity(identity):
    """Verify canonical source identity without importing an app controller."""
    required = {'path', 'size', 'mtimeNs', 'device', 'inode', 'sha256'}
    if not isinstance(identity, dict) or set(identity) != required:
        raise ValueError('素材源身份字段无效')
    if not isinstance(identity['path'], str):
        raise ValueError('素材路径无效')
    source = Path(identity['path']).expanduser()
    resolved = source.resolve(strict=True)
    if not source.is_absolute() or str(resolved) != identity['path'] or not resolved.is_file():
        raise ValueError('素材必须引用完整、规范的本地文件路径')
    if any(part.endswith('.app') for part in resolved.parts):
        raise ValueError('制作素材不能来自应用程序资源目录')
    if not isinstance(identity['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', identity['sha256']):
        raise ValueError('素材摘要无效')
    for key in ('size', 'mtimeNs', 'device', 'inode'):
        if type(identity[key]) is not int or identity[key] < 0:
            raise ValueError('素材身份数值无效')
    before = resolved.stat()
    expected = (identity['size'], identity['mtimeNs'], identity['device'], identity['inode'])
    actual = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    if actual != expected:
        raise ValueError('素材源文件已变化')
    digest = hashlib.sha256()
    with resolved.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = resolved.stat()
    if (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino) != actual or digest.hexdigest() != identity['sha256']:
        raise ValueError('素材源内容已变化')
    return resolved


def validate_composition(value, duration_ms=None, verify_sources=True):
    """Reuse our pure editing contract, then validate all executable style IDs."""
    from app.composition import empty_composition, validate_composition as validate_model
    if value is None:
        value = empty_composition()
    if duration_ms is None:
        if not isinstance(value, dict):
            raise ValueError('组合时间线无效')
        endpoints = [1]
        for key in ('clips', 'texts', 'audioClips'):
            if not isinstance(value.get(key), list):
                raise ValueError('组合时间线列表无效')
            for row in value[key]:
                if not isinstance(row, dict):
                    raise ValueError('时间线条目格式无效')
                endpoints.append(_number(row.get('startMs'), '片段起点') + _number(row.get('durationMs'), '片段长度', .001))
        for marker in value.get('markers', []):
            endpoints.append(_number(marker.get('positionMs'), '标记时间'))
        duration_ms = max(endpoints)
    clean = validate_model(value, duration_ms,
                           identity_validator=check_source_identity if verify_sources else None)
    for clip in clean['clips']:
        find_style(TRANSITIONS, clip['transition']['id'])
    layout_ids = {row['id'] for row in LAYOUTS + TEXT_LAYOUTS}
    for text in clean['texts']:
        if text['layoutId'] not in layout_ids:
            raise ValueError('文字布局不存在')
        find_style(EFFECTS, text['animationId'])
    return clean


def _stop(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    for stream in (process.stdout, process.stderr):
        if stream:
            stream.close()


def _fit_filter(width, height, fit):
    if fit == 'contain':
        return f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black'
    return f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}'


class _VideoReader:
    def __init__(self, path, width, height, fit, step_ms):
        self.path, self.width, self.height = path, width, height
        self.fit, self.step_ms = fit, step_ms
        self.process, self.last_ms = None, None

    def frame(self, position_ms):
        from PySide6.QtGui import QImage
        from worker.studio_render import find_ffmpeg, _read_frame
        sequential = self.process is not None and self.process.poll() is None and self.last_ms is not None and abs(position_ms - self.last_ms - self.step_ms) < .15
        if not sequential:
            self.close()
            executable = find_ffmpeg()
            if not executable:
                raise ValueError('没有可用的通用 FFmpeg')
            self.process = subprocess.Popen([executable, '-hide_banner', '-loglevel', 'error',
                '-ss', f'{position_ms / 1000:.9f}', '-i', str(self.path), '-an', '-sn', '-dn',
                '-vf', _fit_filter(self.width, self.height, self.fit) + f',fps={1000 / self.step_ms:.9f}',
                '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            data = _read_frame(self.process.stdout, self.width * self.height * 4)
        except ValueError:
            self.close()
            raise ValueError('视频源截取位置没有可解码画面')
        self.last_ms = position_ms
        return QImage(data, self.width, self.height, self.width * 4, QImage.Format.Format_RGBA8888).copy()

    def close(self):
        _stop(self.process)
        self.process, self.last_ms = None, None


def apply_transition(previous, incoming, transition_id, progress):
    """Draw thirteen transition algorithms, each with four real directions."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPolygonF
    transition = find_style(TRANSITIONS, transition_id)
    progress = max(0, min(1, float(progress)))
    if transition_id == 'cut' or progress >= 1:
        return incoming.copy()
    if progress <= 0:
        return previous.copy()
    width, height = incoming.width(), incoming.height()
    result = previous.copy().convertToFormat(QImage.Format.Format_RGBA8888)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    algorithm, direction = transition['algorithm'], transition['direction']
    horizontal = direction in ('left', 'right')
    reverse = direction in ('right', 'down')
    sign = 1 if reverse else -1
    if algorithm in ('slide', 'push', 'zoom'):
        if algorithm == 'zoom':
            scale = .08 + .92 * progress
            anchors = {'left': (0, .5), 'right': (1, .5), 'up': (.5, 0), 'down': (.5, 1)}
            x, y = anchors[direction]
            painter.setOpacity(progress)
            painter.drawImage(QRectF(width * x * (1 - scale), height * y * (1 - scale), width * scale, height * scale), incoming)
        else:
            dx, dy = (sign * width * (1 - progress), 0) if horizontal else (0, sign * height * (1 - progress))
            if algorithm == 'push':
                painter.fillRect(QRectF(0, 0, width, height), QColor('#000000'))
                painter.drawImage(QPointF(dx - sign * width if horizontal else 0, dy - sign * height if not horizontal else 0), previous)
            painter.drawImage(QPointF(dx, dy), incoming)
        painter.end()
        return result
    mask = QImage(width, height, QImage.Format.Format_RGBA8888)
    mask.fill(Qt.GlobalColor.transparent)
    m = QPainter(mask)
    m.setRenderHint(QPainter.RenderHint.Antialiasing)
    m.setPen(Qt.PenStyle.NoPen)
    m.setBrush(QColor('#ffffff'))
    if algorithm == 'fade':
        gradient = QLinearGradient(width if reverse and horizontal else 0, height if reverse and not horizontal else 0,
                                   0 if reverse and horizontal else width if horizontal else 0,
                                   0 if reverse and not horizontal else height if not horizontal else 0)
        edge = progress * 1.36 - .18
        for stop in (0, .25, .5, .75, 1):
            alpha = max(0, min(1, (edge - stop + .36) / .36))
            gradient.setColorAt(stop, QColor(255, 255, 255, round(alpha * 255)))
        m.fillRect(QRectF(0, 0, width, height), gradient)
    elif algorithm in ('wipe', 'pixelate'):
        extent = (width if horizontal else height) * progress
        rect = QRectF(width - extent if reverse and horizontal else 0,
                      height - extent if reverse and not horizontal else 0,
                      extent if horizontal else width, height if horizontal else extent)
        m.drawRect(rect)
    elif algorithm == 'iris':
        centers = {'left': (0, .5), 'right': (1, .5), 'up': (.5, 0), 'down': (.5, 1)}
        cx, cy = centers[direction]
        radius = math.hypot(width, height) * progress
        m.drawEllipse(QPointF(width * cx, height * cy), radius, radius)
    elif algorithm == 'blinds':
        for index in range(12):
            if horizontal:
                strip = width / 12
                m.drawRect(QRectF(index * strip + (strip * (1 - progress) if reverse else 0), 0, strip * progress + .2, height))
            else:
                strip = height / 12
                m.drawRect(QRectF(0, index * strip + (strip * (1 - progress) if reverse else 0), width, strip * progress + .2))
    elif algorithm in ('checker', 'dissolve'):
        columns, rows = 12, 8
        for y in range(rows):
            for x in range(columns):
                index = (columns - 1 - x if reverse else x) if horizontal else (rows - 1 - y if reverse else y)
                extent = columns if horizontal else rows
                threshold = ((index + ((x + y) % 2) * .48) / extent if algorithm == 'checker'
                             else (((x * 73 + y * 37 + (13 if horizontal else 53) + (29 if reverse else 0)) % 97) / 97))
                amount = max(0, min(1, (progress - threshold * .82) / .18))
                if amount:
                    cell_width, cell_height = width / columns, height / rows
                    m.drawRect(QRectF((x + (1 - amount) / 2) * cell_width,
                                      (y + (1 - amount) / 2) * cell_height,
                                      cell_width * amount + .3, cell_height * amount + .3))
    elif algorithm == 'split':
        if horizontal:
            extent = width * progress / 2
            if reverse:
                m.drawRect(QRectF(0, 0, extent, height)); m.drawRect(QRectF(width - extent, 0, extent, height))
            else:
                m.drawRect(QRectF(width / 2 - extent, 0, extent * 2, height))
        else:
            extent = height * progress / 2
            if reverse:
                m.drawRect(QRectF(0, 0, width, extent)); m.drawRect(QRectF(0, height - extent, width, extent))
            else:
                m.drawRect(QRectF(0, height / 2 - extent, width, extent * 2))
    elif algorithm == 'diagonal':
        edge = progress * 2
        points = [QPointF(0, 0), QPointF(width * edge, 0), QPointF(0, height * edge)]
        if edge > 1:
            points = [QPointF(0, 0), QPointF(width, 0), QPointF(width, height * (edge - 1)),
                      QPointF(width * (edge - 1), height), QPointF(0, height)]
        transforms = {'left': lambda p: p, 'right': lambda p: QPointF(width - p.x(), height - p.y()),
                      'up': lambda p: QPointF(p.x(), height - p.y()), 'down': lambda p: QPointF(width - p.x(), p.y())}
        m.drawPolygon(QPolygonF([transforms[direction](point) for point in points]))
    elif algorithm == 'clock':
        diameter = math.hypot(width, height) * 2
        start = {'left': 180, 'right': 0, 'up': 90, 'down': 270}[direction]
        m.drawPie(QRectF((width - diameter) / 2, (height - diameter) / 2, diameter, diameter),
                  start * 16, round(progress * 360 * 16))
    m.end()
    layer = incoming.copy()
    if algorithm == 'pixelate':
        pixels = max(2, round(2 + progress * 52))
        layer = layer.scaled(pixels, max(2, round(pixels * height / width)), Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.FastTransformation).scaled(width, height)
    composition = QPainter(layer)
    composition.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    composition.drawImage(0, 0, mask)
    composition.end()
    painter.drawImage(0, 0, layer)
    painter.end()
    return result


class CompositionSampler:
    """Seekable sampler with bounded frame/image/decoder caches.

    At a contiguous boundary the incoming transition blends the preceding tail
    (within its own source window) with the beginning of the new clip.
    """
    FRAME_CACHE_LIMIT = 12
    FRAME_CACHE_BYTES = 64 * 1024 * 1024
    IMAGE_CACHE_LIMIT = 4
    DECODER_LIMIT = 2

    def __init__(self, composition, width, height, fps=24, duration_ms=None):
        self.composition = validate_composition(composition,duration_ms=duration_ms)
        self.width, self.height = int(width), int(height)
        self.fps = _number(self.composition.get('fps', fps), '预览帧率', 1, 60)
        if not 16 <= self.width <= 4096 or not 16 <= self.height <= 4096:
            raise ValueError('预览尺寸无效')
        self.frame_cache_limit = max(1, min(self.FRAME_CACHE_LIMIT,
                                           self.FRAME_CACHE_BYTES // (self.width * self.height * 4)))
        self.cache, self.images, self.readers = OrderedDict(), OrderedDict(), OrderedDict()
        self.closed = False
        self._verify_media()

    def _verify_media(self):
        from PySide6.QtGui import QImageReader
        from worker.studio_render import probe_media
        for source in self.composition['sources'].values():
            path = source['identity']['path']
            if source['kind'] == 'image':
                reader = QImageReader(path)
                size = reader.size()
                if not reader.canRead() or size.width() != source['width'] or size.height() != source['height']:
                    raise ValueError('图片素材实际尺寸与源记录不符')
            else:
                info = probe_media(path, include_video=source['kind'] == 'video')
                kind = source['kind']
                matching = [row for row in info['streams'] if row.get('codec_type') == kind]
                if not matching:
                    raise ValueError('素材源没有声明类型的媒体轨道')
                duration = float(info['format']['duration']) * 1000
                if abs(duration - source['durationMs']) > max(100, duration * .002):
                    raise ValueError('素材源实际时长与源记录不符')
                if kind == 'video' and (matching[0]['width'], matching[0]['height']) != (source['width'], source['height']):
                    raise ValueError('视频源实际尺寸与源记录不符')
                for clip in self.composition['clips'] + self.composition['audioClips']:
                    if clip.get('sourceId') in self.composition['sources'] and self.composition['sources'][clip['sourceId']] is source:
                        if clip.get('sourceInMs', 0) + clip['durationMs'] * clip.get('rate', 1) > duration + .01:
                            raise ValueError('素材截取范围超出实际媒体时长')
                        if clip in self.composition['audioClips'] and not any(stream.get('codec_type') == 'audio' for stream in info['streams']):
                            raise ValueError('附加音轨源文件没有音频轨道')

    def _cache_put(self, cache, key, value, limit):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            _, old = cache.popitem(last=False)
            if hasattr(old, 'close'):
                old.close()

    def _clip_frame(self, clip, timeline_ms):
        from PySide6.QtCore import QSize, Qt
        from PySide6.QtGui import QColor, QImage, QImageReader, QPainter
        if clip['kind'] == 'color':
            image = QImage(self.width, self.height, QImage.Format.Format_RGBA8888)
            image.fill(QColor(clip['color']))
            return image
        source = self.composition['sources'][clip['sourceId']]
        fit = clip.get('fit', 'cover')
        source_ms = clip.get('sourceInMs', 0) + (timeline_ms - clip['startMs']) * clip.get('rate', 1)
        key = (clip['sourceId'], fit, round(source_ms, 3) if clip['kind'] == 'video' else 0)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key].copy()
        if clip['kind'] == 'image':
            mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding if fit == 'cover' else Qt.AspectRatioMode.KeepAspectRatio
            image_key = (clip['sourceId'], fit)
            if image_key not in self.images:
                reader = QImageReader(source['identity']['path'])
                reader.setScaledSize(reader.size().scaled(QSize(self.width, self.height), mode))
                original = reader.read()
                if original.isNull():
                    raise ValueError('图片素材无法解码：' + reader.errorString())
                fitted = original.scaled(self.width, self.height, mode, Qt.TransformationMode.SmoothTransformation)
                image = QImage(self.width, self.height, QImage.Format.Format_RGBA8888)
                image.fill(QColor('#000000'))
                painter = QPainter(image)
                painter.drawImage(round((self.width - fitted.width()) / 2), round((self.height - fitted.height()) / 2), fitted)
                painter.end()
                self._cache_put(self.images, image_key, image, self.IMAGE_CACHE_LIMIT)
            self.images.move_to_end(image_key)
            image = self.images[image_key].copy()
        else:
            if not 0 <= source_ms < source['durationMs'] + .001:
                raise ValueError('视频取帧时间超出源范围')
            reader_key = (clip['sourceId'], fit, clip.get('rate', 1))
            if reader_key not in self.readers:
                self._cache_put(self.readers, reader_key,
                    _VideoReader(source['identity']['path'], self.width, self.height, fit,
                                 clip.get('rate', 1) * 1000 / self.fps), self.DECODER_LIMIT)
            self.readers.move_to_end(reader_key)
            image = self.readers[reader_key].frame(source_ms)
        self._cache_put(self.cache, key, image.copy(), self.frame_cache_limit)
        return image

    def frame(self, position_ms):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        if self.closed:
            raise ValueError('素材采样器已经关闭')
        position_ms = _number(position_ms, '预览时间')
        for index, clip in enumerate(self.composition['clips']):
            if clip['startMs'] <= position_ms < clip['startMs'] + clip['durationMs']:
                image = self._clip_frame(clip, position_ms)
                transition = clip.get('transition', {'id': 'cut', 'durationMs': 0})
                elapsed = position_ms - clip['startMs']
                if index and transition['id'] != 'cut' and elapsed < transition['durationMs']:
                    previous = self.composition['clips'][index - 1]
                    previous_ms = previous['startMs'] + previous['durationMs'] - transition['durationMs'] + elapsed
                    old = self._clip_frame(previous, previous_ms)
                    return apply_transition(old, image, transition['id'], elapsed / transition['durationMs'])
                return image
        image = QImage(self.width, self.height, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.transparent)
        return image

    def close(self):
        for reader in self.readers.values():
            reader.close()
        self.readers.clear()
        self.images.clear()
        self.cache.clear()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
