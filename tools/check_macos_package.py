"""Exercise a relocated macOS bundle using only its own Python and engines.

Uses synthetic media, an empty private runtime, and an OS network-denial
sandbox. No real API Key, songs, AX permissions, or development venv is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import time


PROBE = r'''
import hashlib,importlib.util,json,math,os,struct,subprocess,sys,time,wave
from pathlib import Path
root,work=map(Path,sys.argv[1:3])
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def inside(path,parent):return Path(path).resolve().is_relative_to(parent.resolve())
assert inside(sys.executable,root.parent/'python'),sys.executable
assert inside(sys.prefix,root.parent/'python'),sys.prefix
assert not any('/.venv/' in str(path) for path in sys.path),sys.path
import PySide6,imageio_ffmpeg,tinytag,pypinyin,segno,mido,rtmidi
for module in (PySide6,imageio_ffmpeg,tinytag,pypinyin,segno,mido,rtmidi):
    assert inside(module.__file__,root.parent/'python'),module.__file__
from PySide6.QtCore import QTimer
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication
from app.runtime_paths import packaged_info,is_packaged_ffmpeg
from app.native import NativeClock
from app.serato_overlay import NativeOverlayBridge
from app.studio import StudioController,file_identity
from worker.studio_render import find_ffmpeg,probe_media
info=packaged_info(root)
for resource in ('ui/Main.qml','ui/ScreenContentPanel.qml','ui/GuestWishControls.qml','ui/ScreenLayers.qml','app/screen_layers.py',
                 'ui/ScreenPersonalPanel.qml','app/screen_library.py',
                 'fixtures/replay_timelines.json','assets/style_presets.json',
                 'build/native/libbaofang_sync.dylib','build/native/libbaofang_overlay.dylib'):
    assert (root/resource).is_file(),resource
assert not (root/'assets/models').exists(), 'Unexpected local ASR model'
for module in ('worker.studio_transcribe', 'whisper', 'torch'):
    assert importlib.util.find_spec(module) is None, module
helper=root/'helpers/包房战神-rekordbox-AX.app/Contents/MacOS/rekordbox-ax'
assert sha(helper)==info['axHelperSha256'],'AX helper hash mismatch'
clock=NativeClock(root)
now=time.monotonic_ns()
position,status=clock.estimate(dict(positionMs=1000.,speed=1.,durationMs=3000.,capturedNs=now,
                                   validUntilNs=now+350000000,playing=False),now)
assert position==1000.,(position,status)
overlay=NativeOverlayBridge(root)  # Loads our native library; does not inspect windows.
ffmpeg=find_ffmpeg()
assert ffmpeg and is_packaged_ffmpeg(ffmpeg),ffmpeg
assert sha(ffmpeg)==info['ffmpegSha256']
report={'python':sys.executable,'pythonVersion':sys.version.split()[0],
        'modules':{'PySide6':PySide6.__version__},
        'localASRExcluded':True,'ffmpeg':ffmpeg,'nativeLibrariesLoaded':True,
        'axHelperVerified':True,'cloudRequests':0,'realCredentialsRead':False}
QQuickStyle.setStyle('Basic')
application=QApplication([])
# Check the rebuilt live features using bundled modules and Python. Reader
# substitutes prevent inspection of any real DJ app; the renderer is real.
from unittest.mock import patch
from app.controller import Controller,FileController
from app.preshow import report as preshow_report
from app.source_routing import select_lyric_source
started=[]
with patch.object(Controller,'start_bridge',lambda controller,mode:started.append(mode)):
    live=Controller(root,work/'live-runtime',input_mode='serato')
    try:
        from PySide6.QtGui import QImage,QColor
        logo=QImage(120,60,QImage.Format.Format_ARGB32);logo.fill(QColor('transparent'))
        logo.setPixelColor(60,30,QColor('#F0C17A'))
        logo_path=work/'package-logo.png';assert logo.save(str(logo_path))
        assert live.setScreenText('DJ PACKAGE\n独立客屏图层')
        assert live.chooseScreenLogo(str(logo_path))
        saved_layers=live.state['screenLayers']
        logo_path.unlink()
        live.selectSource('B');live.clearOutput()
        live._state['lyricOffsets']={'demo:B':250};live.save_settings()
        live.openOutput()
        first=live.output
        assert first is not None and first.poll() is None
        first.kill();first.wait(timeout=5)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            application.processEvents();time.sleep(.02)
            if live.output is not None and live.output.pid!=first.pid:break
        assert live.output is not None and live.output.pid!=first.pid,'Packaged renderer did not recover'
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            application.processEvents();time.sleep(.02)
        assert live.output.poll() is None,'Packaged renderer exited after recovery'
        assert live.state['selectedSource']=='none' and live.state['resumeSource']=='B'
        report['rendererRecoveryVerified']=True
    finally:
        live.shutdown()
    restored=Controller(root,work/'live-runtime')
    try:
        assert started==['serato','serato'],started
        assert restored.state['selectedSource']=='none' and restored.state['resumeSource']=='B'
        assert restored.state['lyricOffsets']=={'demo:B':250}
        assert not restored.raw and not restored.ax_bindings and not restored.state['outputRequested']
        assert not restored.state['lyric']['visible']
        assert restored.state['screenLayers']==saved_layers and restored.state['logoImageUrl']
        from PySide6.QtCore import QUrl
        from PySide6.QtQml import QQmlApplicationEngine
        restored.tick()
        reader=FileController(root,work/'live-runtime')
        layer_engine=QQmlApplicationEngine()
        layer_warnings=[]
        layer_engine.warnings.connect(lambda rows:layer_warnings.extend(row.toString() for row in rows))
        layer_engine.rootContext().setContextProperty('controller',reader)
        layer_engine.load(QUrl.fromLocalFile(str(root/'ui/Program.qml')))
        assert layer_engine.rootObjects(),'Packaged guest-screen QML failed'
        layer_window=layer_engine.rootObjects()[0]
        def visual(item):
            yield item
            for child in item.childItems():yield from visual(child)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            application.processEvents();time.sleep(.02)
            items={item.objectName():item for item in visual(layer_window.contentItem()) if item.objectName()}
            if items['screenLogoImage'].isVisible():break
        assert items['screenTextLayer'].isVisible() and items['screenLogoImage'].isVisible()
        assert reader.state['screenLayers']==saved_layers
        layer_window.grabWindow().save(str(work/'客屏图层.png'))
        assert not layer_warnings,layer_warnings
        reader.timer.stop();layer_window.hide();layer_engine.deleteLater()
        report['screenLayersVerified']=True
        state=dict(restored.state,selectedSource='auto',effectiveSource='none',
                   selection=select_lyric_source('auto',-1,None,{side:{'ready':True} for side in 'AB'},
                                                  video_fader_ready=False),
                   guestQr={'running':True,'url':'http://127.0.0.1:5000/package-test'})
        rows={row['id']:row for row in preshow_report(state,(), '192.168.1.20')['rows']}
        assert rows['lyrics']['tone']=='amber' and rows['lyrics']['action']=='routing'
        assert rows['wish']['tone']=='amber' and rows['wish']['action']=='wish'
        report.update(liveStateRestartVerified=True,preshowWarningsVerified=True,
                      liveExtraModules=['segno','mido','rtmidi'])
    finally:
        restored.shutdown()
studio=StudioController(root,work/'studio-runtime')
calls=[]
original_launch=studio._launch
def recorded_launch(program,arguments,complete,**kwargs):
    assert Path(program).resolve()==Path(sys.executable).resolve(),program
    calls.append({'program':str(program),'arguments':[str(a) for a in arguments]})
    return original_launch(program,arguments,complete,**kwargs)
studio._launch=recorded_launch
def idle(timeout=180):
    end=time.monotonic()+timeout
    while studio.state['busy'] and time.monotonic()<end:
        application.processEvents();time.sleep(.01)
    application.processEvents()
    if studio.state['busy']:
        studio.cancelJob();raise AssertionError('Studio task timed out: '+studio.state['status'])
try:
    audio=work/'合成测试.wav'
    with wave.open(str(audio),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(48000)
        stream.writeframes(b''.join(struct.pack('<h',int(1600*math.sin(2*math.pi*220*n/48000)))
                                    for n in range(144000)))
    source=file_identity(audio)
    subtitles=work/'合成测试.srt'
    subtitles.write_text('1\n00:00:00,250 --> 00:00:01,250\n包房战神\n\n'
                         '2\n00:00:01,500 --> 00:00:02,750\n独立安装包测试\n',encoding='utf-8')
    studio.chooseAudio(str(audio));idle()
    assert studio.state['audioPath']==str(audio),studio.state['status']
    assert abs(studio.state['durationMs']-3000)<2,studio.state['durationMs']
    studio.chooseSubtitles(str(subtitles));idle()
    assert len(studio.state['segments'])==2,studio.state['status']
    project=work/'合成测试.bfstudio.json'
    studio.saveProject(str(project))
    assert project.is_file(),studio.state['status']
    studio.openProject(str(project));idle()
    assert not studio.state['dirty'] and len(studio.state['segments'])==2,studio.state['status']
    studio.exportVideo();idle()
    assert studio.state['resultPath'],studio.state['status']
    result=Path(studio.state['resultPath'])
    assert result.is_file() and result.stat().st_size>1000,result
    assert len(calls)>=3,calls
    output=work/'封包验收.mp4'
    studio.saveVideoAs(str(output))
    assert output.is_file(),studio.state['status']
    decoded=subprocess.run([ffmpeg,'-v','error','-i',str(output),'-map','0:v:0','-map','0:a:0',
                            '-f','null','-'],capture_output=True,timeout=60)
    assert decoded.returncode==0,decoded.stderr.decode(errors='replace')
    media=probe_media(output,include_video=True)
    assert {'video','audio'}<={s['codec_type'] for s in media['streams']},media
    assert abs(float(media['format']['duration'])-3)<.35,media
    frame=work/'导出画面.png'
    subprocess.run([ffmpeg,'-v','error','-ss','0.75','-i',str(output),'-frames:v','1',str(frame)],
                   check=True,capture_output=True,timeout=30)
    assert file_identity(audio)==source,'Source audio changed'
    report.update(qprocessCalls=calls,subtitleSegments=2,sourceUnchanged=True,
                  project=str(project),video=str(output),frame=str(frame),media=media,
                  videoSHA256=sha(output),status='passed')
finally:
    studio.shutdown();application.processEvents()
(work/'worker-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':'passed','qprocessCalls':len(calls),'video':str(output)},ensure_ascii=False),flush=True)
'''


def snapshot(bundle):
    records = {}
    for directory, folders, files in os.walk(bundle, followlinks=False):
        for name in folders + files:
            path = Path(directory) / name
            relative = str(path.relative_to(bundle))
            if path.is_symlink():
                records[relative] = {'symlink': os.readlink(path)}
            elif path.is_file():
                with path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                records[relative] = {'size': path.stat().st_size, 'sha256': digest}
    return records


def run_step(name, arguments, environment, work, timeout):
    print(json.dumps({'step': name, 'status': 'running'}, ensure_ascii=False), flush=True)
    started = time.monotonic()
    stdout, stderr = work / (name + '.stdout.log'), work / (name + '.stderr.log')
    # Applies to the entire subprocess tree, including the real QProcess workers.
    command = ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)(deny network*)', *map(str, arguments)]
    with stdout.open('wb') as out, stderr.open('wb') as err:
        process = subprocess.Popen(command, cwd=work, env=environment, stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise RuntimeError(name + ' timed out; see ' + str(stderr)) from None
    result = {'step': name, 'exitCode': code, 'seconds': round(time.monotonic() - started, 3),
              'stdout': str(stdout), 'stderr': str(stderr)}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if code:
        raise RuntimeError(name + ' failed; see ' + str(stderr))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True, help='new or empty evidence directory')
    args = parser.parse_args()
    bundle, work = args.bundle.expanduser().resolve(), args.work_dir.expanduser().resolve()
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        parser.error('This acceptance check requires macOS and sandbox-exec for network denial')
    if bundle.suffix != '.app' or not (bundle / 'Contents/Info.plist').is_file():
        parser.error('--bundle must name a real macOS application bundle')
    if work.is_relative_to(bundle) or (work.exists() and (not work.is_dir() or any(work.iterdir()))):
        parser.error('--work-dir must be empty and outside the bundle')
    work.mkdir(parents=True, exist_ok=True)
    root = bundle / 'Contents/Resources/app'
    python = bundle / 'Contents/Resources/python/bin/python3.12'
    metadata = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    executable_name = metadata.get('CFBundleExecutable', '')
    if not executable_name or Path(executable_name).name != executable_name:
        parser.error('Bundle executable name is invalid')
    launcher = bundle / 'Contents/MacOS' / executable_name
    if not python.is_file() or not launcher.is_file():
        parser.error('Bundled Python or native launcher is missing')
    for folder in ('home', 'tmp'):
        (work / folder).mkdir(mode=0o700)
    environment = {'PATH': str(bundle / 'Contents/Resources/bin') + ':/usr/bin:/bin:/usr/sbin:/sbin',
                   'HOME': str(work / 'home'), 'TMPDIR': str(work / 'tmp'), 'LANG': 'zh_CN.UTF-8',
                   'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                   'QT_QUICK_BACKEND': 'software'}
    script = work / 'package-probe.py'
    script.write_text(PROBE, encoding='utf-8')
    before = snapshot(bundle)
    (work / 'bundle-before.json').write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8')
    report = {'bundle': str(bundle), 'workDirectory': str(work), 'status': 'failed', 'steps': [],
              'networkDeniedForAllChildProcesses': True, 'syntheticMediaOnly': True,
              'realCredentialsRead': False, 'accessibilityPermissionChanged': False}
    try:
        report['steps'].append(run_step('worker', [python, script, root, work],
                                        dict(environment, QT_QPA_PLATFORM='offscreen'), work, 300))
        screenshot = work / '安装包界面.png'
        report['steps'].append(run_step('native-app', [launcher, '--studio', '--runtime', work / 'gui-runtime',
            '--studio-project', work / '合成测试.bfstudio.json', '--smoke-seconds', '3',
            '--screenshot', screenshot], environment, work, 45))
        if not screenshot.is_file() or screenshot.stat().st_size < 1000:
            raise RuntimeError('Native app did not produce a usable screenshot')
        if screenshot.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
            raise RuntimeError('Native screenshot is not a PNG')
        report.update(status='passed', screenshot=str(screenshot))
    except Exception as error:
        report['error'] = str(error)
    finally:
        after = snapshot(bundle)
        changes = [name for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
        report.update(bundleBytesUnchanged=not changes, changedBundleFiles=changes,
                      bundledFiles=len(after), pycacheFilesBefore=sum('__pycache__/' in name for name in before),
                      pycacheFilesAfter=sum('__pycache__/' in name for name in after))
        if changes:
            report['status'] = 'failed'
            report['error'] = 'Application bundle files changed while running'
        (work / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
