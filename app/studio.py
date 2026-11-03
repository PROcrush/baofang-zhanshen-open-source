"""Independent local single-track authoring and CPU subtitle production."""

import copy
import hashlib
import json
import math
import os
import re
import signal
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Property, QProcess, QProcessEnvironment, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from app.studio_tools import correct_reference, load_subtitles
from app.karaoke import token_cursor
from app.studio_authoring import domain
from app.studio_playback import StudioPlayback
from worker.studio_render import EFFECTS, ENGINE_ID, TEMPLATES, find_ffmpeg
from worker.style_catalog import TRADITIONAL_KARAOKE_STYLE, template_settings

PROJECT_KIND = 'baofang-studio-project'
PROJECT_VERSION = 3
DEFAULT_SETTINGS = {'fontSize': 42, 'fontFamily': 'PingFang SC', 'templateId': 'none',
                    'effectId': 'none', 'width': 854, 'height': 480,
                    'layoutId': 'standard', 'textColor': '#FFFFFF', 'highlightColor': '#75E6DB',
                    'strokeColor': '#000000', 'strokeWidth': 2, 'shadow': True, 'fontBold': False,
                    'letterSpacing': 0, 'lineSpacing': 1.25, 'x': .5, 'y': .78,
                    'entranceId': 'fade', 'exitId': 'fade', 'fadeMs': 250,
                    'backgroundColor': '#101622', 'bpm': 120, 'fps': 24, 'layoutParameters': {}}
DEFAULT_SETTINGS.update(TRADITIONAL_KARAOKE_STYLE, templateId='karaoke',
                        entranceId='none', exitId='none', fadeMs=0, lyricSweep=True)


def file_identity(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('所选路径不是文件')
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('读取时源文件发生变化，请重新导入')
    return {'path': str(path), 'size': after.st_size, 'mtimeNs': after.st_mtime_ns,
            'device': after.st_dev, 'inode': after.st_ino, 'sha256': digest.hexdigest()}


def check_identity(value):
    keys = {'path', 'size', 'mtimeNs', 'device', 'inode', 'sha256'}
    if not isinstance(value, dict) or set(value) != keys or not isinstance(value.get('path'), str):
        raise ValueError('工程中的文件身份无效')
    if any(type(value[key]) is not int or value[key] < 0 for key in ('size', 'mtimeNs', 'device', 'inode')):
        raise ValueError('工程中的文件身份字段无效')
    if not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256']):
        raise ValueError('工程中的文件摘要无效')
    path = Path(value['path'])
    if not path.is_absolute() or str(path.resolve(strict=True)) != value['path']:
        raise ValueError('工程的源路径必须是原始绝对路径')
    current = file_identity(path)
    if current != value:
        raise ValueError('工程的源文件已变化；请重新导入原文件')
    return current


def validate_segments(rows, duration_ms):
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError('字幕列表无效')
    clean = []
    previous = -1
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('text'), str) or not row['text'].strip():
            raise ValueError('字幕文本无效')
        start, end = row.get('startMs'), row.get('endMs')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (start, end)):
            raise ValueError('字幕时码无效')
        if not 0 <= start < end <= duration_ms or start < previous or len(row['text']) > 10000:
            raise ValueError('字幕时码超出音频范围或顺序无效')
        result = {'startMs': float(start), 'endMs': float(end), 'text': row['text'],
                  'timingQuality': 'sentence'}
        characters = row.get('characters', [])
        if not isinstance(characters, list):
            raise ValueError('逐字时码无效')
        if characters:
            chars = []
            last = start
            for char in characters:
                if not isinstance(char, dict):
                    raise ValueError('逐字时码格式无效')
                cs, ce = char.get('startMs'), char.get('endMs')
                if not isinstance(char.get('text'), str) or not char['text']:
                    raise ValueError('逐字文本无效')
                if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
                       for v in (cs, ce)) or not start - 1 <= cs < ce <= end + 1 or cs < last - 1:
                    raise ValueError('逐字时码超出句范围')
                chars.append({'text': char['text'], 'startMs': float(cs), 'endMs': float(ce)})
                last = ce
            if ''.join(char['text'] for char in chars) != row['text']:
                raise ValueError('逐字文本与整句不一致')
            result.update(characters=chars, timingQuality='character' if all(len(char['text'])==1 for char in chars) else 'word')
        clean.append(result)
        previous = end
    return clean


def from_recognition(data, duration_ms):
    if not isinstance(data, dict) or not isinstance(data.get('segments'), list):
        raise ValueError('字幕 JSON 中没有有效字幕列表')
    rows = []
    for row in data['segments']:
        item = {'startMs': row['start'] * 1000, 'endMs': row['end'] * 1000, 'text': row['text']}
        if row.get('characters'):
            item['characters'] = [{'text': char['text'], 'startMs': char['start'] * 1000,
                                   'endMs': char['end'] * 1000} for char in row['characters']]
        rows.append(item)
    return validate_segments(rows, duration_ms)


def to_recognition(rows):
    segments = []
    for row in rows:
        item = {'start': row['startMs'] / 1000, 'end': row['endMs'] / 1000, 'text': row['text']}
        if row.get('characters'):
            item['characters'] = [{'text': char['text'], 'start': char['startMs'] / 1000,
                                   'end': char['endMs'] / 1000} for char in row['characters']]
        segments.append(item)
    return {'segments': segments}


def load_catalogs():
    """Only advertise styles implemented by our own CPU renderer."""
    from worker.style_catalog import LAYOUTS, TRANSITIONS
    counts = {'lyricTemplates': len(TEMPLATES), 'captionEffects': len(EFFECTS),
              'jizuraLayouts': len(LAYOUTS), 'transitions': len(TRANSITIONS), 'fonts': 0, 'gradients': 1}
    materials = [{'id': 'lyricTemplates', 'label': '自研字幕模板', 'count': len(TEMPLATES), 'availableInShell': True},
                 {'id': 'captionEffects', 'label': '自研文字效果', 'count': len(EFFECTS), 'availableInShell': True},
                 {'id': 'layouts', 'label': '动态布局', 'count': len(LAYOUTS), 'availableInShell': True},
                 {'id': 'transitions', 'label': '素材转场', 'count': len(TRANSITIONS), 'availableInShell': True},
                 {'id': 'gradients', 'label': '默认渐变背景', 'count': 1, 'availableInShell': True}]
    return copy.deepcopy(TEMPLATES), copy.deepcopy(EFFECTS), counts, materials


class StudioController(StudioPlayback):
    stateChanged = Signal()

    def __init__(self, root, runtime):
        super().__init__()
        self.root, self.runtime = Path(root).resolve(), Path(runtime).resolve()
        self.jobs_root = self.runtime / 'studio-jobs'
        if self.jobs_root.is_symlink():
            raise ValueError('制作任务目录不能是符号链接')
        self.jobs_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        templates, effects, counts, materials = load_catalogs()
        ready = bool(find_ffmpeg())
        self._state = {'engineReady': ready,
                       'engineLabel': '包房战神自研引擎 · CPU 渲染' if ready else '包房战神自研引擎 · 待安装 FFmpeg',
                       'audioPath': '', 'audioTitle': '', 'durationMs': 0, 'segments': [],
                       'positionMs': 0, 'currentText': '', 'nextText': '', 'dirty': False,
                       'canUndo': False, 'canRedo': False, 'busy': False, 'progress': 0,
                       'status': '导入歌曲与已有字幕，开始本地制作', 'resultPath': '', 'resultUrl': '', 'savedVideoPath': '',
                       'savedVideoUrl': '', 'savedVideoCurrent': False,
                       'backgroundPath': '', 'backgroundTitle': '', 'backgroundUrl': '',
                       'backgroundIsImage': False, 'referencePath': '', 'projectPath': '', 'recoveryPath': '',
                       'settings': dict(DEFAULT_SETTINGS),
                       'templates': templates, 'effects': effects,
                       'materials': materials, 'capabilities': counts,
                       'previewMode': 'layout', 'previewRevision': 0, 'timelineSilent': True,
                       'advancedBoundary': '高级制作使用包房战神自己的分镜与渲染引擎'}
        self._sources = {'audio': None, 'background': None, 'reference': None}
        self._undo, self._redo = [], []
        self._init_authoring()
        self._project_id = str(uuid.uuid4())
        self._saved_content = self._content_key()
        self._process = None
        self._job_dir = None
        self._cancelled = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)
        self._init_playback()
        from app.studio_projects import RecentProjects
        self._recent_projects = RecentProjects(self.runtime)
        self._state['recentProjects'] = self._recent_projects.rows()
        self._saved_project_identity = None
        self._opened_recovery = False

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        return copy.deepcopy(self._state)

    def _emit(self):
        self._sync_playback_source()
        if self._state.get('error') and self._state['status']!=self._state['error']:
            self._state['error']=''
        self._check_lyric_invalidation()
        rows = self._state['segments']
        timed = [row for row in rows if token_cursor(row, row['startMs']) is not None]
        self._state['karaokeTiming'] = {
            'total': len(rows), 'timed': len(timed), 'sentence': len(rows) - len(timed),
            'character': sum(all(len(token['text']) == 1 for token in row['characters']) for row in timed)}
        self._state['concertHasDraft']=self._concert_draft is not None
        self._state['previewRevision'] += 1
        self._state['canUndo'], self._state['canRedo'] = bool(self._undo), bool(self._redo)
        self._state['dirty'] = self._content_key() != self._saved_content
        self._project_position()
        self.stateChanged.emit()

    def _content(self):
        return {'durationMs': self._state['durationMs'], 'segments': self._state['segments'],
                'settings': self._state['settings'], 'sources': self._sources,
                'composition': self._state['composition'], 'outputFormat': self._state['outputFormat'],
                'concertDraft': {'lyricDigest':self._lyric_digest(),'composition':self._concert_draft}
                    if self._concert_draft is not None else None}

    def _content_key(self):
        return json.dumps(self._content(), ensure_ascii=False, sort_keys=True, allow_nan=False)

    def _snapshot(self):
        return copy.deepcopy(self._content())

    def _save_dirty_recovery(self):
        if self._content_key() == self._saved_content:
            return ''
        directory = self.runtime / 'studio-recovery'
        if directory.is_symlink():
            raise ValueError('恢复副本目录不能是符号链接，当前工程保持原样')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        output = directory / (uuid.uuid4().hex + '.bfstudio.json')
        temporary = output.with_suffix('.tmp')
        payload = {'kind': PROJECT_KIND, 'version': PROJECT_VERSION, 'projectId': self._project_id,
                   'engineId': ENGINE_ID, 'isRecovery': True,
                   'recoveryOfProjectPath': self._state['projectPath'], **self._snapshot()}
        created = False
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                created = True
                json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.replace(output)
        finally:
            if created and temporary.exists():
                temporary.unlink()
        return str(output)

    def _remember(self):
        self._undo.append(self._snapshot())
        self._undo = self._undo[-100:]
        self._redo.clear()
        self._state.update(resultPath='', resultUrl='', previewMode='layout', savedVideoCurrent=False)

    def _restore(self, content):
        same_audio = self._sources.get('audio') == content['sources'].get('audio')
        same_bpm = self._state['settings'].get('bpm') == content['settings'].get('bpm')
        old_concert=self._state['composition'].get('concert',{}).get('sourceId')
        old_video=self._state['composition'].get('sources',{}).get(old_concert,{}).get('identity')
        self._sources = copy.deepcopy(content['sources'])
        for key in ('durationMs', 'segments', 'settings'):
            self._state[key] = copy.deepcopy(content[key])
        self._state['composition'] = copy.deepcopy(content.get('composition', domain().empty_composition()))
        self._state['outputFormat'] = content.get('outputFormat', 'mp4')
        draft = content.get('concertDraft')
        self._concert_draft = copy.deepcopy(draft['composition']) if draft else None
        self._last_lyric_digest = self._lyric_digest()
        if not same_audio:
            self._state.update(waveform=[], beatsMs=[], analysisStatus='可重新分析歌曲波形')
        elif not same_bpm:
            self._state.update(beatsMs=[], analysisStatus='BPM已变化，可重新生成节拍网格')
        self._sync_authoring()
        new_video=self._state['composition']['sources'].get(self._state['concertSourceId'],{}).get('identity')
        if old_video!=new_video:self._state['concertAnalysis']={}
        self._state['concertStatus']='候选编排已恢复，可继续复核' if self._concert_draft is not None else (
            '已恢复应用的编排，可继续编辑分镜' if self._state['concertRows'] else '导入演唱会视频和对应歌词')
        self._sync_source_state()
        self._state.update(resultPath='', resultUrl='', previewMode='layout', savedVideoCurrent=False)

    def _sync_source_state(self):
        for name in ('audio', 'background', 'reference'):
            source = self._sources[name]
            self._state[name + 'Path'] = source['path'] if source else ''
            if name != 'reference':
                self._state[name + 'Title'] = Path(source['path']).name if source else ''
        image = bool(self._sources['background'] and
                     Path(self._sources['background']['path']).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'))
        self._state['backgroundIsImage'] = image
        self._state['backgroundUrl'] = QUrl.fromLocalFile(self._state['backgroundPath']).toString() if image else ''

    def _project_position(self):
        position = self._state['positionMs']
        self._state.update(currentText='', nextText='', currentSegment=-1, currentTimingQuality='sentence')
        for index, row in enumerate(self._state['segments']):
            if row['startMs'] <= position < row['endMs']:
                self._state['currentText'] = row['text']
                self._state['currentSegment'] = index
                if token_cursor(row, position) is not None:
                    self._state['currentTimingQuality'] = ('character' if all(len(token['text']) == 1
                        for token in row['characters']) else 'word')
            elif row['startMs'] > position:
                self._state['nextText'] = row['text']
                break

    def _failure(self, message):
        self._timer.stop()
        self._state.update(busy=False, status=str(message), error=str(message), progress=0)
        self._emit()

    def _start_job(self, message):
        if self._state['busy']:
            return False
        self._job_dir = self.jobs_root / uuid.uuid4().hex
        self._job_dir.mkdir(mode=0o700)
        self._cancelled = False
        self._state.update(busy=True, progress=0, status=message)
        self._emit()
        return True

    def _environment(self):
        # Do not propagate API keys, proxy authorization, or cloud credentials.
        environment = QProcessEnvironment()
        system = QProcessEnvironment.systemEnvironment()
        for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'BAOFANG_FFMPEG'):
            if system.contains(key):
                environment.insert(key, system.value(key))
        environment.insert('PYTHONDONTWRITEBYTECODE', '1')
        environment.insert('PYTHONNOUSERSITE', '1')
        from PySide6 import __file__ as pyside_file
        plugins = Path(pyside_file).parent / 'Qt/plugins'
        environment.insert('QT_QPA_PLATFORM', 'offscreen')
        environment.insert('QT_PLUGIN_PATH', str(plugins))
        environment.insert('QT_QPA_PLATFORM_PLUGIN_PATH', str(plugins / 'platforms'))
        return environment

    def _launch(self, program, arguments, complete, timeout_ms=300000):
        process = QProcess(self)
        self._process = process
        process.setWorkingDirectory(str(self._job_dir))
        process.setProcessEnvironment(self._environment())
        output, errors = bytearray(), bytearray()

        def read_output():
            output.extend(bytes(process.readAllStandardOutput()))
            for line in output.splitlines()[-20:]:
                try:
                    event = json.loads(line)
                    if event.get('event') == 'progress' or event.get('type') == 'progress':
                        value = event.get('value', event.get('progress', 0))
                        if isinstance(value, (int, float)) and math.isfinite(value):
                            self._state['progress'] = max(0, min(0.99, value / 100 if value > 1 else value))
                        self._state['status'] = event.get('message', self._state['status'])
                        self._emit()
                except (ValueError, TypeError, AttributeError):
                    pass

        def finished(code, exit_status):
            read_output()
            errors.extend(bytes(process.readAllStandardError()))
            self._timer.stop()
            if self._process is process:
                self._process = None
            process.deleteLater()
            if self._cancelled:
                self._failure('任务已取消；源歌曲保持原样')
                return
            if code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
                try:
                    (self._job_dir / 'engine-failure.log').write_bytes(
                        f'exitCode={code} exitStatus={exit_status.name}\n'.encode() + output + b'\n' + errors)
                except OSError:
                    pass
                message = '本地制作任务未完成'
                for line in output.splitlines():
                    try:
                        event = json.loads(line)
                        if event.get('event', event.get('type')) == 'error':
                            message = str(event.get('message', message))[:1500]
                    except (ValueError, TypeError, AttributeError):
                        pass
                self._failure(message)
                return
            try:
                complete(bytes(output))
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
                self._failure('制作数据校验失败：' + str(error))

        process.readyReadStandardOutput.connect(read_output)
        process.readyReadStandardError.connect(lambda: errors.extend(bytes(process.readAllStandardError())))
        process.finished.connect(finished)
        def start_failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                if self._process is process:
                    self._process = None
                process.deleteLater()
                self._failure('无法启动本地引擎')
        process.errorOccurred.connect(start_failed)
        # A separate process group lets cancellation stop ffmpeg descendants too.
        launcher = 'import os,sys; os.setsid(); os.execv(sys.argv[1],sys.argv[1:])'
        process.start(sys.executable, ['-c', launcher, str(program)] + [str(arg) for arg in arguments])
        self._timer.start(timeout_ms)

    def _probe(self, path, complete, verify_video=False, include_video=False):
        arguments = [self.root / 'worker/studio_render.py', 'probe', '--input', path]
        if verify_video:
            arguments.append('--video')
        elif include_video:
            arguments.append('--include-video')
        self._launch(sys.executable, arguments, lambda output: complete(json.loads(output)), timeout_ms=1800000)

    def _write_json(self, name, value):
        path = self._job_dir / name
        path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        return path

    @Slot()
    @Slot(str)
    def chooseAudio(self, path=''):
        if self._state['busy']:
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(None, '导入歌曲', '', '音频 (*.wav *.mp3 *.flac *.m4a *.aac *.aiff *.aif *.ogg);;所有文件 (*)')
        if not path:
            return
        try:
            identity = file_identity(path)
            if identity == self._sources['audio']:
                return
            if not self._start_job('正在读取歌曲，完成后可直接试听'):
                return
            def loaded(data):
                if not any(row.get('codec_type') == 'audio' for row in data.get('streams', [])):
                    raise ValueError('文件中没有音频轨道')
                duration = float(data['format']['duration']) * 1000
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError('无法确定音频时长')
                check_identity(identity)
                # A different song starts a new project. Preserve unsaved work
                # before changing its save target or clearing the timeline.
                try:
                    recovery = self._save_dirty_recovery() if self._sources['audio'] else ''
                except (OSError, ValueError, TypeError) as error:
                    raise ValueError('无法保留上一首的恢复副本，当前工程保持原样：' + str(error)) from error
                self._undo.clear()
                self._redo.clear()
                self._project_id = str(uuid.uuid4())
                self._saved_project_identity = None
                self._opened_recovery = False
                self._sources['audio'] = identity
                self._sources['reference'] = None
                self._state.update(durationMs=duration, segments=[], positionMs=0, busy=False,
                                   projectPath='', resultPath='', resultUrl='', savedVideoPath='',
                                   savedVideoUrl='', savedVideoCurrent=False, previewMode='layout',
                                   recoveryPath=recovery or self._state['recoveryPath'],
                                   progress=1, status='新歌曲工程已建立，保存时会选择新位置' +
                                   ('；上一工程的未保存修改已保留' if recovery else ''))
                composition=domain().empty_composition();composition['fps']=self._state['settings']['fps']
                self._state.update(composition=composition, waveform=[], beatsMs=[],concertAnalysis={},
                                   concertRows=[], concertCoverage={}, concertReviewed=False)
                self._concert_draft = None
                self._sync_authoring()
                self._sync_source_state()
                self._emit()
            self._probe(identity['path'], loaded)
        except (OSError, ValueError) as error:
            self._failure('导入歌曲失败：' + str(error))

    @Slot()
    @Slot(str)
    def chooseSubtitles(self, path=''):
        if self._state['busy']:
            return
        if not self._sources['audio']:
            self._failure('先导入歌曲，再导入字幕')
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(None, '导入已有字幕', '', '字幕 (*.srt *.lrc *.json);;所有文件 (*)')
        if not path:
            return
        try:
            source = file_identity(path)
            if not self._start_job('正在解析本地字幕'):
                return
            data = load_subtitles(source['path'], duration=self._state['durationMs'] / 1000)
            rows = from_recognition(data, self._state['durationMs'])
            if not rows:
                raise ValueError('字幕文件中没有有效字幕')
            check_identity(source)
            self._write_json('imported.json', data)
            self._remember()
            self._state.update(segments=rows, busy=False, progress=1, status=f'已独立解析 {len(rows)} 句字幕')
            self._emit()
        except (OSError, ValueError) as error:
            self._failure('导入字幕失败：' + str(error))

    @Slot()
    @Slot(str)
    def chooseBackground(self, path=''):
        if self._state['busy']:
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(None, '选择背景', '', '背景 (*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.mkv);;所有文件 (*)')
        if not path:
            return
        try:
            identity = file_identity(path)
            self._remember()
            self._sources['background'] = identity
            self._sync_source_state()
            self._state['status'] = '背景已选择；生成影片后可核验合成效果'
            self._emit()
        except (OSError, ValueError) as error:
            self._failure('选择背景失败：' + str(error))

    @Slot()
    @Slot(str)
    def chooseReference(self, path=''):
        if self._state['busy']:
            return
        if not self._state['segments']:
            self._failure('先导入字幕，再使用歌词参考纠错')
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(None, '导入参考歌词', '', '歌词文本 (*.txt *.lrc);;所有文件 (*)')
        if not path:
            return
        try:
            identity = file_identity(path)
            if not self._start_job('正在依据本地参考歌词纠错'):
                return
            reference = Path(identity['path']).read_text(encoding='utf-8-sig')
            if Path(identity['path']).suffix.lower() == '.lrc':
                reference = '\n'.join(re.sub(r'\[[^\]]*\]', '', line).strip() for line in reference.splitlines())
            data = correct_reference(to_recognition(self._state['segments'])['segments'], reference)
            rows = from_recognition(data, self._state['durationMs'])
            check_identity(identity)
            self._write_json('corrected.json', data)
            self._remember()
            self._sources['reference'] = identity
            changed = data.get('stats', {}).get('changedLines', 0)
            self._state.update(segments=rows, busy=False, progress=1,
                               status=f'本地参考纠错完成，修改 {changed} 句；请审阅，句时码保持原样')
            self._sync_source_state()
            self._emit()
        except (OSError, ValueError) as error:
            self._failure('参考纠错失败：' + str(error))

    def applyAIResult(self,rows,expected_version,expected_rows_digest):
        from app.ai_settings import rows_digest
        if self._state['busy'] or not self._sources['audio']:
            raise ValueError('制作工程正在处理或没有歌曲，结果未应用')
        identity=check_identity(self._sources['audio'])
        if 'sha256:'+identity['sha256']!=expected_version or rows_digest(self._state['segments'])!=expected_rows_digest:
            raise ValueError('歌曲或歌词已变化，旧AI结果不能覆盖当前工程')
        validated=validate_segments(rows,self._state['durationMs'])
        if not validated:
            raise ValueError('AI结果没有可应用的歌词')
        self._remember()
        self._state.update(segments=validated,status='已应用审阅后的AI歌词结果；可撤销，句时码来自原数据')
        self._emit()

    @Slot(int, str, result=bool)
    def updateSegment(self, index, text):
        if self._state['busy'] or not 0 <= index < len(self._state['segments']):
            return False
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            self._failure('字幕不能为空或超过 10000 字')
            return False
        row = self._state['segments'][index]
        if text == row['text']:
            return True
        self._remember()
        characters = row.get('characters')
        if characters and len(text) == len(row['text']):
            offset = 0
            for char in characters:
                length = len(char['text'])
                char['text'] = text[offset:offset + length]
                offset += length
            message = '字幕已修改，句时码与逐字时码保留'
        else:
            row.pop('characters', None)
            row['timingQuality'] = 'sentence'
            message = '字幕已修改，句时码保留；增删字后使用整句时码'
        row['text'] = text
        self._state['status'] = message
        self._emit()
        return True

    @Slot()
    def undo(self):
        if not self._state['busy'] and self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())
            self._state['status'] = '已撤销'
            self._emit()

    @Slot()
    def redo(self):
        if not self._state['busy'] and self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())
            self._state['status'] = '已重做'
            self._emit()

    @Slot(float)
    def seek(self, ms):
        if isinstance(ms, (int, float)) and math.isfinite(ms):
            self._state['positionMs'] = max(0, min(self._state['durationMs'], ms))
            self._seek_playback(self._state['positionMs'])
            self._emit()

    def _validated_settings(self, settings):
        if not isinstance(settings, dict) or set(settings) - set(DEFAULT_SETTINGS):
            raise ValueError('工程设置字段无效')
        sweep = settings.get('lyricSweep', settings.get('templateId', DEFAULT_SETTINGS['templateId']) in ('karaoke','karaoke-card'))
        settings = dict(DEFAULT_SETTINGS, **settings)
        settings['lyricSweep'] = sweep
        if any(isinstance(settings[key], bool) or not isinstance(settings[key], (int, float))
               or not math.isfinite(settings[key]) for key in ('fontSize', 'width', 'height')):
            raise ValueError('字号或尺寸无效')
        if not 16 <= settings['fontSize'] <= 150:
            raise ValueError('字号范围为 16–150')
        if (settings['width'], settings['height']) not in ((854, 480), (1280, 720), (1920, 1080)):
            raise ValueError('仅支持已核验的 480p、720p、1080p 尺寸')
        if not isinstance(settings['fontFamily'], str) or not settings['fontFamily'].strip() or len(settings['fontFamily']) > 200:
            raise ValueError('字体名称无效')
        for key, catalog in (('templateId', 'templates'), ('effectId', 'effects'),
                             ('entranceId','effects'), ('exitId','effects'), ('layoutId','layouts')):
            if settings[key] not in [row['id'] for row in self._state[catalog]]:
                raise ValueError('所选模板或效果尚未在自研渲染器实现')
        for key in ('textColor','highlightColor','strokeColor','backgroundColor'):
            if not isinstance(settings[key],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',settings[key]):
                raise ValueError('颜色必须使用 #RRGGBB')
        for key in ('shadow','fontBold','lyricSweep'):
            if type(settings[key]) is not bool: raise ValueError('字体、阴影或走带开关无效')
        from app.studio_authoring import finite
        for key,low,high in [('strokeWidth',0,12),('letterSpacing',0,20),('lineSpacing',.8,2),
                             ('x',0,1),('y',0,1),('fadeMs',0,2000),('bpm',40,240)]:
            finite(settings[key],key,low,high)
        if type(settings['fps']) is not int or settings['fps'] not in (24,25,30):
            raise ValueError('帧率须为24、25或30')
        overrides=settings['layoutParameters']
        ranges={'offsetX':(-1,1),'offsetY':(-1,1),'amplitude':(0,.5),'frequency':(0,10),
                'phase':(-100,100),'scale':(.25,3),'rotation':(-180,180)}
        if not isinstance(overrides,dict) or set(overrides)-set(ranges):raise ValueError('布局参数字段无效')
        for key,value in overrides.items():finite(value,key,*ranges[key])
        return dict(settings)

    @Slot(str, 'QVariant')
    def setSetting(self, key, value):
        if hasattr(value, 'toVariant'):
            value = value.toVariant()
        if key == 'templateId':
            return self.applyTemplate(value)
        if self._state['busy']:
            return
        if key not in DEFAULT_SETTINGS:
            self._failure('未知制作设置')
            return
        settings = dict(self._state['settings'], **{key: value})
        # Static templates set fadeMs=0. An explicit animation choice must
        # initialise its time window, otherwise it renders exactly like none.
        if (settings['fadeMs']==0 and ((key=='effectId' and value in ('fade','rise'))
                or (key in ('entranceId','exitId') and value!='none'))):
            settings['fadeMs']=250
        if key=='lyricSweep' and value is True:
            from app.karaoke import sweep_color
            settings['highlightColor']=sweep_color(settings['textColor'],settings['highlightColor'])
        if key=='layoutId':settings['layoutParameters']={}
        # Width and height are paired so a single control can change resolution.
        if key == 'width':
            settings['height'] = {854: 480, 1280: 720, 1920: 1080}.get(value, 0)
        elif key == 'height':
            settings['width'] = {480: 854, 720: 1280, 1080: 1920}.get(value, 0)
        try:
            settings = self._validated_settings(settings)
            if settings != self._state['settings']:
                composition = copy.deepcopy(self._state['composition'])
                composition['fps'] = settings['fps']
                composition = domain().validate_composition(composition,self._state['durationMs'])
                draft = copy.deepcopy(self._concert_draft)
                if draft is not None:
                    draft['fps'] = settings['fps']
                    draft = domain().validate_composition(draft,self._state['durationMs'])
                self._remember()
                self._state['settings'] = settings
                self._state['composition'] = composition
                self._concert_draft = draft
                if key=='bpm':
                    self._state.update(beatsMs=[],analysisStatus='BPM已变化，请重新分析节拍网格')
                self._state['status'] = '制作设置已更新，生成影片后可核验实际效果'
                self._emit()
        except (ValueError, TypeError) as error:
            self._failure(str(error))

    @Slot(str)
    def applyTemplate(self, template):
        """Apply the same complete preset used by gallery thumbnails in one undo."""
        if self._state['busy']:
            return
        try:
            settings = self._validated_settings(template_settings(self._state['settings'], template))
            if settings != self._state['settings']:
                self._remember()
                self._state['settings'] = settings
            label = next(row['label'] for row in TEMPLATES if row['id'] == template)
            self._state['status'] = '已应用' + label + '，可直接播放检查'
            self._emit()
        except (ValueError, TypeError) as error:
            self._failure(str(error))

    @Slot()
    @Slot(str)
    def applyKaraokeStyle(self, template='karaoke'):
        """One undoable preset; timing remains bound to the imported song."""
        if self._state['busy'] or template not in ('karaoke', 'karaoke-card'):
            return
        settings = dict(self._state['settings'], templateId=template, effectId='none',
                        entranceId='none', exitId='none', fadeMs=0,
                        layoutId='standard', layoutParameters={})
        if template == 'karaoke':
            settings.update(TRADITIONAL_KARAOKE_STYLE)
        if settings != self._state['settings']:
            self._remember()
            self._state['settings'] = self._validated_settings(settings)
        timed = any(token_cursor(row, row['startMs']) is not None for row in self._state['segments'])
        self._state['status'] = ('跟唱样式已应用 · 歌词走带已关闭' if not self._state['settings']['lyricSweep'] else
                                '已开启跟唱 · 空格播放，按词时码扫色' if timed else
                                '已开启跟唱 · 按句估算扫色')
        self._emit()

    @Slot()
    @Slot(str)
    def saveProject(self, path=''):
        if self._state['busy']:
            return
        if not self._sources['audio']:
            self._failure('请先导入歌曲')
            return
        if not path and self._state['projectPath'] and not self._opened_recovery:
            path = self._state['projectPath']
        if not path:
            path, _ = QFileDialog.getSaveFileName(None, '保存包房战神制作工程',
                         self._project_save_suggestion(),
                         '包房战神制作工程 (*.bfstudio.json)')
        if not path:
            return
        try:
            output = Path(path).expanduser().resolve()
            if not output.suffix:
                output = output.with_suffix('.bfstudio.json')
            if self._saved_project_identity and str(output) == self._saved_project_identity['path']:
                try:
                    check_identity(self._saved_project_identity)
                except (OSError, ValueError) as error:
                    raise ValueError('工程文件已变化或不可用，请另存为或重新打开，避免覆盖其他改动') from error
            if output.suffix != '.json' or output.is_symlink():
                raise ValueError('请保存为独立 .bfstudio.json 工程')
            for identity in self._sources.values():
                if identity:
                    check_identity(identity)
                    if output == Path(identity['path']):
                        raise ValueError('工程不能覆盖源文件')
            self._validate_composition_sources()
            for source in self._state['composition']['sources'].values():
                if output == Path(source['identity']['path']):raise ValueError('工程不能覆盖制作素材')
            if output.exists():
                previous = json.loads(output.read_text())
                if not isinstance(previous, dict) or previous.get('kind') != PROJECT_KIND or previous.get('projectId') != self._project_id:
                    raise ValueError('该文件不是当前工程，请另存为新文件')
            payload = {'kind': PROJECT_KIND, 'version': PROJECT_VERSION, 'projectId': self._project_id,
                       'engineId': ENGINE_ID, **self._snapshot()}
            temporary = output.with_name(output.name + '.' + uuid.uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            temporary.replace(output)
            self._state.update(projectPath=str(output), status='工程已保存；源音频和背景文件未改动')
            self._saved_content = self._content_key()
            self._saved_project_identity = file_identity(output)
            self._opened_recovery = False
            self._recent_projects.remember(output)
            self._state['recentProjects'] = self._recent_projects.rows()
            self._emit()
        except (OSError, ValueError, TypeError) as error:
            self._failure('保存工程失败：' + str(error))

    def _project_save_suggestion(self):
        if self._state['projectPath'] and not self._opened_recovery:
            return self._state['projectPath']
        title = Path(self._state['audioTitle'] or '歌曲制作').stem
        return str(self.runtime / (title + '.bfstudio.json'))

    @Slot()
    def saveProjectAs(self):
        if self._state['busy'] or not self._sources['audio']:
            return
        path, _ = QFileDialog.getSaveFileName(None, '工程另存为', self._project_save_suggestion(),
                                            '包房战神制作工程 (*.bfstudio.json)')
        if path:
            self.saveProject(path)

    @Slot()
    @Slot(str)
    def openProject(self, path=''):
        if self._state['busy']:
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(None, '打开包房战神制作工程', '', '包房战神制作工程 (*.bfstudio.json)')
        if not path:
            return
        try:
            source = Path(path).expanduser().resolve(strict=True)
            if source.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('工程文件过大')
            data = json.loads(source.read_text())
            if not isinstance(data, dict):
                raise ValueError('工程必须是 JSON 对象')
            if data.get('kind') != PROJECT_KIND or type(data.get('version')) is not int or data['version'] not in (2,PROJECT_VERSION):
                raise ValueError('工程类型或版本不兼容，请打开包房战神制作工程')
            if data.get('engineId') != ENGINE_ID or not isinstance(data.get('projectId'), str):
                raise ValueError('工程引擎版本或工程身份无效')
            uuid.UUID(data['projectId'])
            sources = data['sources']
            if not isinstance(sources, dict) or set(sources) != set(self._sources):
                raise ValueError('工程源文件身份无效')
            sources = {key: check_identity(value) if value else None for key, value in sources.items()}
            duration = data['durationMs']
            blank_recovery = not sources['audio'] and data.get('isRecovery') is True and data['segments'] == []
            if not sources['audio'] and not blank_recovery:
                raise ValueError('工程没有源音频')
            if isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration) or (
                    duration != 0 if blank_recovery else duration <= 0):
                raise ValueError('工程时长无效')
            rows = validate_segments(data['segments'], duration)
            settings = self._validated_settings(data['settings'])
            composition = domain().validate_composition(data.get('composition',domain().empty_composition()),
                                                       duration,identity_validator=check_identity)
            composition['fps'] = settings['fps']
            from worker.composition_render import validate_composition
            composition = validate_composition(composition,duration,verify_sources=False)
            if composition.get('concert',{}).get('lyricsIdentity'):
                check_identity(composition['concert']['lyricsIdentity'])
            draft = self._validate_draft(data.get('concertDraft'),composition,rows,duration,settings)
            output_format = data.get('outputFormat','mp4')
            if output_format not in ('mp4','mov-alpha'):raise ValueError('工程导出格式无效')
            try:
                recovery = self._save_dirty_recovery()
            except (OSError, ValueError, TypeError) as error:
                raise ValueError('未能保存未保存改动的恢复副本，当前工程保持原样：' + str(error)) from error
            self._restore({'sources': sources, 'durationMs': duration, 'segments': rows, 'settings': settings,
                           'composition':composition, 'outputFormat':output_format,'concertDraft':draft})
            self._undo.clear()
            self._redo.clear()
            self._project_id = data['projectId']
            status = '工程已打开，源文件身份已核验'
            if recovery:
                self._state['recoveryPath'] = recovery
                status += '；未保存改动已保存恢复副本：' + recovery
            self._state.update(projectPath=str(source), positionMs=0, status=status,
                               savedVideoPath='', savedVideoUrl='', savedVideoCurrent=False)
            self._saved_content = self._content_key()
            self._saved_project_identity = file_identity(source)
            self._opened_recovery = data.get('isRecovery') is True
            if not self._opened_recovery:
                self._recent_projects.remember(source)
                self._state['recentProjects'] = self._recent_projects.rows()
            self._emit()
        except (OSError, ValueError, TypeError, KeyError) as error:
            self._failure('打开工程失败：' + str(error))

    def _render_settings(self):
        return copy.deepcopy(self._state['settings'])

    def _validate_composition_sources(self):
        value=domain().validate_composition(self._state['composition'],self._state['durationMs'],identity_validator=check_identity)
        if value.get('concert',{}).get('lyricsIdentity'):check_identity(value['concert']['lyricsIdentity'])
        from worker.composition_render import validate_composition
        value=validate_composition(value,self._state['durationMs'],verify_sources=False)
        self._validate_draft(self._content()['concertDraft'],value,self._state['segments'],self._state['durationMs'],self._state['settings'])
        return value

    def _validate_draft(self,envelope,composition,rows,duration,settings):
        if envelope is None:return None
        if not isinstance(envelope,dict) or set(envelope)!={'lyricDigest','composition'}:
            raise ValueError('演唱会候选工程字段无效')
        digest=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if envelope['lyricDigest']!=digest:raise ValueError('演唱会候选已不对应当前歌词，请重新生成')
        from worker.composition_render import validate_composition
        draft=validate_composition(envelope['composition'],duration,verify_sources=True)
        if any(draft[key]!=composition[key] for key in ('sources','texts','audioClips','markers')) or draft['fps']!=settings['fps'] or not draft.get('concert',{}).get('coverage'):
            raise ValueError('演唱会候选的素材、其他轨道、帧率或覆盖数据不一致')
        if draft['concert'].get('lyricsIdentity'):check_identity(draft['concert']['lyricsIdentity'])
        return {'lyricDigest':digest,'composition':draft}

    def _render(self, preview, save_to=''):
        if self._state['busy']:
            return
        if not self._sources['audio'] or not (self._state['segments'] or self._state['composition']['clips'] or self._state['composition']['texts']):
            self._failure('请先导入歌曲，并添加歌词、分镜或文字')
            return
        if not find_ffmpeg():
            self._state['engineReady'] = False
            self._failure('请安装通用 imageio-ffmpeg 或系统 ffmpeg 后导出')
            return
        try:
            render_sources = copy.deepcopy(self._sources)
            render_composition = self._validate_composition_sources()
            for key,value in render_composition['sources'].items():render_sources['material-'+key]=value['identity']
            if render_composition.get('concert',{}).get('lyricsIdentity'):
                render_sources['concert-lyrics']=render_composition['concert']['lyricsIdentity']
            for identity in render_sources.values():
                if identity:
                    check_identity(identity)
            self._validated_settings(self._state['settings'])
            validate_segments(self._state['segments'], self._state['durationMs'])
            message = 'CPU 引擎正在生成完整影片预览；长歌曲需要较长时间' if preview else 'CPU 引擎正在导出完整影片'
            if not self._start_job(message):
                return
            self._state.update(resultPath='', resultUrl='')
            output_format = 'mp4' if preview else self._state['outputFormat']
            output = self._job_dir / (('preview' if preview else 'export')+('.mp4' if output_format=='mp4' else '.mov'))
            request = self._write_json('render-request.json', {'engineId': ENGINE_ID,
                'audioPath': render_sources['audio']['path'],
                'backgroundPath': render_sources['background']['path'] if render_sources['background'] else '',
                'segments': copy.deepcopy(self._state['segments']), 'settings': self._render_settings(),
                'composition':render_composition, 'outputFormat':output_format,
                'durationMs': self._state['durationMs'], 'output': str(output)})
            def rendered(_):
                if output.is_symlink() or not output.is_file() or output.stat().st_size < 1000:
                    raise ValueError('自研渲染器没有生成有效影片')
                with output.open('rb') as stream:
                    if b'ftyp' not in stream.read(64):
                        raise ValueError('生成文件不是 MP4/MOV')
                self._state['status'] = '正在核验生成影片的音视频轨道和时长'
                self._emit()
                def verified(data):
                    streams = data.get('streams', [])
                    videos = [row for row in streams if row.get('codec_type') == 'video']
                    if not videos or (output_format=='mp4' and not any(row.get('codec_type') == 'audio' for row in streams)):
                        raise ValueError('影片缺少音频或视频轨道')
                    if (videos[0].get('width'), videos[0].get('height')) != (
                            self._state['settings']['width'], self._state['settings']['height']):
                        raise ValueError('影片尺寸与设置不一致')
                    duration = float(data['format']['duration']) * 1000
                    if not math.isfinite(duration) or abs(duration - self._state['durationMs']) > max(350, self._state['durationMs'] * 0.02):
                        raise ValueError('影片时长与源歌曲不一致')
                    try:
                        for identity in render_sources.values():
                            if identity:
                                check_identity(identity)
                    except (OSError, ValueError) as error:
                        raise ValueError('渲染期间源文件发生变化，请重新导入后生成；本次结果未发布') from error
                    self._state.update(resultPath=str(output), resultUrl=QUrl.fromLocalFile(str(output)).toString(),resultFormat=output_format,savedVideoPath='',
                                       savedVideoUrl='', savedVideoCurrent=False,
                                       busy=False, progress=1, previewMode='rendered',
                                       status='完整影片预览已生成并核验' if preview else '影片已导出并核验，可打开结果')
                    self._emit()
                    if save_to:
                        self.saveVideoAs(save_to)
                self._probe(output, verified, verify_video=output_format=='mp4',include_video=output_format=='mov-alpha')
            self._launch(sys.executable, [self.root / 'worker/studio_render.py', 'render', '--request', request],
                         rendered, timeout_ms=1800000)
        except (OSError, ValueError, TypeError) as error:
            self._failure('导出准备失败：' + str(error))

    @Slot()
    def renderPreview(self):
        self._render(True)

    @Slot()
    @Slot(str)
    def exportVideoTo(self, path=''):
        if self._state['busy']:
            return
        suffix = '.mov' if self._state['outputFormat'] == 'mov-alpha' else '.mp4'
        if not path:
            title = Path(self._state['audioTitle'] or '包房战神视频').stem
            path, _ = QFileDialog.getSaveFileName(None, '导出视频到',
                        str(Path.home() / (title + suffix)), '视频 (*' + suffix + ')')
        if not path:
            return
        try:
            output = Path(path).expanduser().absolute()
            if not output.suffix:
                output = output.with_suffix(suffix)
            if output.suffix.lower() != suffix:
                raise ValueError('请选择 ' + suffix + ' 视频文件名')
            if output.exists() or output.is_symlink():
                raise ValueError('此文件已存在，请换一个文件名导出')
            if not output.parent.is_dir():
                raise ValueError('保存目录不可用，请重新选择位置')
            self._render(False, str(output))
        except (OSError, ValueError, TypeError) as error:
            self._failure('导出准备失败：' + str(error))

    @Slot()
    def exportVideo(self):
        self._render(False)

    def _timeout(self):
        self.cancelJob()
        self._state['status'] = '本地任务超时，正在停止'
        self._emit()

    @Slot()
    def cancelJob(self):
        process = self._process
        if not self._state['busy'] or process is None:
            return
        self._cancelled = True
        pid = process.processId()
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                process.terminate()
            def force_stop():
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    if self._process is process and process.state() != QProcess.ProcessState.NotRunning:
                        process.kill()
            QTimer.singleShot(1000, force_stop)
        else:
            process.kill()
        self._state['status'] = '正在取消本地任务'
        self._emit()

    @Slot()
    def openResult(self):
        path = self._state['resultPath']
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @Slot()
    def shutdown(self):
        self._shutdown_playback()
        self.cancelJob()
        if self._process is not None:
            process = self._process
            pid = process.processId()
            if not process.waitForFinished(1500):
                if pid:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        process.kill()
                else:
                    process.kill()
                process.waitForFinished(500)
