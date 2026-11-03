"""Clear UF_HIDDEN in this project's Qt wheel; leave bytes and other flags intact."""
import os
import stat
import sys
import sysconfig
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def repair_qt_metadata():
    root = Path(__file__).resolve().parent.parent
    from app.runtime_paths import packaged_info
    if packaged_info(root) is not None:
        # The builder repairs its staging copy before signing. Installed apps
        # and worker processes must never mutate their bundled Qt runtime.
        return 0
    package = Path(sysconfig.get_paths()['purelib']) / 'PySide6'
    if not package.resolve().is_relative_to(root / '.venv'):
        raise RuntimeError('仅允许修复本项目 .venv 内的 Qt 运行时')
    if not hasattr(os, 'chflags') or not hasattr(stat, 'UF_HIDDEN'):
        return 0
    changed = 0
    for current, directories, files in os.walk(package):
        for item in [Path(current)] + [Path(current) / name for name in directories + files]:
            metadata = item.lstat()
            if item.is_symlink():
                continue
            if metadata.st_flags & stat.UF_HIDDEN:
                os.chflags(item, metadata.st_flags & ~stat.UF_HIDDEN, follow_symlinks=False)
                changed += 1
    return changed


if __name__ == '__main__':
    print(f'Qt 运行时文件标记修复：{repair_qt_metadata()} 项；文件字节未修改')
