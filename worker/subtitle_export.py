"""SRT/LRC serialization of validated source-media millisecond timelines."""
import os
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from app.storage import validate_timeline


def _checked(timeline):
    if not isinstance(timeline, dict) or not isinstance(timeline.get('versionId'), str) or not timeline['versionId']:
        raise ValueError('导出需要已绑定版本的歌词时间轴')
    try:
        validate_timeline(timeline, timeline['versionId'])
    except (TypeError, KeyError, OverflowError) as exc:
        raise ValueError('导出时间轴格式无效') from exc
    rows = []
    previous_end = 0
    for segment in timeline['segments']:
        # Decimal from the textual millisecond value avoids binary float's
        # ties-to-even behavior. A positive interval must survive rounding.
        start = int(Decimal(str(segment['startMs'])).to_integral_value(rounding=ROUND_HALF_UP))
        end = int(Decimal(str(segment['endMs'])).to_integral_value(rounding=ROUND_HALF_UP))
        if start < previous_end or end <= start:
            raise ValueError('毫秒舍入后字幕区间重叠或长度为零')
        text = segment['text']
        if any(ord(char) < 32 and char not in ('\n', '\r', '\t') for char in text):
            raise ValueError('字幕文本包含不支持的控制字符')
        lines = [line for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n') if line.strip()]
        rows.append((start, end, '\n'.join(lines).strip()))
        previous_end = end
    return rows


def _srt_time(milliseconds):
    seconds, fraction = divmod(milliseconds, 1000)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f'{hour:02}:{minute:02}:{second:02},{fraction:03}'


def _lrc_time(milliseconds):
    seconds, fraction = divmod(milliseconds, 1000)
    minute, second = divmod(seconds, 60)
    return f'[{minute:02}:{second:02}.{fraction:03}]'


def export_srt(timeline):
    """Return SRT text, rounding milliseconds half up and preserving gaps."""
    rows = _checked(timeline)
    blocks = [f'{number}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}'
              for number, (start, end, text) in enumerate(rows, 1)]
    return '\n\n'.join(blocks) + '\n'


def export_lrc(timeline):
    """Return millisecond LRC; blank end markers represent intentional gaps.

    LRC has no formal duration field. Some players ignore blank end markers;
    use SRT when exact lyric disappearance in silent gaps is required.
    """
    rows = _checked(timeline)
    lines = []
    for index, (start, end, text) in enumerate(rows):
        lines.append(_lrc_time(start) + ' '.join(text.splitlines()))
        if index + 1 == len(rows) or end < rows[index + 1][0]:
            lines.append(_lrc_time(end))
    return '\n'.join(lines) + '\n'


def write_subtitles(timeline, path, format='srt'):
    """Atomically write UTF-8 output and return its absolute Path."""
    if not isinstance(format, str) or format.casefold() not in ('srt', 'lrc'):
        raise ValueError('字幕导出格式必须为 srt 或 lrc')
    content = export_srt(timeline) if format.casefold() == 'srt' else export_lrc(timeline)
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n',
                                         dir=target.parent, prefix=target.name + '.',
                                         suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
