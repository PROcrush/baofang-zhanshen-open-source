"""Build a relocatable, self-contained Apple Silicon test app and DMG.

Uses this project's validated standalone CPython and installed dependencies.
Never collects runtime, credentials, user songs, or developer absolute paths.
Only this product's new build artifacts are compiled/ad-hoc signed.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.13.0'
IDENTIFIER = 'local.baofangzhanshen.mac'
NAME = '包房战神'
MINIMUM = '15.0'
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', '_virtualenv*')


def run(*command, **kwargs):
    return subprocess.run([str(v) for v in command], check=True, **kwargs)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(source.stat().st_mode & 0o777)


def copy_tree(source, target):
    shutil.copytree(source, target, symlinks=True, ignore=IGNORE,
                    copy_function=shutil.copyfile, dirs_exist_ok=True)
    # copyfile avoids user xattrs; retain executable modes for private tools.
    for current, _, files in os.walk(source):
        for name in files:
            original = Path(current) / name
            copied = target / original.relative_to(source)
            if not original.is_symlink() and copied.is_file():
                copied.chmod(original.stat().st_mode & 0o777)


def remove_legacy_asr(site):
    # A development venv or reused package runtime can contain the old engine.
    for entry in site.iterdir():
        if entry.name not in {'whisper', 'torch', 'functorch', 'torchgen'} and not (
                entry.name.startswith(('openai_whisper-', 'torch-')) and entry.name.endswith('.dist-info')):
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def write_plist(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(value))


def manual(output):
    import markdown
    source = ROOT / 'docs/macOS使用手册.md'
    copy_file(source, output / '使用手册.md')
    body = markdown.markdown(source.read_text(), extensions=['tables', 'fenced_code', 'toc'])
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>包房战神 · macOS 使用手册</title><style>
:root{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#20202a;background:#f6f5fa}
body{max-width:920px;margin:36px auto;padding:32px 44px;background:white;line-height:1.8}
h1{font-size:32px}h2{border-top:1px solid #ddd;padding-top:24px;margin-top:38px}h3{margin-top:28px}
a{color:#5940b0}code,pre{font-size:13px;background:#f1eff6;border-radius:4px}code{padding:2px 5px}
pre{padding:16px;overflow-wrap:anywhere;white-space:pre-wrap}table{border-collapse:collapse;width:100%}
th,td{border:1px solid #ddd;padding:9px 12px;text-align:left;vertical-align:top}th{background:#f3f1f9}
blockquote{margin-left:0;padding:8px 16px;border-left:4px solid #8a70d6;background:#f5f2fc}
@media(max-width:650px){body{margin:0;padding:18px}table{font-size:13px}}
@media print{body{max-width:none;margin:0;padding:0;background:white;font-size:10pt}h2{break-after:avoid}pre,table{break-inside:avoid}a{color:inherit}}
</style><body>''' + body + '</body></html>'
    (output / '使用手册.html').write_text(html, encoding='utf-8')


def icon(resources):
    # Same simple waveform motif as the application's navigation brand.
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    with tempfile.TemporaryDirectory(prefix='baofang-icon-') as directory:
        iconset = Path(directory) / 'AppIcon.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = size * scale
                bitmap = QImage(pixels, pixels, QImage.Format_ARGB32_Premultiplied)
                bitmap.fill(Qt.transparent)
                painter = QPainter(bitmap)
                painter.setRenderHint(QPainter.Antialiasing)
                painter.scale(pixels / 1024, pixels / 1024)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor('#23212e'))
                painter.drawRoundedRect(QRectF(28, 28, 968, 968), 220, 220)
                painter.setBrush(QColor('#bba4ff'))
                for x, height in zip((235, 355, 475, 595, 715), (230, 400, 560, 400, 230)):
                    painter.drawRoundedRect(QRectF(x, (1024-height)/2, 74, height), 37, 37)
                painter.end()
                filename = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
                if not bitmap.save(str(iconset / filename)):
                    raise RuntimeError('应用图标生成失败')
        run('/usr/bin/iconutil', '-c', 'icns', iconset, '-o', resources / 'AppIcon.icns')


def build(args):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64' or sys.version_info[:2] != (3, 12):
        raise SystemExit('本构建需要 Apple Silicon macOS 与项目 Python 3.12')
    # Documents/FileProvider may re-add FinderInfo while codesign is reading.
    # Seal the app outside synced workspace folders; publish archives to dist.
    output_root = (args.output_root.expanduser().resolve() if args.output_root else
                   Path.home() / 'Library/Caches/包房战神/build')
    output = output_root / f'{NAME}-{VERSION}-macOS-arm64'
    bundle = output / f'{NAME}.app'
    resources = bundle / 'Contents/Resources'
    source = resources / 'app'
    private_python = resources / 'python'
    site = private_python / 'lib/python3.12/site-packages'
    output.mkdir(parents=True, exist_ok=True)
    if not args.reuse_runtime or not site.exists():
        if private_python.exists():
            shutil.rmtree(private_python)
        print('Copying standalone Python and dependencies', flush=True)
        copy_tree(Path(sys.base_prefix), private_python)
        shutil.rmtree(site, ignore_errors=True)
        copy_tree(Path(sysconfig.get_paths()['purelib']), site)
    remove_legacy_asr(site)
    copy_file(ROOT / 'scripts/packaged_sitecustomize.py', site / 'sitecustomize.py')
    # The app uses Python sqlite3. These unused optional Qt SQL drivers refer
    # to Homebrew/Postgres/Mimer installations that must not be required.
    for name in ('libqsqlodbc.dylib', 'libqsqlpsql.dylib', 'libqsqlmimer.dylib'):
        (site / 'PySide6/Qt/plugins/sqldrivers' / name).unlink(missing_ok=True)
    for cache in list(private_python.rglob('__pycache__')):
        if cache.is_dir() and not cache.is_symlink():
            shutil.rmtree(cache)
    # Clear only the generated source subtree, never the workspace/runtime.
    if source.exists():
        shutil.rmtree(source)
    for name in ('app', 'worker', 'ui', 'fixtures'):
        copy_tree(ROOT / name, source / name)
    # Runtime code only: probes also contains historical machine diagnostics.
    for path in (ROOT / 'probes').rglob('*.py'):
        copy_file(path, source / path.relative_to(ROOT))
    copy_file(ROOT / 'scripts/repair_qt_metadata.py', source / 'scripts/repair_qt_metadata.py')
    copy_file(ROOT / 'assets/style_presets.json', source / 'assets/style_presets.json')
    (resources / 'bin').mkdir(exist_ok=True)
    ffmpeg = site / 'imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1'
    link = resources / 'bin/ffmpeg'
    link.unlink(missing_ok=True)
    link.symlink_to('../python/lib/python3.12/site-packages/imageio_ffmpeg/binaries/' + ffmpeg.name)
    compile_env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=MINIMUM)
    native = source / 'build/native'
    native.mkdir(parents=True)
    print('Building native launcher, clock, overlay and AX helper', flush=True)
    common = ['-std=c++17', '-Wall', '-Wextra', '-Werror', '-O2', '-mmacosx-version-min='+MINIMUM,
              '-I'+str(ROOT / 'native/include')]
    run('/usr/bin/clang++', *common, '-DBF_SYNC_BUILD', '-dynamiclib', '-fPIC', '-fvisibility=hidden',
        '-Wl,-install_name,@rpath/libbaofang_sync.dylib',
        ROOT / 'native/src/baofang_sync.cpp', '-o', native / 'libbaofang_sync.dylib', env=compile_env)
    run('/usr/bin/clang++', *common, '-fobjc-arc', '-dynamiclib', '-fPIC', '-fvisibility=hidden',
        '-Wl,-install_name,@rpath/libbaofang_overlay.dylib',
        ROOT / 'native/src/baofang_overlay.mm', '-framework', 'AppKit', '-framework', 'CoreGraphics',
        '-o', native / 'libbaofang_overlay.dylib', env=compile_env)
    helper = source / 'helpers/包房战神-rekordbox-AX.app'
    helper_exe = helper / 'Contents/MacOS/rekordbox-ax'
    helper_exe.parent.mkdir(parents=True)
    run('/usr/bin/clang', '-fobjc-arc', '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min='+MINIMUM,
        '-framework', 'Foundation', '-framework', 'ApplicationServices',
        '-DREKORDBOX_TARGET_PATH="/Applications/rekordbox 7/rekordbox.app/Contents/MacOS/rekordbox"',
        ROOT / 'probes/native/rekordbox_ax.m', '-o', helper_exe, env=compile_env)
    write_plist(helper / 'Contents/Info.plist', {
        'CFBundleIdentifier': 'local.baofangzhanshen.rekordbox-ax',
        'CFBundleName': '包房战神-rekordbox-AX', 'CFBundleDisplayName': '包房战神-rekordbox-AX',
        'CFBundleExecutable': 'rekordbox-ax', 'CFBundlePackageType': 'APPL',
        'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0', 'LSUIElement': True,
        'LSMinimumSystemVersion': MINIMUM,
        'NSAccessibilityUsageDescription': '只读原版 rekordbox 双盘曲名和已播时间以显示歌词。'})
    run('/usr/bin/codesign', '--force', '--sign', '-', helper)
    executable = bundle / 'Contents/MacOS/baofang'
    executable.parent.mkdir(parents=True, exist_ok=True)
    run('/usr/bin/clang', '-fobjc-arc', '-O2', '-Wall', '-Wextra', '-mmacosx-version-min='+MINIMUM,
        ROOT / 'scripts/macos_launcher.m', '-framework', 'AppKit', '-o', executable, env=compile_env)
    write_plist(bundle / 'Contents/Info.plist', {
        'CFBundleIdentifier': IDENTIFIER, 'CFBundleName': NAME, 'CFBundleDisplayName': NAME,
        'CFBundleExecutable': 'baofang', 'CFBundlePackageType': 'APPL',
        'CFBundleIconFile': 'AppIcon',
        'CFBundleShortVersionString': VERSION, 'CFBundleVersion': '130',
        'LSMinimumSystemVersion': MINIMUM, 'NSHighResolutionCapable': True,
        'NSPrincipalClass': 'NSApplication', 'LSArchitecturePriority': ['arm64'],
        'NSHumanReadableCopyright': '包房战神 · 私人测试版',
        'NSAppleEventsUsageDescription': '打开用户选择的文件和辅助功能设置。'})
    icon(resources)
    revision = run('git', 'rev-parse', 'HEAD', cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(run('git', 'status', '--porcelain', cwd=ROOT, capture_output=True, text=True).stdout.strip())
    manifest = {'schemaVersion': 1, 'bundleIdentifier': IDENTIFIER, 'version': VERSION,
                'minimumMacOS': MINIMUM, 'architecture': 'arm64', 'sourceCommit': revision,
                'sourceDirty': dirty, 'axHelperSha256': digest(helper_exe),
                'ffmpegSha256': digest(ffmpeg),
                'pythonVersion': platform.python_version(), 'notarized': False,
                'signature': 'ad-hoc', 'includesPersonalData': False}
    (source / 'packaged.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    (output / '构建信息.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    packages = sorted(({'name': d.metadata['Name'], 'version': d.version,
                       'license': d.metadata.get('License-Expression') or d.metadata.get('License', ''),
                       'homePage': d.metadata.get('Home-page', '')}
                      for d in importlib.metadata.distributions(path=[str(site)])), key=lambda d: d['name'].lower())
    (output / '第三方组件.json').write_text(json.dumps(packages, ensure_ascii=False, indent=2)+'\n')
    manual(output)
    for name in ('使用手册.html', '使用手册.md', '第三方组件.json'):
        copy_file(output / name, resources / name)
    # Retain all installed package LICENSE/COPYING files in the private runtime.
    # Remove Finder metadata only from the generated product before sealing it.
    for current, directories, files in os.walk(bundle):
        for path in [Path(current), *(Path(current)/name for name in files)]:
            if not path.is_symlink() and hasattr(os, 'chflags'):
                flags = path.stat().st_flags
                if flags & stat.UF_HIDDEN:
                    os.chflags(path, flags & ~stat.UF_HIDDEN)
    run('/usr/bin/xattr', '-cr', bundle)
    run('/usr/bin/codesign', '--force', '--sign', '-', bundle)
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', bundle)
    applications = output / 'Applications'
    applications.unlink(missing_ok=True)
    applications.symlink_to('/Applications')
    published = ROOT / 'dist'
    published.mkdir(exist_ok=True)
    for name in ('使用手册.html', '使用手册.md', '第三方组件.json', '构建信息.json'):
        copy_file(output / name, published / name)
    if args.archives:
        dmg = published / f'{output.name}.dmg'
        zip_file = published / f'{output.name}.zip'
        print('Creating DMG and ZIP', flush=True)
        run('/usr/bin/hdiutil', 'create', '-volname', f'{NAME} {VERSION}', '-srcfolder', output,
            '-format', 'UDZO', '-ov', dmg)
        run('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', output, zip_file)
        checksums = ''.join(digest(p)+'  '+p.name+'\n' for p in (dmg, zip_file))
        (published / f'{output.name}-SHA256SUMS.txt').write_text(checksums)
    print(json.dumps({'app': str(bundle), 'output': str(output), 'manifest': manifest}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-runtime', action='store_true', help='Refresh app code while retaining this generated Python runtime')
    parser.add_argument('--output-root', type=Path, help='Local unsynced staging folder; defaults to the user Library/Caches')
    parser.add_argument('--archives', action='store_true', help='Also produce a drag-install DMG, ZIP and checksums')
    build(parser.parse_args())
