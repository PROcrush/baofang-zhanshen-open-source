"""Exercise the offline-library QML contract with a synthetic facade and zero I/O to services."""
import copy
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QObject, Property, QPointF, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class SyntheticFacade(QObject):
    stateChanged = Signal()

    def __init__(self):
        super().__init__()
        self.data = {}
        self.calls = []

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        return copy.deepcopy(self.data)

    def update(self, data):
        self.data = copy.deepcopy(data)
        self.stateChanged.emit()

    def record(self, method, *values):
        self.calls.append([method, *values])

    @Slot()
    def importAudioBatch(self): self.record('importAudioBatch')
    @Slot()
    def importAudioFolder(self): self.record('importAudioFolder')
    @Slot()
    def verifyOfflineLibrary(self): self.record('verifyOfflineLibrary')
    @Slot()
    def exportLyricCache(self): self.record('exportLyricCache')
    @Slot()
    def importLyricCache(self): self.record('importLyricCache')
    @Slot()
    def importTimeline(self): self.record('importTimeline')
    @Slot()
    def rescanLibrary(self): self.record('rescanLibrary')
    @Slot(bool)
    def setOfflineOnly(self, enabled):
        self.record('setOfflineOnly', enabled)
        self.data['offlineLibrary']['offlineOnly'] = enabled
        self.stateChanged.emit()
    @Slot()
    def planLibraryBatch(self): self.record('planLibraryBatch')
    @Slot(str)
    def startLibraryBatch(self, plan): self.record('startLibraryBatch', plan)
    @Slot()
    def pauseLibraryBatch(self): self.record('pauseLibraryBatch')
    @Slot(str)
    def planLibraryTrack(self, version): self.record('planLibraryTrack', version)
    @Slot(str)
    def queryLibraryTrack(self, version): self.record('queryLibraryTrack', version)
    @Slot(str)
    def chooseLibraryReference(self, version): self.record('chooseLibraryReference', version)
    @Slot(str, str)
    def exportLibraryLyrics(self, version, fmt): self.record('exportLibraryLyrics', version, fmt)
    @Slot(str, str)
    def loadTrack(self, version, deck): self.record('loadTrack', version, deck)


def sample_state():
    tracks = [
        dict(versionId='sha256:' + 'a' * 64, title='合成演出歌曲 · 已缓存', path='/合成曲库/演出歌曲 A.wav', durationText='03:15', offlineReady=True,
             offlineState='ready', offlineLabel='离线就绪', needsReview=False, canQuery=False, canCorrect=True, canExport=True),
        dict(versionId='sha256:' + 'b' * 64, title='合成歌曲 · 待续查', path='/合成曲库/演出歌曲 B.mp3', durationText='04:08', offlineReady=False,
             offlineState='missing', offlineLabel='未准备歌词', needsReview=False, canQuery=True, canCorrect=True, canExport=False,
             preparationDetail='合成任务有回执，等待续查；没有真实服务请求'),
        dict(versionId='sha256:' + 'c' * 64, title='合成歌曲 · 文件变化', path='/合成曲库/演出歌曲 C.mp3', durationText='03:09', offlineReady=False,
             offlineState='invalid', offlineLabel='源文件变化', needsReview=False, canQuery=False, canCorrect=False, canExport=False),
    ]
    batch = dict(id='synthetic-reviewed-batch', status='planned', total=3, ready=1, planned=1, pending=1, failed=0, estimatedCostPTC=.051,
                 canStart=False, blockedReason='合成后端：未配置真实凭据，新提交关闭；本测试不读取 Key 或联系云端。',
                 rows=[dict(versionId=t['versionId'], title=t['title'], path=t['path'], phase='ready' if t['offlineReady'] else 'planned',
                            detail='合成计划 · 不上传原音频', estimatedCostPTC=0 if t['offlineReady'] else .051) for t in tracks])
    return dict(mode='replay', inputMode='replay', selectedSource='none', effectiveSource='none', stale=False,
                status='合成界面测试 · 不播放声音', outputOpen=False, library=tracks, style={},
                preparation={'config': {'summary': '合成配置 · 无云端连接'}},
                offlineLibrary=dict(offlineOnly=False, cacheDirectory='/合成本地缓存/歌词', total=3, ready=1, missing=1, invalid=1, review=0,
                                    tracks=tracks, batch=batch, indexingCount=0))


def main():
    runtime = ROOT / 'runtime/test-20261009/offline-ui'
    runtime.mkdir(parents=True, exist_ok=True)
    network_attempts = []
    original_connection = socket.create_connection
    original_connect = socket.socket.connect

    def no_network(*args, **kwargs):
        network_attempts.append('unexpected network attempt')
        raise AssertionError('UI test must not access a network')

    socket.create_connection = no_network
    socket.socket.connect = no_network
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    from app.lyric_paint import register_lyric_types
    register_lyric_types()
    QQuickStyle.setStyle('Basic')
    controller = SyntheticFacade()
    engine = QQmlApplicationEngine()
    warnings, checks, failures, screenshots = [], [], [], []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    engine.rootContext().setContextProperty('controller', controller)
    engine.load(QUrl.fromLocalFile(str(ROOT / 'ui/Main.qml')))
    window = engine.rootObjects()[0] if engine.rootObjects() else None

    def items():
        def walk(item):
            yield item
            for child in item.childItems():
                yield from walk(child)
        return {item.objectName(): item for item in walk(window.contentItem()) if item.objectName()}

    def click(name):
        item = items()[name]
        assert item.isVisible() and item.isEnabled(), f'Control unavailable: {name}'
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
        assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), f'Control clipped: {name}'
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        QTest.qWait(100)

    def expand_more(version):
        # 识别与导出操作收进「更多…」内联展开区，点击前先展开那一行
        panel = items().get('offlineMorePanel-' + version)
        if panel is not None and panel.isVisible():
            return
        click('offlineMore-' + version)

    def capture(name):
        path = runtime / (name + '.png')
        assert window.grabWindow().save(str(path))
        screenshots.append(str(path))

    def search(text):
        field = items()['offlineLibrarySearch']
        field.setProperty('text', text)
        QTest.qWait(80)

    try:
        assert window is not None, 'Main.qml failed to load'
        window.setProperty('currentPage', 1)
        window.resize(1000, 700)
        QTest.qWait(150)
        assert not warnings, '\n'.join(warnings)
        assert not items()['offlineBatchStart'].isEnabled()
        capture('empty-small')
        checks.append('Empty legacy state loads at 1000x700 without QML warnings or enabled cloud start')
        state = sample_state()
        controller.update(state)
        QTest.qWait(120)
        summary = items()['offlineReadySummary'].property('text')
        # 统计改版后摘要只写「离线就绪 N / 总」，「需处理」数量移到 offlineStat-failed 的 value
        assert '离线就绪 1 / 3' in summary
        assert int(items()['offlineStat-failed'].property('value')) == 1
        for name, method in [('offlineImportAudio', 'importAudioBatch'), ('offlineImportFolder', 'importAudioFolder'),
                             ('offlineVerify', 'verifyOfflineLibrary'), ('offlineExportCache', 'exportLyricCache'),
                             ('offlineImportCache', 'importLyricCache'), ('offlineImportTimeline', 'importTimeline'), ('offlineRefresh', 'rescanLibrary')]:
            click(name)
            assert controller.calls[-1] == [method]
        click('offlineBatchPlan')
        assert controller.calls[-1] == ['planLibraryBatch']
        assert not items()['offlineBatchStart'].isEnabled()
        click('offlineBatchReview')
        capture('batch-review-small')
        assert items()['offlineBatchReviewList'].property('count') == 3
        click('offlineBatchReviewClose')
        ready = state['offlineLibrary']['tracks'][0]['versionId']
        missing = state['offlineLibrary']['tracks'][1]['versionId']
        changed = state['offlineLibrary']['tracks'][2]['versionId']
        search('已缓存')
        expand_more(ready)
        for suffix, method, args in [('Reference', 'chooseLibraryReference', [ready]), ('Lrc', 'exportLibraryLyrics', [ready, 'lrc']),
                                     ('Srt', 'exportLibraryLyrics', [ready, 'srt']), ('LoadA', 'loadTrack', [ready, 'A']), ('LoadB', 'loadTrack', [ready, 'B'])]:
            click('offline' + suffix + '-' + ready)
            assert controller.calls[-1] == [method, *args]
        search('待续查')
        expand_more(missing)
        click('offlinePlan-' + missing)
        assert controller.calls[-1] == ['planLibraryTrack', missing]
        click('offlineQuery-' + missing)
        assert controller.calls[-1] == ['queryLibraryTrack', missing]
        assert not items()['offlineLrc-' + missing].isEnabled()
        search('文件变化')
        assert not items()['offlineSrt-' + changed].isEnabled()
        assert not items()['offlineReference-' + changed].isEnabled()
        checks.append('Import, backup, verification, per-song plan/query/reference/export and replay routes reach the facade; incomplete/changed files cannot export')
        search('')
        capture('library-small')
        click('offlineOnlyToggle')
        assert controller.calls[-1] == ['setOfflineOnly', True]
        assert not items()['offlineBatchPlan'].isEnabled() and not items()['offlineBatchStart'].isEnabled()
        search('待续查')
        assert not items()['offlinePlan-' + missing].isEnabled() and not items()['offlineQuery-' + missing].isEnabled()
        search('已缓存')
        expand_more(ready)
        assert items()['offlineSrt-' + ready].isEnabled()
        click('offlineSrt-' + ready)
        capture('offline-mode-small')
        checks.append('Offline mode disables new cloud plans/submissions/queries while local cached exports remain available')
        click('offlineOnlyToggle')
        state = sample_state()
        state['offlineLibrary']['batch'].update(canStart=True, blockedReason='')
        state['offlineLibrary']['indexingCount'] = 1
        controller.update(state)
        QTest.qWait(80)
        assert not items()['offlineBatchStart'].isEnabled(), 'Indexing must block batch start even before backend state catches up'
        state['offlineLibrary']['indexingCount'] = 0
        controller.update(state)
        QTest.qWait(80)
        click('offlineBatchStart')
        assert controller.calls[-1] == ['startLibraryBatch', 'synthetic-reviewed-batch']
        state['offlineLibrary']['batch']['status'] = 'running'
        controller.update(state)
        QTest.qWait(80)
        click('offlineBatchPause')
        assert controller.calls[-1] == ['pauseLibraryBatch']
        click('offlineCloudSettings')
        assert window.property('currentPage') == 4
        window.setProperty('currentPage', 1)
        checks.append('Reviewed batch ID is forwarded exactly; pause and cloud settings navigation work')
        search('')
        state = sample_state()
        state['offlineLibrary']['tracks'] = [dict(state['offlineLibrary']['tracks'][0], versionId=f'sha256:{i:064x}', title=f'合成曲库第 {i + 1} 首') for i in range(3000)]
        state['offlineLibrary'].update(total=3000, ready=3000, missing=0, invalid=0, review=0)
        controller.update(state)
        QTest.qWait(150)
        assert items()['offlineTrackList'].property('count') == 3000
        realized = sum(name.startswith('offlineTrack-') for name in items())
        assert realized < 40, f'ListView realized {realized} delegates for 3000 songs'
        search('第 3000 首')
        assert items()['offlineTrackList'].property('count') == 1
        assert not warnings, '\n'.join(warnings)
        capture('large-library-search')
        checks.append(f'3000-song model is virtualized ({realized} realized rows); search reaches the final song')
        assert not network_attempts
    except Exception as exc:
        failures.append(str(exc) or type(exc).__name__)
    finally:
        socket.create_connection = original_connection
        socket.socket.connect = original_connect
        result = dict(passed=not failures, syntheticOnly=True, cloudRequests=0, credentialsRead=False, networkAttempts=network_attempts,
                      checks=checks, warnings=warnings, failures=failures, calls=controller.calls, screenshots=screenshots)
        (runtime / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        (runtime / 'README.md').write_text('# 离线曲库界面测试\n\n此测试只使用合成 Controller 状态，没有读取密钥、上传音频或访问服务；不代表真实云端识别或现场输出已验收。\n\n'
                                         + '\n'.join('- ' + check for check in checks) + '\n\n结果见 result.json；截图使用 1000×700 小窗。\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if window is not None:
            window.close()
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
