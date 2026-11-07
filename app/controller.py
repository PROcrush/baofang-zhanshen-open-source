import copy
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Property, QProcess, QTimer, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from app.lyrics import project_lyrics
from app.clock import monotonic_ns
from app.messages import accept_snapshot, accept_serato_frame, validate_program_payload
from app.native import NativeClock
from app.serato_adapter import adapt_serato_frame, proof_is_current, file_signature
from app.rekordbox_adapter import accept_rekordbox_frame, adapt_rekordbox_frame
from app.rekordbox_ax_adapter import accept_ax_frame, adapt_ax_frame, make_ax_binding
from app.source_routing import check_source_snapshot, select_lyric_source
from app.storage import Library, timeline_needs_review
from app.styles import DEFAULT_STYLE, changed_style, traditional_style, validate_style
from app.live_settings import MAX_OFFSET_TRACKS, validate_offsets, validate_output_target, screen_options, place_output, tap_offset
from app.live_settings import INPUT_MODES, input_mode_state, load_live_session, save_live_session
from app.setlist import Setlist
from app.duet import duet_view, singer_at
from app.showlog import ShowLog
from app.guest_inbox import MAX_QUEUE, GuestInbox, clean_text, lan_address, write_qr
from app.wish_relay_client import WishRelayClient
from app.midi_control import MidiControl, input_names
from app.preshow import report as preshow_report
from app.duplicates import mark_duplicates
from app.screen_layers import default_layers, load_layers, save_layers, validate_layers, clean_layer_text, import_logo, logo_url
from app.screen_library import default_library, load_library, save_library, validate_library, with_history, save_profile, export_profile, prepare_profile

STATE_COLLECTIONS = ('library', 'diagnostics', 'preparation', 'offlineLibrary', 'outputScreens', 'rekordboxAX', 'setlist', 'midi', 'showLog', 'guestInbox', 'screenContentLibrary')


def stamp(ms):
    total = int(max(0, ms))
    return f'{total // 60000:02}:{total // 1000 % 60:02}.{total % 1000:03}'


def blank_deck(key):
    return {'title': f'原创同步样例 {key}', 'path': '', 'positionMs': 0.0, 'durationMs': 120000.0,
            'playing': False, 'valid': False, 'quality': '合成时码', 'positionText': '--:--.---'}


def initial_state():
    return {'mode': 'replay', 'inputMode': 'replay', 'modeLabel': '回放演示', 'status': '正在启动回放桥', 'playing': False,
            'speed': 1.0, 'selectedSource': 'auto', 'effectiveSource': 'none', 'deckA': blank_deck('A'),
            'deckB': blank_deck('B'), 'lyric': project_lyrics(None, 0), 'library': [],
            'diagnostics': {'summary': '尚未执行真实环境诊断', 'checks': []},
            'notice': '回放数据，不播放声音；未连接真实 DJ。新云识别提交默认关闭。',
            'outputOpen': False, 'outputRequested': False, 'outputDestination': 'standalone',
            'outputRecovery': {'phase': 'idle', 'detail': ''},
            'overlayStatus': {'phase': 'idle', 'detail': '尚未打开 Serato 字幕叠加'},
            'stale': True, 'scene': 'amber', 'loopEnabled': False,
            'resumeSource': 'auto', 'lyricOffsets': {}, 'lyricOffsetMs': 0,
            'outputTarget': {'screenName': '', 'fullScreen': False}, 'outputScreens': [],
            'nativeCore': 'C++ 同步核心已载入', 'style': dict(DEFAULT_STYLE),
            'bridgeStatus': '正在启动回放桥', 'videoFader': -1.0, 'autoSide': None,
            'readerConnected': False, 'readerHasTracks': False,
            'faderValidUntilNs': 0, 'faderLeftSource': 'A', 'faderRightSource': 'B',
            'selectionReason': '合成回放跟随当前盘；真实人声来源尚未核验', 'bridgeRunning': True,
            'preparation': {'config': {'ready': False, 'newSubmissionEnabled': False,
                                      'summary': '尚未配置云端识别；新提交默认关闭'},
                            'route': 'domestic', 'decks': {}},
            'offlineLibrary': {'offlineOnly': False, 'cacheDirectory': '', 'total': 0,
                               'ready': 0, 'missing': 0, 'invalid': 0, 'review': 0,
                               'tracks': [], 'batch': {}, 'indexingCount': 0},
            'setlist': {'items': [], 'total': 0, 'played': 0,
                        'counts': {'ready': 0, 'review': 0, 'missing': 0, 'gone': 0}, 'next': None},
            'lyricTap': {'canUndo': False},
            'guestMessage': {'text': '', 'visible': False}, 'guestInbox': [],
            'guestQr': {'running': False, 'url': '', 'image': '', 'onScreen': False},
            'guestRelayConfigured': False, 'guestRelayStatus': '',
            'duetColors': False, 'midi': {'rows': [], 'ports': [], 'port': '', 'connected': False},
            'screenLayers': default_layers(), 'logoImageUrl': '', 'screenLayerNotice': '',
            'screenTextValidUntilNs': 0, 'screenTextRemaining': 0, 'guestMessageRemaining': 0, 'guestMessageValidUntilNs': 0,
            'screenContentLibrary': default_library(),
            'showLog': {'day': '', 'days': [], 'rows': [], 'summary': {}}}


def project_state(state, raw, timelines, clock, now):
    result = copy.deepcopy({key: value for key, value in state.items() if key not in STATE_COLLECTIONS})
    deadline = result.get('screenTextValidUntilNs', 0)
    result['screenTextRemaining'] = max(0, (deadline - now + 999_999_999) // 1_000_000_000) if deadline else 0
    if deadline:
        if now >= deadline:
            result['screenLayers']['text']['visible'] = False
    guest_deadline = result.get('guestMessageValidUntilNs', 0)
    result['guestMessageRemaining'] = max(0, (guest_deadline - now + 999_999_999) // 1_000_000_000) if guest_deadline else 0
    if guest_deadline:
        if now >= guest_deadline:
            result['guestMessage']['visible'] = False
    result.update({key: state[key] for key in STATE_COLLECTIONS if key in state})
    if result.get('bridgeRunning') is False:
        raw = {}
    valid = {}
    rekordbox = result.get('mode') in ('rekordbox', 'rekordbox_ax')
    live = result.get('mode') in ('serato', 'rekordbox', 'rekordbox_ax')
    sources = {}
    for key in ('A', 'B'):
        if key not in raw:
            result['deck' + key].update(valid=False, positionText='--:--.---', cacheState='suspended', cacheLabel='暂无有效播放观测', needsReview=False)
    for key, deck in raw.items():
        position, status = clock.estimate(deck, now)
        valid[key] = status == 0
        identity_ready = deck.get('identityReady', False) and proof_is_current(deck.get('identityProof'), deck['path']) if live else True
        timeline = timelines.get(deck['versionId'])
        sources[key] = check_source_snapshot(deck, timeline, now, clock_status=status, identity_ready=identity_ready)
        cloud_timeline = bool(timeline and timeline.get('provider'))
        label = '合成时码' if deck['versionId'].startswith('demo:') else (('云端字幕缓存' if cloud_timeline else '已导入歌词') if timeline else '暂无歌词')
        fresh_observation = deck['capturedNs'] <= now <= deck['validUntilNs']
        cache_state = ('error' if deck.get('errorText') else 'preparing') if live and not identity_ready else ('ready' if timeline else 'missing')
        if not fresh_observation:
            cache_state = 'suspended'
        needs_review = cache_state == 'ready' and timeline_needs_review(timeline)
        result['deck' + key] = dict(deck, positionMs=position if status == 0 else deck['positionMs'],
                                    lyricOffsetMs=result.get('lyricOffsets', {}).get(deck['versionId'], 0),
                                    positionText=stamp(position if status == 0 else deck['positionMs']) if fresh_observation else '--:--.---',
                                    valid=status == 0, quality=label, cacheState=cache_state, needsReview=needs_review,
                                    cacheLabel={'ready': '歌词已缓存 · 待复核' if needs_review else '云端字幕缓存已就绪' if cloud_timeline else '歌词缓存已就绪', 'preparing': '后台核验实际音频文件', 'missing': '本曲无歌词缓存 · 请到歌曲准备页审阅识别计划',
                                                'error': '文件核验失败', 'suspended': '播放状态过期 · 已隐藏歌词'}[cache_state])
        if result.get('mode') == 'rekordbox_ax' and not identity_ready and fresh_observation:
            result['deck' + key]['cacheLabel'] = deck.get('bindingMessage', '请确认此盘实际歌曲文件')
    selected = result['selectedSource']
    if live:
        selection = select_lyric_source(selected, result.get('videoFader', -1), result.get('autoSide'), sources,
                    video_fader_ready=now <= result.get('faderValidUntilNs', 0),
                    left_source=result.get('faderLeftSource', 'A'), right_source=result.get('faderRightSource', 'B'))
        if rekordbox:
            selection['reason'] = selection['reason'].replace('视频推子', 'rekordbox 混音推子').replace('视频盘', '混音盘')
            if selection['selectionBasis'] == 'video-fader':
                selection['selectionBasis'] = 'crossfader'
                selection['diagnosticSource'] = 'crossfader:' + selection['requestedSource']
        effective = selection['effectiveSource']
        result.update(autoSide=selection['autoSide'], selectionReason=selection['reason'], selection=selection)
        display_source = selection['requestedSource'] if selection['requestedSource'] in ('A', 'B') else selection['autoSide']
        if selected == 'none' and result.get('resumeSource') in ('A', 'B'):
            display_source = result['resumeSource']
        result['activeDeck'] = display_source if display_source in ('A', 'B') else 'A'
        result['speed'] = raw.get(result['activeDeck'], {}).get('speed', 0.0)
        result['playing'] = any(deck['playing'] for deck in raw.values())
    else:
        effective = result.get('activeDeck', 'A') if selected == 'auto' else selected
        result['selectionReason'] = '合成回放跟随当前盘；真实人声来源尚未核验'
    if effective not in raw or not valid.get(effective):
        effective = 'none'
    result['effectiveSource'] = effective
    result['stale'] = not valid or not any(valid.values())
    adjustment_key = effective if effective in ('A', 'B') else result.get('activeDeck', 'A')
    if effective == 'none':
        requested_key = result.get('resumeSource') if selected == 'none' else selected
        if requested_key in ('A', 'B'):
            adjustment_key = requested_key
    result['lyricAdjustmentDeck'] = adjustment_key
    result['lyricOffsetMs'] = result.get('lyricOffsets', {}).get(result['deck' + adjustment_key].get('versionId'), 0)
    if effective != 'none':
        deck = result['deck' + effective]
        offset = result.get('lyricOffsets', {}).get(deck['versionId'], 0)
        style = result.get('style', {})
        traditional = style.get('lineMode') == 'karaoke' and style.get('outputMode') != 'dynamic'
        timeline = timelines.get(deck['versionId'])
        duet = bool(result.get('duetColors')) and timeline is not None
        if duet:
            timeline = duet_view(timeline)
        result['lyric'] = project_lyrics(timeline, deck['positionMs'] - offset,
                                       traditional=traditional, sweep=style.get('sweepMode') != 'sentence')
        result['lyric']['singer'] = singer_at(timeline, deck['positionMs'] - offset) if duet else None
    else:
        result['lyric'] = project_lyrics(None, 0)
    if live:
        name = 'rekordbox' if rekordbox else 'Serato Video'
        result['status'] = (name + ' 状态过期 · 歌词已隐藏' if raw else result.get('bridgeStatus', '等待 ' + name)) if result['stale'] else name + ' 只读联动 · 音频人声来源未核验'
        if now > result.get('faderValidUntilNs', 0):
            result['videoFader'] = -1.0
    else:
        result['status'] = '回放桥数据过期 · 歌词已隐藏' if result['stale'] else '回放桥运行中 · 未连接真实 DJ'
    return result


class Controller(QObject):
    stateChanged = Signal()

    def __init__(self, root, runtime, input_mode=None):
        super().__init__()
        from app.lyric_paint import register_lyric_types
        register_lyric_types()
        self.root, self.runtime = Path(root), Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.clock = NativeClock(root)
        self.library = Library(self.runtime)
        self._state = initial_state()
        self._state.update(load_live_session(self.runtime, input_mode))
        self._state.update(input_mode_state(self._state['inputMode']))
        self._state['screenLayers'] = load_layers(self.runtime)
        self._state['screenContentLibrary'] = load_library(self.runtime)
        self._state['logoImageUrl'] = logo_url(self.runtime, self._state['screenLayers'])
        self.file_proofs = {}
        self.file_errors = {}
        self.failed_file_signatures = {}
        self.ax_frame = None
        self.ax_bindings = {}
        self.ax_binding_notes = {}
        self.ax_binding_requests = {}
        self.shutting_down = False
        self._offline_rows = []
        self._import_paths = []
        self._import_scheduled = False
        self.scanner = None
        self._planning_library = False
        self._planning_versions = []
        self._planning_tracks = []
        self._planning_failed = []
        self._planning_remaining = 0
        self.settings_path = self.runtime / 'settings.json'
        self.setlist = Setlist(self.runtime / 'setlist.json')
        self._setlist_loaded = frozenset()
        self._tap_undo = None
        self.showlog = ShowLog(self.runtime / 'show-log')
        self._logged_song = None
        self.inbox = GuestInbox()
        self.wish_relay = WishRelayClient(self, lambda: MAX_QUEUE - len(self._state['guestInbox']))
        self.wish_relay.changed.connect(self._guest_relay_changed)
        self.wish_relay.received.connect(self._receive_wishes)
        self._state['guestRelayConfigured'] = bool(self.wish_relay.origin)
        self.midi = MidiControl(self.runtime / 'midi.json')
        self._midi_ports = []
        self._message_timer = None
        self._state['style'] = traditional_style(self._state['style'])
        settings = {}
        try:
            settings = json.loads(self.settings_path.read_text())
            if not isinstance(settings, dict):
                settings = {}
            if settings.get('schemaVersion') == 1:
                self._state['style'] = dict(validate_style(settings['style']))
                if settings.get('scene') in ('amber', 'blue', 'black'):
                    self._state['scene'] = settings['scene']
        except (OSError, ValueError, TypeError, KeyError):
            pass
        for name, validator in (('lyricOffsets', validate_offsets), ('outputTarget', validate_output_target)):
            try:
                self._state[name] = validator(settings[name])
            except (OSError, ValueError, TypeError, KeyError):
                pass
        if settings.get('outputDestination') in ('standalone', 'serato_overlay'):
            self._state['outputDestination'] = settings['outputDestination']
        if self._state['mode'] in ('rekordbox', 'rekordbox_ax'):
            self._state['outputDestination'] = 'standalone'
        if type(settings.get('duetColors')) is bool:
            self._state['duetColors'] = settings['duetColors']
        self.refreshOutputScreens()
        from PySide6.QtGui import QGuiApplication
        application = QGuiApplication.instance()
        if isinstance(application, QGuiApplication):
            application.screenAdded.connect(self.refreshOutputScreens)
            application.screenRemoved.connect(self.refreshOutputScreens)
        self.raw = {}
        self.timelines = json.loads((self.root / 'fixtures/replay_timelines.json').read_text())
        for track in self.library.tracks():
            timeline = self.library.timeline(track['versionId'])
            if timeline:
                self.timelines[track['versionId']] = timeline
        self.output = None
        self._output_retries = 0
        self._output_retry_at = 0
        self._output_started_at = 0
        self.jobs = []
        self.index_queue = []
        self.index_pending = {}
        self.probe = None
        self.bridge = QProcess(self)
        self.bridge.setWorkingDirectory(str(root))
        self.bridge.readyReadStandardOutput.connect(self.read_bridge)
        self.bridge.errorOccurred.connect(lambda _: self.bridge_error())
        self.bridge.finished.connect(self.bridge_finished)
        from app.preparation import PreparationManager
        self.preparation = PreparationManager(self.root, self.runtime, self.library, self.timelines,
                                             self.preparation_changed, self.preparation_timeline)
        self.refresh_preparation()
        # Publish an empty, hidden projection before any reader or renderer can
        # see a previous process's program.json. Observations are always fresh.
        self.tick()
        self.start_bridge(self._state['mode'])
        if input_mode is not None:
            Controller.persist_live_session(self)
        self.refresh_library()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        if self.midi.port_name:
            QTimer.singleShot(500, self.midiRefresh)
        for path in self.library.interrupted_jobs():
            self.index_audio(path)
        try:
            pending = json.loads((self.runtime / 'library-import.json').read_text())
            if isinstance(pending, list):
                self.queue_audio_paths(pending)
        except (OSError, ValueError, TypeError):
            pass

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        return self._state

    @Property('QVariantList', constant=True)
    def stylePresets(self):
        from app.style_catalog import style_catalog
        return style_catalog()

    def notice(self, text):
        self._state['notice'] = text
        self.stateChanged.emit()

    def send(self, command):
        if self._state['mode'] == 'replay' and self._state.get('bridgeRunning', False) and self.bridge.state() == QProcess.ProcessState.Running:
            self.bridge.write((json.dumps(command, ensure_ascii=False) + '\n').encode())

    def bridge_error(self):
        if self.shutting_down:
            return
        self._state['bridgeStatus'] = '接入进程启动失败；请查看诊断'
        self._state['bridgeRunning'] = False
        self._state['readerConnected'] = False
        self.raw = {}
        self.notice(self._state['bridgeStatus'])

    def bridge_finished(self, *_):
        if self.shutting_down:
            return
        self._state['bridgeRunning'] = False
        self._state['readerConnected'] = False
        self.raw = {}
        self._state['bridgeStatus'] = '接入进程已停止'

    def start_bridge(self, mode):
        if self.bridge.isOpen():
            self.bridge.readAllStandardOutput()
            self.bridge.readAllStandardError()
        self._state['bridgeRunning'] = True
        self.bridge.setProgram(sys.executable)
        module = {'serato': 'probes.serato_reader', 'rekordbox': 'probes.rekordbox_live',
                  'rekordbox_ax': 'probes.rekordbox_ax'}.get(mode, 'app.replay_bridge')
        self.bridge.setArguments(['-m', module])
        if mode == 'rekordbox_ax':
            self.bridge.setArguments(['-m', module, '--application-host'])
        self.bridge.start()

    @Slot(str)
    def setInputMode(self, mode):
        if mode not in INPUT_MODES or mode == self._state['mode']:
            return
        self.stopBridge()
        if mode in ('rekordbox', 'rekordbox_ax') and self._state.get('outputDestination') == 'serato_overlay':
            self.closeOutput()
            self._state['outputDestination'] = 'standalone'
        self._state.update(input_mode_state(mode))
        self._state.update(bridgeEpoch=None, bridgeSequence=0, autoSide=None, videoFader=-1.0, faderValidUntilNs=0,
                           playing=False, loopEnabled=False, speed=1.0, readerConnected=False)
        self._state['deckA'], self._state['deckB'] = blank_deck('A'), blank_deck('B')
        self.ax_frame, self.ax_bindings, self.ax_binding_notes = None, {}, {}
        self.ax_binding_requests = {}
        if mode == 'rekordbox_ax':
            if self._state['selectedSource'] == 'auto':
                self._state['selectedSource'] = 'A'
            if self._state.get('resumeSource') == 'auto':
                self._state['resumeSource'] = 'A'
            self.refresh_ax_view()
        else:
            self._state['rekordboxAX'] = {}
        Controller.persist_live_session(self)
        self.start_bridge(mode)
        self.tick()

    def persist_live_session(self):
        try:
            save_live_session(self.runtime, self._state)
            if self._state.get('notice', '').startswith('现场状态未保存：'):
                self._state['notice'] = '现场状态已保存'
        except (OSError, ValueError) as exc:
            # A disk failure must never undo an emergency hide or manual take-over.
            self.notice('现场状态未保存：下次启动可能恢复旧设置；' + str(exc))

    def refresh_ax_view(self):
        observed = {}
        now = monotonic_ns()
        fresh = bool(self.ax_frame and self._state.get('bridgeRunning') and
                     self._state.get('readerConnected') and
                     self.ax_frame['capturedNs'] <= now <= self.ax_frame['validUntilNs'])
        if self.ax_frame:
            observed = {item['deck']: dict(item, available=item['available'] and fresh)
                        for item in self.ax_frame['decks']}
        bindings = {}
        for side in 'AB':
            binding = self.ax_bindings.get(side)
            bindings[side] = dict(binding, status='已确认当前文件') if binding else {
                'status': self.ax_binding_notes.get(side, '待确认当前歌曲')}
        candidates = [{'title': row['title'], 'path': row['path'], 'versionId': row['versionId']}
                      for row in self._offline_rows if row.get('path') and
                      row['versionId'] in self.timelines and not row.get('isDemo')]
        self._state['rekordboxAX'] = {'observed': observed, 'bindings': bindings, 'candidates': candidates}

    def read_ax_message(self, message):
        if message.get('type') == 'rekordbox_ax_status':
            self.raw = {}
            self._state.update(readerConnected=False, readerHasTracks=False, videoFader=-1.,
                               faderValidUntilNs=0, autoSide=None,
                               bridgeStatus=str(message.get('detail', '等待原版 rekordbox'))[:2000],
                               readerDiagnostics=message.get('diagnostics', {}))
            self.refresh_ax_view()
            return
        if not accept_ax_frame(self._state.get('bridgeEpoch'), self._state.get('bridgeSequence', 0), message):
            return
        if message['epoch'] != self._state.get('bridgeEpoch'):
            self.ax_bindings.clear()
            self.ax_binding_requests = {}
            self.ax_binding_notes = {side: '新会话，请确认当前歌曲' for side in 'AB'}
        for item in message['decks']:
            side = item['deck']
            binding = self.ax_bindings.get(side)
            if binding and (not item['available'] or item['observationId'] != binding['observationId']
                            or item['title'] != binding['title']):
                self.ax_bindings.pop(side, None)
                self.ax_binding_notes[side] = '曲目变化，请重新确认'
        self.ax_frame = message
        self.raw = adapt_ax_frame(message, self.ax_bindings, self.file_proofs)
        self._state.update(bridgeEpoch=message['epoch'], bridgeSequence=message['sequence'],
                           readerConnected=True, readerHasTracks=bool(self.raw),
                           videoFader=-1., faderValidUntilNs=0, autoSide=None,
                           bridgeStatus='已只读连接原版 rekordbox · 手动 A/B · 位置直读',
                           readerDiagnostics=message.get('diagnostics', {}))
        self.refresh_ax_view()

    @Slot(str, str)
    def bindRekordboxAX(self, side, version):
        if self._state['mode'] != 'rekordbox_ax' or side not in ('A', 'B'):
            return
        frame = self.ax_frame
        now = monotonic_ns()
        if not frame or not frame['capturedNs'] <= now <= frame['validUntilNs'] or not self._state.get('readerConnected'):
            self.notice('请等待原版 rekordbox 的新鲜盘位状态，再确认歌曲')
            return
        item = next(d for d in frame['decks'] if d['deck'] == side)
        track = self.library.get_track(version)
        timeline = self.timelines.get(version)
        if not item['available'] or not track or not timeline or track.get('isDemo'):
            self.notice('请在此盘加载歌曲，并选择已准备歌词的实际文件')
            return
        expected = frame['epoch'], item['observationId'], item['title']
        token = uuid.uuid4().hex
        if not hasattr(self, 'ax_binding_requests'):
            self.ax_binding_requests = {}
        self.ax_binding_requests[side] = token
        self.ax_bindings.pop(side, None)
        self.ax_binding_notes[side] = '正在核验所选文件'
        self.raw = adapt_ax_frame(frame, self.ax_bindings, self.file_proofs)
        self.tick()
        def failed(detail):
            if self.ax_binding_requests.get(side) != token:
                return
            self.ax_binding_requests.pop(side, None)
            self.ax_binding_notes[side] = str(detail)
            self.refresh_ax_view()
            self.notice(str(detail))
        def completed(proof):
            if self.ax_binding_requests.get(side) != token:
                return
            current = self.ax_frame
            if self._state['mode'] != 'rekordbox_ax' or not current or not self._state.get('readerConnected'):
                failed('接入已变化，请重新确认当前歌曲')
                return
            observed = next(d for d in current['decks'] if d['deck'] == side)
            now = monotonic_ns()
            if (current['epoch'], observed['observationId'], observed['title']) != expected or not current['capturedNs'] <= now <= current['validUntilNs']:
                failed('盘位已变化或观测过期，请重新确认')
                return
            try:
                if proof['versionId'] != version:
                    raise ValueError('所选文件内容已变化，请为新版本重新准备歌词')
                # An exact-hash cache may supply duration for compressed files.
                proof = dict(proof)
                if not proof.get('durationMs'):
                    proof['durationMs'] = timeline['durationMs']
                self.file_proofs[proof['path']] = proof
                self.ax_bindings[side] = make_ax_binding(current, side, proof)
                self.ax_binding_requests.pop(side, None)
                self.ax_binding_notes.pop(side, None)
                self.raw = adapt_ax_frame(current, self.ax_bindings, self.file_proofs)
                self.tick()
                self.notice('已确认 ' + side + ' 盘当前文件；换歌后请重新确认')
            except (ValueError, OSError, KeyError) as exc:
                failed(str(exc))
        self.index_audio(track['path'], completed, failed)

    @Slot(str)
    def clearRekordboxAXBinding(self, side):
        if side not in ('A', 'B'):
            return
        self.ax_bindings.pop(side, None)
        if hasattr(self, 'ax_binding_requests'):
            self.ax_binding_requests.pop(side, None)
        self.ax_binding_notes[side] = '已清除，请确认当前歌曲'
        if self._state['mode'] == 'rekordbox_ax' and self.ax_frame:
            self.raw = adapt_ax_frame(self.ax_frame, self.ax_bindings, self.file_proofs)
        self.tick()

    @Slot()
    def connectRekordboxAX(self):
        if self._state['mode'] != 'rekordbox_ax':
            self.setInputMode('rekordbox_ax')
            return
        self.stopBridge()
        if self.bridge.state() != QProcess.ProcessState.NotRunning:
            self.notice('旧接入进程未能停止，请稍后重试')
            return
        self.ax_frame, self.ax_bindings, self.ax_binding_notes = None, {}, {}
        self.ax_binding_requests = {}
        self._state.update(bridgeEpoch=None, bridgeSequence=0,
                           bridgeStatus='正在重新连接原版 rekordbox；新会话需确认歌曲文件')
        self.start_bridge('rekordbox_ax')
        self.tick()

    @Slot()
    def requestRekordboxAXAccess(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if getattr(self, 'ax_access_process', None) is None:
            process = QProcess(self)
            self.ax_access_process = process
            process.setWorkingDirectory(str(self.root))
            process.setProgram(sys.executable)
            process.setArguments(['-m', 'probes.rekordbox_ax', '--application-host', '--once', '--request-access'])
            def finished(*_):
                self.ax_access_process = None
                process.deleteLater()
            process.finished.connect(finished)
            process.start()
        QDesktopServices.openUrl(QUrl('x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'))
        from probes.rekordbox_ax import HELPER_APP
        self.notice('仅在读取被拒绝时允许“包房战神-rekordbox-AX”辅助功能；不需要改签 rekordbox。助手位于 ' + str(HELPER_APP))

    @Slot()
    def connectRekordbox(self):
        if self._state['mode'] != 'rekordbox':
            self.setInputMode('rekordbox')
            return
        self.stopBridge()
        if self.bridge.state() != QProcess.ProcessState.NotRunning:
            self.notice('旧接入进程未能停止，请稍后重试')
            return
        self.failed_file_signatures.clear()
        self.file_errors.clear()
        self._state.update(bridgeEpoch=None, bridgeSequence=0, readerDiagnostics={},
                           deckA=blank_deck('A'), deckB=blank_deck('B'),
                           bridgeStatus='正在重新连接 rekordbox，等待新的播放位置')
        self.start_bridge('rekordbox')
        self.tick()

    @Slot()
    def connectSerato(self):
        if self._state['mode'] != 'serato':
            self.setInputMode('serato')
            return
        # A reader can still be alive while its observations have stopped.
        # Stop/drain it before accepting any data from the new connection.
        self.stopBridge()
        if self.bridge.state() != QProcess.ProcessState.NotRunning:
            self._state['bridgeStatus'] = '旧接入进程未能停止，请稍后重试或查看诊断'
            self.notice(self._state['bridgeStatus'])
            self.tick()
            return
        self._state.update(bridgeEpoch=None, bridgeSequence=0, autoSide=None,
                           readerDiagnostics={}, deckA=blank_deck('A'), deckB=blank_deck('B'),
                           bridgeStatus='正在重新连接 Serato Video，等待新的播放状态')
        self.start_bridge('serato')
        self.tick()

    @Slot()
    def stopBridge(self):
        self._state['bridgeRunning'] = False
        if self.bridge.state() != QProcess.ProcessState.NotRunning:
            self.bridge.terminate()
            if not self.bridge.waitForFinished(500):
                self.bridge.kill()
                self.bridge.waitForFinished(500)
        if self.bridge.isOpen():
            self.bridge.readAllStandardOutput()
            self.bridge.readAllStandardError()
        self.raw = {}
        self._state.update(bridgeRunning=False, bridgeStatus='联动已停止', videoFader=-1.0,
                           faderValidUntilNs=0, autoSide=None, playing=False, readerConnected=False)
        self._state['lyric'] = project_lyrics(None, 0)
        self._state['stale'] = True
        self.stateChanged.emit()

    @Slot()
    def clearOutput(self):
        self.selectSource('none')
        Controller.log(self, 'hide')
        self.tick()
        if not self._state.get('notice', '').startswith('现场状态未保存：'):
            self.notice('已隐藏歌词；背景和只读联动继续运行')

    @Slot()
    def restoreOutput(self):
        self.selectSource(self._state.get('resumeSource', 'auto'))
        Controller.log(self, 'restore')
        self.tick()
        if not self._state.get('notice', '').startswith('现场状态未保存：'):
            self.notice('已恢复原歌词来源；来源就绪后显示歌词')

    def read_bridge(self):
        if not self._state.get('bridgeRunning', False):
            self.bridge.readAllStandardOutput()
            return
        while self.bridge.canReadLine():
            line = bytes(self.bridge.readLine())
            if len(line) > 65536:
                continue
            try:
                pending_paths = []
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError('接入消息必须是对象')
                if self._state['mode'] == 'rekordbox_ax':
                    self.read_ax_message(message)
                    continue
                if self._state['mode'] in ('serato', 'rekordbox', 'rekordbox_ax'):
                    rekordbox = self._state['mode'] == 'rekordbox'
                    if message.get('type') == ('rekordbox_status' if rekordbox else 'serato_status'):
                        self._state['bridgeStatus'] = str(message.get('detail', '等待播放状态'))[:2000]
                        self._state['readerDiagnostics'] = message.get('diagnostics', {})
                        if rekordbox:
                            self.raw = {}
                            self._state.update(videoFader=-1.0, faderValidUntilNs=0, autoSide=None, readerConnected=False)
                        continue
                    accept = accept_rekordbox_frame if rekordbox else accept_serato_frame
                    if not accept(self._state.get('bridgeEpoch'), self._state.get('bridgeSequence', 0), message):
                        continue
                    candidate_state = dict(self._state)
                    if message['epoch'] != self._state.get('bridgeEpoch'):
                        candidate_state['autoSide'] = None
                    candidate_raw = (adapt_rekordbox_frame if rekordbox else adapt_serato_frame)(message, self.file_proofs)
                    for deck in candidate_raw.values():
                        path = deck['path']
                        deck['errorText'] = self.file_errors.get(path, '')
                        if path and not deck['identityReady']:
                            try:
                                signature = file_signature(path)
                            except OSError:
                                signature = None
                            if path not in self.index_pending and (path not in self.failed_file_signatures or self.failed_file_signatures[path] != signature):
                                pending_paths.append(path)
                    fader = message['fader']
                    candidate_state.update(videoFader=fader['normalized'] if fader else -1.0,
                               faderValidUntilNs=message['validUntilNs'] if fader else 0,
                               faderLeftSource=fader['leftSource'] if rekordbox and fader else 'AB'[fader['left_deck_index']] if fader else 'A',
                               faderRightSource=fader['rightSource'] if rekordbox and fader else 'AB'[fader['right_deck_index']] if fader else 'B',
                               bridgeStatus='已只读连接 rekordbox · 位置直接跟随' if rekordbox else '已只读连接 Serato Video · 两份快照一致',
                               readerConnected=True,
                               readerHasTracks=bool(candidate_raw),
                               readerDiagnostics=message.get('diagnostics', {}))
                else:
                    if not accept_snapshot(self._state.get('bridgeEpoch'), self._state.get('bridgeSequence', 0), message):
                        continue
                    candidate_raw = message['decks']
                    candidate_state = dict(self._state)
                    for key in ('activeDeck', 'playing', 'speed', 'loopEnabled'):
                        candidate_state[key] = message[key]
                candidate_state['bridgeEpoch'] = message['epoch']
                candidate_state['bridgeSequence'] = message['sequence']
                self.raw, self._state = candidate_raw, candidate_state
                for path in pending_paths:
                    try:
                        self.index_audio(path)
                    except Exception as exc:
                        self.file_errors[path] = str(exc)
                        self.notice('已接受播放观测；文件核验排队失败：' + str(exc))
            except (ValueError, KeyError, TypeError) as exc:
                if self._state['mode'] in ('serato', 'rekordbox', 'rekordbox_ax'):
                    detail = str(exc)[:300]
                    self._state['bridgeStatus'] = '接入消息未通过接收校验：' + detail
                    self._state['readerDiagnostics'] = {'consumerStatus': 'rejected', 'detail': detail}
                self.notice('接入消息格式错误；已拒绝此消息')

    def tick(self):
        if self._state['mode'] == 'rekordbox_ax':
            self.refresh_ax_view()
        self._state = project_state(self._state, self.raw, self.timelines, self.clock, monotonic_ns())
        Controller.track_setlist(self)
        Controller.live_inputs(self)
        self.refresh_preparation()
        if self.output or self._state.get('outputRequested'):
            self.refresh_output()
        if self.output:
            self.refresh_overlay_status()
        self.stateChanged.emit()
        render_state = {key: value for key, value in self._state.items() if key not in (*STATE_COLLECTIONS, 'notice')}
        active_versions = {deck['versionId'] for deck in self.raw.values()}
        payload = {'state': render_state, 'raw': self.raw,
                   'timelines': {key: value for key, value in self.timelines.items() if key in active_versions}}
        temporary = self.runtime / 'program.tmp'
        temporary.write_text(json.dumps(payload, ensure_ascii=False))
        os.replace(temporary, self.runtime / 'program.json')

    def refresh_library(self):
        demo = [{'title': f'原创同步样例 {key}', 'versionId': f'demo:{key}', 'path': '内置合成回放',
                 'durationText': '02:00', 'durationMs': 120000, 'quality': '合成字时码',
                 'status': '用于验证同步；无音频', 'isDemo': True} for key in ('A', 'B')]
        tracks = self.library.offline_tracks()
        for track in tracks:
            track['durationText'] = stamp(track['durationMs'])[:5] if track.get('durationMs') else '待解析'
        self._offline_rows = tracks
        self._state['library'] = demo + tracks
        self.refresh_offline_library()

    def refresh_overlay_status(self):
        if not self.output or self._state.get('outputDestination') != 'serato_overlay':
            return
        try:
            path = self.runtime / 'serato-overlay-status.json'
            if path.stat().st_size > 16384:
                raise ValueError('叠加状态过大')
            status = json.loads(path.read_text())
            age = monotonic_ns() - status['capturedNs']
            if status.get('rendererPid') != self.output.pid or not 0 <= age <= 1_000_000_000:
                raise ValueError('叠加状态已过期')
            if status.get('phase') not in ('attached', 'waiting', 'covered', 'suspended', 'error', 'closed'):
                raise ValueError('叠加状态无效')
            self._state['overlayStatus'] = status
        except (OSError, ValueError, KeyError, TypeError):
            self._state['overlayStatus'] = {'phase': 'waiting', 'detail': '正在等待字幕叠加进程确认视频窗口'}

    def refresh_offline_library(self):
        manager = getattr(self, 'preparation', None)
        rows = [dict(track) for track in self._offline_rows]
        if manager:
            for offset in range(0, len(rows), 2):
                pair = {'AB'[i]: dict(track, identityVerified=bool(
                    self.file_proofs.get(track['path'], {}).get('versionId') == track['versionId'] and
                    proof_is_current(self.file_proofs.get(track['path']), track['path'])))
                        for i, track in enumerate(rows[offset:offset + 2])}
                views = manager.view(pair)['decks']
                for i, track in enumerate(rows[offset:offset + 2]):
                    value = views['AB'[i]]
                    track.update(canQuery=value['canQuery'], canCorrect=value['canCorrect'],
                                 canExport=value['canExport'], preparationPhase=value['phase'],
                                 preparationDetail=value['detail'])
        offline = bool(manager and getattr(manager, 'offline_only', False))
        duplicates = mark_duplicates(rows)
        self._state['offlineLibrary'] = {
            'offlineOnly': offline, 'cacheDirectory': str(self.library.cache_directory),
            'total': len(rows), 'ready': sum(bool(row.get('offlineReady')) for row in rows),
            'missing': sum(row.get('offlineState') == 'missing' for row in rows),
            'invalid': sum(row.get('offlineState') not in ('ready', 'missing') for row in rows),
            'review': sum(bool(row.get('needsReview')) for row in rows),
            'tracks': rows, 'batch': manager.batch_view() if manager and hasattr(manager, 'batch_view') else {},
            'indexingCount': len(self.index_pending) + len(self._import_paths) + bool(self.scanner),
            'planning': self._planning_library, 'duplicates': duplicates}
        Controller.refresh_setlist(self)

    def library_track(self, version, require_verified=True):
        track = self.library.get_track(version)
        if not track or not str(version).startswith('sha256:'):
            raise ValueError('请选择本地曲库中的真实音频版本')
        proof = self.file_proofs.get(track['path'])
        verified = bool(proof and proof.get('versionId') == version and proof_is_current(proof, track['path']))
        if verified:
            duration = track.get('durationMs')
            track.update(proof)
            if not track.get('durationMs') and duration:
                track['durationMs'] = duration
        track['identityVerified'] = verified
        if require_verified and not verified:
            raise ValueError('先核验离线曲库，确认歌曲文件版本后再准备')
        return track

    # ---- 演出记录 ----
    def log(self, kind, detail='', **extra):
        showlog = getattr(self, 'showlog', None)
        if showlog is None or self._state.get('mode') == 'replay' and kind == 'song' and str(detail).startswith('原创同步样例'):
            return
        try:
            showlog.add(kind, detail, **extra)
        except (OSError, ValueError):
            pass

    def live_inputs(self):
        """Per-tick: log songs reaching the screen, MIDI presses, guest wishes."""
        if getattr(self, 'showlog', None) is None:
            return
        effective = self._state.get('effectiveSource')
        if effective in ('A', 'B') and self._state.get('lyric', {}).get('visible'):
            deck = self._state['deck' + effective]
            version = deck.get('versionId')
            if version and version != self._logged_song:
                self._logged_song = version
                Controller.log(self, 'song', deck.get('title') or '未知歌曲', deck=effective)
        was_learning = self.midi.learning
        for action in self.midi.poll():
            self.midi_action(action)
        if was_learning and not self.midi.learning:
            self.refresh_midi(enumerate_ports=False)
            self.notice('MIDI 按键已记住')
        wishes = self.inbox.drain() if self.inbox.running else []
        if wishes:
            self._receive_wishes(wishes)

    def _receive_wishes(self, wishes):
        self._state['guestInbox'] = (self._state['guestInbox'] + wishes)[-MAX_QUEUE:]

    # ---- 客屏插字 ----
    def _save_screen_layers(self, candidate, text_seconds=None):
        try:
            candidate = validate_layers(candidate)
            now = monotonic_ns()
            deadline = self._state.get('screenTextValidUntilNs', 0)
            if text_seconds is None and deadline and deadline <= now:
                candidate['text']['visible'] = False
            if text_seconds is not None:
                if type(text_seconds) is not int or not 0 <= text_seconds <= 3600:
                    raise ValueError('显示时长须在 0–3600 秒之间')
                deadline = now + text_seconds * 1_000_000_000 if text_seconds else 0
            if not candidate['text']['visible'] or deadline <= now:
                deadline = 0
            persisted = copy.deepcopy(candidate)
            if deadline:
                persisted['text']['visible'] = False
            save_layers(self.runtime, persisted)
            self._state.update(screenLayers=candidate, logoImageUrl=logo_url(self.runtime, candidate),
                               screenLayerNotice='', screenTextValidUntilNs=deadline,
                               screenTextRemaining=max(0, (deadline-now+999_999_999)//1_000_000_000) if deadline else 0)
            if text_seconds is not None and candidate['text']['visible']:
                if getattr(self, '_message_timer', None):
                    self._message_timer.stop()
                self._state['guestMessage'] = {'text': '', 'visible': False}
                self._state['guestMessageValidUntilNs'] = 0
                self._state['guestMessageRemaining'] = 0
            self.tick()
            return True
        except (OSError, ValueError, TypeError) as exc:
            self._state['screenLayerNotice'] = '客屏图层未保存：' + str(exc)
            self.notice(self._state['screenLayerNotice'])
            return False

    @Slot(str, result=bool)
    def setScreenText(self, text):
        try:
            text = clean_layer_text(text)
            if not text:
                raise ValueError('先输入文字，再上屏')
            candidate = copy.deepcopy(self._state['screenLayers'])
            candidate['text'].update(text=text, visible=True)
            return self._save_screen_layers(candidate, text_seconds=0)
        except ValueError as exc:
            self._state['screenLayerNotice'] = str(exc)
            self.notice(str(exc))
            return False

    @Slot(str, str, 'QVariant', result=bool)
    def setScreenLayer(self, layer, key, value):
        allowed = {'text': ('visible', 'position', 'fontSize', 'opacity', 'color'),
                   'logo': ('visible', 'position', 'size', 'opacity')}
        if layer not in allowed or key not in allowed[layer]:
            return False
        candidate = copy.deepcopy(self._state['screenLayers'])
        candidate[layer][key] = value
        if layer == 'logo' and key == 'visible' and value and not logo_url(self.runtime, candidate):
            self._state['screenLayerNotice'] = 'DJ Logo 文件不可用，请重新导入'
            self.notice(self._state['screenLayerNotice'])
            return False
        return self._save_screen_layers(candidate, text_seconds=0 if layer == 'text' and key == 'visible' and value else None)

    @Slot(result=bool)
    @Slot(str, result=bool)
    def chooseScreenLogo(self, path=''):
        if not path:
            path, _ = QFileDialog.getOpenFileName(None, '选择 DJ Logo（推荐透明 PNG）', '', '图片 (*.png *.jpg *.jpeg *.webp)')
        if not path:
            return False
        try:
            image, name = import_logo(self.runtime, path)
            candidate = copy.deepcopy(self._state['screenLayers'])
            candidate['logo'].update(image=image, fileName=name, visible=True)
            return self._save_screen_layers(candidate)
        except (OSError, ValueError) as exc:
            self._state['screenLayerNotice'] = 'DJ Logo 未导入：' + str(exc)
            self.notice(self._state['screenLayerNotice'])
            return False

    @Slot(result=bool)
    def removeScreenLogo(self):
        candidate = copy.deepcopy(self._state['screenLayers'])
        candidate['logo'].update(image='', fileName='', visible=False)
        return self._save_screen_layers(candidate)

    @Slot(result='QVariantMap')
    @Slot(str, result='QVariantMap')
    def prepareScreenLogo(self, path=''):
        """Import an owned asset for local preview without changing output."""
        if not path:
            path, _ = QFileDialog.getOpenFileName(None, '选择 DJ Logo（先预览，再上屏）', '', '图片 (*.png *.jpg *.jpeg *.webp)')
        if not path:
            return {}
        if path.startswith('file:'):
            from PySide6.QtCore import QUrl
            path = QUrl(path).toLocalFile()
            if not path:
                return {}
        try:
            image, name = import_logo(self.runtime, path)
            candidate = default_layers()
            candidate['logo']['image'] = image
            self._state['screenLayerNotice'] = ''
            self.stateChanged.emit()
            return {'image': image, 'fileName': name, 'url': logo_url(self.runtime, candidate)}
        except (OSError, ValueError) as exc:
            self._state['screenLayerNotice'] = 'DJ Logo 未导入：' + str(exc)
            self.notice(self._state['screenLayerNotice'])
            return {}

    @Slot(str, 'QVariantMap', int, result=bool)
    def applyScreenContent(self, layer, row, seconds):
        if layer not in ('text', 'logo'):
            return False
        candidate = copy.deepcopy(self._state['screenLayers'])
        candidate[layer] = dict(row, visible=True)
        try:
            candidate = validate_layers(candidate)
            if layer == 'text' and ('{姓名}' in candidate['text']['text'] or '___' in candidate['text']['text']):
                raise ValueError('请先填写姓名，再上屏')
            if layer == 'logo' and not logo_url(self.runtime, candidate):
                raise ValueError('DJ Logo 文件不可用，请重新导入')
        except (ValueError, TypeError) as exc:
            self._state['screenLayerNotice'] = str(exc)
            self.notice(str(exc))
            return False
        success = self._save_screen_layers(candidate, text_seconds=seconds if layer == 'text' else None)
        if success:
            Controller.log(self, 'message' if layer == 'text' else 'logo', candidate[layer].get('text', candidate[layer].get('fileName', '')))
            if layer == 'text':
                Controller._record_screen_send(self, candidate['text'], seconds)
        return success

    def _save_screen_library(self, library):
        try:
            library = validate_library(library)
            persisted = copy.deepcopy(self._state['screenLayers'])
            if self._state.get('screenTextValidUntilNs', 0):
                persisted['text']['visible'] = False
            save_library(self.runtime, library, layers=persisted)
            self._state['screenContentLibrary'] = library
            self._state['screenLayerNotice'] = ''
            self.stateChanged.emit()
            return True
        except (OSError, ValueError, TypeError) as exc:
            self._state['screenLayerNotice'] = '常用语 / 历史未保存：' + str(exc)
            self.notice(self._state['screenLayerNotice'])
            return False

    def _record_screen_send(self, row, seconds, source='dj'):
        library = with_history(self._state['screenContentLibrary'], row, max(0, min(seconds, 3600)), source)
        if not Controller._save_screen_library(self, library):
            self._state['screenLayerNotice'] = '已上屏，但发送历史未保存；请勿重复发送'
            self.notice(self._state['screenLayerNotice'])

    @Slot(str, str, str, result=bool)
    def saveScreenPhrase(self, phrase_id, label, text):
        library = copy.deepcopy(self._state['screenContentLibrary'])
        if phrase_id and not any(row['id'] == phrase_id for row in library['phrases']):
            self.notice('常用语已不存在，请重新选择')
            return False
        row = {'id': phrase_id or uuid.uuid4().hex, 'label': label, 'text': text}
        if phrase_id:
            library['phrases'] = [row if value['id'] == phrase_id else value for value in library['phrases']]
        else:
            library['phrases'].insert(0, row)
        return Controller._save_screen_library(self, library)

    @Slot(str, result=bool)
    def deleteScreenPhrase(self, phrase_id):
        library = copy.deepcopy(self._state['screenContentLibrary'])
        library['phrases'] = [row for row in library['phrases'] if row['id'] != phrase_id]
        return Controller._save_screen_library(self, library)

    @Slot('QVariantMap', str, result=bool)
    def exportScreenProfile(self, layers, path):
        if not path:
            path, _ = QFileDialog.getSaveFileName(None, '导出 DJ 客屏配置（不含发送历史）', 'DJ客屏配置.bfdj.zip', 'DJ 配置 (*.bfdj.zip)')
        if not path:
            return False
        try:
            export_profile(self.runtime, layers, self._state['screenContentLibrary']['phrases'], path)
            self._state['screenLayerNotice'] = 'DJ 配置已导出：' + Path(path).name
            self.notice(self._state['screenLayerNotice'])
            return True
        except (OSError, ValueError, TypeError) as exc:
            self._state['screenLayerNotice'] = 'DJ 配置未导出：' + str(exc)
            self.notice(self._state['screenLayerNotice'])
            return False

    @Slot(str, result='QVariantMap')
    def prepareScreenProfile(self, path):
        if not path:
            path, _ = QFileDialog.getOpenFileName(None, '导入 DJ 配置，先预览再采用', '', 'DJ 配置 (*.bfdj.zip *.zip)')
        if not path:
            return {}
        try:
            result = prepare_profile(self.runtime, path)
            self._state['screenLayerNotice'] = ''
            self.stateChanged.emit()
            return result
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._state['screenLayerNotice'] = 'DJ 配置未导入：' + str(exc)
            self.notice(self._state['screenLayerNotice'])
            return {}

    @Slot('QVariantMap', 'QVariantList', result=bool)
    def applyScreenProfile(self, layers, phrases):
        try:
            from app.screen_library import portable_layers
            candidate = portable_layers(layers)
            if candidate['logo']['image'] and not logo_url(self.runtime, candidate):
                raise ValueError('配置中的 Logo 不可用，请重新导入')
            candidate['logo']['visible'] = bool(candidate['logo']['image'])
            library = validate_library(dict(self._state['screenContentLibrary'], phrases=list(phrases)))
            save_profile(self.runtime, candidate, library)
            self._state.update(screenLayers=candidate, screenContentLibrary=library, logoImageUrl=logo_url(self.runtime, candidate),
                               screenTextValidUntilNs=0, screenTextRemaining=0, screenLayerNotice='')
            self.clearGuestMessage()
            return True
        except (OSError, ValueError, TypeError) as exc:
            self._state['screenLayerNotice'] = 'DJ 配置未采用：' + str(exc)
            self.notice(self._state['screenLayerNotice'])
            return False

    def _withdraw_screen_content(self, layers):
        candidate = copy.deepcopy(self._state['screenLayers'])
        for layer in layers:
            candidate[layer]['visible'] = False
        try:
            persisted = copy.deepcopy(candidate)
            if self._state.get('screenTextValidUntilNs', 0):
                persisted['text']['visible'] = False
            save_layers(self.runtime, persisted)
            self._state['screenLayerNotice'] = ''
        except (OSError, ValueError) as exc:
            # Taking content down must still work if the settings disk fails.
            self._state['screenLayerNotice'] = '已撤下；设置未保存，重启后请核对标识：' + str(exc)
        self._state['screenLayers'] = candidate
        if 'text' in layers:
            self._state['screenTextValidUntilNs'] = 0
            self._state['screenTextRemaining'] = 0
            if getattr(self, '_message_timer', None):
                self._message_timer.stop()
            self._state['guestMessage'] = {'text': '', 'visible': False}
            self._state['guestMessageValidUntilNs'] = 0
            self._state['guestMessageRemaining'] = 0
        self.tick()
        return True

    @Slot(str, result=bool)
    def hideScreenContent(self, layer):
        if layer not in ('text', 'logo'):
            return False
        return self._withdraw_screen_content((layer,))

    @Slot(result=bool)
    def hideScreenExtras(self):
        qr = dict(self._state.get('guestQr', {}))
        qr['onScreen'] = False
        self._state['guestQr'] = qr
        return self._withdraw_screen_content(('text', 'logo'))

    @Slot(str, int, result=bool)
    def setGuestMessage(self, text, seconds):
        value = clean_text(text, 40)
        if not value:
            self.notice('客屏插字：先写一句话')
            return False
        if self._state['screenLayers']['text']['visible']:
            candidate = copy.deepcopy(self._state['screenLayers'])
            candidate['text']['visible'] = False
            if not self._save_screen_layers(candidate, text_seconds=0):
                return False
        self._state['guestMessage'] = {'text': value, 'visible': True}
        self._state['guestMessageValidUntilNs'] = monotonic_ns() + min(seconds, 3600) * 1_000_000_000 if seconds > 0 else 0
        Controller.log(self, 'message', value)
        if self._message_timer is None:
            self._message_timer = QTimer(self)
            self._message_timer.setSingleShot(True)
            self._message_timer.timeout.connect(self.clearGuestMessage)
        self._message_timer.stop()
        if seconds > 0:
            self._message_timer.start(min(seconds, 3600) * 1000)
        self.tick()
        row = dict(default_layers()['text'], text=value, position='top-center', fontSize=40)
        Controller._record_screen_send(self, row, min(max(seconds, 0), 3600), 'guest')
        return True

    @Slot()
    def clearGuestMessage(self):
        if self._message_timer is not None:
            self._message_timer.stop()
        if self._state.get('guestMessage', {}).get('visible'):
            Controller.log(self, 'message_clear')
        self._state['guestMessage'] = {'text': '', 'visible': False}
        self._state['guestMessageValidUntilNs'] = 0
        self._state['guestMessageRemaining'] = 0
        self.tick()

    # ---- 扫码送祝福 ----
    @Slot(bool)
    def setGuestServer(self, enabled):
        if self.wish_relay.active:
            return
        if not enabled:
            self.inbox.stop()
            self._state['guestQr'] = {'running': False, 'url': '', 'image': '', 'onScreen': False}
            self._state['guestInbox'] = []
            self.notice('扫码送祝福已关闭')
            self.tick()
            return
        try:
            url = self.inbox.start()
            image = write_qr(url, self.runtime / 'guest-qr.png')
            self._state['guestQr'] = {'running': True, 'url': url, 'onScreen': False,
                                      'image': image.as_uri() + '?' + self.inbox.token}
            self.notice('扫码送祝福已打开：同一局域网的手机可扫码；你同意后才上屏')
        except (OSError, ImportError, ValueError) as exc:
            self.inbox.stop()
            self.notice('扫码送祝福没打开：' + str(exc)[:160])
        self.tick()

    @Slot(bool)
    def setPublicGuestServer(self, enabled):
        if self.inbox.running:
            return
        if not enabled:
            self.wish_relay.stop()
            self._state['guestInbox'] = []
            self.tick()
        elif not self._state['offlineLibrary'].get('offlineOnly'):
            self.wish_relay.start()

    def _guest_relay_changed(self):
        relay = self.wish_relay
        qr = self._state['guestQr']
        if relay.phase == 'ready':
            if qr.get('url') != relay.url:
                try:
                    image = write_qr(relay.url, self.runtime / 'guest-qr.png', border=4)
                except (OSError, ImportError, ValueError):
                    relay.stop('error', '无法生成公网二维码，请重新开启')
                    return
                qr = {'url': relay.url, 'image': image.as_uri() + '?' + relay.room, 'onScreen': False}
            qr = dict(qr, mode='public', running=True, active=True)
        elif relay.active:
            qr = dict(qr, mode='public', running=False, active=True)
        else:
            qr = {'running': False, 'url': '', 'image': '', 'onScreen': False}
        self._state.update(guestQr=qr, guestRelayStatus=relay.detail)
        self.tick()

    @Slot(bool)
    def setGuestQrOnScreen(self, visible):
        qr = dict(self._state.get('guestQr', {}))
        if not qr.get('running'):
            return
        qr['onScreen'] = bool(visible)
        self._state['guestQr'] = qr
        self.tick()

    def take_wish(self, wish_id):
        inbox = list(self._state.get('guestInbox', []))
        found = next((row for row in inbox if row.get('id') == wish_id), None)
        self._state['guestInbox'] = [row for row in inbox if row.get('id') != wish_id]
        return found

    @Slot(str)
    def approveGuestWish(self, wish_id):
        wish = next((row for row in self._state.get('guestInbox', []) if row.get('id') == wish_id), None)
        if wish and self.setGuestMessage((wish['name'] + '：' if wish.get('name') else '') + wish['text'], 15):
            self.take_wish(wish_id)
            self.tick()

    @Slot(str)
    def rejectGuestWish(self, wish_id):
        self.take_wish(wish_id)
        self.stateChanged.emit()

    # ---- 对唱分色 ----
    @Slot(bool)
    def setDuetColors(self, enabled):
        previous = self._state.get('duetColors', False)
        self._state['duetColors'] = bool(enabled)
        try:
            self.save_settings()
        except OSError as exc:
            self._state['duetColors'] = previous
            self.notice('对唱分色未保存：' + str(exc))
            return
        self.tick()
        self.notice('对唱分色已打开：按歌词里的「男：女：合：」标记上色' if enabled else '对唱分色已关闭')

    # ---- MIDI ----
    def refresh_midi(self, enumerate_ports=True):
        if enumerate_ports:
            self._midi_ports = input_names()
        self._state['midi'] = self.midi.view(self._midi_ports)
        self.stateChanged.emit()

    def midi_action(self, action):
        if action == 'toggleLyrics':
            self.restoreOutput() if self._state.get('selectedSource') == 'none' else self.clearOutput()
        elif action == 'tap':
            self.tapLyricSync()
        elif action in ('sourceA', 'sourceB'):
            self.selectSource(action[-1])
        elif action in ('earlier', 'later'):
            offset = int(self._state.get('lyricOffsetMs', 0)) + (-100 if action == 'earlier' else 100)
            if -3000 <= offset <= 3000:
                self.setLyricOffset(offset)
        elif action == 'clearMessage':
            self.clearGuestMessage()

    @Slot()
    def midiRefresh(self):
        self.refresh_midi()
        if self.midi.port_name and self.midi.port is None and self.midi.port_name in self._midi_ports:
            self.midi.open(self.midi.port_name)
            self.refresh_midi()

    @Slot(str)
    def midiSelectPort(self, name):
        if self.midi.open(name):
            self.notice('MIDI 已连接：' + name)
        elif name:
            self.notice('MIDI：' + self.midi.error)
        self.refresh_midi()

    @Slot(str)
    def midiLearn(self, action):
        try:
            self.midi.learn(action)
        except ValueError as exc:
            self.notice('MIDI：' + str(exc))
        self.refresh_midi(enumerate_ports=False)

    @Slot(str)
    def midiClear(self, action):
        self.midi.clear(action)
        self.refresh_midi(enumerate_ports=False)

    # ---- 演出记录查看 ----
    @Slot(str)
    def refreshShowLog(self, day):
        days = self.showlog.days()
        day = day if day in days else (days[0] if days else '')
        rows = self.showlog.read(day) if day else []
        self._state['showLog'] = {'day': day, 'days': days, 'rows': rows[::-1][:500],
                                  'summary': self.showlog.summary(rows)}
        self.stateChanged.emit()

    # ---- 开场自检 ----
    # 直接返回给弹窗，不放进 state：state 每 33ms 刷新一次，放进去会让清单每帧重建、按钮点不中。
    @Slot(result='QVariantMap')
    def preshowReport(self):
        return preshow_report(self._state, screen_options(), lan_address())

    @Slot(str)
    def exportShowLog(self, day):
        if not day:
            self.notice('还没有演出记录')
            return
        path, _ = QFileDialog.getSaveFileName(None, '导出演出记录', str(Path.home() / f'包房战神演出记录-{day}.csv'), 'CSV (*.csv)')
        if path:
            try:
                count = self.showlog.export_csv(day, path if path.endswith('.csv') else path + '.csv')
                self.notice(f'演出记录已导出：{count} 条')
            except OSError as exc:
                self.notice('演出记录未导出：' + str(exc))

    # ---- 拖入导入 ----
    @Slot('QVariantList')
    def importDroppedPaths(self, urls):
        from PySide6.QtCore import QUrl
        from app.storage import AUDIO_EXTENSIONS
        paths, truncated = [], False
        for value in list(urls)[:10000]:
            local = QUrl(value).toLocalFile() if not isinstance(value, QUrl) else value.toLocalFile()
            if not local:
                continue
            item = Path(local)
            if item.is_dir():
                for child in sorted(item.rglob('*')):
                    if len(paths) >= 10000:
                        truncated = True
                        break
                    if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS and not child.name.startswith('.'):
                        paths.append(str(child))
            elif item.is_file():
                paths.append(str(item))
        if not paths:
            self.notice('拖进来的东西里没有音频')
            return
        try:
            self.queue_audio_paths(paths)
        except ValueError as exc:
            self.notice('导入未完成：' + str(exc))
            return
        if truncated:
            self.notice('文件太多，只加入了前 10000 个音频，请分批拖入')

    # ---- 搬家包 ----
    @Slot()
    def exportMovePack(self):
        from app.move_pack import export_pack
        folder = QFileDialog.getExistingDirectory(None, '选择放搬家包的文件夹（不复制音乐和 Key）')
        if folder:
            try:
                report = export_pack(self.library, self.runtime, folder)
                self.notice(f'搬家包已导出：歌词 {report["lyrics"]} 首、设置 {report["settings"]} 份、演出记录 {report["logs"]} 天')
            except (OSError, ValueError) as exc:
                self.notice('搬家包未导出：' + str(exc))

    @Slot()
    def importMovePack(self):
        folder = QFileDialog.getExistingDirectory(None, '选择包房战神搬家包文件夹')
        if folder:
            self.import_move_pack(folder)

    def import_move_pack(self, folder):
        from app.move_pack import import_logs, read_pack
        try:
            origin, settings = read_pack(folder)
            lyrics = self.library.import_cache(origin / '歌词库') if (origin / '歌词库' / 'manifest.json').is_file() else {'imported': 0}
            for track in self.library.tracks():
                timeline = self.library.timeline(track['versionId'])
                if timeline:
                    self.timelines[track['versionId']] = timeline
            restored = []
            values = settings.get('settings.json')
            if isinstance(values, dict):
                if isinstance(values.get('style'), dict):
                    self._state['style'] = dict(validate_style(values['style']))
                merged = dict(self._state.get('lyricOffsets', {}))
                merged.update(values.get('lyricOffsets') or {})
                self._state['lyricOffsets'] = validate_offsets(dict(list(merged.items())[:MAX_OFFSET_TRACKS]))
                if type(values.get('duetColors')) is bool:
                    self._state['duetColors'] = values['duetColors']
                self.save_settings()
                restored.append('设置')
            if isinstance(settings.get('setlist.json'), dict):
                (self.runtime / 'setlist.json').write_text(json.dumps(settings['setlist.json'], ensure_ascii=False), encoding='utf-8')
                self.setlist = Setlist(self.runtime / 'setlist.json')
                restored.append('歌单')
            if isinstance(settings.get('midi.json'), dict):
                (self.runtime / 'midi.json').write_text(json.dumps(settings['midi.json'], ensure_ascii=False), encoding='utf-8')
                self.midi.close()
                self.midi = MidiControl(self.runtime / 'midi.json')
                restored.append('MIDI')
            logs = import_logs(origin, self.runtime)
            self.refresh_library()
            self.tick()
            self.notice(f'搬家包已导入：歌词 {lyrics.get("imported", 0)} 首' + ('、' + '、'.join(restored) if restored else '') +
                        f'、演出记录 {logs} 天。请在曲库点「核验曲库」确认音乐文件。')
            return True
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.notice('搬家包未导入：' + str(exc))
            return False

    # ---- 今晚歌单 ----
    def loaded_versions(self):
        return frozenset(deck['versionId'] for deck in self.raw.values() if deck.get('versionId'))

    def refresh_setlist(self):
        setlist = getattr(self, 'setlist', None)
        if setlist is not None:
            self._state['setlist'] = setlist.view(self._offline_rows, self.loaded_versions())

    def track_setlist(self):
        """Mark a set-list song played once its lyrics are on the guest screen."""
        setlist = getattr(self, 'setlist', None)
        if setlist is None:
            return
        changed = False
        effective = self._state.get('effectiveSource')
        if effective in ('A', 'B') and self._state.get('lyric', {}).get('visible'):
            version = self._state['deck' + effective].get('versionId')
            try:
                changed = bool(version) and setlist.mark_played([version])
            except OSError:
                changed = False
        loaded = self.loaded_versions()
        if changed or loaded != self._setlist_loaded:
            self._setlist_loaded = loaded
            self.refresh_setlist()

    def setlist_action(self, operation, *arguments):
        try:
            result = getattr(self.setlist, operation)(*arguments)
            self.refresh_setlist()
            self.stateChanged.emit()
            return result
        except (OSError, ValueError, TypeError) as exc:
            self.notice('歌单未保存：' + str(exc))

    @Slot(str)
    def addToSetlist(self, version):
        if str(version).startswith('demo:'):
            self.notice('演示样例不能加入歌单')
            return
        if not self.library.get_track(version):
            self.notice('歌单未保存：请选择曲库里的歌曲')
            return
        added = self.setlist_action('add', version)
        if added is True:
            self.notice('已加入今晚歌单')
        elif added is False:
            self.notice('这首已经在歌单里')

    @Slot(int)
    def removeFromSetlist(self, index):
        self.setlist_action('remove', index)

    @Slot(int, int)
    def moveSetlistItem(self, index, delta):
        if delta in (-1, 1):
            self.setlist_action('move', index, delta)

    @Slot(int, bool)
    def setSetlistPlayed(self, index, played):
        self.setlist_action('set_played', index, played)

    @Slot(bool)
    def clearSetlist(self, played_only):
        self.setlist_action('clear', played_only)
        self.notice('已清掉放过的歌' if played_only else '歌单已清空')

    @Slot(str)
    def loadSetlistNext(self, key):
        upcoming = (self._state.get('setlist') or {}).get('next')
        if not upcoming:
            self.notice('歌单里没有下一首')
            return
        self.loadTrack(upcoming['versionId'], key)

    # ---- 打拍校准 ----
    @Slot()
    def tapLyricSync(self):
        key = self._state.get('lyricAdjustmentDeck', self._state.get('activeDeck', 'A'))
        deck = self._state.get('deck' + key, {})
        raw = self.raw.get(key)
        version = deck.get('versionId')
        if self._state.get('stale') or not raw or not deck.get('valid') or deck.get('cacheState') != 'ready' or not version:
            self.notice('同步微调未保存：歌曲在播、歌词就绪后才能打拍')
            return
        position, status = self.clock.estimate(raw, monotonic_ns())
        if status != 0:
            self.notice('同步微调未保存：播放位置不可用')
            return
        style = self._state.get('style', {})
        traditional = style.get('lineMode') == 'karaoke' and style.get('outputMode') != 'dynamic'
        current = self._state.get('lyricOffsets', {}).get(version, 0)
        found = tap_offset(self.timelines.get(version), position, current, traditional=traditional)
        if not found:
            self.notice('附近没有句子开头，请在一句刚开唱时再按')
            return
        offset, _ = found
        self.setLyricOffset(offset)
        if self._state.get('lyricOffsets', {}).get(version, 0) == offset:
            self._tap_undo = (version, current)
            self._state['lyricTap'] = {'canUndo': True}
            change = offset - current
            Controller.log(self, 'tap', f'{offset} ms')
            self.notice(f'打拍已对齐：{"+" if offset > 0 else ""}{offset} ms（这次 {"+" if change > 0 else ""}{change} ms）')

    @Slot()
    def undoLyricTap(self):
        if not self._tap_undo:
            return
        version, previous = self._tap_undo
        key = self._state.get('lyricAdjustmentDeck', 'A')
        if self._state.get('deck' + key, {}).get('versionId') != version:
            self.notice('已换歌，撤销不了上一次打拍')
            self._tap_undo, self._state['lyricTap'] = None, {'canUndo': False}
            return
        self.setLyricOffset(previous)
        self._tap_undo, self._state['lyricTap'] = None, {'canUndo': False}
        self.stateChanged.emit()

    @Slot(bool)
    def setOfflineOnly(self, enabled):
        self.preparation_action('set_offline_only', enabled)
        self.refresh_offline_library()
        if enabled and self.wish_relay.active:
            self.wish_relay.stop('idle', '离线演出已开启，公网扫码已关闭')
        self.notice('离线演出已开启：云请求已停止，歌词只从本机缓存读取' if enabled else
                    '已退出离线演出；新的云识别仍需审阅计划并手动启动')

    @Slot()
    def importAudioBatch(self):
        paths, _ = QFileDialog.getOpenFileNames(None, '导入需要提前准备的曲库音频', '',
                      '音频 (*.wav *.mp3 *.flac *.m4a *.aiff *.aif *.ogg)')
        if paths:
            self.queue_audio_paths(paths)

    def _save_import_queue(self):
        path = self.runtime / 'library-import.json'
        if self._import_paths:
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self._import_paths, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, path)
        else:
            path.unlink(missing_ok=True)

    def queue_audio_paths(self, paths):
        from app.storage import AUDIO_EXTENSIONS
        if not isinstance(paths, list) or len(paths) > 10000:
            raise ValueError('每批最多导入 10000 个音频文件')
        seen = set(self._import_paths) | set(self.index_pending)
        added = 0
        for raw in paths:
            if not isinstance(raw, str) or len(raw) > 8192:
                continue
            path = str(Path(raw).expanduser().resolve())
            if path in seen or Path(path).suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            self._import_paths.append(path)
            seen.add(path)
            added += 1
        self._save_import_queue()
        if self._import_paths and not self._import_scheduled:
            self._import_scheduled = True
            QTimer.singleShot(0, self._enqueue_next_import)
        self.refresh_offline_library()
        self.notice(f'已加入 {added} 个音频到后台核验队列；未提交云识别')

    def _enqueue_next_import(self):
        if self.shutting_down:
            return
        for _ in range(min(16, len(self._import_paths))):
            path = self._import_paths.pop(0)
            try:
                self.index_audio(path)
            except (OSError, ValueError) as exc:
                self.file_errors[path] = str(exc)
        if self._import_paths:
            QTimer.singleShot(20, self._enqueue_next_import)
        else:
            self._import_scheduled = False
            self._save_import_queue()
        self.refresh_offline_library()
        self.stateChanged.emit()

    @Slot()
    def importAudioFolder(self):
        folder = QFileDialog.getExistingDirectory(None, '选择要提前识别的音频文件夹（包含子文件夹）')
        if not folder:
            return
        if self.scanner:
            self.notice('已有文件夹正在扫描，请等待完成')
            return
        process = QProcess(self)
        self.scanner = process
        process.setWorkingDirectory(str(self.root))
        process.setProgram(sys.executable)
        process.setArguments(['-m', 'worker.scan_audio', folder])
        def finished(*_):
            if self.shutting_down:
                return
            self.scanner = None
            try:
                body = bytes(process.readAllStandardOutput())
                if len(body) > 8_000_000:
                    raise ValueError('扫描结果过大，请按子文件夹分批导入')
                value = json.loads(body)
                if not value.get('ok'):
                    raise ValueError(value.get('error', '文件夹扫描未完成'))
                self.queue_audio_paths(value['paths'])
                if value.get('truncated') or value.get('errors'):
                    self.notice('文件夹未完整扫描，已加入找到的音频；请分批补齐或检查访问权限')
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.notice('文件夹导入：' + str(exc))
            process.deleteLater()
            self.refresh_offline_library()
        process.finished.connect(finished)
        process.start()
        self.refresh_offline_library()
        self.notice('后台正在扫描所选曲库文件夹；不读取音频内容或提交云任务')

    @Slot()
    def verifyOfflineLibrary(self):
        self.queue_audio_paths([track['path'] for track in self._offline_rows])

    @Slot()
    def planLibraryBatch(self):
        if self._planning_library:
            self.notice('正在核验批次歌曲，完成后会生成计划')
            return
        if self._import_paths or self.index_pending or self.scanner:
            self.notice('请等待曲库导入与核验完成，再审阅批量计划')
            return
        rows = [dict(row) for row in self._offline_rows]
        if not rows:
            self.notice('先导入需要提前准备的音频文件或文件夹')
            return
        self._planning_library = True
        self._planning_tracks, self._planning_failed = [], []
        self._planning_remaining = len(rows)
        def completed(current, expected):
            if current['versionId'] != expected:
                self._planning_failed.append('歌曲文件已变化，请重新审阅当前曲库')
            else:
                self._planning_tracks.append(dict(current, identityVerified=True))
            self._finish_library_identity()
        def failed(message):
            self._planning_failed.append(message)
            self._finish_library_identity()
        for track in rows:
            try:
                current = self.library_track(track['versionId'], require_verified=False)
                if current['identityVerified']:
                    completed(current, track['versionId'])
                else:
                    self.index_audio(track['path'],
                                     lambda value, expected=track['versionId']: completed(value, expected), failed)
            except (OSError, ValueError) as exc:
                failed(str(exc))
        self.refresh_offline_library()
        self.stateChanged.emit()

    def _finish_library_identity(self):
        self._planning_remaining -= 1
        if self._planning_remaining:
            return
        self._planning_library = False
        if self._planning_failed:
            self.notice(f'有 {len(self._planning_failed)} 首文件核验未通过；补齐或重新审阅曲库后再生成批量计划')
        else:
            self.preparation_action('plan_batch', self._planning_tracks)
        self.refresh_offline_library()
        self.stateChanged.emit()

    @Slot(str)
    def startLibraryBatch(self, batch_id):
        if self._planning_library or self.index_pending or self._import_paths or self.scanner:
            self.notice('文件核验仍在进行，暂不启动批量识别')
            return
        self.preparation_action('start_batch', batch_id)

    @Slot()
    def pauseLibraryBatch(self):
        self.preparation_action('pause_batch')

    @Slot(str)
    def planLibraryTrack(self, version):
        try:
            track = self.library_track(version, require_verified=False)
            if track['identityVerified']:
                self.preparation_action('plan_batch', [track])
            else:
                self.index_audio(track['path'], lambda current:
                    self.preparation_action('plan_batch', [dict(current, identityVerified=True)])
                    if current['versionId'] == version else self.notice('文件版本已变化，请为新曲目重新准备'))
        except (OSError, ValueError) as exc:
            self.notice(str(exc))

    @Slot(str)
    def queryLibraryTrack(self, version):
        try:
            self.preparation_action('query', '', self.library_track(version))
        except (OSError, ValueError) as exc:
            self.notice(str(exc))

    @Slot(str)
    def chooseLibraryReference(self, version):
        try:
            track = self.library_track(version)
            path, _ = QFileDialog.getOpenFileName(None, '选择该音频版本的 UTF-8 参考歌词', '', '歌词文本 (*.txt)')
            if path:
                self.preparation_action('correct', '', self.library_track(version), path)
        except (OSError, ValueError) as exc:
            self.notice(str(exc))

    @Slot(str, str)
    def exportLibraryLyrics(self, version, subtitle_format):
        try:
            track = self.library_track(version)
            if subtitle_format not in ('srt', 'lrc'):
                raise ValueError('字幕导出仅支持 SRT/LRC')
            path, _ = QFileDialog.getSaveFileName(None, '导出本地缓存歌词',
                     str(self.runtime / (Path(track['path']).stem + '.' + subtitle_format)),
                     subtitle_format.upper() + ' (*.' + subtitle_format + ')')
            if path:
                destination = Path(path)
                if not destination.suffix:
                    destination = destination.with_suffix('.' + subtitle_format)
                self.preparation_action('export', '', self.library_track(version), str(destination), subtitle_format)
        except (OSError, ValueError) as exc:
            self.notice(str(exc))

    @Slot()
    def exportLyricCache(self):
        folder = QFileDialog.getExistingDirectory(None, '选择歌词库备份目标文件夹（不复制音乐）')
        if folder:
            try:
                value = self.library.export_cache(folder)
                self.notice(f'歌词文件库已备份：{value.get("exported", 0)} 首；未复制音乐或密钥')
            except (OSError, ValueError) as exc:
                self.notice('备份未完成：' + str(exc))

    @Slot()
    def importLyricCache(self):
        folder = QFileDialog.getExistingDirectory(None, '选择歌词库备份文件夹')
        if folder:
            try:
                value = self.library.import_cache(folder)
                for track in self.library.tracks():
                    timeline = self.library.timeline(track['versionId'])
                    if timeline:
                        self.timelines[track['versionId']] = timeline
                self.refresh_library()
                self.notice(f'歌词库已恢复：{value.get("imported", 0)} 首，拒绝 {value.get("rejected", 0)} 项；按音频 SHA256 匹配')
            except (OSError, ValueError) as exc:
                self.notice('恢复未完成：' + str(exc))

    def refresh_preparation(self):
        manager = getattr(self, 'preparation', None)
        if manager is not None:
            decks = {key: dict(self.raw.get(key) or self._state.get('deck' + key) or {}) for key in ('A', 'B')}
            self._state['preparation'] = manager.view(decks)

    def preparation_changed(self):
        if self.shutting_down:
            return
        self.refresh_preparation()
        self.refresh_library()
        self.stateChanged.emit()

    def preparation_timeline(self, version, timeline):
        self.timelines[version] = timeline
        self.refresh_library()
        self.preparation_changed()

    def recognition_track(self, key, require_verified=True):
        if key not in ('A', 'B'):
            raise ValueError('识别盘必须为 A 或 B')
        deck = dict(self.raw.get(key) or self._state.get('deck' + key) or {})
        path, version = deck.get('path', ''), deck.get('versionId', '')
        proof = self.file_proofs.get(path)
        verified = bool(proof and proof.get('versionId') == version and proof_is_current(proof, path))
        if verified:
            track = self.library.get_track(version) or {}
            deck.update(track)
            deck.update(proof)
        deck['identityVerified'] = verified
        if require_verified and not verified:
            raise ValueError('当前盘实际音频尚未核验，或文件已变化；先完成版本核验再继续准备')
        return deck

    def preparation_action(self, operation, *arguments):
        try:
            result = getattr(self.preparation, operation)(*arguments)
            self.refresh_preparation()
            self.stateChanged.emit()
            return result
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.notice('歌曲准备：' + str(exc))

    @Slot()
    def choosePreparationConfig(self):
        path, _ = QFileDialog.getOpenFileName(None, '选择本软件云端服务配置，不读取其他应用密钥', '', '服务配置 (*.json)')
        if path:
            self.preparation_action('configure_file', path)

    @Slot(str)
    def setRecognitionRoute(self, route):
        self.preparation_action('set_route', route)

    @Slot(float)
    def setRecognitionBudget(self, value):
        self.preparation_action('set_budget', value)

    @Slot(bool)
    def setNewSubmissionEnabled(self, enabled):
        self.preparation_action('set_new_submission_enabled', enabled)

    @Slot(str)
    def planRecognition(self, key):
        try:
            self.preparation_action('plan', key, self.recognition_track(key, require_verified=False))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.notice('歌曲准备：' + str(exc))

    @Slot(str, str)
    def startRecognition(self, key, plan_id):
        try:
            self.preparation_action('start', key, self.recognition_track(key), plan_id)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.notice('歌曲准备：' + str(exc))

    @Slot(str)
    def queryRecognition(self, key):
        try:
            self.preparation_action('query', key, self.recognition_track(key))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.notice('歌曲准备：' + str(exc))

    def bindLocalSubtitles(self, path, track, deck_key=None):
        version = track['versionId']
        def verified(current):
            try:
                if current['versionId'] != version:
                    raise ValueError('歌曲文件已变化，请为新的音频版本重新导入字幕')
                if deck_key and self.recognition_track(deck_key)['versionId'] != version:
                    raise ValueError('导入字幕时当前盘已切歌，请重新选择')
                from app.local_subtitles import load_local_timeline
                payload = load_local_timeline(path, current)
                if deck_key and self.recognition_track(deck_key)['versionId'] != version:
                    raise ValueError('导入字幕时当前盘已切歌，请重新选择')
                self.library.add_timeline(payload, current)
                self.timelines[version] = payload
                manager = getattr(self, 'preparation', None)
                if manager:
                    manager.reports[version] = {'phase': 'ready', 'detail': '已导入本地字幕，保留原时码；同步准确度待复核'}
                self.refresh_library()
                self.refresh_preparation()
                self.stateChanged.emit()
                self.notice('已绑定本地字幕：' + Path(path).name + '；原有时码已保留')
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.notice('本地字幕：' + str(exc))
        self.index_audio(track['path'], verified)

    @Slot(str)
    def chooseLocalSubtitles(self, key):
        try:
            track = self.recognition_track(key)
            path, _ = QFileDialog.getOpenFileName(None, '为 ' + track['title'] + ' 导入本地字幕',
                         str(Path(track['path']).parent), '本地字幕 (*.srt *.lrc *.json)')
            if path:
                if self.recognition_track(key)['versionId'] != track['versionId']:
                    raise ValueError('选取字幕时当前盘已切歌，请重新选择')
                self.bindLocalSubtitles(path, track, key)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.notice('本地字幕：' + str(exc))

    @Slot(str)
    def chooseReferenceLyrics(self, key):
        try:
            track = self.recognition_track(key)
            path, _ = QFileDialog.getOpenFileName(None, '选择 UTF-8 参考歌词进行本机纠错', '', '歌词文本 (*.txt);;所有文件 (*)')
            if path:
                current = self.recognition_track(key)
                if current.get('versionId') != track.get('versionId'):
                    raise ValueError('选取参考时当前盘已切歌，请为新的音频版本重新选择')
                self.preparation_action('correct', key, current, path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.notice('歌曲准备：' + str(exc))

    @Slot(str, str)
    def exportLyrics(self, key, subtitle_format):
        try:
            if subtitle_format not in ('lrc', 'srt'):
                raise ValueError('字幕导出仅支持 LRC 或 SRT')
            track = self.recognition_track(key)
            suggested = self.runtime / (Path(track['path']).stem + '.' + subtitle_format)
            path, _ = QFileDialog.getSaveFileName(None, '导出当前音频版本歌词', str(suggested), subtitle_format.upper() + ' (*.' + subtitle_format + ')')
            if path:
                current = self.recognition_track(key)
                if current.get('versionId') != track.get('versionId'):
                    raise ValueError('选择导出位置时当前盘已切歌，请重新导出')
                destination = Path(path)
                if not destination.suffix:
                    destination = destination.with_suffix('.' + subtitle_format)
                result = self.preparation_action('export', key, current, str(destination), subtitle_format)
                if result:
                    self.notice('歌词已导出：' + str(result))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.notice('歌曲准备：' + str(exc))

    @Slot()
    def togglePlayback(self):
        self.send({'type': 'toggle'})

    @Slot(float)
    def seek(self, seconds):
        self.send({'type': 'seek', 'seconds': seconds})

    @Slot(float)
    def setSpeed(self, value):
        self.send({'type': 'speed', 'speed': value})

    @Slot(str)
    def selectSource(self, value):
        if value == 'auto' and self._state['mode'] == 'rekordbox_ax':
            self.notice('原版跟随当前使用手动 A / B，请指定歌词来源')
            return
        if value in ('auto', 'A', 'B', 'none'):
            if value == 'none' and self._state['selectedSource'] != 'none':
                self._state['resumeSource'] = self._state['selectedSource']
            elif value != 'none':
                self._state['resumeSource'] = value
            changed = self._state['selectedSource'] != value
            self._state['selectedSource'] = value
            Controller.persist_live_session(self)
            if changed and value != 'none':
                Controller.log(self, 'source', {'auto': '自动跟随', 'A': '指定 A 盘', 'B': '指定 B 盘'}[value])
            self.tick()

    @Slot(int)
    def setLyricOffset(self, milliseconds):
        key = self._state.get('lyricAdjustmentDeck', self._state.get('activeDeck', 'A'))
        deck = self._state.get('deck' + key, {})
        version = deck.get('versionId')
        if self._state.get('stale') or not deck.get('valid') or deck.get('cacheState') != 'ready' or not version:
            self.notice('当前歌曲的播放状态和歌词就绪后，才能微调同步')
            return
        try:
            validate_offsets({version: milliseconds})
            offsets = dict(self._state.get('lyricOffsets', {}))
            if milliseconds:
                if version not in offsets and len(offsets) >= MAX_OFFSET_TRACKS:
                    self.notice('歌曲同步设置已满，请先将不需要的微调归零')
                    return
                offsets[version] = milliseconds
            else:
                offsets.pop(version, None)
            offsets = validate_offsets(offsets)
            previous = self._state['lyricOffsets']
            self._state['lyricOffsets'] = offsets
            try:
                self.save_settings()
            except OSError:
                self._state['lyricOffsets'] = previous
                raise
            self.tick()
            self.notice('同步微调已按当前歌曲版本保存')
        except (ValueError, OSError) as exc:
            self.notice('同步微调未保存：' + str(exc))

    @Slot()
    def refreshOutputScreens(self):
        self._state['outputScreens'] = screen_options()
        self.stateChanged.emit()

    @Slot(str, bool)
    def setOutputTarget(self, screen_name, full_screen):
        try:
            target = validate_output_target({'screenName': screen_name, 'fullScreen': full_screen})
            if screen_name and screen_name not in {row['name'] for row in screen_options()}:
                raise ValueError('所选显示器已断开，请刷新显示器列表')
            previous = self._state['outputTarget']
            self._state['outputTarget'] = target
            try:
                self.save_settings()
            except OSError:
                self._state['outputTarget'] = previous
                raise
            self.tick()
            self.notice('客屏显示设置已保存')
        except (ValueError, OSError) as exc:
            self.notice('客屏设置未保存：' + str(exc))

    @Slot(str)
    def setDeck(self, key):
        if key in ('A', 'B'):
            self.send({'type': 'deck', 'deck': key})

    @Slot(str, str)
    def loadTrack(self, version, key):
        if self._state['mode'] != 'replay':
            self.notice('只读联动请在 DJ 软件中加载曲目')
            return
        if version.startswith('demo:'):
            self.send({'type': 'load', 'deck': key, 'track': {'versionId': version, 'title': '原创同步样例 ' + version[-1],
                       'path': '', 'durationMs': 120000}})
            return
        track = self.library.get_track(version)
        if not track or key not in ('A', 'B'):
            return
        if not track.get('durationMs'):
            self.notice('此格式已索引；解码与时长解析尚未实现，暂不载入回放')
            return
        def verified(current):
            if current['versionId'] != version:
                self.notice('文件内容已变化；旧版本歌词未载入，请使用新的曲库记录')
                return
            self.send({'type': 'load', 'deck': key, 'track': current})
            self.notice('已核验并载入文件位置回放；不播放音频，也不控制真实 DJ')
        self.index_audio(track['path'], verified)

    @Slot(str)
    def setScene(self, scene):
        if scene in ('amber', 'blue', 'black'):
            self._state['scene'] = scene
            self.save_settings()

    def save_settings(self):
        temporary = self.runtime / 'settings.tmp'
        temporary.write_text(json.dumps({'schemaVersion': 1, 'scene': self._state['scene'],
                                         'style': self._state['style'], 'lyricOffsets': self._state['lyricOffsets'],
                                         'outputTarget': self._state['outputTarget'],
                                         'outputDestination': self._state.get('outputDestination', 'standalone'),
                                         'duetColors': bool(self._state.get('duetColors'))}, ensure_ascii=False))
        os.replace(temporary, self.settings_path)

    @Slot(str, 'QVariant')
    def setStyle(self, key, value):
        try:
            previous = self._state['style']
            self._state['style'] = changed_style(self._state['style'], key, value)
            try:
                self.save_settings()
            except OSError:
                self._state['style'] = previous
                raise
            if key in ('liveEntrance', 'liveExit') and self._state.get('notice', '').startswith('字幕特效未保存：'):
                self._state['notice'] = ''
            self.tick()
        except (ValueError, TypeError, OSError) as exc:
            self.notice(('字幕特效未保存：' if key in ('liveEntrance', 'liveExit') else '') + str(exc))

    @Slot(str)
    def setStylePreset(self, name):
        Controller.setStylePresetScheme(self, name, 0)

    @Slot(bool)
    def setLyricSweep(self, enabled):
        from app.karaoke import sweep_color
        previous = self._state['style']
        try:
            candidate = changed_style(previous, 'sweepMode', 'word' if enabled else 'sentence')
            if enabled:
                candidate = changed_style(candidate, 'highlightColor', sweep_color(candidate['baseColor'], candidate['highlightColor']))
            self._state['style'] = candidate
            try:
                self.save_settings()
            except OSError:
                self._state['style'] = previous
                raise
            if self._state.get('notice', '').startswith('歌词走带未保存：'):
                self._state['notice'] = ''
            self.tick()
        except (ValueError, TypeError, OSError) as exc:
            self.notice('歌词走带未保存：' + str(exc))

    @Slot(str)
    def setLiveLayout(self, layout):
        try:
            if layout not in ('single', 'double', 'karaoke'):
                raise ValueError('请选择传统KTV、双行或单行歌词')
            previous = self._state['style']
            if layout == 'karaoke':
                candidate = traditional_style(previous)
                candidate['sweepMode'] = previous['sweepMode']
            else:
                candidate = changed_style(previous, 'lineMode', layout)
                candidate = changed_style(candidate, 'outputMode', 'dynamic' if layout == 'single' else 'standard')
            self._state['style'] = candidate
            try:
                self.save_settings()
            except OSError:
                self._state['style'] = previous
                raise
            if self._state.get('notice', '').startswith('字幕布局未保存：'):
                self._state['notice'] = ''
            self.tick()
        except (ValueError, TypeError, OSError) as exc:
            self.notice('字幕布局未保存：' + str(exc))

    @Slot(str, int)
    def setStylePresetScheme(self, name, scheme_index):
        try:
            from app.styles import style_preset
            previous = self._state['style']
            self._state['style'] = style_preset(name, previous, scheme_index)
            self._state['style']['sweepMode'] = previous['sweepMode']
            if previous['sweepMode'] != 'sentence':
                from app.karaoke import sweep_color
                self._state['style']['highlightColor'] = sweep_color(self._state['style']['baseColor'], self._state['style']['highlightColor'])
            try:
                self.save_settings()
            except OSError:
                self._state['style'] = previous
                raise
            self.tick()
        except (ValueError, TypeError, OSError) as exc:
            self.notice(str(exc))

    @Slot()
    def toggleLoop(self):
        self.send({'type': 'loop'})

    @Slot()
    def simulateDisconnect(self):
        self.send({'type': 'disconnect'})
        self.notice('已停止回放状态发布；350 ms 后隐藏过期歌词')

    @Slot()
    def reconnect(self):
        if self._state['mode'] == 'rekordbox_ax':
            self.connectRekordboxAX()
            return
        if self._state['mode'] == 'rekordbox':
            self.connectRekordbox()
            return
        if self._state['mode'] == 'serato':
            self.connectSerato()
            return
        if self.bridge.state() == QProcess.ProcessState.NotRunning:
            self.start_bridge('replay')
            return
        self.send({'type': 'reconnect'})
        self.notice('回放桥已请求重连，使用新的会话身份')

    @Slot()
    def runProbe(self):
        if self._state['mode'] == 'rekordbox_ax':
            self.tick()
            self._state['diagnostics'] = {'summary': self._state.get('bridgeStatus', ''), 'checks': [
                {'label': '原版界面读取', 'status': 'ok' if self._state.get('readerConnected') else 'unavailable',
                 'detail': '只读 macOS 辅助功能；不改签原版，不读取进程内存。'},
                {'label': '歌曲文件确认', 'status': 'ok' if self._state['effectiveSource'] != 'none' else 'unavailable',
                 'detail': '每盘手动确认当前文件；换歌需重新确认。同名同长无法由界面区分，不能自动绑定。'},
                {'label': '播放位置', 'status': 'warning',
                 'detail': '界面时间分辨率 0.1 秒；仅使用新观测，不按经过时间外推。播放状态与瞬时速度未提供。'}]}
            self.stateChanged.emit()
            return
        if self._state['mode'] == 'rekordbox':
            self.tick()
            fresh = not self._state['stale']
            self._state['diagnostics'] = {'summary': self._state.get('bridgeStatus', '等待 rekordbox'), 'checks': [
                {'label': 'rekordbox 播放位置', 'status': 'ok' if fresh else 'unavailable',
                 'detail': '仅连接已准备的 7.2.19 测试副本；普通原版和其他版本不会被读取。'},
                {'label': '歌词来源', 'status': 'ok' if self._state['effectiveSource'] != 'none' else 'unavailable',
                 'detail': self._state.get('selectionReason', '请选择 A / B 盘')},
                {'label': '自动切换范围', 'status': 'warning',
                 'detail': '内部混音且两盘分配到推子两侧时按混音推子选择；THRU、同侧或外部混音请指定 A / B。'},
                {'label': '现场时钟', 'status': 'ok' if fresh else 'unavailable',
                 'detail': '歌词直接查询实际源位置；停止、空盘或过期时隐藏，不按经过时间继续推进。'}]}
            self.stateChanged.emit()
            return
        if self.probe and self.probe.state() != QProcess.ProcessState.NotRunning:
            return
        self.notice('正在只读核查真实 Serato 环境，不读取音频、不提交识别')
        self.probe = QProcess(self)
        self.probe.setWorkingDirectory(str(self.root))
        self.probe.setProgram(sys.executable)
        self.probe.setArguments(['-m', 'probes.serato_probe'])
        self.probe.finished.connect(self.probe_finished)
        self.probe.start()

    @Slot()
    def refreshProbe(self):
        self.failed_file_signatures.clear()
        self.file_errors.clear()
        self.runProbe()

    def probe_finished(self, code, *_):
        try:
            payload = json.loads(bytes(self.probe.readAllStandardOutput()))
            self._state['diagnostics'] = payload
            self.notice(payload['summary'])
            (self.runtime / 'serato-probe.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception:
            error = bytes(self.probe.readAllStandardError()).decode(errors='replace')[:500]
            self._state['diagnostics'] = {'summary': '诊断未完成', 'checks': [{'label': '诊断进程', 'status': 'error', 'detail': error or '未收到有效报告'}]}
            self.notice('诊断未完成；不能据此判断是否连接')

    @Slot()
    def importAudio(self):
        path, _ = QFileDialog.getOpenFileName(None, '导入本地音频，仅核验文件', '', '音频 (*.wav *.mp3 *.flac *.m4a *.aiff *.aif *.ogg)')
        if path:
            self.index_audio(path)

    def index_audio(self, path, callback=None, error_callback=None):
        path = str(Path(path).expanduser().resolve())
        if path in self.index_pending:
            if callback:
                self.index_pending[path]['callbacks'].append(callback)
            if error_callback:
                self.index_pending[path]['errorCallbacks'].append(error_callback)
            self.notice('该文件核验已在队列中，复用现有任务')
            return
        job = str(uuid.uuid4())
        entry = {'path': path, 'job': job, 'callbacks': [callback] if callback else [],
                 'errorCallbacks': [error_callback] if error_callback else []}
        self.index_pending[path] = entry
        self.index_queue.append(entry)
        self.library.record_job(job, 'queued', {'path': path, 'type': 'file_identity'})
        self.start_next_index()

    def start_next_index(self):
        if self.jobs or not self.index_queue:
            return
        entry = self.index_queue.pop(0)
        path, job = entry['path'], entry['job']
        process = QProcess(self)
        process.setWorkingDirectory(str(self.root))
        process.setProgram(sys.executable)
        process.setArguments(['-m', 'worker.index_audio', path])
        self.library.record_job(job, 'running', {'path': path, 'type': 'file_identity'})
        def finished(code, *_):
            if self.shutting_down:
                process.deleteLater()
                return
            try:
                response = json.loads(bytes(process.readAllStandardOutput()))
                if not response['ok']:
                    raise ValueError(response.get('error', '文件核验失败'))
                self.library.add_track(response['track'])
                self.file_proofs[path] = response['track']
                version = response['track']['versionId']
                cached = self.library.timeline(version)
                if cached:
                    self.timelines[version] = cached
                else:
                    self.timelines.pop(version, None)
                self.file_errors.pop(path, None)
                self.failed_file_signatures.pop(path, None)
                self.library.record_job(job, 'ready', response)
                self.refresh_library()
                self.notice('文件已核验并索引；自动歌词识别尚未启用')
                for callback in entry['callbacks']:
                    callback(response['track'])
            except Exception as exc:
                self.file_errors[path] = str(exc)
                try:
                    self.failed_file_signatures[path] = file_signature(path)
                except OSError:
                    self.failed_file_signatures[path] = None
                self.library.record_job(job, 'failed', {'error': str(exc)})
                self.notice(str(exc))
                for callback in entry['errorCallbacks']:
                    callback(str(exc))
            self.jobs.remove(process)
            del self.index_pending[path]
            process.deleteLater()
            self.start_next_index()
            self.refresh_library()
            self.refresh_preparation()
            self.stateChanged.emit()
        process.finished.connect(finished)
        self.jobs.append(process)
        process.start()
        self.notice('后台正在计算实际文件身份，演出画面继续运行')

    @Slot()
    def importTimeline(self):
        key = self._state.get('lyricAdjustmentDeck', 'A')
        try:
            current_track = self.recognition_track(key)
        except (OSError, ValueError, TypeError, KeyError):
            current_track = None
        path, _ = QFileDialog.getOpenFileName(None, '导入本地字幕或已绑定的歌词时间轴', '',
                                              '本地字幕与时间轴 (*.srt *.lrc *.json)')
        if not path:
            return
        try:
            source = Path(path)
            from app.lyric_cache import read_bytes
            payload = json.loads(read_bytes(source).decode('utf-8-sig')) if source.suffix.lower() == '.json' else None
            if source.suffix.lower() != '.json' or (isinstance(payload, dict) and payload.get('schema') == 'baofang-subtitles/v1'):
                if not current_track:
                    raise ValueError('先连接现场并核验当前歌曲，再为这首歌导入 SRT/LRC 字幕')
                if self.recognition_track(key)['versionId'] != current_track['versionId']:
                    raise ValueError('选取字幕时当前盘已切歌，请重新选择')
                self.bindLocalSubtitles(path, current_track, key)
                return
            if not isinstance(payload, dict):
                raise ValueError('歌词必须是 JSON 对象')
            track = self.library.get_track(payload.get('versionId'))
            if not track:
                raise ValueError('先导入对应的音频文件，再导入歌词')
            self.bindLocalSubtitles(path, track)
        except Exception as exc:
            self.notice(str(exc))

    @Slot()
    def rescanLibrary(self):
        self.refresh_library()
        self.verifyOfflineLibrary()
        if not self._offline_rows:
            self.notice('暂无真实音频文件，请先导入')

    @Slot()
    def openOutput(self):
        if self.shutting_down or self._state.get('outputRequested'):
            return
        if self.output and self.output.poll() is None:
            return
        self.tick()
        self._state['outputRequested'] = True
        self._output_retries = 0
        self._output_retry_at = 0
        self._start_output()
        self.stateChanged.emit()

    def _start_output(self):
        arguments = [sys.executable, '-m', 'app.main', '--renderer', '--runtime', str(self.runtime)]
        if self._state.get('outputDestination') == 'serato_overlay':
            arguments.append('--serato-overlay')
        try:
            with (self.runtime / 'renderer.log').open('a') as log:
                self.output = subprocess.Popen(arguments, cwd=self.root, stdout=log, stderr=log)
        except OSError as exc:
            self.output = None
            self._queue_output_retry('输出未能启动：' + str(exc))
            return
        self._output_started_at = monotonic_ns()
        self._state['outputOpen'] = True
        self._state['overlayStatus'] = {'phase': 'waiting', 'detail': '正在等待字幕叠加进程确认视频窗口'}
        self._state['outputRecovery'] = {
            'phase': 'recovered' if self._output_retries else 'idle',
            'detail': '输出进程已重开，请确认客屏画面。' if self._output_retries else ''}
        if self._state.get('notice', '').startswith(('输出进程意外退出', '输出未能启动')):
            self.notice('输出进程已启动，请确认客屏画面。')

    def _queue_output_retry(self, reason):
        self._state['outputOpen'] = False
        self._state['overlayStatus'] = {'phase': 'error', 'detail': reason}
        if self._output_retries >= 3:
            self._state['outputRequested'] = False
            self._output_retry_at = 0
            detail = reason + '；自动重开 3 次后仍失败，请检查后手动打开。'
            self._state['outputRecovery'] = {'phase': 'failed', 'detail': detail}
            self.notice(detail)
            return
        delay = 2 ** self._output_retries
        self._output_retry_at = monotonic_ns() + delay * 1_000_000_000
        self._state['outputRecovery'] = {
            'phase': 'waiting',
            'detail': f'{reason}；{delay} 秒后重开（{self._output_retries + 1}/3）。'}

    def refresh_output(self):
        if self.shutting_down:
            return
        if self.output:
            code = self.output.poll()
            if code is not None:
                self.output = None
                self._state['outputOpen'] = False
                if code == 0:  # Esc / 客屏窗口关闭，尊重用户的主动关闭。
                    self.closeOutput()
                elif self._state.get('outputRequested'):
                    self._queue_output_retry(f'输出进程意外退出（{code}）')
            elif monotonic_ns() - self._output_started_at >= 60_000_000_000:
                # 连续运行一分钟才恢复重试额度，防止启动即崩溃时无限重开。
                self._output_retries = 0
        if self._state.get('outputRequested') and self._output_retry_at and monotonic_ns() >= self._output_retry_at:
            self._output_retry_at = 0
            self._output_retries += 1
            self._start_output()

    def open_destination(self, destination):
        try:
            previous = self._state['outputDestination']
            self._state['outputDestination'] = destination
            try:
                self.save_settings()
            except OSError:
                self._state['outputDestination'] = previous
                raise
            if previous != destination:
                self.closeOutput()
            self.openOutput()
            self.stateChanged.emit()
        except (OSError, ValueError) as exc:
            self.notice('字幕输出未打开：' + str(exc))

    @Slot()
    def openSeratoOverlay(self):
        if sys.platform != 'darwin':
            self.notice('Serato 窗口字幕叠加目前支持 macOS')
            return
        self.open_destination('serato_overlay')

    @Slot()
    def openStandaloneOutput(self):
        self.open_destination('standalone')

    @Slot()
    def closeOutput(self):
        self._state['outputRequested'] = False
        self._output_retry_at = 0
        self._output_retries = 0
        self._state['outputRecovery'] = {'phase': 'idle', 'detail': ''}
        if self.output and self.output.poll() is None:
            self.output.terminate()
            try:
                self.output.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.output.kill()
                self.output.wait(timeout=1)
        self.output = None
        self._state['outputOpen'] = False
        self._state['overlayStatus'] = {'phase': 'idle', 'detail': 'Serato 字幕叠加已关闭'}
        self.stateChanged.emit()

    @Slot()
    def exportDiagnostics(self):
        report = self.runtime / ('diagnostics-' + time.strftime('%Y%m%d-%H%M%S') + '.json')
        report.write_text(json.dumps({'mode': self._state['mode'], 'nativeCore': 'C++', 'probe': self._state['diagnostics'],
                          'reader': self._state.get('readerDiagnostics', {}), 'selection': self._state.get('selection', {}),
                          'bridge': {'epoch': self._state.get('bridgeEpoch'), 'sequence': self._state.get('bridgeSequence')},
                          'scope': '无原始音频、无云凭据；可能包含本机路径和进程信息'}, ensure_ascii=False, indent=2))
        self.notice('诊断已保存：' + str(report))

    def shutdown(self):
        self.shutting_down = True
        self.timer.stop()
        self.inbox.stop()
        self.wish_relay.stop(notify=False)
        self.midi.close()
        self._save_import_queue()
        self.preparation.shutdown()
        self.closeOutput()
        self.stopBridge()
        for process in self.jobs + ([self.probe] if self.probe else []) + ([self.scanner] if self.scanner else []):
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(500)
        self.library.close()


class FileController(QObject):
    stateChanged = Signal()

    def __init__(self, root, runtime):
        super().__init__()
        from app.lyric_paint import register_lyric_types
        register_lyric_types()
        self.path = Path(runtime) / 'program.json'
        self.clock = NativeClock(root)
        self._state = initial_state()
        self.payload = None
        self.last_mtime = None
        self.output_window = None
        self.placement_signature = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        self.tick()

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        return self._state

    def tick(self):
        try:
            modified = self.path.stat().st_mtime_ns
            if modified != self.last_mtime:
                if self.path.stat().st_size > 4_000_000:
                    raise ValueError('客屏状态文件过大')
                self.payload = validate_program_payload(json.loads(self.path.read_text()))
                self.last_mtime = modified
            if self.payload:
                self._state = project_state(self.payload['state'], self.payload['raw'], self.payload['timelines'], self.clock, monotonic_ns())
                self._state['logoImageUrl'] = logo_url(self.path.parent, self._state.get('screenLayers', default_layers()))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self._state['lyric'] = project_lyrics(None, 0)
            self._state['stale'] = True
        window = getattr(self, 'output_window', None)
        if window is not None:
            from PySide6.QtGui import QGuiApplication
            target = self._state.get('outputTarget', {'screenName': '', 'fullScreen': False})
            signature = (target['screenName'], target['fullScreen'], tuple((id(screen), screen.geometry().getRect()) for screen in QGuiApplication.screens()))
            if signature != self.placement_signature:
                place_output(window, target)
                self.placement_signature = signature
        self.stateChanged.emit()

    def attach_output_window(self, window):
        self.output_window = window
        self.tick()

    @Slot()
    def closeOutput(self):
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.quit()
