"""Independent CPU subtitle frames and local FFmpeg audio/video composition."""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.style_catalog import (EFFECTS, LAYOUTS, TEMPLATES, TEXT_LAYOUTS,
                                  find_style, normalize_settings)
from app.karaoke import lyric_frame, slice_cursor, sweep_cursor, traditional_lines
ENGINE_ID = 'baofang-cpu-render/v1'


def _usable_tool(path):
    if not path:
        return None
    source = Path(path).expanduser().resolve()
    from app.runtime_paths import is_packaged_ffmpeg
    # Permit our pinned redistributable FFmpeg, never another app's tools.
    if any(part.endswith('.app') for part in source.parts) and not is_packaged_ffmpeg(source):
        return None
    return str(source) if source.is_file() and os.access(source, os.X_OK) else None


def find_ffmpeg():
    for candidate in (os.environ.get('BAOFANG_FFMPEG'), shutil.which('ffmpeg'),
                      '/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg'):
        tool = _usable_tool(candidate)
        if tool:
            return tool
    try:
        import imageio_ffmpeg
        return _usable_tool(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, RuntimeError, OSError):
        return None


def find_ffprobe():
    for candidate in (shutil.which('ffprobe'), '/opt/homebrew/bin/ffprobe', '/usr/local/bin/ffprobe'):
        tool = _usable_tool(candidate)
        if tool:
            return tool
    return None


def _info_from_ffmpeg(text):
    match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', text)
    if not match:
        raise ValueError('无法读取媒体时长')
    hours, minutes, seconds = map(float, match.groups())
    streams = []
    for line in text.splitlines():
        if 'Stream #' not in line:
            continue
        if 'Audio:' in line:
            streams.append({'codec_type': 'audio'})
        elif 'Video:' in line:
            size = re.search(r'\b(\d{2,5})x(\d{2,5})\b', line)
            if size:
                codec = re.search(r'Video:\s*([^\s,]+)', line)
                pixel = re.search(r'\b((?:yuv|yuva|gbrp|gbrap|rgba|bgra|rgb24|gray)[a-z0-9]*)\b', line)
                streams.append({'codec_type': 'video', 'width': int(size[1]), 'height': int(size[2]),
                                'codec_name': codec[1] if codec else '', 'pix_fmt': pixel[1] if pixel else ''})
    return {'format': {'duration': str(hours * 3600 + minutes * 60 + seconds)}, 'streams': streams}


def probe_media(path, verify_video=False, include_video=False):
    path = Path(path).resolve(strict=True)
    if not verify_video and not include_video:
        if path.suffix.lower() == '.wav':
            try:
                with wave.open(str(path), 'rb') as stream:
                    duration = stream.getnframes() / stream.getframerate()
                return {'format': {'duration': str(duration)}, 'streams': [{'codec_type': 'audio'}]}
            except (wave.Error, EOFError):
                pass
        try:
            from tinytag import TinyTag
            duration = TinyTag.get(str(path)).duration
            if duration and math.isfinite(duration):
                return {'format': {'duration': str(duration)}, 'streams': [{'codec_type': 'audio'}]}
        except (ImportError, OSError, ValueError):
            pass
    ffprobe = find_ffprobe()
    if ffprobe:
        result = subprocess.run([ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                                capture_output=True, check=True, timeout=120)
        information = json.loads(result.stdout)
        if verify_video:
            kinds = {stream.get('codec_type') for stream in information.get('streams', [])}
            if not {'audio', 'video'} <= kinds:
                raise ValueError('生成影片缺少音频或视频轨道')
            decoder = find_ffmpeg()
            if not decoder:
                raise ValueError('完整影片校验需要通用 FFmpeg 解码器')
            decoded = subprocess.run([decoder, '-v', 'error', '-i', str(path), '-map', '0:v:0', '-map', '0:a:0',
                                      '-nostats', '-f', 'null', '-'], capture_output=True, timeout=1800)
            if decoded.returncode:
                raise ValueError('生成影片的音视频解码校验失败')
        return information
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError('没有可用的通用 FFmpeg，请安装 imageio-ffmpeg 或系统 ffmpeg')
    arguments = [ffmpeg, '-hide_banner', '-i', str(path)]
    if verify_video:
        arguments += ['-map', '0:v:0', '-map', '0:a:0', '-nostats', '-f', 'null', '-']
    result = subprocess.run(arguments, capture_output=True, timeout=1800)
    if verify_video and result.returncode:
        raise ValueError('生成影片的音视频解码校验失败')
    return _info_from_ffmpeg(result.stderr.decode('utf-8', errors='replace'))


def _qt():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import __file__ as pyside_file
    plugins = Path(pyside_file).parent / 'Qt/plugins'
    os.environ.setdefault('QT_PLUGIN_PATH', str(plugins))
    os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', str(plugins / 'platforms'))
    from PySide6.QtGui import QGuiApplication
    if sys.platform == 'darwin' and QGuiApplication.instance() is None:
        # The renderer starts later than the UI; repair metadata in this process
        # immediately before Qt scans its platform plugins as well.
        from scripts.repair_qt_metadata import repair_qt_metadata
        repair_qt_metadata()
    return QGuiApplication.instance() or QGuiApplication([])


def _animation(identifier, elapsed, duration, fade_ms=180):
    """Visual animation only; never synthesize semantic character timestamps."""
    find_style(EFFECTS, identifier)
    elapsed = max(0, elapsed)
    entry = 1 if fade_ms <= 0 else min(1, elapsed / max(1, min(fade_ms, duration / 2)))
    opacity, scale, dx, dy, rotation, reveal, glow = 1, 1, 0, 0, 0, 1, 0
    if identifier == 'fade':
        opacity = 1 if fade_ms <= 0 else min(entry, max(0, (duration - elapsed) / max(1, min(fade_ms, duration / 2))))
    elif identifier == 'pulse':
        scale = 1 + .055 * math.sin(elapsed * math.pi / 420)
    elif identifier == 'rise':
        dy, opacity = (1 - entry) ** 2 * .1, entry
    elif identifier == 'drift':
        dx = .045 * math.sin(elapsed / 530)
    elif identifier == 'typewriter':
        reveal = min(1, elapsed / max(1, min(duration * .7, 1600)))
    elif identifier == 'glow':
        glow = .5 + .5 * math.sin(elapsed / 210)
    elif identifier == 'bounce':
        dy = -.065 * abs(math.sin(elapsed / 70)) * math.exp(-elapsed / 350)
        scale = .96 + .04 * entry
    return {'opacity': opacity, 'scale': scale, 'dx': dx, 'dy': dy,
            'rotation': rotation, 'reveal': reveal, 'glow': glow}


def _motion(parameters, elapsed, bpm, overrides=None):
    parameters = dict(parameters, **(overrides or {}))
    motion = parameters['motion']
    phase = elapsed / 1000 * parameters['frequency'] * math.pi * 2 + parameters['phase']
    amplitude = parameters['amplitude']
    dx, dy, scale, rotation = parameters['offsetX'], parameters['offsetY'], parameters['scale'], parameters['rotation']
    if motion == 'orbit':
        dx += amplitude * math.cos(phase); dy += amplitude * math.sin(phase)
    elif motion == 'float':
        dy += amplitude * math.sin(phase)
    elif motion == 'sway':
        dx += amplitude * math.sin(phase); rotation += amplitude * 80 * math.sin(phase)
    elif motion == 'zoom':
        scale *= 1 + amplitude * 2 * math.sin(phase)
    elif motion == 'rotate':
        rotation += amplitude * 180 * math.sin(phase)
    elif motion == 'wave':
        dx += amplitude * math.sin(phase); dy += amplitude * math.sin(phase * 2) / 2
    elif motion == 'step':
        dx += amplitude * math.floor(math.sin(phase) * 3) / 3
    elif motion == 'beat':
        beat = .5 + .5 * math.cos(elapsed / 1000 * bpm / 60 * math.pi * 2 + parameters['phase'])
        scale *= 1 + amplitude * beat * 2
        dy -= amplitude * beat / 2
    return dx, dy, scale, rotation


def _karaoke_edge(text, font, cursor):
    """Measure token boundaries in the complete, final shaped line."""
    from PySide6.QtGui import QTextLayout, QTextOption
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.NoWrap)
    layout.setTextOption(option)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(1e9)
    layout.endLayout()
    first, last, fraction = cursor
    # QTextLine uses UTF-16 cursor offsets; Python indexes Unicode code points.
    first = len(text[:first].encode('utf-16-le')) // 2
    last = len(text[:last].encode('utf-16-le')) // 2
    left, right = line.cursorToX(first)[0], line.cursorToX(last)[0]
    return left + (right - left) * fraction


def _paint_line(painter, text, x, y, size, settings, max_width, color=None, align='center',
                opacity=1, rotation=0, reveal=1, glow=0, highlight_width=None,
                highlight_cursor=None):
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetricsF
    text = text.replace('\n', ' ')
    font = QFont(settings['fontFamily'])
    font.setPixelSize(max(8, round(size)))
    font.setWeight(QFont.Weight.Bold if settings['fontBold'] else QFont.Weight.Normal)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, settings['letterSpacing'] * settings['height'] / 480)
    metrics = QFontMetricsF(font)
    while metrics.horizontalAdvance(text) > max_width and font.pixelSize() > 8:
        font.setPixelSize(font.pixelSize() - 1)
        metrics = QFontMetricsF(font)
    advance = metrics.horizontalAdvance(text)
    local_x = 0 if align == 'left' else -advance if align == 'right' else -advance / 2
    baseline = (metrics.ascent() - metrics.descent()) / 2
    painter.save()
    painter.translate(x, y)
    painter.rotate(rotation)
    # Extremely long single-token / sentence captions may still exceed the
    # available width at the minimum font size. Keep the full shaped line,
    # including its real karaoke clipping, inside the chosen text bounds.
    painter.scale(min(1, max_width / max(1, advance)), 1)
    painter.setOpacity(opacity)
    painter.setFont(font)
    if reveal < 1:
        # Grapheme fallback via prefix measurement; this changes visibility only.
        prefix = text[:max(0, math.ceil(len(text) * reveal))]
        painter.setClipRect(QRectF(local_x - 18, -metrics.height(), metrics.horizontalAdvance(prefix) + 18, metrics.height() * 2))
    stroke = settings['strokeWidth'] * settings['height'] / 480
    if glow:
        painter.setPen(QColor(settings['highlightColor']))
        painter.setOpacity(opacity * glow * .22)
        for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)):
            painter.drawText(QPointF(local_x + dx * max(1, stroke), baseline + dy * max(1, stroke)), text)
        painter.setOpacity(opacity)
    if settings['shadow']:
        classic = settings['templateId'] == 'karaoke'
        unit = settings['height'] / 480
        painter.setPen(QColor(0, 0, 0, 190 if classic else 145))
        painter.drawText(QPointF(local_x + (2 if classic else .8 * unit),
                                baseline + (3 if classic else 1.4 * unit)), text)
    if stroke:
        painter.setPen(QColor(settings['strokeColor']))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-.7, -.7), (.7, .7), (-.7, .7), (.7, -.7)):
            painter.drawText(QPointF(local_x + dx * stroke, baseline + dy * stroke), text)
    painter.setPen(QColor(color or settings['textColor']))
    painter.drawText(QPointF(local_x, baseline), text)
    if highlight_cursor is not None:
        highlight_width = _karaoke_edge(text, font, highlight_cursor) / max(1., advance)
    if highlight_width is not None:
        painter.setClipRect(QRectF(local_x, -metrics.height(), advance * highlight_width, metrics.height() * 2),
                            Qt.ClipOperation.IntersectClip)
        painter.setPen(QColor(settings['highlightColor']))
        painter.drawText(QPointF(local_x, baseline), text)
    painter.restore()


def _wrapped(text, maximum=12):
    if '\n' in text:
        return text.splitlines()
    return [text[index:index + maximum] for index in range(0, len(text), maximum)]


def _title_lines(text, maximum, maximum_lines=3):
    """Prefer existing phrase punctuation when it fits the display block."""
    phrases = [part for part in re.split(r'(?<=[，。！？；,!?;])', text) if part]
    if '\n' not in text and 1 < len(phrases) <= maximum_lines and max(map(len, phrases)) <= maximum * 2:
        return phrases
    return _wrapped(text, maximum)


def _paint_lyric(painter, current, following, settings, position_ms, transparent, waiting=False, line_index=0, *, sweep=None, line_renderer=None):
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QRadialGradient
    width, height = settings['width'], settings['height']
    elapsed, duration = position_ms - current['startMs'], current['endMs'] - current['startMs']
    animation = _animation('none' if waiting else settings['effectId'], elapsed, duration, settings['fadeMs'])
    fade_ms = min(settings['fadeMs'], duration / 2)
    for identifier, clock in ((settings['entranceId'], elapsed), (settings['exitId'], duration - elapsed)):
        if not waiting and identifier != 'none' and clock < fade_ms:
            extra = _animation(identifier, clock, max(1, fade_ms * 2), fade_ms)
            animation['opacity'] *= extra['opacity']
            animation['scale'] *= extra['scale']
            animation['dx'] += extra['dx']
            animation['dy'] += extra['dy']
            animation['reveal'] *= extra['reveal']
            animation['glow'] = max(animation['glow'], extra['glow'])
    dx, dy, scale, rotation = _motion(find_style(LAYOUTS, settings['layoutId'])['parameters'], max(0, elapsed),
                                     settings['bpm'], settings['layoutParameters'])
    xshift = (settings['x'] - .5 + dx + animation['dx']) * width
    yshift = (settings['y'] - .78 + dy + animation['dy']) * height
    size = settings['fontSize'] * height / 480 * scale * animation['scale']
    template = settings['templateId']
    if sweep is None:
        sweep = settings.get('lyricSweep', template in ('karaoke','karaoke-card'))
    active_cursor = sweep_cursor(current, position_ms) if sweep else None
    painter.save()
    painter.setOpacity(animation['opacity'])
    painter.setPen(Qt.PenStyle.NoPen)
    if not transparent and template == 'karaoke-card':
        painter.setBrush(QColor(19, 23, 29, 165))
        painter.drawRoundedRect(QRectF(width*.08+xshift,height*.62+yshift,width*.84,height*.29),
                                height*.025,height*.025)
    elif not transparent and template == 'spotlight':
        center = QPointF(width*.5+xshift,height*.5+yshift)
        gradient = QRadialGradient(center,width*.45)
        gradient.setColorAt(0,QColor(15,19,23,115))
        gradient.setColorAt(1,QColor(15,19,23,0))
        painter.setBrush(gradient)
        painter.fillRect(QRectF(0,0,width,height),gradient)
    painter.restore()
    kwargs = dict(opacity=animation['opacity'], rotation=rotation, reveal=animation['reveal'],
                  glow=animation['glow'])

    def line(text, x, y, relative_size=1, color=None, align='center', max_width=.86, cursor=None, source_start=None, future=False):
        if not future and cursor is None and active_cursor is not None:
            if source_start is not None:
                cursor = slice_cursor(active_cursor,source_start,len(text))
            elif text == current['text']:
                cursor = active_cursor
        def draw(target):
            _paint_line(target, text, x * width + xshift, y * height + yshift, size * relative_size,
                        settings, max_width * width, color, align, highlight_cursor=cursor, **kwargs)
        if line_renderer:
            line_renderer(painter, draw, QRectF(0, y * height + yshift - size * 1.6,
                                              width, size * 3.2), future)
        else:
            draw(painter)

    def block(text, x, y, relative_size=1, align='center', max_width=.82, columns=12,
              maximum_lines=3, color=None, opacity=1, future=False):
        # Display-only wrapping: every character remains, with no invented timing.
        lines = _title_lines(text, max(columns,math.ceil(len(text)/maximum_lines)), maximum_lines)
        bottom = .82 if following and template in ('none','sidecard') else .66 if following and template=='poster' else .94
        available = height * 2 * min(y-.05,bottom-y)
        leading = min(size*relative_size*settings['lineSpacing'],
                      max(8,min(height*.64,available))/max(1,len(lines)))
        offset = 0
        for index,part in enumerate(lines):
            old_opacity=kwargs['opacity'];kwargs['opacity']*=opacity
            line(part,x,y+(index-(len(lines)-1)/2)*leading/height,
                 min(relative_size,leading/max(1,size)/settings['lineSpacing']),color,align,max_width,
                 source_start=current['text'].find(part,offset) if not future else None, future=future)
            offset = current['text'].find(part,offset)+len(part) if not future else offset+len(part)
            kwargs['opacity']=old_opacity

    if template == 'vertical':
        columns = _wrapped(current['text'].replace('\n',''),max(8,math.ceil(len(current['text'])/3)))
        glyph_size = min(size,height*.72/max(1,max(map(len,columns)))/settings['lineSpacing'])
        offset=0
        for column,text in enumerate(columns):
            for index,character in enumerate(text):
                _paint_line(painter,character,width*(.84-column*.065)+xshift,
                    height*.5+yshift+(index-(len(text)-1)/2)*glyph_size*settings['lineSpacing'],
                    glyph_size,settings,width*.07,
                    highlight_cursor=slice_cursor(active_cursor,current['text'].find(character,offset),1) if active_cursor is not None else None,**kwargs)
                offset=current['text'].find(character,offset)+1
    elif template == 'stacked':
        block(current['text'],.5,.48,1.12,columns=4,maximum_lines=3)
    elif template == 'split':
        block(current['text'],.25,.70,.94,max_width=.38,columns=9,maximum_lines=3)
        if following:
            block(following['text'],.75,.70,.8,max_width=.38,columns=9,maximum_lines=3,opacity=.55,future=True)
    elif template in ('sidecard','poster'):
        left=.09
        block(current['text'],left,.71 if template=='sidecard' else .43,
              1 if template=='sidecard' else 1.12,align='left',max_width=.80,
              columns=15 if template=='sidecard' else 9,maximum_lines=3)
        if following:
            line(following['text'],left,.88 if template=='sidecard' else .72,min(.50,height*.055/max(1,size)),align='left',max_width=.76,
                 color='#C1C7C6',future=True)
    elif template == 'karaoke':
        # Stable upper-left / lower-right slots alternate without moving the
        # next line when it becomes active. Both lines keep the same emphasis.
        size = min(size, height * .12)
        gap = min(height * .18, max(size * 1.25, height * .14 * settings['lineSpacing'] / 1.25))
        upper_y, lower_y = .70, .70 + gap / height
        lower = line_index % 2 == 1
        current_y = lower_y if lower else upper_y
        margin = settings.get('karaokeMargin', .07)
        line(current['text'], 1-margin if lower else margin, current_y,
             align='right' if lower else 'left', max_width=1-2*margin,
             cursor=sweep_cursor(current, position_ms) if sweep else None)
        if following:
            line(following['text'], margin if lower else 1-margin, upper_y if lower else lower_y,
                 align='left' if lower else 'right', max_width=1-2*margin,future=True)
    elif template == 'karaoke-card':
        # Fit the two-line block vertically as well as each line horizontally.
        size = min(size, height * .16)
        gap = max(height * .088, size * .95) * settings['lineSpacing'] / 1.15
        current_y = .77 - gap * .31 / height
        line(current['text'], .5, current_y, cursor=sweep_cursor(current, position_ms) if sweep else None)
        if following:
            line(following['text'], .5, current_y + gap / height, .62, '#b9ccdc',future=True)
    elif template == 'ribbon':
        from PySide6.QtGui import QFont,QFontMetricsF
        lines=_title_lines(current['text'],max(13,math.ceil(len(current['text'])/3)))
        font=QFont(settings['fontFamily']);font.setPixelSize(max(8,round(size)))
        font.setWeight(QFont.Weight.Bold if settings['fontBold'] else QFont.Weight.Normal)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,settings['letterSpacing']*height/480)
        metrics=QFontMetricsF(font)
        span=min(width*.80,max(metrics.horizontalAdvance(part) for part in lines))
        leading=min(size*settings['lineSpacing'],height*.34/max(1,len(lines)))
        block_height=leading*(len(lines)-1)+leading/settings['lineSpacing']*1.3+height*.05
        if not transparent:
            painter.save();painter.setOpacity(animation['opacity']);painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#EFEEE8') if QColor(settings['textColor']).lightnessF()<.55 else QColor('#242930'))
            painter.drawRoundedRect(QRectF(width*.5-span/2-height*.035+xshift,
                height*.77-block_height/2+yshift,span+height*.07,block_height),height*.018,height*.018)
            painter.restore()
        block(current['text'],.5,.77,columns=max(13,math.ceil(len(current['text'])/3)),max_width=.80)
    elif template == 'none':
        block(current['text'],.5,.73,columns=18,maximum_lines=2)
        if following:line(following['text'],.5,.88,min(.58,height*.055/max(1,size)),color='#BBC2C4',future=True)
    elif template == 'minimal':
        block(current['text'],.5,.86,columns=22,maximum_lines=2,max_width=.84)
    elif template == 'headline':
        block(current['text'],.5,.48,1.1,columns=10)
    elif template == 'banner':
        block(current['text'],.5,.15,columns=26,maximum_lines=2,max_width=.80)
    elif template == 'neon':
        phrases=_title_lines(current['text'],max(4,math.ceil(len(current['text'])/3)))
        leading=min(size*1.18,height*.62/max(1,len(phrases)))
        offset=0
        for index,part in enumerate(phrases):
            line(part,.46 if index%2==0 else .54,
                 .52+(index-(len(phrases)-1)/2)*leading/height,
                 min(1,leading/max(1,size)/1.18),
                 color=settings['textColor'] if sweep or index%2==0 else settings['highlightColor'],max_width=.76,
                 source_start=current['text'].find(part,offset))
            offset=current['text'].find(part,offset)+len(part)
    elif template == 'stage':
        block(current['text'],.5,.61,columns=16,maximum_lines=2)
        if following:line(following['text'],.5,.84,min(.56,height*.055/max(1,size)),color='#BCC3C4',future=True)
    elif template == 'kinetic':
        block(current['text'],.5,.46,1.04,columns=8,maximum_lines=3)
    elif template == 'spotlight':
        block(current['text'],.5,.50,columns=14)


def _paint_independent_text(painter, row, settings, position_ms):
    elapsed = position_ms - row['startMs']
    duration = row['durationMs']
    animation = _animation(row.get('animationId', 'none'), elapsed, duration, settings['fadeMs'])
    style = dict(settings)
    style.update(fontFamily=row.get('fontFamily', settings['fontFamily']), fontBold=row.get('fontBold', True),
                 textColor=row.get('color', '#ffffff'))
    layout_id = row.get('layoutId', 'standard')
    text_layout = next((layout for layout in TEXT_LAYOUTS if layout['id'] == layout_id), None)
    dx, dy, scale, rotation = 0, 0, 1, 0
    if text_layout:
        parameters = text_layout['parameters']
        x = row.get('x', parameters['x']); y = row.get('y', parameters['y'])
    else:
        parameters = {'vertical': False, 'multiline': False}
        dx, dy, scale, rotation = _motion(find_style(LAYOUTS, layout_id)['parameters'], elapsed,
                                         settings['bpm'], settings['layoutParameters'])
        x, y = row.get('x', .5), row.get('y', .5)
    x = (x + dx + animation['dx']) * settings['width']
    y = (y + dy + animation['dy']) * settings['height']
    size = row.get('fontSize', settings['fontSize']) * settings['height'] / 480 * scale * animation['scale']
    opacity = row.get('opacity', 1) * animation['opacity']
    lines = list(row['text'].replace('\n', '')) if parameters['vertical'] else row['text'].splitlines()
    if parameters['multiline']:
        lines = _wrapped(row['text'], 16)
    for index, text in enumerate(lines):
        _paint_line(painter, text, x, y + (index - (len(lines) - 1) / 2) * size * settings['lineSpacing'],
                    size, style, settings['width'] * .9, opacity=opacity, rotation=rotation,
                    reveal=animation['reveal'], glow=animation['glow'])


def draw_frame(segments, settings, position_ms, background=None, composition=None, transparent=False):
    """Shared CPU preview/export drawing; optional transparent subtitle/text layer.

    CompositionSampler supplies decoded background frames. This routine paints
    lyric templates and independent composition text without spawning processes.
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
    settings = normalize_settings(settings)
    width, height = settings['width'], settings['height']
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    transparent = transparent or settings.get('transparentBackground', False)
    image.fill(Qt.GlobalColor.transparent if transparent else QColor(settings['backgroundColor']))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    if not transparent:
        if background is not None and not background.isNull():
            source = background.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)
            painter.drawImage(round((width - source.width()) / 2), round((height - source.height()) / 2), source)
        else:
            gradient = QLinearGradient(0, 0, width, height)
            gradient.setColorAt(0, QColor(settings['backgroundColor']))
            gradient.setColorAt(.55, QColor('#142c38')); gradient.setColorAt(1, QColor('#342442'))
            painter.fillRect(QRectF(0, 0, width, height), gradient)
    try:
        lyric_rows = traditional_lines(segments) if settings['templateId'] == 'karaoke' else segments
        frame = lyric_frame(lyric_rows, position_ms, pre_display=settings['templateId'] in ('karaoke', 'karaoke-card'))
        if frame:
            _paint_lyric(painter, frame['current'], frame['following'], settings, position_ms,
                         transparent, frame['waiting'], frame['lineIndex'])
        for text in (composition or {}).get('texts', []):
            if text['startMs'] <= position_ms < text['startMs'] + text['durationMs']:
                _paint_independent_text(painter, text, settings, position_ms)
    finally:
        painter.end()
    return image


def _read_frame(stream, size):
    chunks, remaining = [], size
    while remaining:
        data = stream.read(remaining)
        if not data:
            raise ValueError('背景视频解码未返回完整画面')
        chunks.append(data)
        remaining -= len(data)
    return b''.join(chunks)


def _source_snapshot(path):
    import hashlib
    source = Path(path).resolve(strict=True)
    if not source.is_file() or any(part.endswith('.app') for part in source.parts):
        raise ValueError('只支持应用资源目录之外的本地素材文件')
    before = source.stat()
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino):
        raise ValueError('读取时源文件发生变化')
    return {'path': str(source), 'size': after.st_size, 'mtimeNs': after.st_mtime_ns,
            'device': after.st_dev, 'inode': after.st_ino, 'sha256': digest.hexdigest()}


def _atempo(rate):
    factors = []
    while rate < .5:
        factors.append(.5); rate /= .5
    while rate > 2:
        factors.append(2); rate /= 2
    factors.append(rate)
    return ','.join(f'atempo={factor:.9f}' for factor in factors)


def _audio_arguments(audio, clips, sources, duration):
    arguments = ['-i', str(audio)]
    chains = [f'[1:a:0]atrim=duration={duration:.9f},asetpts=PTS-STARTPTS,apad,atrim=duration={duration:.9f}[main]']
    outputs = ['[main]']
    for index, clip in enumerate(clips):
        source = sources[clip['sourceId']]['identity']['path']
        rate = float(clip.get('rate', 1))
        length = clip['durationMs'] / 1000
        source_in = clip.get('sourceInMs', 0) / 1000
        arguments += ['-ss', f'{source_in:.9f}', '-t', f'{length * rate:.9f}', '-i', source]
        input_index = index + 2
        delay = round(clip['startMs'] * 48)
        chains.append(f'[{input_index}:a:0]atrim=duration={length * rate:.9f},asetpts=PTS-STARTPTS,'
                      f'aresample=48000,{_atempo(rate)},asetpts=N/SR/TB,atrim=duration={length:.9f},'
                      f'volume={clip.get("gain", 1):.9f},adelay={delay}S:all=1,asetpts=N/SR/TB,'
                      f'apad,atrim=duration={duration:.9f}[extra{index}]')
        outputs.append(f'[extra{index}]')
    chains.append(''.join(outputs) + f'amix=inputs={len(outputs)}:duration=longest:normalize=0:dropout_transition=0,'
                  f'alimiter=limit=0.95:level=false,atrim=duration={duration:.9f}[mixed]')
    return arguments, ';'.join(chains)


def render(request):
    application = _qt()
    from PySide6.QtGui import QImage
    from worker.composition_render import CompositionSampler, check_source_identity, validate_composition
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError('没有可用的通用 FFmpeg，请安装 imageio-ffmpeg 或系统 ffmpeg')
    if request.get('engineId') != ENGINE_ID:
        raise ValueError('制作请求引擎版本不兼容')
    output = Path(request['output']).resolve()
    if output.exists():
        raise ValueError('导出路径必须是新文件，不能覆盖源文件')
    settings = normalize_settings(request['settings'])
    width, height, fps = settings['width'], settings['height'], float(settings['fps'])
    duration_ms = request['durationMs']
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or not math.isfinite(duration_ms) or duration_ms <= 0 or width % 2 or height % 2:
        raise ValueError('影片时长或输出尺寸无效')
    duration = duration_ms / 1000
    output_format = request.get('outputFormat', settings.get('outputFormat', 'mp4'))
    if output_format not in ('mp4', 'mov-alpha'):
        raise ValueError('不支持的影片输出格式')
    alpha = output_format == 'mov-alpha'
    audio_path = request.get('audioPath', '')
    if not alpha and not audio_path:
        raise ValueError('完整影片必须有主音频')
    audio = Path(audio_path).resolve(strict=True) if audio_path else None
    composition = validate_composition(request.get('composition'), duration_ms)
    if composition.get('fps', fps) != fps:
        raise ValueError('组合时间线帧率与渲染设置不一致')
    snapshots = {}
    for source in composition['sources'].values():
        snapshots[source['identity']['path']] = source['identity']
    background_path = request.get('backgroundPath', '')
    for source_path in (audio_path, background_path):
        if source_path:
            snapshot = _source_snapshot(source_path)
            snapshots[snapshot['path']] = snapshot
    if str(output) in snapshots:
        raise ValueError('导出路径不能覆盖任何源文件')
    # A parent may preserve the import-time identities in its request.
    for snapshot in request.get('sourceIdentities', []):
        check_source_identity(snapshot)
        snapshots[snapshot['path']] = snapshot
    segments = request.get('segments', [])
    previous_end = 0
    for row in segments:
        if not isinstance(row.get('text'), str) or not row['text'].strip() or not 0 <= row['startMs'] < row['endMs'] <= duration_ms or row['startMs'] < previous_end:
            raise ValueError('渲染字幕时码或文本无效')
        previous_end = row['endMs']
    frames = math.ceil(duration * fps)
    sample_row = max(segments, key=lambda row: row['endMs'] - row['startMs'], default=None)
    sample_ms = (sample_row['startMs'] + sample_row['endMs']) / 2 if sample_row else duration_ms / 2
    sample_index = max(0, min(frames - 1, round(sample_ms * fps / 1000)))
    background, decoder, sampler = None, None, None
    encoder_log = (output.parent / 'ffmpeg-encode.log').open('wb')
    decoder_log = (output.parent / 'ffmpeg-background.log').open('wb')
    encoder = None
    try:
        if composition['clips']:
            sampler = CompositionSampler(composition, width, height, fps,duration_ms=duration_ms)
        elif composition['sources']:
            # Validate declared media even when an audio/text-only project has no picture clips.
            sampler = CompositionSampler(composition, width, height, fps,duration_ms=duration_ms)
        if background_path and not alpha:
            source = Path(background_path).resolve(strict=True)
            if source.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                background = QImage(str(source))
                if background.isNull():
                    raise ValueError('背景图片无法解码')
            else:
                decoder = subprocess.Popen([ffmpeg, '-hide_banner', '-loglevel', 'error', '-stream_loop', '-1',
                    '-i', str(source), '-an', '-vf',
                    f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}',
                    '-r', str(fps), '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1'],
                    stdout=subprocess.PIPE, stderr=decoder_log)
        arguments = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'rgba',
                     '-s', f'{width}x{height}', '-r', str(fps), '-i', 'pipe:0']
        if alpha:
            arguments += ['-map', '0:v:0', '-an', '-c:v', 'prores_ks', '-profile:v', '4',
                          '-pix_fmt', 'yuva444p10le', '-alpha_bits', '16', '-vendor', 'apl0',
                          '-t', str(duration), '-f', 'mov', str(output)]
        else:
            audio_arguments, mix = _audio_arguments(audio, composition['audioClips'], composition['sources'], duration)
            arguments += audio_arguments + ['-filter_complex', mix, '-map', '0:v:0', '-map', '[mixed]',
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k', '-t', str(duration), '-movflags', '+faststart', '-f', 'mp4', str(output)]
        encoder = subprocess.Popen(arguments, stdin=subprocess.PIPE, stderr=encoder_log)
        for index in range(frames):
            position_ms = index * 1000 / fps
            frame_background = background
            if decoder:
                data = _read_frame(decoder.stdout, width * height * 4)
                frame_background = QImage(data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
            if sampler and composition['clips'] and not alpha:
                sampled = sampler.frame(position_ms)
                if any(clip['startMs'] <= position_ms < clip['startMs'] + clip['durationMs'] for clip in composition['clips']):
                    frame_background = sampled
            frame = draw_frame(segments, settings, position_ms, frame_background, composition, transparent=alpha)
            encoder.stdin.write(frame.bits().tobytes())
            if index == sample_index:
                frame.save(str(output.parent / 'preview-frame.png'))
            if index % max(1, round(fps / 2)) == 0:
                print(json.dumps({'type': 'progress', 'value': index / frames,
                                  'message': f'CPU 组合渲染 {index + 1}/{frames} 帧'}, ensure_ascii=False), flush=True)
        encoder.stdin.close()
        if encoder.wait(timeout=120):
            raise ValueError('FFmpeg 合成失败，请查看当前任务的编码日志')
        if not output.is_file() or output.stat().st_size <= 32:
            raise ValueError('影片导出没有生成有效文件')
        if alpha:
            info = probe_media(output, include_video=True)
            videos = [stream for stream in info['streams'] if stream.get('codec_type') == 'video']
            if not videos or videos[0].get('codec_name') != 'prores' or 'a' not in videos[0].get('pix_fmt', ''):
                raise ValueError('透明影片没有有效 ProRes alpha 通道')
            result = subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-map', '0:v:0', '-f', 'null', '-'],
                                    capture_output=True, timeout=1800)
            if result.returncode:
                raise ValueError('透明影片解码校验失败')
        else:
            probe_media(output, verify_video=True)
        for snapshot in snapshots.values():
            check_source_identity(snapshot)
        print(json.dumps({'type': 'done', 'output': str(output), 'engineId': ENGINE_ID,
                          'outputFormat': output_format, 'frames': frames}, ensure_ascii=False), flush=True)
    except BrokenPipeError as error:
        raise ValueError('FFmpeg 提前停止，影片未完成') from error
    finally:
        from worker.composition_render import _stop
        if sampler:
            sampler.close()
        for process in (encoder, decoder):
            _stop(process)
        if encoder and encoder.stdin and not encoder.stdin.closed:
            try:
                encoder.stdin.close()
            except OSError:
                pass
        encoder_log.close()
        decoder_log.close()
    return application


def main():
    parser = argparse.ArgumentParser(description='包房战神独立 CPU 制作引擎')
    parser.add_argument('command', choices=('probe', 'render'))
    parser.add_argument('--input')
    parser.add_argument('--request')
    parser.add_argument('--video', action='store_true')
    parser.add_argument('--include-video', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'probe':
            print(json.dumps(probe_media(args.input, verify_video=args.video,
                                         include_video=args.include_video)), flush=True)
        else:
            request_path = Path(args.request).resolve(strict=True)
            request = json.loads(request_path.read_text())
            if Path(request['output']).resolve().parent != request_path.parent:
                raise ValueError('输出必须位于当前私有任务目录')
            render(request)
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'type': 'error', 'message': str(error)}, ensure_ascii=False), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
