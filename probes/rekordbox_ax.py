"""Read the original rekordbox's displayed A/B clocks using macOS AX only.

The helper is our own bounded, read-only program. It never signs or modifies
rekordbox, requests a task port, executes AX actions, or extrapolates time.
AX titles are display metadata, not a file or audio-version identity.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from app.clock import monotonic_ns
from app.runtime_paths import default_runtime, packaged_info
from probes.rekordbox_probe import DEFAULT_APP, executable_path
from probes.rekordbox_reader import ReaderError, VERSION, _find_pid


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'probes/native/rekordbox_ax.m'
RUNTIME = default_runtime(ROOT) / 'rekordbox-ax-helper'
HELPER_APP = ((ROOT / 'helpers') if packaged_info(ROOT) is not None else RUNTIME) / '包房战神-rekordbox-AX.app'
HELPER = HELPER_APP / 'Contents/MacOS/rekordbox-ax'
SESSION_ROOT = Path(tempfile.gettempdir())
TTL_NS = 350_000_000
MAX_CAPTURE_NS = 200_000_000
# A confirmed file is not an observed clock. Brief AX stalls may retain the
# confirmation, while every old clock still expires after TTL_NS.
MAX_IDENTITY_GAP_NS = 2_000_000_000
MAX_RESPONSE = 256 * 1024
MAX_READ_ATTEMPTS = 3
TRANSIENT_ERRORS = frozenset(('inconsistent_ax_observation', 'clock_fields_missing',
    'deck_title_missing', 'deck_clock_pair_missing', 'ax_fields_unavailable',
    'track_changed_during_capture', 'duration_changed_during_capture',
    'window_not_unique', 'layout_scan_timeout', 'capture_timeout'))
CLOCK = re.compile(r'^(?P<negative>-?)(?P<minutes>\d{2,3}):(?P<seconds>[0-5]\d)$')
TENTHS = re.compile(r'^\.([0-9])$')


def empty_deck(side):
    return {'deck': side, 'available': False, 'title': '', 'positionSeconds': None,
            'remainingSeconds': None, 'durationSeconds': None, 'normalRate': None,
            'playRequested': False, 'observationId': ''}


def _scalars(nodes):
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 200:
        raise ReaderError('layout_scalar_bounds')
    result = []
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('text'), str):
            raise ReaderError('invalid_ax_snapshot')
        text = node['text'].strip()
        if len(text) > 4096 or not isinstance(node.get('role'), str):
            raise ReaderError('invalid_ax_snapshot')
        result.append({**node, 'text': text})
    return result


def _parse_sample(nodes):
    nodes = _scalars(nodes)
    # This first implementation deliberately supports one observed layout.
    if (not any(n['text'] == '2Deck Horizontal' for n in nodes) or
            not any(n['text'] == 'PERFORMANCE' for n in nodes)):
        raise ReaderError('unsupported_layout', '需要 2Deck Horizontal 双盘布局')
    static = [n for n in nodes if n['role'] in ('AXStaticText', 'AXImage') and
              (n['text'] or n['role'] == 'AXImage')]
    groups = []
    for index, node in enumerate(static):
        clock = CLOCK.fullmatch(node['text'])
        if not clock or not clock['negative']:
            continue
        tail = static[index:index + 4]
        if len(tail) != 4:
            raise ReaderError('clock_fields_missing')
        remaining_tenth, elapsed, elapsed_tenth = (TENTHS.fullmatch(tail[1]['text']),
            CLOCK.fullmatch(tail[2]['text']), TENTHS.fullmatch(tail[3]['text']))
        if not remaining_tenth or not elapsed or elapsed['negative'] or not elapsed_tenth:
            raise ReaderError('clock_fields_missing')
        prior_images = [i for i in range(max(0, index - 20), index)
                        if static[i]['role'] == 'AXImage']
        if not prior_images:
            raise ReaderError('deck_title_missing')
        image = prior_images[-1]
        if not image or static[image - 1]['role'] != 'AXStaticText':
            raise ReaderError('deck_title_missing')
        title = static[image - 1]['text']
        if not title or len(title) > 512 or CLOCK.fullmatch(title) or TENTHS.fullmatch(title):
            raise ReaderError('deck_title_missing')
        position = int(elapsed['minutes']) * 60 + int(elapsed['seconds']) + int(elapsed_tenth[1]) / 10
        remaining = int(clock['minutes']) * 60 + int(clock['seconds']) + int(remaining_tenth[1]) / 10
        duration = round(position + remaining, 1)
        empty = duration == 0 and title in ('Not Loaded.', 'Not Loaded', '未载入', '未加载')
        if not empty and not 0 < duration <= 86400:
            raise ReaderError('clock_bounds')
        x, y = node.get('x'), node.get('y')
        if (type(x) not in (int, float) or type(y) not in (int, float) or
                not -100000 <= x <= 100000 or not -100000 <= y <= 100000 or
                not math.isfinite(x) or not math.isfinite(y)):
            raise ReaderError('deck_geometry_missing')
        groups.append({'title': title, 'available': not empty, 'positionSeconds': position,
                       'remainingSeconds': remaining, 'durationSeconds': duration, 'x': x, 'y': y})
    if len(groups) != 2:
        raise ReaderError('deck_clock_pair_missing')
    groups.sort(key=lambda group: group['x'])
    if groups[1]['x'] - groups[0]['x'] < 100 or abs(groups[1]['y'] - groups[0]['y']) > 60:
        raise ReaderError('unsupported_layout', '双盘时钟必须左右排列')
    return {side: value for side, value in zip('AB', groups)}


def parse_observations(first, second):
    """Two independently copied scalar sets must agree across seconds/track changes."""
    before, after = _parse_sample(first), _parse_sample(second)
    changed = tuple(side for side in 'AB' if before[side]['available'] != after[side]['available']
                    or before[side]['title'] != after[side]['title'])
    if changed:
        error = ReaderError('track_changed_during_capture')
        error.changed_decks = changed
        raise error
    result = []
    for side in 'AB':
        previous, current = before[side], after[side]
        if (abs(previous['positionSeconds'] - current['positionSeconds']) > .300001 or
                abs(previous['durationSeconds'] - current['durationSeconds']) > .200001 or
                abs(previous['x'] - current['x']) > 1 or abs(previous['y'] - current['y']) > 1):
            raise ReaderError('inconsistent_ax_observation')
        if not current['available']:
            result.append(empty_deck(side))
            continue
        result.append({'deck': side, 'available': True, **{key: current[key] for key in
            ('title', 'positionSeconds', 'remainingSeconds', 'durationSeconds')},
            'normalRate': 1.0, 'playRequested': False})
    return result


def inspect_original():
    app = DEFAULT_APP.resolve(strict=True)
    if app != DEFAULT_APP.absolute():
        raise ReaderError('original_target_not_allowed', '不读取符号链接或测试副本')
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleIdentifier') != 'com.pioneerdj.rekordboxdj' or
            info.get('CFBundleExecutable') != 'rekordbox' or
            info.get('CFBundleShortVersionString') != VERSION):
        raise ReaderError('unsupported_application', '当前仅验证原版 7.2.19.0342')
    binary = (app / 'Contents/MacOS/rekordbox').resolve(strict=True)
    if binary != app / 'Contents/MacOS/rekordbox':
        raise ReaderError('original_target_not_allowed')
    return {'app': str(app), 'binary': str(binary), 'version': VERSION,
            'sourceKind': 'accessibility', 'applicationModified': False}


def _hash(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def prepare_helper():
    """Compile only our own local helper. No codesign is invoked anywhere here."""
    if sys.platform != 'darwin':
        raise ReaderError('unsupported_platform')
    profile = inspect_original()
    package = packaged_info(ROOT)
    if package is not None:
        expected = package.get('axHelperSha256', '')
        if (not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected) or
                HELPER.resolve() != HELPER.absolute() or
                not HELPER.is_file() or not os.access(HELPER, os.X_OK) or _hash(HELPER) != expected):
            raise ReaderError('ax_helper_package_invalid', '随包辅助功能助手缺失或损坏，请重新安装包房战神')
        RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
        return HELPER
    digest = _hash(SOURCE)
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = RUNTIME / 'build.json'
    with (RUNTIME / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            manifest = json.loads(manifest_path.read_text())
            if (not HELPER.is_symlink() and manifest.get('sourceSha256') == digest and
                    manifest.get('helperSha256') == _hash(HELPER)):
                return HELPER
        except (OSError, ValueError):
            pass
        (HELPER_APP / 'Contents/MacOS').mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='build-', dir=RUNTIME) as temporary:
            candidate = Path(temporary) / 'rekordbox-ax'
            command = ['/usr/bin/clang', '-fobjc-arc', '-O2', '-Wall', '-Wextra', '-Werror',
                '-framework', 'Foundation', '-framework', 'ApplicationServices',
                '-DREKORDBOX_TARGET_PATH=' + json.dumps(profile['binary']),
                str(SOURCE), '-o', str(candidate)]
            try:
                subprocess.run(command, capture_output=True, check=True, timeout=60)
            except (OSError, subprocess.SubprocessError) as error:
                detail = getattr(error, 'stderr', b'') or str(error).encode()
                raise ReaderError('ax_helper_build_failed', detail.decode(errors='replace')[:1000]) from None
            if digest != _hash(SOURCE):
                raise ReaderError('ax_helper_source_changed')
            candidate.chmod(0o700)
            os.replace(candidate, HELPER)
        (HELPER_APP / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'local.baofangzhanshen.rekordbox-ax',
            'CFBundleName': '包房战神-rekordbox-AX', 'CFBundleDisplayName': '包房战神-rekordbox-AX',
            'CFBundleExecutable': 'rekordbox-ax', 'CFBundlePackageType': 'APPL',
            'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0', 'LSUIElement': True,
            'NSAccessibilityUsageDescription': '只读原版 rekordbox 双盘曲名和已播时间以显示歌词。'}))
        manifest_path.write_text(json.dumps({'sourceSha256': digest, 'helperSha256': _hash(HELPER),
            'targetExecutable': profile['binary'], 'signingCommandInvoked': False}, indent=2) + '\n')
    return HELPER


class RekordboxAXReader:
    def __init__(self, *, clock=monotonic_ns, helper_builder=prepare_helper, request_access=False,
                 application_host=False):
        if type(request_access) is not bool or type(application_host) is not bool:
            raise ValueError('permission and application host options must be explicit booleans')
        self.clock, self.builder = clock, helper_builder
        self.request_access = request_access
        self.application_host = application_host
        self.process = self.selector = None
        self.stdin = self.stdout = None
        self.session = None
        self.launch_log = None
        self.pending = bytearray()
        self.epoch = 'rekordbox-ax:' + uuid.uuid4().hex
        self.sequence = 0
        self.observed = {}
        self.identity_changes = []
        self.identity_history = []
        self.rejection_history = []
        self.counters = {'frames': 0, 'retries': 0, 'recoveredFrames': 0,
                         'rejectedFrames': 0, 'identityInvalidations': 0}

    def __enter__(self):
        self.profile = inspect_original()
        self.pid = _find_pid(Path(self.profile['binary']), executable_path)
        helper = self.builder()
        try:
            command = [str(helper), str(self.pid)]
            if self.request_access:
                command.append('--request-access')
            if self.application_host:
                # xpcproxy opens these streams before the app starts. Keeping
                # them in Documents would require unrelated folder permission.
                self.session = Path(tempfile.mkdtemp(prefix='baofang-rekordbox-ax-', dir=SESSION_ROOT))
                input_path, output_path = self.session / 'requests.fifo', self.session / 'responses.fifo'
                for path in (input_path, output_path):
                    os.mkfifo(path, 0o600)
                error_path = self.session / 'helper.stderr.log'
                os.close(os.open(error_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
                launch_error_path = self.session / 'launch.stderr.log'
                self.launch_log = os.fdopen(os.open(launch_error_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb', buffering=0)
                # Holding both FIFO ends avoids LaunchServices' open ordering
                # deadlocks. Only our helper can access the private directory.
                self.stdin = os.fdopen(os.open(input_path, os.O_RDWR | os.O_NONBLOCK), 'wb', buffering=0)
                self.stdout = os.fdopen(os.open(output_path, os.O_RDWR | os.O_NONBLOCK), 'rb', buffering=0)
                arguments = command[1:] + ['--owner-pid', str(os.getpid())]
                launch = ['/usr/bin/open', '-n', '-g', '-W', '-a', str(HELPER_APP),
                    '--stdin', str(input_path), '--stdout', str(output_path),
                    '--stderr', str(error_path), '--args', *arguments]
                self.process = subprocess.Popen(launch, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=self.launch_log, bufsize=0)
            else:
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                self.stdin, self.stdout = self.process.stdin, self.process.stdout
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.stdout, selectors.EVENT_READ)
            ready = self._line(timeout=8 if self.application_host else 2)
            if ready != {'ok': True, 'ready': True}:
                raise ReaderError(ready.get('code', 'ax_helper_handshake_invalid'))
            return self
        except Exception as error:
            try:
                if self.application_host and self.session:
                    diagnostics = {'code': getattr(error, 'code', type(error).__name__),
                        'openReturnCode': self.process.poll() if self.process else None,
                        'helperStderr': '', 'launchStderr': ''}
                    for name, key in (('helper.stderr.log', 'helperStderr'), ('launch.stderr.log', 'launchStderr')):
                        try:
                            diagnostics[key] = (self.session / name).read_bytes()[:4096].decode(errors='replace')
                        except OSError:
                            pass
                    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
                    (RUNTIME / 'last-launch-error.json').write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + '\n')
                    if isinstance(error, ReaderError):
                        detail = diagnostics['launchStderr'] or diagnostics['helperStderr']
                        if detail:
                            error.detail = detail[:1000]
            except (OSError, ValueError, TypeError):
                # Diagnostic storage is best-effort; preserve the actual read
                # failure and always remove FIFO streams and the session.
                pass
            finally:
                self.close()
            raise

    def close(self):
        if self.process:
            try:
                if self.process.poll() is None:
                    self.stdin.write(b'Q\n')
                    self.stdin.flush()
                    if self.application_host:
                        self.stdin.close()
                        self.stdout.close()
                    self.process.wait(timeout=2 if self.application_host else .5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
            for stream in (self.stdin, self.stdout):
                if stream and not stream.closed:
                    stream.close()
            self.process = None
        if self.selector:
            self.selector.close()
            self.selector = None
        for stream in (self.stdin, self.stdout):
            if stream and not stream.closed:
                stream.close()
        self.stdin = self.stdout = None
        if self.launch_log:
            self.launch_log.close()
            self.launch_log = None
        if self.session:
            shutil.rmtree(self.session)
            self.session = None

    def __exit__(self, *_):
        self.close()

    def _line(self, *, timeout=2):
        deadline = time.monotonic() + timeout
        while True:
            if b'\n' in self.pending:
                line, _, rest = self.pending.partition(b'\n')
                self.pending = bytearray(rest)
                if rest:
                    raise ReaderError('ax_helper_unsolicited_output')
                try:
                    result = json.loads(line)
                    if not isinstance(result, dict):
                        raise ValueError()
                    return result
                except (ValueError, UnicodeError):
                    raise ReaderError('ax_helper_response_invalid') from None
            if len(self.pending) > MAX_RESPONSE:
                raise ReaderError('ax_helper_response_too_large')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReaderError('ax_helper_timeout')
            ready = self.selector.select(min(remaining, .25) if self.application_host else remaining)
            if not ready:
                if self.application_host and self.process.poll() is not None:
                    raise ReaderError('ax_helper_launch_failed')
                continue
            block = os.read(self.stdout.fileno(), min(8192, MAX_RESPONSE + 1 - len(self.pending)))
            if not block:
                raise ReaderError('ax_helper_eof')
            self.pending.extend(block)

    def forget_observations(self, reason, sides='AB'):
        for side in sides:
            previous = self.observed.pop(side, None)
            if previous:
                self.counters['identityInvalidations'] += 1
                self.identity_changes.append({'deck': side, 'reason': reason,
                                              'previousObservationId': previous['observationId']})
                self.identity_history.append(dict(self.identity_changes[-1]))
        self.identity_changes = self.identity_changes[-16:]
        self.identity_history = self.identity_history[-16:]

    def expire_observations(self, now):
        for side, previous in list(self.observed.items()):
            if now > previous['identityValidUntilNs']:
                self.forget_observations('observation_continuity_expired', (side,))

    def reader_diagnostics(self):
        return {'identityChanges': list(self.identity_changes),
                'recentIdentityChanges': list(self.identity_history),
                'recentRejectedFrames': list(self.rejection_history),
                'readerCounters': dict(self.counters)}

    def read_frame(self):
        started = self.clock()
        self.expire_observations(started)
        recovered_codes = []
        pending_durations = {}
        for attempt in range(MAX_READ_ATTEMPTS):
            finished = None
            writer = self.stdin if self.stdin is not None else self.process.stdin
            writer.write(b'R\n')
            writer.flush()
            try:
                raw = self._line()
                finished = self.clock()
                # All retries share the original budget and TTL. Never publish
                # old clocks or extend freshness because a retry succeeded.
                if finished - started > MAX_CAPTURE_NS:
                    raise ReaderError('capture_timeout')
                if not raw.get('ok'):
                    raise ReaderError(raw.get('code', 'ax_observation_unavailable'))
                decks = parse_observations(raw.get('first'), raw.get('second'))
                # Both native copies can catch the same half-refreshed clock
                # pair. Confirm a changed total in another fresh capture before
                # treating a same-title track as a different audio version.
                unconfirmed = False
                for deck in decks:
                    side = deck['deck']
                    previous = self.observed.get(side)
                    if (deck['available'] and previous and previous['title'] == deck['title']
                            and abs(previous['durationSeconds'] - deck['durationSeconds']) > .300001):
                        candidate = pending_durations.get(side)
                        if candidate is None or abs(candidate - deck['durationSeconds']) > .200001:
                            pending_durations[side] = deck['durationSeconds']
                            unconfirmed = True
                if unconfirmed:
                    raise ReaderError('duration_changed_during_capture')
            except ReaderError as error:
                if finished is None:
                    finished = self.clock()
                changed = getattr(error, 'changed_decks', ())
                if changed:
                    self.forget_observations('track_changed_during_capture', changed)
                if error.code not in TRANSIENT_ERRORS:
                    self.forget_observations(error.code)
                self.expire_observations(finished)
                if (error.code in TRANSIENT_ERRORS and error.code != 'capture_timeout'
                        and attempt + 1 < MAX_READ_ATTEMPTS and finished - started < MAX_CAPTURE_NS):
                    recovered_codes.append(error.code)
                    self.counters['retries'] += 1
                    continue
                self.counters['rejectedFrames'] += 1
                self.rejection_history.append({'code': error.code, 'capturedNs': started,
                    'finishedNs': finished, 'readAttempts': attempt + 1})
                self.rejection_history = self.rejection_history[-8:]
                error.ax_diagnostics = self.reader_diagnostics()
                error.ax_diagnostics.update(readAttempts=attempt + 1, recoveryCodes=recovered_codes)
                raise
            break
        self.expire_observations(finished)
        for deck in decks:
            if not deck['available']:
                self.forget_observations('deck_unloaded', (deck['deck'],))
                continue
            previous = self.observed.get(deck['deck'])
            if previous and (previous['title'] != deck['title'] or
                    abs(previous['durationSeconds'] - deck['durationSeconds']) > .300001):
                self.forget_observations('track_metadata_changed', (deck['deck'],))
                previous = None
            if previous is None:
                previous = {'title': deck['title'], 'durationSeconds': deck['durationSeconds'],
                            'observationId': uuid.uuid4().hex}
                self.observed[deck['deck']] = previous
            previous['identityValidUntilNs'] = started + MAX_IDENTITY_GAP_NS
            deck['observationId'] = previous['observationId']
        self.sequence += 1
        self.counters['frames'] += 1
        self.counters['recoveredFrames'] += bool(recovered_codes)
        diagnostics = self.reader_diagnostics()
        self.identity_changes.clear()
        return {'type': 'rekordbox_ax_frame', 'mode': 'rekordbox_ax', 'protocolVersion': 1,
            'sourceKind': 'accessibility', 'producerVerified': True, 'positionOnly': True,
            'epoch': self.epoch, 'sequence': self.sequence, 'capturedNs': started,
            'validUntilNs': started + TTL_NS, 'decks': decks, 'fader': None,
            'diagnostics': {'profile': self.profile, 'process': raw.get('process'),
                'clockMode': 'observed', 'resolutionMs': 100, 'captureNs': finished - started,
                'layout': '2Deck Horizontal', 'playbackStateObserved': False,
                'tempoObserved': False, 'normalRateIsNeutralPlaceholder': True,
                'processIdentityCheckedBeforeAndAfterCapture': True,
                'fileIdentityObserved': False, 'originalCompatibilityVerified': False,
                'readAttempts': attempt + 1, 'recoveryCodes': recovered_codes, **diagnostics}}


def status(error):
    descriptions = {'not_running': '请打开 /Applications 中的原版 rekordbox 7.2.19',
        'process_identity_not_unique': '等待唯一原版 rekordbox 进程；测试副本不会被读取',
        'accessibility_permission_required': '请在系统设置 → 隐私与安全性 → 辅助功能，允许包房战神-rekordbox-AX读取界面',
        'unsupported_layout': '请使用 rekordbox PERFORMANCE 的 2Deck Horizontal 布局',
        'window_minimized': 'rekordbox 窗口已最小化，歌词已隐藏',
        'capture_timeout': 'rekordbox 界面采集较慢，歌词已隐藏；正在重试'}
    return {'type': 'rekordbox_ax_status', 'mode': 'rekordbox_ax', 'producerVerified': False,
        'sourceKind': 'accessibility', 'detail': descriptions.get(error.code, '原版 rekordbox AX 状态不可用，歌词已隐藏；正在重试'),
        'diagnostics': {'code': error.code, 'detail': error.detail, **getattr(error, 'ax_diagnostics', {})},
        'decks': [empty_deck(side) for side in 'AB']}


def run(stop, emit, *, factory=RekordboxAXReader, interval=.05, duration=None, once=False):
    deadline = time.monotonic() + duration if duration is not None else math.inf
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            with factory() as reader:
                while not stop.is_set() and time.monotonic() < deadline:
                    try:
                        emit(reader.read_frame())
                    except ReaderError as error:
                        # Keep cached AX handles during a bounded warm-up/clock race.
                        if error.code in ('ax_helper_eof', 'ax_helper_timeout', 'process_identity_changed',
                                          'process_identity_unavailable', 'accessibility_permission_required'):
                            raise
                        emit(status(error))
                    if once:
                        return
                    stop.wait(interval)
        except (ReaderError, OSError, ValueError) as error:
            if not isinstance(error, ReaderError):
                error = ReaderError('ax_observation_unavailable', str(error))
            emit(status(error))
            if once:
                return
            stop.wait(min(2, max(0, deadline - time.monotonic())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--request-access', action='store_true',
                        help='With --once only, ask macOS to show our own AX permission prompt; never grants access')
    parser.add_argument('--application-host', action='store_true',
                        help='Launch our named AX helper app through LaunchServices with private FIFO transport')
    parser.add_argument('--duration', type=float)
    parser.add_argument('--interval', type=float, default=.05)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.request_access and not args.once:
        parser.error('--request-access requires --once to avoid repeated permission prompts')
    if (not math.isfinite(args.interval) or not .025 <= args.interval <= 2 or
            args.duration is not None and (not math.isfinite(args.duration) or not 0 < args.duration <= 3600)):
        parser.error('interval must be .025..2; duration must be 0..3600')
    if args.output and not args.output.resolve().is_relative_to(default_runtime(ROOT).resolve()):
        parser.error('runtime logs must remain in the application data directory')
    stream = None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        stream = args.output.open('w')
    stop = threading.Event()
    for kind in (signal.SIGINT, signal.SIGTERM):
        signal.signal(kind, lambda *_: stop.set())
    def emit(frame):
        encoded = json.dumps(frame, ensure_ascii=False, allow_nan=False)
        print(encoded, flush=True)
        if stream:
            stream.write(encoded + '\n')
            stream.flush()
    try:
        factory = lambda: RekordboxAXReader(request_access=args.request_access,
                                          application_host=args.application_host)
        run(stop, emit, factory=factory, interval=args.interval, duration=args.duration, once=args.once)
    finally:
        if stream:
            stream.close()


if __name__ == '__main__':
    main()
