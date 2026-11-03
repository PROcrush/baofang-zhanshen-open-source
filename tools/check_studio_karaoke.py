"""Exercise KTV controls, real audio transport, and a local synthetic MP4."""
import hashlib
import json
import os
import subprocess
import uuid
import wave
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPoint, QSize, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from app.studio import StudioController
from app.studio_preview import StudioPreviewProvider
from tests.test_studio import APPLICATION, ROOT, wait_idle
from tools.ui_state_smoke import named_items, click
from worker.studio_render import find_ffmpeg, probe_media


def digest(image):
    return hashlib.sha256(image.constBits().tobytes()).hexdigest()


def main():
    target = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else ROOT / 'runtime/ktv-20261015'
    target.mkdir(parents=True, exist_ok=True)
    audio = target / '合成时码验证-无演唱.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(48000)
        stream.writeframes(b'\0\0' * 48000 * 16)
    rows = [
        {'startMs': 4000, 'endMs': 8000, 'text': '星光照亮今晚', 'characters': [
            {'text': '星光', 'startMs': 4000, 'endMs': 5500},
            {'text': '照亮', 'startMs': 6500, 'endMs': 7200},
            {'text': '今晚', 'startMs': 7200, 'endMs': 8000}]},
        {'startMs': 8200, 'endMs': 10500, 'text': '跟着节奏一起唱', 'characters': [
            {'text': '跟着', 'startMs': 8200, 'endMs': 8800},
            {'text': '节奏', 'startMs': 8800, 'endMs': 9500},
            {'text': '一起唱', 'startMs': 9500, 'endMs': 10500}]},
        {'startMs': 12000, 'endMs': 15000, 'text': '这句仅有句级时码'}]
    subtitle = target / '合成时码.json'
    subtitle.write_text(json.dumps({'segments': rows}, ensure_ascii=False, indent=2))
    studio = StudioController(ROOT, target / 'test-runtime')
    studio.chooseAudio(str(audio)); wait_idle(studio)
    studio.chooseSubtitles(str(subtitle)); wait_idle(studio)
    QQuickStyle.setStyle('Basic')
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(message.toString() for message in messages))
    engine.rootContext().setContextProperty('studio', studio)
    provider = StudioPreviewProvider(studio)
    engine.addImageProvider('studioPreview', provider)
    engine.loadData(('import QtQuick\nimport QtQuick.Controls\nimport "' +
        QUrl.fromLocalFile(str(ROOT / 'ui')).toString() +
        '"\nApplicationWindow { visible: true; width: 1440; height: 900; color: "#17171b"; StudioPanel { anchors.fill: parent } }').encode())
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    named_items(window)['studioWorkspace'].setProperty('advancedEditing', True)
    screenshots = []
    def item(name):
        result = named_items(window).get(name)
        assert result, name
        return result
    def frame(position):
        studio.seek(position)
        return provider.requestImage('frame', QSize(), QSize())
    def shot(name):
        QTest.mouseMove(window, QPoint(window.width()-4,window.height()-4))
        QTest.qWait(200)
        path = target / name
        assert window.grabWindow().save(str(path))
        screenshots.append(str(path))
    try:
        QTest.qWait(150)
        control = item('studioKaraokeMode')
        assert control.isVisible() and control.isEnabled()
        click(window, control); QTest.qWait(100)
        assert studio.state['settings']['templateId'] == 'karaoke'
        assert studio.state['settings']['highlightColor'] == '#FFE600'
        click(window,item('studioKaraokeCardMode')); QTest.qWait(50)
        assert studio.state['settings']['templateId'] == 'karaoke-card'
        click(window,control); QTest.qWait(50)
        assert studio.state['settings']['templateId'] == 'karaoke'
        assert '2 / 3' in item('studioKaraokeTiming').property('text')
        first = frame(4700); first.save(str(target / 'sweep-4700.png'))
        assert digest(first) != digest(frame(5100)), 'Word highlight did not advance'
        assert digest(frame(5700)) == digest(frame(6200)), 'Sweep moved during a real word gap'
        assert digest(first) == digest(frame(4700)), 'Backward seek did not restore the same frame'
        assert studio.state['currentText'] == '星光照亮今晚'
        for width, height in [(1440,900),(1000,700)]:
            window.resize(width,height); QTest.qWait(100)
            assert item('studioKaraokeMode').isVisible()
            assert item('studioKaraokeTiming').isVisible()
            shot(f'ktv-{width}.png')
        window.resize(1440,900)
        for position, name in [(2000,'before-singing.png'),(8500,'next-line.png'),(13000,'sentence.png')]:
            frame(position); shot(name)
        # DESIGN「歌词走带开关 2026-10-21」: with the sweep on, a sentence without
        # word timing sweeps by its own start/end (labelled 按句估算扫色); with
        # the sweep off it only changes lines. Neither case writes word timings.
        assert studio.state['settings']['lyricSweep'] is True
        sentence_a = frame(12500); sentence_b = frame(14500)
        assert digest(sentence_a) != digest(sentence_b), 'Sentence estimate did not sweep with 歌词走带 on'
        assert not studio.state['segments'][2].get('characters'), 'Estimate wrote word timings into the project'
        studio.setSetting('lyricSweep', False); QTest.qWait(50)
        sentence_a = frame(12500); sentence_b = frame(14500)
        assert digest(sentence_a) == digest(sentence_b), 'Sentence-only timing swept with 歌词走带 off'
        studio.setSetting('lyricSweep', True); QTest.qWait(50)
        frame(4700)
        play = item('studioPlayPreview')
        click(window, play); QTest.qWait(450)
        assert studio.state['playbackPlaying'] and studio.state['positionMs'] > 4900
        click(window, play); QTest.qWait(80)
        paused = studio.state['positionMs']; paused_digest = digest(frame(paused))
        QTest.qWait(220)
        assert studio.state['positionMs'] == paused
        assert paused_digest == digest(provider.requestImage('frame',QSize(),QSize()))
        frame(8100); click(window, play); QTest.qWait(450)
        assert studio.state['currentText'] == '跟着节奏一起唱', 'Playback did not change lyric lines automatically'
        click(window, play)
        project = target / '合成KTV验证.bfstudio.json'
        studio.saveProject(str(project))
        output = target / '合成KTV验证.mp4'
        if output.exists():
            output = target / ('合成KTV验证-' + uuid.uuid4().hex[:8] + '.mp4')
        studio.exportVideoTo(str(output)); wait_idle(studio)
        assert studio.state['savedVideoPath'] == str(output), studio.state['status']
        metadata = probe_media(output, verify_video=True)
        assert {row['codec_type'] for row in metadata['streams']} >= {'audio','video'}
        decoded = {}
        for seconds in (4.7,5.7,6.2,8.5,13):
            path = target / f'mp4-{seconds}.png'
            subprocess.run([find_ffmpeg(),'-hide_banner','-loglevel','error','-y','-ss',str(seconds),
                '-i',str(output),'-frames:v','1',str(path)],check=True,timeout=30)
            decoded[str(seconds)] = str(path)
        assert not warnings, warnings
        report = {'passed':True,'synthetic':True,'audio':'silent synthetic WAV; no singing-alignment claim',
            'checks':['one-click KTV','timing-quality labels','word-gap hold','backward seek','pause hold',
                      'automatic line advance','sentence-only fallback','1440/1000 window','MP4 audio/video decode'],
            'screenshots':screenshots,'video':str(output),'decodedFrames':decoded,'warnings':warnings}
        (target/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
    finally:
        studio.shutdown(); provider.shutdown(); window.close(); APPLICATION.processEvents()


if __name__ == '__main__':
    main()
