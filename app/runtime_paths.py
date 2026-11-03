"""Separate immutable packaged resources from per-user application data."""
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_IDENTIFIER = 'local.baofangzhanshen.mac'
TINY_SHA256 = '65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9'


def packaged_info(root=None):
    """Recognize only our app's fixed Resources/app layout, never an env flag."""
    root = Path(root or ROOT).resolve()
    marker = root / 'packaged.json'
    layout = (root.name == 'app' and root.parent.name == 'Resources' and
              root.parent.parent.name == 'Contents' and root.parents[2].suffix == '.app')
    if not layout:
        return None
    try:
        if marker.is_symlink():
            raise ValueError('包信息不能使用符号链接')
        info = json.loads(marker.read_text(encoding='utf-8'))
        if (not isinstance(info, dict) or type(info.get('schemaVersion')) is not int or
                info['schemaVersion'] != 1 or info.get('bundleIdentifier') != BUNDLE_IDENTIFIER):
            raise ValueError('包信息格式无效')
        return info
    except (OSError, ValueError) as error:
        raise RuntimeError('包房战神安装包信息缺失或损坏，请重新安装') from error


def default_runtime(root=None):
    root = Path(root or ROOT).resolve()
    if packaged_info(root) is not None:
        return Path.home() / 'Library/Application Support/包房战神'
    return root / 'runtime'


def is_packaged_model(path, root=None):
    root = Path(root or ROOT).resolve()
    if packaged_info(root) is None:
        return False
    expected = root / 'assets/models/tiny.pt'
    # An external file reached through a symlink is not a bundled model.
    return Path(path).resolve() == expected and expected.resolve() == expected


def validate_model_location(path, root=None):
    path = Path(path).resolve()
    if any(part.endswith('.app') for part in path.parts) and not is_packaged_model(path, root):
        raise ValueError('不使用其他应用包模型；请选择普通本地 Whisper .pt 模型文件')
    return path


def is_packaged_ffmpeg(path, root=None):
    """Allow only the pinned imageio executable shipped with this product."""
    root = Path(root or ROOT).resolve()
    info = packaged_info(root)
    if info is None:
        return False
    expected = root.parent / ('python/lib/python3.12/site-packages/imageio_ffmpeg/'
                              'binaries/ffmpeg-macos-aarch64-v7.1')
    digest = info.get('ffmpegSha256', '')
    if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or
            Path(path).resolve() != expected or expected.resolve() != expected):
        return False
    try:
        with expected.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() == digest
    except OSError:
        return False
