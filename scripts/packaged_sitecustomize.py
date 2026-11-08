"""Installed only in the private, relocatable Python shipped inside our app."""
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path[:] = [p for p in sys.path if p and p != os.getcwd()]
resources = Path(sys.prefix).parent
source = resources / 'app'
if source.is_dir():
    sys.path.insert(0, str(source))
cache = Path.home() / 'Library/Caches/包房战神'
# Every worker imports sitecustomize even with the app's minimal environment.
os.environ['XDG_CACHE_HOME'] = str(cache)
os.environ['PYTHONNOUSERSITE'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['PATH'] = str(resources / 'bin') + ':/usr/bin:/bin:/usr/sbin:/sbin'
ffmpeg = (Path(sys.prefix) / 'lib/python3.12/site-packages/imageio_ffmpeg'
          / 'binaries/ffmpeg-macos-aarch64-v7.1')
os.environ['BAOFANG_FFMPEG'] = str(ffmpeg)
os.environ['IMAGEIO_FFMPEG_EXE'] = str(ffmpeg)
