"""Qt preparation facade/dialog regression with a synthetic backend and no cloud I/O."""
import copy
import json
import os
import sys
import types
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QMetaObject, QPointF, Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from app.clock import monotonic_ns


class SyntheticPreparationManager:
    """Contract double: only creates local test files, never reads credentials."""
    def __init__(self, root, runtime, library, timelines, on_change, on_timeline):
        self.library, self.timelines = library, timelines
        self.changed, self.timeline_ready = on_change, on_timeline
        self.config = {'ready': False, 'newSubmissionEnabled': False, 'budgetLimit': 0,
                       'budgetUnit': '合成测试单位', 'summary': '合成后端 · 缺少测试配置；无云端连接'}
        self.route, self.records, self.calls = 'domestic', {}, []

    def view(self, decks):
        states = {}
        for key, track in decks.items():
            version = track.get('versionId')
            value = copy.deepcopy(self.records.get(version, {'phase': 'waiting', 'label': '待审阅',
                                                              'detail': '合成后端未执行任何云端请求'}))
            value['versionId'] = version
            plan = value.get('plan')
            if plan:
                plan['canSubmit'] = (self.config['ready'] and self.config['newSubmissionEnabled']
                                     and self.config['budgetLimit'] >= 0.1 and plan['route'] == self.route)
                plan['blockedReason'] = '' if plan['canSubmit'] else '配置、额度与本次手动允许开关尚未全部满足'
            value['canQuery'] = value['phase'] == 'processing'
            value['canCorrect'] = value['canExport'] = version in self.timelines and not str(version).startswith('demo:')
            states[key] = value
        return {'config': dict(self.config), 'route': self.route, 'decks': states,
                'routes': [{'id': 'domestic', 'label': '国内合成路线', 'service': '合成字幕服务'},
                           {'id': 'international', 'label': '国际合成路线', 'service': '合成字幕服务'}]}

    def configure_file(self, path):
        assert Path(path).is_file()
        self.calls.append(['config', path])
        self.config.update(ready=True, path=path, summary='合成配置就绪 · 未读取密钥或发起请求')
        self.invalidate_plans()
        self.changed()

    def invalidate_plans(self):
        for record in self.records.values():
            record['plan'] = {}

    def set_route(self, route):
        assert route in ('domestic', 'international')
        self.route = route
        self.invalidate_plans()
        self.changed()

    def set_budget(self, value):
        self.config['budgetLimit'] = value
        self.invalidate_plans()
        self.changed()

    def set_new_submission_enabled(self, value):
        self.config['newSubmissionEnabled'] = value
        self.changed()

    def plan(self, deck, track):
        version = track.get('versionId')
        if not track.get('identityVerified'):
            self.records[version] = {'phase': 'blocked', 'label': '未核验', 'detail': '合成回放不可上传；实际文件身份尚未核验'}
        else:
            self.records[version] = {'phase': 'planned', 'label': '计划可审阅', 'detail': '合成服务，不提交真实识别',
                                    'plan': {'planId': str(uuid.uuid4()), 'title': track['title'], 'versionId': version,
                                             'sourcePath': track['path'], 'uploadSource': '合成测试原文件 · 不上传',
                                             'service': '合成字幕服务', 'routeLabel': '国内合成路线' if self.route == 'domestic' else '国际合成路线',
                                             'route': self.route, 'durationText': '00:12', 'estimatedCost': 0.1,
                                             'budgetLabel': '预估 0.1 合成测试单位'}}
        self.changed()

    def start(self, deck, track, plan_id):
        state = self.records[track['versionId']]
        assert state['plan']['planId'] == plan_id and state['plan']['route'] == self.route
        assert self.config['ready'] and self.config['newSubmissionEnabled'] and self.config['budgetLimit'] >= 0.1
        self.calls.append(['start', deck, track['versionId'], plan_id])
        state.update(phase='processing', label='合成任务处理中', detail='等待合成续查；无真实上传')
        state['plan'] = {}
        self.changed()

    def query(self, deck, track):
        version = track['versionId']
        assert self.records[version]['phase'] == 'processing'
        timeline = {'schemaVersion': 1, 'versionId': version, 'durationMs': 12000,
                    'quality': 'sentence', 'segments': [{'startMs': 0, 'endMs': 3000, 'text': '这是合成字幕'}]}
        self.library.add_timeline(timeline, track)
        self.timeline_ready(version, timeline)
        self.records[version].update(phase='ready', label='合成字幕就绪', detail='合成句级时间轴已按实际版本绑定')
        self.calls.append(['query', deck, version])
        self.changed()

    def correct(self, deck, track, path):
        assert Path(path).is_file()
        version = track['versionId']
        self.records[version].update(referencePath=path, correctionSummary='合成纠错已记录；未搜索外部歌词',
                                     phase='corrected', label='合成纠错完成')
        self.calls.append(['correct', deck, version, path])
        self.changed()

    def export(self, deck, track, path, subtitle_format):
        assert track['versionId'] in self.timelines
        Path(path).write_text('[00:00.00]这是合成字幕\n' if subtitle_format == 'lrc' else '1\n00:00:00,000 --> 00:00:03,000\n这是合成字幕\n')
        self.calls.append(['export', deck, track['versionId'], path, subtitle_format])
        self.changed()

    def shutdown(self):
        pass


def main():
    runtime = ROOT / 'runtime/test-20261009/recognition-ui'
    runtime.mkdir(parents=True, exist_ok=True)
    config, reference, audio = runtime / 'opaque-config.json', runtime / 'reference.txt', runtime / 'synthetic-12s.wav'
    config.write_text('{}')
    reference.write_text('这是合成参考歌词\n')
    with wave.open(str(audio), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b'\0\0' * 96000)
    fake_module = types.ModuleType('app.preparation')
    fake_module.PreparationManager = SyntheticPreparationManager
    sys.modules['app.preparation'] = fake_module
    from app.controller import Controller
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    controller = Controller(ROOT, runtime / 'isolated-controller')
    original_open, original_save = QFileDialog.getOpenFileName, QFileDialog.getSaveFileName
    QFileDialog.getOpenFileName = lambda *args, **kwargs: (str(config if '服务配置' in args[1] else reference), '')
    QFileDialog.getSaveFileName = lambda *args, **kwargs: (str(runtime / ('export.lrc' if 'LRC' in args[3] else 'export.srt')), '')
    engine = QQmlApplicationEngine()
    warnings, checks, failures, screenshots = [], [], [], []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    engine.rootContext().setContextProperty('controller', controller)
    engine.load(QUrl.fromLocalFile(str(ROOT / 'ui/Main.qml')))
    window = engine.rootObjects()[0] if engine.rootObjects() else None

    def wait_for(predicate, duration=2500):
        deadline = monotonic_ns() + duration * 1_000_000
        while monotonic_ns() < deadline:
            if predicate():
                return True
            QTest.qWait(10)
        return bool(predicate())

    def items():
        def walk(item):
            yield item
            for child in item.childItems():
                yield from walk(child)
        return {item.objectName(): item for item in walk(window.contentItem()) if item.objectName()}

    def click(name):
        item = items()[name]
        ancestor = item.parentItem()
        while ancestor is not None:
            if ancestor.metaObject().indexOfProperty('contentY') >= 0:
                point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
                top = ancestor.mapToScene(QPointF(0, 0)).y()
                bottom = top + ancestor.height()
                if point.y() < top + 24 or point.y() > bottom - 24:
                    desired = ancestor.property('contentY') + point.y() - (top + bottom)/2
                    maximum = max(0, ancestor.property('contentHeight') - ancestor.height())
                    ancestor.setProperty('contentY', max(0, min(maximum, desired)))
                    QTest.qWait(50)
                break
            ancestor = ancestor.parentItem()
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        QTest.qWait(100)

    def capture(name):
        path = runtime / (name + '.png')
        assert window.grabWindow().save(str(path))
        screenshots.append(str(path))

    try:
        assert window is not None, 'Main.qml failed to load'
        assert wait_for(lambda: not controller.state['stale'])
        click('nav-4')
        assert not controller.state['preparation']['config']['newSubmissionEnabled']
        click('recognitionPlan-A')
        assert controller.state['preparation']['decks']['A']['phase'] == 'blocked'
        assert not items()['recognitionStart-A'].isVisible()
        checks.append('new submission defaults off and synthetic demo cannot create a submission plan')
        controller.index_audio(str(audio))
        assert wait_for(lambda: str(audio) in controller.file_proofs), 'Synthetic WAV identity did not finish'
        track = controller.library.tracks()[0]
        controller.loadTrack(track['versionId'], 'A')
        assert wait_for(lambda: controller.state['deckA'].get('versionId') == track['versionId'])
        click('recognitionPlan-A')
        assert items()['recognitionStart-A'].isVisible() and not items()['recognitionStart-A'].isEnabled()
        capture('missing-config')
        click('recognitionConfigButton')
        click('recognitionBudgetField')
        QMetaObject.invokeMethod(items()['recognitionBudgetField'], 'selectAll')
        QTest.keyClick(window, Qt.Key.Key_1)
        QTest.qWait(150)
        assert items()['recognitionBudgetField'].property('text') == '1', 'Budget edit was reset by playback state updates'
        QTest.keyClick(window, Qt.Key.Key_Return)
        QTest.qWait(100)
        assert controller.preparation.config['budgetLimit'] == 1, 'Budget editing did not reach facade'
        click('recognitionAllowSubmission')
        assert not items()['recognitionStart-A'].isVisible(), 'Budget/config changes retained the stale plan'
        click('recognitionPlan-A')
        assert items()['recognitionStart-A'].isEnabled()
        assert controller.state['preparation']['decks']['A']['plan']['sourcePath'] == str(audio)
        checks.append('configuration dialog route, budget and explicit session toggle unlock a concrete reviewed plan')
        capture('reviewed-plan')
        click('recognitionStart-A')
        assert controller.state['preparation']['decks']['A']['phase'] == 'processing'
        click('recognitionQuery-A')
        assert controller.state['preparation']['decks']['A']['phase'] == 'ready'
        click('recognitionCorrect-A')
        assert controller.state['preparation']['decks']['A']['referencePath'] == str(reference)
        click('recognitionLrc-A')
        click('recognitionSrt-A')
        assert (runtime / 'export.lrc').is_file() and (runtime / 'export.srt').is_file()
        checks.append('synthetic start/query/reference and LRC/SRT save dialog paths reach the facade')
        capture('ready-and-exported')
        controller.planRecognition('A')
        old_plan = controller.state['preparation']['decks']['A']['plan']['planId']
        controller.setDeck('A')
        controller.loadTrack('demo:A', 'A')
        assert wait_for(lambda: controller.state['deckA'].get('versionId') == 'demo:A')
        submitted_before = sum(call[0] == 'start' for call in controller.preparation.calls)
        controller.startRecognition('A', old_plan)
        assert sum(call[0] == 'start' for call in controller.preparation.calls) == submitted_before
        checks.append('switching the deck rejects the stale plan before backend invocation')
        window.resize(1000, 700)
        QTest.qWait(100)
        capture('small-preparation')
        assert not warnings, '\n'.join(warnings)
    except Exception as exc:
        failures.append(str(exc) or type(exc).__name__)
    finally:
        QFileDialog.getOpenFileName, QFileDialog.getSaveFileName = original_open, original_save
        controller.shutdown()
        result = {'passed': not failures, 'syntheticOnly': True, 'cloudRequests': 0, 'credentialsRead': False,
                  'runtimeIsolated': str(runtime), 'checks': checks, 'warnings': warnings, 'failures': failures,
                  'calls': controller.preparation.calls, 'screenshots': screenshots}
        (runtime/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
