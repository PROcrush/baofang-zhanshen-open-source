"""Independent offline CPU ASR using the official OpenAI Whisper package.

Decode at most 30 seconds of ordinary local media per call. Token clocks are
the model's actual word alignment; no proportional character clocks are made.
"""

import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import wave

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.studio_tools import validate_segments
from app.runtime_paths import is_packaged_model, validate_model_location
from worker.studio_render import _info_from_ffmpeg, find_ffmpeg, find_ffprobe

ENGINE_ID = 'baofang-whisper-cpu/v1'
SAMPLE_RATE = 16000
WINDOW_SECONDS = 30
TINY_SHA256 = '65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9'
DEFAULT_MODEL = Path(__file__).resolve().parent.parent / 'assets/models/tiny.pt'


class TranscriptionCancelled(Exception):
    pass


def _number(value, label, minimum=0):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid or value < minimum:
        raise ValueError(label + ' 必须是有限有效数值')
    return float(value)


def _check_cancel(cancelled):
    if cancelled and cancelled():
        raise TranscriptionCancelled('本地识别已取消')


def _digest(path, cancelled=None):
    before = path.stat()
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            _check_cancel(cancelled)
            result.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('读取期间源文件发生变化')
    return result.hexdigest()


def validate_request(request):
    if (not isinstance(request, dict) or not {'audioPath', 'durationMs', 'output'} <= set(request)
            or set(request) - {'audioPath', 'durationMs', 'output', 'model', 'modelPath', 'language'}):
        raise ValueError('本地识别请求字段无效')
    duration = _number(request['durationMs'], '音频时长') / 1000
    if not 0 < duration <= 86400:
        raise ValueError('一次本地识别的音频时长须大于零且不超过24小时')
    for key in ('audioPath', 'output'):
        if not isinstance(request[key], str) or not request[key] or not Path(request[key]).is_absolute():
            raise ValueError(key + ' 必须是明确选择的绝对路径')
    audio = Path(request['audioPath']).resolve(strict=True)
    if not audio.is_file() or any(part.endswith('.app') for part in audio.parts):
        raise ValueError('请使用普通本地音频文件')
    output = Path(request['output'])
    if output.suffix.lower() != '.json' or not output.parent.is_dir() or output.exists() or output.is_symlink():
        raise ValueError('识别结果必须是不存在的 .json 文件，保存目录须已存在')
    output = output.parent.resolve() / output.name
    if output == audio:
        raise ValueError('识别结果不能覆盖原音频')
    if request.get('model', 'tiny') != 'tiny':
        raise ValueError('模型名只接受 tiny；其他本地模型请用 modelPath 明确选择')
    model_path = request.get('modelPath')
    if model_path is not None:
        if (not isinstance(model_path, str) or not Path(model_path).is_absolute()
                or Path(model_path).suffix != '.pt'):
            raise ValueError('modelPath 必须是明确选择的绝对 .pt 路径')
        validate_model_location(model_path)
    language = request.get('language', 'zh')
    if language is not None and (not isinstance(language, str) or not language.isalpha() or len(language) > 12):
        raise ValueError('识别语言必须是 Whisper 语言代码或 null')
    return audio, output, duration, language


def _load_backend(request, cancelled):
    try:
        import torch
        import whisper
    except ImportError as error:
        raise ValueError('本地识别依赖尚未安装，请安装 requirements-local-asr.txt') from error
    path = Path(request.get('modelPath') or DEFAULT_MODEL).resolve(strict=True)
    validate_model_location(path)
    if not path.is_file():
        raise ValueError('请选择普通本地 Whisper .pt 模型文件')
    model_sha = _digest(path, cancelled)
    if ('modelPath' not in request or is_packaged_model(path)) and model_sha != TINY_SHA256:
        raise ValueError('tiny 模型与 OpenAI 官方 SHA256 不一致，请重新准备模型')
    # Explicit local path prevents runtime downloads. Whisper 20250625 uses
    # weights_only=True on supported PyTorch versions when loading a checkpoint.
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    model = whisper.load_model(str(path), device='cpu', in_memory=False)
    if _digest(path, cancelled) != model_sha:
        raise ValueError('加载期间本地模型发生变化')
    # Loading by local path does not select Whisper's official named head mask.
    # The verified tiny checkpoint can use the exact mask shipped with Whisper.
    alignment_preset = 'model-default'
    if model_sha == TINY_SHA256:
        model.set_alignment_heads(whisper._ALIGNMENT_HEADS['tiny'])
        alignment_preset = 'tiny'
    def recognize(samples, language):
        return model.transcribe(samples, language=language, task='transcribe', fp16=False,
                                word_timestamps=True, verbose=None, temperature=0,
                                condition_on_previous_text=False, beam_size=1)
    return recognize, {'model': path.stem, 'modelPath': str(path), 'modelSHA256': model_sha,
                       'package': 'openai-whisper', 'packageVersion': whisper.__version__,
                       'alignmentHeadPreset': alignment_preset}


def decode_window(path, start_seconds, duration_seconds):
    """Bounded 16kHz mono float32 decode using our ordinary FFmpeg discovery."""
    import numpy as np
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError('本地识别需要通用 FFmpeg')
    command = [ffmpeg, '-v', 'error', '-nostdin', '-ss', str(start_seconds),
               '-protocol_whitelist', 'file,pipe', '-i', str(path),
               '-t', str(duration_seconds), '-map', '0:a:0', '-ac', '1', '-ar', str(SAMPLE_RATE),
               '-f', 'f32le', 'pipe:1']
    result = subprocess.run(command, capture_output=True, timeout=180)
    if result.returncode:
        raise ValueError('音频解码失败：' + result.stderr.decode('utf-8', errors='replace')[-1000:])
    maximum_bytes = int(math.ceil(duration_seconds * SAMPLE_RATE) + 2) * 4
    if len(result.stdout) > maximum_bytes or len(result.stdout) % 4:
        raise ValueError('音频解码输出超出窗口范围')
    samples = np.frombuffer(result.stdout, dtype='<f4').copy()
    if not np.isfinite(samples).all():
        raise ValueError('音频包含无效PCM数值')
    return samples


def probe_local_audio(path):
    """Read local media duration without allowing nested network protocols."""
    if Path(path).suffix.lower() == '.wav':
        try:
            with wave.open(str(path), 'rb') as stream:
                return {'format': {'duration': stream.getnframes() / stream.getframerate()}}
        except (wave.Error, EOFError):
            pass
    ffprobe = find_ffprobe()
    if ffprobe:
        result = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                 '-show_format', '-show_streams', '-of', 'json', str(path)],
                                capture_output=True, check=True, timeout=120)
        return json.loads(result.stdout)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError('本地识别需要通用 FFmpeg')
    result = subprocess.run([ffmpeg, '-hide_banner', '-protocol_whitelist', 'file,pipe', '-i', str(path)],
                            capture_output=True, timeout=120)
    return _info_from_ffmpeg(result.stderr.decode('utf-8', errors='replace'))


def convert_window(result, offset, window_duration):
    """Translate real model word spans without subdividing multi-character words."""
    if not isinstance(result, dict) or not isinstance(result.get('segments'), list):
        raise ValueError('Whisper 没有返回有效字幕段落')
    converted, word_segments, discarded = [], 0, 0
    for row in result['segments']:
        if not isinstance(row, dict) or not isinstance(row.get('text'), str):
            raise ValueError('Whisper 字幕段落无效')
        text = row['text'].strip()
        if not text:
            continue
        start = _number(row.get('start'), '模型段落起点')
        end = _number(row.get('end'), '模型段落终点')
        if end < start:
            raise ValueError('模型段落起止时间倒置')
        # Segment endpoints are clipped to the actual decoded window; word
        # clocks are either retained unchanged or omitted entirely.
        start, end = min(start, window_duration), min(end, window_duration)
        if start >= end:
            continue
        segment = {'start': start + offset, 'end': end + offset, 'text': text}
        words, valid_words, previous = [], True, start
        model_words = row.get('words', [])
        if not isinstance(model_words, list):
            raise ValueError('Whisper 词级时码无效')
        for index, word in enumerate(model_words):
            if not isinstance(word, dict) or not isinstance(word.get('word'), str):
                raise ValueError('Whisper 词级时码无效')
            word_text = word['word']
            if index == 0:
                word_text = word_text.lstrip()
            if index == len(model_words) - 1:
                word_text = word_text.rstrip()
            ws, we = _number(word.get('start'), '模型词起点'), _number(word.get('end'), '模型词终点')
            if not word_text or not start <= ws < we <= end or ws < previous:
                valid_words = False
                break
            words.append({'text': word_text, 'start': ws + offset, 'end': we + offset})
            previous = we
        if words and valid_words and ''.join(word['text'] for word in words) == text:
            segment['characters'] = words
            word_segments += 1
        elif model_words:
            discarded += 1
        converted.append(segment)
    return validate_segments(converted, duration=offset + window_duration), word_segments, discarded


def transcribe_audio(request, backend=None, decoder=None, event=None, cancelled=None, probe=None):
    """Run offline recognition; injected backend/decoder make boundaries testable.

    backend(samples, language) returns official Whisper-shaped output. Decoder
    returns at most 30*16000 samples. Both offsets and receipts are managed here.
    """
    audio, output, duration, language = validate_request(request)
    _check_cancel(cancelled)
    emit = event or (lambda value: None)
    emit({'type': 'progress', 'value': 0, 'message': '正在核验源音频与本地模型'})
    source_sha = _digest(audio, cancelled)
    information = (probe or probe_local_audio)(audio)
    actual_duration = _number(float(information['format']['duration']), '实际音频时长')
    if abs(actual_duration - duration) > .25:
        raise ValueError('请求时长与实际音频不一致，请重新导入歌曲')
    duration = min(duration, actual_duration)
    if duration <= 0:
        raise ValueError('源音频没有有效时长')
    if backend is None:
        backend, model_info = _load_backend(request, cancelled)
    else:
        model_info = {'model': 'injected-test', 'package': 'injected-test', 'packageVersion': '',
                      'modelPath': '', 'modelSHA256': ''}
    decode = decoder or decode_window
    rows, word_count, dropped, offset, windows = [], 0, 0, 0.0, 0
    while offset < duration - 1 / SAMPLE_RATE:
        _check_cancel(cancelled)
        length = min(WINDOW_SECONDS, duration - offset)
        samples = decode(audio, offset, length)
        count = len(samples)
        if not 0 < count <= math.ceil(length * SAMPLE_RATE) + 2:
            raise ValueError('解码窗口样本为空或超出边界')
        actual_length = min(length, count / SAMPLE_RATE)
        if length - actual_length > .25:
            raise ValueError('实际PCM长度与音频时钟不一致')
        _check_cancel(cancelled)
        emit({'type': 'progress', 'value': offset / duration,
              'message': '正在识别本地音频窗口 ' + str(windows + 1)})
        result = backend(samples, language)
        _check_cancel(cancelled)
        converted, words, invalid = convert_window(result, offset, actual_length)
        rows.extend(converted)
        word_count += words
        dropped += invalid
        if len(rows) > 10000:
            raise ValueError('识别结果超过10,000个段落，请拆分音频')
        windows += 1
        offset += length
        emit({'type': 'progress', 'value': min(.99, offset / duration),
              'message': '已识别 ' + str(windows) + ' 个本地音频窗口'})
    rows = validate_segments(rows, duration=duration)
    _check_cancel(cancelled)
    if _digest(audio, cancelled) != source_sha:
        raise ValueError('识别期间原音频发生变化，结果未发布')
    receipt = {'engineId': ENGINE_ID, 'sourcePath': str(audio), 'sourceSHA256': source_sha,
               'durationMs': duration * 1000, 'device': 'cpu', 'windowSeconds': WINDOW_SECONDS,
               'windows': windows, 'segmentCount': len(rows), 'wordTimedSegments': word_count,
               'actualWordTiming': word_count > 0, 'allSegmentsWordTimed': bool(rows) and word_count == len(rows),
               'timingGranularity': 'model-word-spans', 'characterTimingSynthesized': False,
               'discardedInvalidWordTimingSegments': dropped, 'manualReviewRequired': True,
               'networkUsedDuringRecognition': False, **model_info}
    data = {'schema': 'baofang-subtitles/v1', 'segments': rows, 'receipt': receipt}
    with tempfile.TemporaryDirectory(prefix='.baofang-asr-', dir=output.parent) as directory:
        stage = Path(directory) / 'timeline.json'
        stage.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        _check_cancel(cancelled)
        staged_identity = (stage.stat().st_dev, stage.stat().st_ino)
        published = False
        try:
            os.link(stage, output)  # Exclusive publication, including racing destinations.
            published = True
            _check_cancel(cancelled)
            done = {'type': 'done', 'timelinePath': str(output), 'receipt': receipt}
            emit(done)
        except BaseException:
            # Only retract the file we linked, never another publisher's result.
            if published and output.exists():
                current = output.lstat()
                if (current.st_dev, current.st_ino) == staged_identity:
                    output.unlink()
            raise
    return done


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('识别请求存在重复字段')
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    arguments = parser.parse_args(argv)
    def interrupt(signum, frame):
        raise TranscriptionCancelled('本地识别已取消')
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    def event(value):
        print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
    try:
        path = Path(arguments.request)
        if path.stat().st_size > 65536:
            raise ValueError('识别请求文件过大')
        request = json.loads(path.read_text(encoding='utf-8'),
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError('请求包含非有限数值')),
                             object_pairs_hook=_unique_object)
        # Whisper's verbose output cannot pollute the JSON event channel.
        with contextlib.redirect_stdout(sys.stderr):
            emit_stdout = sys.__stdout__
            def json_event(value):
                print(json.dumps(value, ensure_ascii=False, allow_nan=False), file=emit_stdout, flush=True)
            transcribe_audio(request, event=json_event)
        return 0
    except TranscriptionCancelled as error:
        event({'type': 'cancelled', 'message': str(error)})
        return 130
    except Exception as error:
        event({'type': 'error', 'message': str(error)})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
