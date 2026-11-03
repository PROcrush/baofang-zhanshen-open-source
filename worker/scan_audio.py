"""Enumerate a user-selected audio folder; never open audio or use the network."""
import json
import os
import sys
from pathlib import Path

from app.storage import AUDIO_EXTENSIONS


def scan_audio(folder, limit=10000):
    root = Path(folder).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('请选择音频文件夹')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError('文件数量上限无效')
    paths, errors, skipped_links, byte_count = [], [], 0, 0
    truncated = False
    def error(value):
        if len(errors) < 20:
            errors.append(str(value))
    for current, directories, files in os.walk(root, followlinks=False, onerror=error):
        kept = []
        for name in sorted(directories):
            child = Path(current) / name
            if child.is_symlink():
                skipped_links += 1
            elif not name.startswith('.'):
                kept.append(name)
        directories[:] = kept
        for name in sorted(files):
            path = Path(current) / name
            if path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            if path.is_symlink():
                skipped_links += 1
                continue
            text = str(path)
            byte_count += len(text.encode('utf-8'))
            if len(paths) >= limit or byte_count > 4_000_000:
                truncated = True
                break
            paths.append(text)
        if truncated:
            break
    return {'ok': True, 'folder': str(root), 'paths': paths, 'count': len(paths),
            'truncated': truncated, 'skippedLinks': skipped_links, 'errors': errors}


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('需要用户选择的音频文件夹路径')
        value = scan_audio(sys.argv[1])
    except (OSError, ValueError) as exc:
        value = {'ok': False, 'error': str(exc)}
    print(json.dumps(value, ensure_ascii=False), flush=True)
    raise SystemExit(0 if value['ok'] else 1)
