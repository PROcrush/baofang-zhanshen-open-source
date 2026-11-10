"""One isolated indexing job; returns one bounded JSON response."""
import hashlib
import json
import re
import subprocess
import sys

from app.serato_adapter import proof_is_current
from app.storage import inspect_audio
from worker.studio_render import find_ffmpeg

FRAME_HASH_LIMIT = 8 * 1024 * 1024


def decoded_audio_proof(path):
    # Keep the source sample rate and channel layout. The frame hash includes
    # both, along with the decoded samples and their timing.
    try:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return None
        result = subprocess.run([
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror',
            '-protocol_whitelist', 'file',
            '-format_whitelist', 'aiff,flac,mov,mp3,ogg,wav',
            '-i', path, '-map', '0:a', '-c:a', 'pcm_f64le',
            '-fflags', '+bitexact', '-flags:a', '+bitexact',
            '-f', 'framehash', '-hash', 'sha256',
            '-fs', str(2 * FRAME_HASH_LIMIT), 'pipe:1',
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=30)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None

    data = result.stdout
    if len(data) >= FRAME_HASH_LIMIT or data.count(b'#media_type ') != 1:
        return None
    time_base = re.search(rb'^#tb 0: ([1-9][0-9]*)/([1-9][0-9]*)$', data, re.MULTILINE)
    if not time_base or not re.search(rb'^#channel_layout_name 0: .+$', data, re.MULTILINE):
        return None
    end = 0
    for line in data.splitlines():
        if not line or line.startswith(b'#'):
            continue
        frame = re.fullmatch(rb'0,\s*(-?\d+),\s*(-?\d+),\s*(\d+),\s*[1-9]\d*, [0-9a-f]{64}', line)
        if not frame:
            return None
        dts, pts, duration_ticks = (int(field) for field in frame.groups())
        if dts != pts or pts < end or duration_ticks <= 0:
            return None
        end = pts + duration_ticks
    numerator, denominator = (int(field) for field in time_base.groups())
    duration = end * numerator * 1000 / denominator
    if not 0 < duration <= 86_400_000:
        return None
    return {'audioContentId': 'pcm-sha256-v1:' + hashlib.sha256(data).hexdigest(),
            'audioClockDurationMs': duration}


def inspect_indexed_audio(path):
    track = inspect_audio(path)
    decoded = decoded_audio_proof(track['path'])
    if not proof_is_current(track, path):
        raise ValueError('文件在核验期间发生变化，请等待写入完成')
    # Compressed files can report encoder padding in their media duration.
    # Keep that timeline convention separate from the exact decoded clock.
    duration = track['durationMs']
    if decoded and (duration is None or abs(duration - decoded['audioClockDurationMs']) <= 20):
        track.update(decoded)
    return track


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('需要一个音频路径')
        result = {'ok': True, 'track': inspect_indexed_audio(sys.argv[1])}
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    sys.exit(0 if result['ok'] else 1)
