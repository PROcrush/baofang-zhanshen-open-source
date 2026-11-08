"""Main-thread preparation coordinator; file/network work runs in a child."""
import json
import hashlib
import math
import os
import re
import sys
import uuid
from pathlib import Path
from decimal import Decimal

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer

from app.lyric_cache import portable_timeline
from app.speech_cache import pending_speech, read_speech_cache, standalone_candidate
from app.storage import validate_timeline


ROUTES = {'domestic': ('国内路线', 'api.302ai.cn'),
          'international': ('国外路线', 'api.302.ai')}
LABELS = {'idle': '等待歌曲', 'missing': '等待识别', 'preflight': '核验音频与费用',
          'planned': '识别计划已准备', 'uploading': '上传音频', 'submitting': '提交识别',
          'polling': '云端识别中', 'queued': '云端排队中', 'ready': '歌词缓存可用',
          'unknown': '提交状态待核对', 'failed': '识别失败', 'corrected': '参考纠错完成',
          'needs_review': '部分歌词需要复核',
          'candidate_ready': '云端结果待审阅',
          'reference_ready': '参考歌词已准备',
          'blocked': '需要配置或复核', 'publishing': '核验并发布歌词'}


def _atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        os.chmod(temporary, 0o600)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _version(track):
    value = track.get('versionId', '') if isinstance(track, dict) else ''
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise ValueError('请选择已经核验的真实歌曲；合成回放不能提交云识别')
    return value


def _proof_current(track):
    try:
        value = Path(track['path']).stat()
        return tuple(track.get(k) for k in ('size', 'mtimeNs', 'inode', 'ctimeNs', 'device')) == (
            value.st_size, value.st_mtime_ns, value.st_ino, value.st_ctime_ns, value.st_dev)
    except (KeyError, OSError):
        return False


def _plan_number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + '必须是有效数值')
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(label + '必须是有限的有效数值')
    return float(value)


class PreparationManager(QObject):
    def __init__(self, root, runtime, library, timelines, on_change, on_timeline, *, process_factory=None):
        super().__init__()
        self.root, self.runtime, self.library = Path(root), Path(runtime), library
        self.timelines = timelines
        self.process_factory = process_factory or QProcess
        self.on_change, self.on_timeline = on_change, on_timeline
        self.directory = self.runtime / 'cloud-jobs'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.route, self.budget, self.enabled = 'domestic', 0.0, False
        self.bailian_config = None
        self.budgets = {}
        self._other_bailian_reserved = 0.0
        self.offline_only = False
        self.config_path, self._key = '', ''
        self._redaction_keys = set()
        self._plan_generation = 0
        self.plans, self.reports, self.processes, self.tracks = {}, {}, {}, {}
        self.references = {}
        self.closed = False
        self.reservations = {}
        self.batch = None
        self._batch_current_version = None
        self._batch_planning_version = None
        try:
            settings = json.loads((self.runtime / 'preparation-settings.json').read_text())
            if settings.get('route') in ROUTES:
                self.route = settings['route']
            self.budget = self._budget(settings.get('budgetLimit', 0))
            self.budgets = settings.get('budgets', {})
            if self.budgets:
                self.budget = self._budget(self.budgets.get('estimatedCostPTC',0))
            self.offline_only = settings.get('offlineOnly') is True
        except (OSError, ValueError, TypeError):
            pass
        for path in self.directory.glob('*/receipt.json'):
            try:
                job = json.loads(path.read_text())
                version = _version(job)
                self.reports[version] = {'phase': job.get('state', 'unknown'),
                                         'detail': '已保留任务回执；已知任务可继续查询',
                                         'taskId': job.get('taskId', '')}
                cost = job.get('reservedCostPTC', job.get('estimatedCostPTC', 0))
                if isinstance(cost, (int, float)) and math.isfinite(cost) and cost > 0:
                    self.reservations[version] = float(cost)
            except (OSError, ValueError, TypeError):
                continue
        for path in self.directory.glob('*/reservation.json'):
            try:
                reservation = json.loads(path.read_text())
                version = _version(reservation)
                cost = reservation['reservedCostPTC']
                if isinstance(cost, (int, float)) and math.isfinite(cost) and cost > 0:
                    self.reservations[version] = float(cost)
            except (OSError, ValueError, TypeError, KeyError):
                continue
        for path in self.directory.glob('*/reference.json'):
            try:
                reference = json.loads(path.read_text())
                version = _version(reference)
                if isinstance(reference.get('path'), str) and re.fullmatch(r'[0-9a-f]{64}', reference.get('sha256', '')):
                    self.references[version] = reference
            except (OSError, ValueError, TypeError):
                continue
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._resume_known)
        if not self.offline_only:
            self.timer.start(5000)
        self._load_batch()

    @staticmethod
    def _budget(value):
        if isinstance(value, bool):
            raise ValueError('额度应为非负数值')
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 1000:
            raise ValueError('额度应为 0–1000；单位见当前服务')
        return value

    def _settings(self):
        _atomic(self.runtime / 'preparation-settings.json',
                {'route': self.route, 'budgetLimit': self.budget,
                 'offlineOnly': self.offline_only, 'budgets': dict(self.budgets, **{self.cost_field: self.budget})})

    def _changed(self):
        if not self.closed:
            self.on_change()

    def _detail(self, value):
        result = str(value)
        for key in sorted(self._redaction_keys | {self._key}, key=len, reverse=True):
            if key:
                result = result.replace(key, '[密钥]')
        return re.sub(r'https://[^\s]+', '[服务地址]', result)[:1000]

    def _invalidate_plans(self):
        self._plan_generation += 1
        self.plans.clear()
        if self.batch and self.batch.get('status') != 'completed':
            self.batch.update(status='paused', stale=True,
                              detail='配置、路线或额度已变化，请重新规划批次')
            self._save_batch()

    def set_offline_only(self, value):
        if not isinstance(value, bool):
            raise ValueError('离线演出开关无效')
        self.offline_only = value
        if value:
            self.timer.stop()
            self.pause_batch()
            # Remove entries before kill: a process may finish synchronously.
            entries = [(version, entry) for version, entry in self.processes.items()
                       if entry['action'] != 'preflight']
            for version, _ in entries:
                self.processes.pop(version, None)
                self.reports[version] = {
                    'phase': 'unknown',
                    'detail': '离线演出已停止本机云请求；任务回执、预算和原歌词保留，云端任务可能继续'}
            for _, entry in entries:
                process = entry['process']
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
                    process.waitForFinished(500)
                process.deleteLater()
        elif not self.closed:
            self.timer.start(5000)
        self._settings()
        self._save_batch()
        self._changed()

    def configure_file(self, path):
        source = Path(path).expanduser().resolve(strict=True)
        if source.stat().st_size > 16384:
            raise ValueError('云配置文件过大')
        config = json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('云配置应为 JSON 对象')
        key = config.get('apiKey')
        if key is None:
            name = config.get('apiKeyEnv', 'BAOFANG_302_API_KEY')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}', name):
                raise ValueError('密钥环境变量名称无效')
            key = os.environ.get(name, '')
        if not isinstance(key, str) or not 1 <= len(key.strip()) <= 1024 or any(ord(c) < 33 for c in key.strip()):
            raise ValueError('配置缺少有效的 302 API Key；密钥只在当前进程内使用')
        budget = self._budget(config.get('budgetLimitPTC', self.budget))
        route = config.get('route', self.route)
        if route not in ROUTES:
            raise ValueError('路线只能为 domestic 或 international')
        self.configure_inline(key.strip(),route,budget)
        self.config_path = str(source)
        self._changed()

    def set_route(self, route):
        if self.bailian_config:
            raise ValueError('百炼地址和模型请在 API 设置修改')
        if route not in ROUTES:
            raise ValueError('云端路线无效')
        self.route = route
        self._invalidate_plans()
        self._settings()
        self._changed()

    def configure_inline(self,key,route,budget):
        from worker.ai_providers import credential
        secret=credential(key)
        if route not in ROUTES:
            raise ValueError('云端路线无效')
        amount=self._budget(budget)
        if self.bailian_config:
            if self.processes or (self.batch and self.batch.get('status') == 'running'):
                raise ValueError('请先暂停批次并等待当前歌曲完成')
            self.budgets[self.cost_field] = self.budget
            self.bailian_config = None
            self._other_bailian_reserved = 0.0
            self._load_reservations()
            self.reports.clear()
        self._key,self.config_path=secret,''
        self._redaction_keys.add(secret)
        self.route,self.budget=route,amount
        self._invalidate_plans()
        self._settings()
        self._changed()

    @property
    def cost_field(self):
        return 'estimatedCostCNY' if self.bailian_config else 'estimatedCostPTC'

    @property
    def cost_unit(self):
        return '元' if self.bailian_config else 'PTC'

    def configure_bailian(self, config, key=''):
        from worker.bailian import config as validate_config
        clean = validate_config(config)
        if self.processes or (self.batch and self.batch.get('status') == 'running'):
            raise ValueError('请先暂停批次并等待当前歌曲处理完成，再修改 API 配置')
        self.budgets[self.cost_field] = self.budget
        self.bailian_config = clean
        self.budget = self._budget(self.budgets.get(self.cost_field, 0))
        self._key, self.config_path = key, ''
        if key:
            self._redaction_keys.add(key)
        self.enabled = False
        self._load_reservations()
        self.reports.clear()
        self._invalidate_plans()
        self._settings()
        self._changed()

    def _load_reservations(self):
        self.reservations.clear()
        self._other_bailian_reserved = 0.0
        directory = self.runtime / ('bailian-jobs' if self.bailian_config else 'cloud-jobs')
        field = 'reservedCostCNY' if self.bailian_config else 'reservedCostPTC'
        for path in directory.glob('*/reservation.json'):
            try:
                value = json.loads(path.read_text())
                version = _version(value)
                cost = _plan_number(value[field], '预留费用')
                if path.parent == self._receipt(version).parent:
                    self.reservations[version] = cost
                elif self.bailian_config:
                    # Switching host/model cannot reset the CNY allowance.
                    self._other_bailian_reserved += cost
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if not self.bailian_config:
            for path in directory.glob('*/receipt.json'):
                try:
                    value = json.loads(path.read_text())
                    self.reservations.setdefault(_version(value), _plan_number(value.get('reservedCostPTC',value.get('estimatedCostPTC',0)), '预留费用'))
                except (OSError, ValueError, KeyError, TypeError):
                    continue

    def _spent(self):
        return sum(self.reservations.values()) + self._other_bailian_reserved

    def clear_key(self):
        self._key,self.config_path='',''
        self.enabled=False
        self._invalidate_plans()
        self._changed()

    def set_budget(self, value):
        self.budget = self._budget(value)
        self._invalidate_plans()
        self._settings()
        self._changed()

    def set_new_submission_enabled(self, value):
        if not isinstance(value, bool):
            raise ValueError('新提交开关无效')
        self.enabled = value
        self._changed()

    def _receipt(self, version):
        if self.bailian_config:
            from worker.bailian import receipt_path
            return receipt_path(self.runtime, self.bailian_config, version)
        return self.directory / version.removeprefix('sha256:') / 'receipt.json'

    def _remaining_budget(self):
        # Decimal monetary subtraction avoids refusing 0.2 after 0.1 from 0.3.
        spent = sum((Decimal(str(cost)) for cost in self.reservations.values()), Decimal(str(self._other_bailian_reserved)))
        return float(Decimal(str(self.budget)) - spent)

    def _job(self, version):
        try:
            path = self._receipt(version)
            if path.stat().st_size > 1024 * 1024:
                return {'state': 'unknown'}
            value = json.loads(path.read_text())
            if value.get('versionId') != version:
                return {'state': 'unknown'}
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError, AttributeError):
            return {'state': 'unknown'}

    def _cached_lyrics(self, version, track=None, *, stage=False):
        if not version.startswith('sha256:'):
            return None
        has_pending = getattr(self.library, 'has_pending_lyric_revision', None)
        pending = has_pending(version) if has_pending else False
        if not pending:
            track = track or {}
            legacy = read_speech_cache(self.runtime, version, track.get('durationMs') or None) or {}
            timeline = standalone_candidate(self.library, version, legacy)
            if timeline and timeline != self.timelines.get(version):
                pending = True
                if stage:
                    self.library.add_timeline(timeline, track, activate=False)
                    pending = bool(pending_speech(self.library, version))
        if pending:
            detail = ('保留当前输出歌词；' if version in self.timelines else '') + '云端候选已缓存，可到曲库的歌词版本审阅'
            return 'candidate_ready', detail
        if version in self.timelines:
            return 'ready', '本地歌词缓存已就绪，直接复用'
        return None

    def _submit_block(self, version, plan):
        if self.closed:
            return '歌曲准备已关闭'
        if self.offline_only:
            return '离线演出已开启，不发送云端请求'
        if self._batch_network_conflict(version):
            return '批次正在处理其他歌曲，等待当前歌曲完成'
        cached = self._cached_lyrics(version, self.tracks.get(version))
        if cached:
            return cached[1]
        if self._job(version) is not None:
            return '已有任务回执；继续查询或核对，不重复提交'
        if version in self.reservations:
            return '已有提交预留记录；先核对任务，不重复提交'
        if version in self.processes:
            return '当前正在处理该文件'
        if not plan:
            return '先生成并审阅识别计划'
        if not self._key:
            return '请先在 API 设置填写 Key'
        if not self.enabled:
            return '尚未允许本次手动新提交'
        if plan.get('uploadAllowed') is False:
            return '该音频超过服务上传限制，不能直接提交'
        try:
            cost = _plan_number(plan.get(self.cost_field), '预估费用')
        except ValueError:
            return '识别计划费用无效，请重新生成计划'
        if self._remaining_budget() < cost:
            return '剩余额度不足；不新增上传或识别'
        if not _proof_current(self.tracks.get(version, {})):
            return '源文件已变化，请重新核验并生成计划'
        return ''

    def view(self, decks):
        result = {'config': {'path': self.config_path, 'ready': bool(self._key),
                  'summary': (('百炼 Key 已就绪，单首与批量共用' if self._key else '请在 API 设置填入百炼 Key') if self.bailian_config else ('302 Key 已配置（仅本次运行）' if self._key else '请选择云配置 JSON')),
                  'provider': 'bailian' if self.bailian_config else '302', 'budgetUnit': self.cost_unit,
                  'newSubmissionEnabled': self.enabled, 'budgetLimit': self.budget,
                  'offlineOnly': self.offline_only,
                  'spent': round(self._spent(), 6)},
                  'route': self.route, 'routes': [{'id': key, 'label': value[0],
                  'service': '302 / 豆包歌唱'} for key, value in ROUTES.items()], 'decks': {}}
        if self.bailian_config:
            result.update(route='bailian', routes=[{'id':'bailian','label':'百炼北京','service':self.bailian_config['model']}])
        for key in ('A', 'B'):
            track = decks.get(key, {}) if isinstance(decks, dict) else {}
            version = track.get('versionId', '')
            report = dict(self.reports.get(version, {}))
            cached = version in self.timelines and version.startswith('sha256:')
            reuse = self._cached_lyrics(version, track)
            default_phase = reuse[0] if reuse else 'missing' if version.startswith('sha256:') else 'idle'
            phase = report.get('phase', default_phase)
            if (default_phase == 'candidate_ready' and version not in self.processes) or phase == 'candidate_ready':
                phase = default_phase
                report['detail'] = reuse[1] if reuse else ''
            job = self._job(version) if version.startswith('sha256:') else None
            plan = dict(self.plans.get(version, {}))
            if plan:
                blocked = self._submit_block(version, plan)
                plan.update(canSubmit=not blocked, blockedReason=blocked,
                            budgetLabel=f'剩余 {max(0, self.budget - self._spent()):.4f} {self.cost_unit}')
            result['decks'][key] = {'versionId': version, 'phase': phase,
                'label': LABELS.get(phase, '任务需要核对'), 'detail': report.get('detail', ''),
                'canQuery': bool(not self.offline_only and self._key and job and job.get('taskId')
                                 and version not in self.processes and default_phase != 'candidate_ready'),
                'canCorrect': bool(version.startswith('sha256:') and version not in self.processes and
                                   (cached or track.get('identityReady') or track.get('identityVerified'))),
                'canExport': cached, 'referencePath': self.references.get(version, {}).get('path', report.get('referencePath', '')),
                'correctionSummary': report.get('correctionSummary', ''), 'plan': plan}
        return result

    def plan(self, deck, track):
        version = _version(track)
        if not track.get('identityVerified') or not _proof_current(track):
            raise ValueError('当前盘音频身份未核验，等待核验完成后再规划')
        if version in self.processes:
            raise ValueError('该歌曲已有正在运行的本机处理')
        self.tracks[version] = dict(track)
        cached = self._cached_lyrics(version, track, stage=True)
        if cached:
            self.plans.pop(version, None)
            self.reports[version] = {'phase': cached[0], 'detail': cached[1]}
            self._changed()
            return
        label, host = ROUTES[self.route]
        request = {'track': dict(track), 'host': host, 'maxCostPTC': self.budget,
                   'route': self.route, 'routeLabel': label, 'planId': str(uuid.uuid4())}
        if self.bailian_config:
            request.update(config=self.bailian_config, routeLabel='百炼北京')
        self._spawn(version, 'preflight', request)

    def start(self, deck, track, plan_id):
        version = _version(track)
        plan = self.plans.get(version)
        if not plan or plan_id != plan.get('planId'):
            raise ValueError('计划已失效，请重新生成并审阅当前曲目的计划')
        if track.get('path') != plan.get('sourcePath') or not _proof_current(track):
            raise ValueError('当前盘或源文件已变化，不能沿用旧识别计划')
        reason = self._submit_block(version, plan)
        if reason:
            raise ValueError(reason)
        request = dict(plan, intentId=str(uuid.uuid4()))
        request['maxCostCNY' if self.bailian_config else 'maxCostPTC'] = self._remaining_budget()
        # Reserve conservatively before child dispatch. Unknown submission retains it.
        self.reservations[version] = float(plan[self.cost_field])
        _atomic(self._receipt(version).with_name('reservation.json'),
                {'versionId': version, ('reservedCostCNY' if self.bailian_config else 'reservedCostPTC'): self.reservations[version],
                 'intentId': request['intentId']})
        self._spawn(version, 'start', request)

    def query(self, deck, track):
        if self.offline_only:
            raise ValueError('离线演出已开启，不发送云端请求')
        version = _version(track)
        if self._batch_network_conflict(version):
            raise ValueError('批次正在处理其他歌曲，等待当前歌曲完成')
        if version in self.processes:
            return
        job = self._job(version)
        if not self._key or not job or not job.get('taskId'):
            raise ValueError('没有可续查的已确认任务，或尚未配置 Key')
        if not _proof_current(track):
            raise ValueError('歌曲文件已变化，请先核验版本')
        self.tracks[version] = dict(track)
        # Routing is pinned in the receipt, independent of the current selector.
        request = dict(job.get('plan', {}))
        request.update(versionId=version, sourcePath=track['path'],
                       host=job.get('host', request.get('host')), durationMs=job.get('durationMs', request.get('durationMs')))
        if self.bailian_config:
            request.update(config=self.bailian_config, track=dict(track))
        self._spawn(version, 'query', request)

    def _spawn(self, version, action, request):
        if self.closed:
            raise ValueError('歌曲准备已关闭')
        if action != 'preflight' and self.offline_only:
            raise ValueError('离线演出已开启，不发送云端请求')
        if action != 'preflight' and self._batch_network_conflict(version):
            raise ValueError('批次正在处理其他歌曲，等待当前歌曲完成')
        directory = self._receipt(version).parent
        directory.mkdir(parents=True, exist_ok=True)
        input_path = directory / ('preflight.json' if action == 'preflight' else 'plan.json')
        _atomic(input_path, request)
        process = self.process_factory(self)
        process.setWorkingDirectory(str(self.root))
        environment = QProcessEnvironment()
        inherited = QProcessEnvironment.systemEnvironment()
        for name in ('PATH','TMPDIR','LANG','LC_ALL','LC_CTYPE','BAOFANG_FFMPEG','BAOFANG_FFPROBE'):
            if inherited.contains(name):
                environment.insert(name, inherited.value(name))
        environment.insert('PYTHONDONTWRITEBYTECODE', '1')
        environment.insert('PYTHONNOUSERSITE', '1')
        environment.insert('BAOFANG_TASK_KEY', self._key if action != 'preflight' else '')
        process.setProcessEnvironment(environment)
        entry = {'process': process, 'action': action, 'request': request, 'buffer': b'',
                 'last': {}, 'error': False, 'planGeneration': self._plan_generation}
        self.processes[version] = entry
        self.reports[version] = {'phase': 'preflight' if action == 'preflight' else 'polling' if action == 'query' else 'uploading',
                                 'detail': '独立后台处理，不占用演出时钟'}
        process.readyReadStandardOutput.connect(lambda: self._read(version))
        process.finished.connect(lambda code, *_: self._finished(version, code))
        process.errorOccurred.connect(lambda _: self._failed_launch(version))
        process.start(sys.executable, ['-m', 'worker.bailian_preparation' if self.bailian_config else 'worker.cloud_pipeline', '--action', action,
                      '--plan', str(input_path), '--receipt', str(self._receipt(version))])
        self._changed()

    def _read(self, version):
        entry = self.processes.get(version)
        if not entry:
            return
        entry['buffer'] += bytes(entry['process'].readAllStandardOutput())
        if len(entry['buffer']) > 2_000_000:
            entry['error'] = True
            entry['process'].kill()
            return
        while b'\n' in entry['buffer']:
            line, entry['buffer'] = entry['buffer'].split(b'\n', 1)
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    continue
                entry['last'] = value
                phase = value.get('phase', 'unknown')
                if not isinstance(phase, str):
                    entry['error'] = True
                    phase = 'unknown'
                self.reports[version] = {'phase': phase, 'detail': self._detail(value.get('detail', '')),
                                         'taskId': value.get('taskId', '')}
            except (ValueError, TypeError):
                entry['error'] = True
        self._changed()

    def _failed_launch(self, version):
        entry = self.processes.get(version)
        if entry and entry['process'].state() == QProcess.ProcessState.NotRunning:
            self._finished(version, -1)

    def _finished(self, version, code):
        entry = self.processes.get(version)
        if not entry:
            return
        self._read(version)
        self.processes.pop(version, None)
        value = entry['last']
        try:
            if code != 0 or entry['error']:
                raise ValueError(self._detail(value.get('detail', '后台处理未完成；保留回执与原歌词')))
            if entry['action'] == 'preflight':
                if entry['planGeneration'] != self._plan_generation:
                    raise ValueError('配置、路线或额度已变化，请重新生成识别计划')
                plan = value.get('plan')
                if not isinstance(plan, dict) or plan.get('versionId') != version:
                    raise ValueError('后台预检未返回匹配曲目版本的计划')
                request = entry['request']
                track = self.tracks[version]
                if self.bailian_config and plan.get(self.cost_field) is None:
                    raise ValueError('当前模型暂无人民币费用预估，批量准备请使用默认 Fun-ASR')
                _plan_number(plan.get(self.cost_field), '预估费用')
                _plan_number(plan.get('durationMs'), '音频时长', positive=True)
                if plan.get('sourcePath') != track['path']:
                    raise ValueError('后台预检音频路径与当前歌曲不一致')
                plan.update(planId=request['planId'], routeLabel=request['routeLabel'],
                            title=track.get('title', Path(track['path']).name),
                            service=('阿里云百炼 / '+self.bailian_config['model']) if self.bailian_config else '302 / 豆包中文歌唱字幕',
                            durationText=f"{plan['durationMs'] / 1000:.1f} 秒",
                            estimatedCost=f"预估上限 {plan[self.cost_field]:.4f} {self.cost_unit}",
                            budgetLabel=f'剩余 {max(0, self.budget - self._spent()):.4f} {self.cost_unit}')
                self.plans[version] = plan
                self.reports[version] = {'phase': 'planned', 'detail': '审阅上传文件、服务路线和费用后再启动'}
            elif value.get('timelinePath'):
                self._publish(version, value['timelinePath'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.reports[version] = {'phase': 'blocked' if entry['action'] == 'preflight' else 'unknown',
                                     'detail': self._detail(exc)}
        entry['process'].deleteLater()
        self._batch_process_finished(version, entry['action'])
        self._changed()

    def _publish(self, version, path):
        source = Path(path).resolve(strict=True)
        if not source.is_relative_to(self._receipt(version).parent.resolve()) or source.stat().st_size > 4_000_000:
            raise ValueError('后台结果路径或大小无效')
        timeline = json.loads(source.read_text())
        track = dict(self.tracks[version])
        expected_duration = track.get('durationMs') or self.plans.get(version, {}).get('durationMs')
        if not expected_duration:
            expected_duration = (self._job(version) or {}).get('durationMs')
        validate_timeline(timeline, version, expected_duration)
        reference = self.references.get(version)
        if reference:
            from worker.correction import correct_with_reference
            reference_file = Path(reference['path'])
            if reference_file.stat().st_size > 100_000:
                raise ValueError('参考文本在识别期间变化或过大，暂不发布')
            current = reference_file.read_text(encoding='utf-8-sig')
            if hashlib.sha256(current.encode('utf-8')).hexdigest() != reference['sha256']:
                raise ValueError('参考文本在识别期间变化；原始识别结果已保留，重新选择参考后再发布')
            timeline = correct_with_reference(timeline, current, reference_label=Path(reference['path']).name)
        if not _proof_current(track):
            raise ValueError('识别期间源文件已变化，保留结果但不绑定当前歌曲')
        active = self.library.timeline(version) or self.timelines.get(version)
        if active is not None:
            if portable_timeline(active) != portable_timeline(timeline):
                self.library.add_timeline(timeline, track, activate=False)
                self.reports[version] = {'phase': 'candidate_ready',
                    'detail': '云端结果已保存，当前歌词继续使用。到曲库 → 更多 → 歌词版本审阅。'}
                return
        else:
            track['durationMs'] = timeline['durationMs']
            self.library.add_track(track)
        self.library.add_timeline(timeline, track)
        self.timelines[version] = timeline
        self.on_timeline(version, timeline)
        correction = timeline.get('correction', {})
        if reference:
            summary = f"已改 {len(correction.get('changes', []))} 句，待复核 {len(correction.get('review', []))} 句"
            self.reports[version] = {'phase': 'needs_review' if correction.get('status') == 'needs_review' else 'ready',
                'correctionSummary': summary, 'detail': summary + '；纠错结果已绑定歌曲并输出，源句时间保留'}
        else:
            self.reports[version] = {'phase': 'ready', 'detail': '云识别歌词已缓存并输出；未选择参考文本，可补充参考纠错'}

    def _resume_known(self):
        if self.closed or self.offline_only or not self._key:
            return
        batch_versions = {row['versionId'] for row in self.batch.get('rows', [])} if self.batch else set()
        if self.batch and self.batch.get('status') == 'running':
            self._drive_batch(allow_query=True)
            return
        for version, track in list(self.tracks.items()):
            if version in batch_versions and version != self._batch_current_version:
                continue
            if version not in self.processes and not self._cached_lyrics(version, track):
                job = self._job(version)
                if job and job.get('taskId') and job.get('state') in ('polling', 'queued', 'running', 'submitted'):
                    try:
                        self.query('', track)
                    except (OSError, ValueError):
                        pass

    def _save_batch(self):
        if self.batch:
            _atomic(self.runtime / 'batch-preparation.json', self.batch)

    def _batch_network_conflict(self, version):
        return bool(self.batch and self.batch.get('status') == 'running' and
                    any(key != version and entry['action'] != 'preflight'
                        for key, entry in self.processes.items()))

    def _load_batch(self):
        try:
            source = self.runtime / 'batch-preparation.json'
            if source.stat().st_size > 8_000_000:
                return
            batch = json.loads(source.read_text(encoding='utf-8'))
            if not isinstance(batch, dict) or batch.get('schemaVersion') != 1:
                return
            if not isinstance(batch.get('id'), str) or not isinstance(batch.get('rows'), list):
                return
            for row in batch['rows']:
                version = _version(row)
                track = row.get('track')
                if not isinstance(track, dict) or _version(track) != version:
                    return
                self.tracks[version] = dict(track)
            self.batch = batch
            self.batch.update(status='paused', stale=True,
                              detail='上次批次已保留；重新规划后才能新增识别，不自动付费')
            self._save_batch()
        except (OSError, ValueError, TypeError, KeyError):
            return

    def plan_batch(self, tracks):
        """Review one deduplicated local set; preflights are serial and offline."""
        if self.closed:
            raise ValueError('歌曲准备已关闭')
        if self.batch and self.batch.get('status') == 'running':
            raise ValueError('先暂停当前批次，再规划新的曲库')
        if not isinstance(tracks, (list, tuple)) or not tracks:
            raise ValueError('请先选择曲库中已核验的歌曲')
        unique = {}
        for track in tracks:
            version = _version(track)
            if not track.get('identityVerified') or not _proof_current(track):
                raise ValueError('批次包含未核验或已变化的音频，请先重新索引')
            if not self.library.get_track(version):
                raise ValueError('请先将歌曲导入本地曲库，再生成批次')
            unique.setdefault(version, dict(track))
        batch_id = str(uuid.uuid4())
        rows = []
        for version, track in unique.items():
            self.tracks[version] = track
            cached = self._cached_lyrics(version, track, stage=True)
            job = self._job(version)
            if cached:
                phase, detail = cached
            elif job is not None or version in self.reservations:
                phase = 'query_ready' if job and job.get('taskId') else 'unknown'
                detail = '已有任务回执，仅续查，不新增上传或识别' if phase == 'query_ready' else '已有提交记录，状态待核对，不重复付费'
                if version not in self.processes:
                    self.reports.pop(version, None)
            else:
                phase, detail = 'pending', '等待本地核验完整文件和预估费用'
                if version not in self.processes:
                    self.reports.pop(version, None)
            rows.append({'versionId': version, 'title': track.get('title', Path(track['path']).name),
                         'path': track['path'], 'track': track, 'phase': phase,
                         'detail': detail, self.cost_field: 0.0, 'planId': '',
                         'attempted': False})
        self.batch = {'schemaVersion': 1, 'id': batch_id, 'status': 'planning',
                      'title': f'曲库预准备 · {len(rows)} 首', 'rows': rows,
                      'generation': self._plan_generation, 'stale': False,
                      'route': self.route, 'detail': '逐曲本地预检；审阅总费用后再启动'}
        self._batch_current_version = None
        self._batch_planning_version = None
        self._save_batch()
        self._drive_preflights()
        self._changed()
        return batch_id

    def _sync_batch_rows(self):
        if not self.batch:
            return
        for row in self.batch['rows']:
            version = row['versionId']
            cached = self._cached_lyrics(version, row['track'])
            if cached:
                if _proof_current(row['track']):
                    row.update(phase=cached[0], detail=cached[1])
                else:
                    row.update(phase='blocked', detail='源音频已变化或不存在；重新索引后才能确认离线就绪')
                continue
            entry = self.processes.get(version)
            report = self.reports.get(version, {})
            if entry:
                row.update(phase=report.get('phase', 'preflight' if entry['action'] == 'preflight' else 'polling'),
                           detail=report.get('detail', '后台处理当前歌曲'))
                continue
            job = self._job(version)
            if job is not None or version in self.reservations:
                phase = report.get('phase')
                if job and job.get('state') == 'failed':
                    phase = 'failed'
                if phase not in ('unknown', 'failed', 'blocked', 'polling', 'queued', 'uploading', 'submitting'):
                    phase = 'query_ready' if job and job.get('taskId') else 'unknown'
                row.update(phase=phase, detail=report.get('detail') or
                           ('已有任务回执，仅续查，不重复提交' if job and job.get('taskId') else '已有提交记录，状态待核对'))
                continue
            plan = self.plans.get(version)
            if row.get('attempted') and report.get('phase') in ('blocked', 'failed', 'unknown'):
                row.update(phase=report['phase'], detail=report.get('detail', '需要重新核对当前歌曲'))
            elif plan and self.batch.get('generation') == self._plan_generation and not self.batch.get('stale'):
                row.update(phase='planned', detail='识别计划已准备，等待审阅后启动',
                           planId=plan['planId'], **{self.cost_field:plan[self.cost_field]})
            elif self.batch.get('stale'):
                row.update(phase='stale', detail='批次计划已失效，请重新规划', planId='')
            elif report.get('phase') in ('blocked', 'failed', 'unknown'):
                row.update(phase='failed', detail=report.get('detail', '本地预检未完成'))

    def _batch_block(self):
        if self.closed:
            return '歌曲准备已关闭'
        if not self.batch:
            return '请先选择曲库并生成批次'
        if self.offline_only:
            return '离线演出已开启，只复用本地歌词，不发送云请求'
        if self.batch.get('stale') or self.batch.get('generation') != self._plan_generation:
            return '批次计划已失效，请重新规划并审阅总费用'
        if self.batch.get('status') == 'planning':
            return '等待全部本地预检完成'
        if self.batch.get('status') == 'running':
            return '批次正在运行'
        new_cost, actionable, has_new = Decimal(0), False, False
        for row in self.batch['rows']:
            version, track = row['versionId'], row['track']
            if not _proof_current(track):
                return '批次音频已变化，请重新索引并规划'
            if self._cached_lyrics(version, track):
                continue
            job = self._job(version)
            if job is not None or version in self.reservations:
                if job and job.get('taskId') and job.get('state') != 'failed':
                    if row.get('attempted') and row.get('phase') in ('unknown', 'blocked', 'failed'):
                        continue
                    actionable = True
                continue
            if row.get('attempted') and row.get('phase') in ('blocked', 'failed', 'unknown'):
                return '批次有歌曲需要重新核对，请重新规划后再启动'
            plan = self.plans.get(version)
            if not plan or row.get('planId') != plan.get('planId'):
                return '批次仍有歌曲未完成预检，请重新规划'
            if plan.get('sourcePath') != track['path']:
                return '歌曲路径已变化，请重新规划'
            if plan.get('uploadAllowed') is False:
                return '批次包含超过服务上传限制的音频'
            new_cost += Decimal(str(_plan_number(plan.get(self.cost_field), '预估费用')))
            actionable = True
            has_new = True
        if not actionable:
            return '全部已有缓存，或提交状态待核对；没有可启动的歌曲'
        if not self._key:
            return '请先在 API 设置填写 Key，再重新规划批次'
        if has_new and not self.enabled:
            return '尚未允许本次手动新提交'
        if new_cost > Decimal(str(self._remaining_budget())):
            return '批次总预估费用超过剩余额度，不新增上传或识别'
        return ''

    def batch_view(self):
        if not self.batch:
            return {'id': '', 'status': 'idle', 'title': '提前准备演出曲库', 'total': 0,
                    'ready': 0, 'review': 0, 'planned': 0, 'pending': 0, 'failed': 0,
                    self.cost_field: 0.0, 'estimatedCost': 0.0, 'costUnit':self.cost_unit, 'canStart': False,
                    'blockedReason': '请先选择曲库并生成批次', 'rows': []}
        self._sync_batch_rows()
        rows = [{key: row.get(key, 0 if key==self.cost_field else '') for key in
                 ('versionId', 'title', 'path', 'phase', 'detail', self.cost_field, 'planId')}
                for row in self.batch['rows']]
        ready = sum(row['phase'] == 'ready' for row in rows)
        review = sum(row['phase'] == 'candidate_ready' for row in rows)
        planned = sum(row['phase'] == 'planned' for row in rows)
        failed = sum(row['phase'] in ('unknown', 'failed', 'blocked', 'stale') for row in rows)
        cost = sum(row[self.cost_field] for row in rows
                   if row['phase'] in ('pending', 'preflight', 'planned', 'stale')
                   and self._job(row['versionId']) is None
                   and row['versionId'] not in self.reservations)
        reason = self._batch_block()
        return {'id': self.batch['id'], 'status': self.batch['status'],
                'title': self.batch['title'], 'detail': self.batch.get('detail', ''),
                'total': len(rows), 'ready': ready, 'review': review, 'planned': planned,
                'pending': len(rows) - ready - review - planned - failed, 'failed': failed,
                self.cost_field: round(cost, 6), 'estimatedCost': round(cost, 6), 'costUnit': self.cost_unit, 'canStart': not reason,
                'blockedReason': reason, 'rows': [dict(row, estimatedCost=row[self.cost_field], costUnit=self.cost_unit) for row in rows]}

    def _drive_preflights(self):
        if self.closed or not self.batch or self.batch.get('status') != 'planning' or self.batch.get('stale'):
            return
        self._sync_batch_rows()
        for row in self.batch['rows']:
            version = row['versionId']
            if row['phase'] in ('pending', 'preflight'):
                if version in self.processes:
                    self._batch_planning_version = version
                    return
                try:
                    self._batch_planning_version = version
                    self.plan('', row['track'])
                    self._save_batch()
                    return
                except (OSError, ValueError, TypeError) as exc:
                    row.update(phase='failed', detail=self._detail(exc))
                    self.reports[version] = {'phase': 'blocked', 'detail': row['detail']}
        self._batch_planning_version = None
        self.batch['status'] = 'completed' if all(row['phase'] in ('ready', 'candidate_ready') for row in self.batch['rows']) else 'planned'
        self.batch['detail'] = '本地预检已完成；审阅批次总费用后启动，仅对缺少缓存的歌曲新增识别'
        self._save_batch()
        self._changed()

    def start_batch(self, batch_id):
        if not self.batch or batch_id != self.batch.get('id'):
            raise ValueError('批次已变化，请审阅当前批次后再启动')
        self._sync_batch_rows()
        reason = self._batch_block()
        if reason:
            raise ValueError(reason)
        self.batch.update(status='running', detail='单首处理；完成后缓存歌词，再准备下一首')
        self._save_batch()
        self._drive_batch(allow_query=True)
        self._changed()

    def pause_batch(self):
        if self.batch and self.batch.get('status') in ('running', 'planning'):
            self.batch.update(status='paused', detail='批次已暂停，不启动下一首；已受理的云端任务保留')
            self._save_batch()
            self._changed()

    def _batch_process_finished(self, version, action):
        if not self.batch or version not in {row['versionId'] for row in self.batch['rows']}:
            return
        self._sync_batch_rows()
        self._save_batch()
        if action == 'preflight' and self.batch.get('status') == 'planning':
            self._drive_preflights()
        elif action != 'preflight' and self.batch.get('status') == 'running':
            self._drive_batch(allow_query=False)

    def _drive_batch(self, *, allow_query):
        if self.closed or self.offline_only or not self.batch or self.batch.get('status') != 'running':
            return
        if any(entry['action'] != 'preflight' for entry in self.processes.values()):
            return
        self._sync_batch_rows()
        if self._batch_current_version:
            version = self._batch_current_version
            report = self.reports.get(version, {})
            job = self._job(version)
            if not self._cached_lyrics(version, self.tracks[version]) and report.get('phase') in ('polling', 'queued') and job and job.get('taskId'):
                if allow_query:
                    try:
                        self.query('', self.tracks[version])
                    except (OSError, ValueError) as exc:
                        self.reports[version] = {'phase': 'blocked', 'detail': self._detail(exc)}
                        self._batch_current_version = None
                        self._sync_batch_rows()
                        self._save_batch()
                        self._changed()
                return
            self._batch_current_version = None
        if self.batch.get('stale') or self.batch.get('generation') != self._plan_generation:
            self.batch.update(status='paused', detail='批次计划已失效，请重新规划')
            self._save_batch()
            self._changed()
            return
        for row in self.batch['rows']:
            version = row['versionId']
            job = self._job(version)
            known_unknown = row['phase'] == 'unknown' and job and job.get('taskId') and not row.get('attempted')
            if row['phase'] in ('ready', 'candidate_ready') or (row['phase'] in ('failed', 'blocked', 'unknown') and not known_unknown):
                continue
            try:
                self._batch_current_version = version
                row['attempted'] = True
                if job is not None:
                    if not job.get('taskId') or job.get('state') == 'failed':
                        self._batch_current_version = None
                        continue
                    self.query('', row['track'])
                else:
                    self.start('', row['track'], row['planId'])
                self._save_batch()
                return
            except (OSError, ValueError) as exc:
                self._batch_current_version = None
                row.update(phase='blocked', detail=self._detail(exc))
                self.reports[version] = {'phase': 'blocked', 'detail': row['detail']}
        self.batch.update(status='completed', detail='批次处理已结束；待核对或失败的歌曲保留记录，不重复付费')
        self._save_batch()
        self._changed()

    def correct(self, deck, track, reference_path):
        from worker.correction import correct_with_reference
        version = _version(track)
        if not _proof_current(track):
            raise ValueError('需要当前歌曲的有效文件身份')
        source = Path(reference_path).resolve(strict=True)
        if source.stat().st_size > 100_000:
            raise ValueError('参考歌词文件过大')
        text = source.read_text(encoding='utf-8-sig')
        self.references[version] = {'path': str(source), 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
        _atomic(self._receipt(version).with_name('reference.json'),
                dict(self.references[version], versionId=version))
        self.tracks[version] = dict(track)
        if version not in self.timelines:
            self.reports[version] = {'phase': 'reference_ready', 'detail': '识别完成后先按此参考纠错，再发布歌词',
                                    'referencePath': str(source)}
            self._changed()
            return
        corrected = correct_with_reference(self.timelines[version], text,
                                            reference_label=source.name)
        self.library.add_timeline(corrected, track)
        self.timelines[version] = corrected
        self.on_timeline(version, corrected)
        correction = corrected.get('correction', {})
        summary = f"已改 {len(correction.get('changes', []))} 句，待复核 {len(correction.get('review', []))} 句"
        self.reports[version] = {'phase': 'needs_review' if correction.get('status') == 'needs_review' else 'corrected',
            'referencePath': str(source), 'correctionSummary': summary,
            'detail': summary + '；保留源时间轴，未明确匹配的片段保留原文'}
        self._changed()

    def export(self, deck, track, path, format):
        from worker.subtitle_export import write_subtitles
        version = _version(track)
        if version not in self.timelines or not _proof_current(track):
            raise ValueError('需要当前实际歌曲的有效歌词缓存')
        result = write_subtitles(self.timelines[version], path, format=format)
        return str(result)

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        self._key = ''
        self._redaction_keys.clear()
        entries = list(self.processes.values())
        self.processes.clear()
        for entry in entries:
            process = entry['process']
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(500)
