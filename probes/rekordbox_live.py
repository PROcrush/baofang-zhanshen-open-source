"""Read-only source-position bridge for the approved rekordbox 7.2.19 test copy.

This protocol uses observed positions only. Normal tempo/play-request fields
are informational; neither is used to extrapolate the lyric clock. The older
experimental JSON remains incompatible with the controller.
"""
import json
import signal
import struct
import threading
import time
import uuid

from app.clock import monotonic_ns
from probes.rekordbox_native import DEFAULT_TEST_APP, NativeReadOnlyTask
from probes.rekordbox_mixer import read_mixer
from probes.rekordbox_reader import ReaderError, RekordboxExperimentalReader


TTL_NS = 350_000_000
MAX_CAPTURE_NS = 100_000_000


class RekordboxLiveReader:
    def __init__(self, *, reader_factory=None, clock=monotonic_ns):
        self.reader = (reader_factory or (lambda: RekordboxExperimentalReader(
            DEFAULT_TEST_APP, native_factory=NativeReadOnlyTask)))()
        self.clock = clock
        self.epoch = 'rekordbox:' + uuid.uuid4().hex
        self.sequence = 0

    def __enter__(self):
        self.reader.__enter__()
        return self

    def __exit__(self, *args):
        self.reader.__exit__(*args)

    def read_frame(self):
        started = self.clock()
        observed = self.reader.read_frame()
        if self.clock() - started > MAX_CAPTURE_NS:
            raise ReaderError('capture_timeout')
        fader = read_mixer(self.reader)
        self.reader._check_process()
        if self.clock() - started > MAX_CAPTURE_NS:
            raise ReaderError('capture_timeout')
        decks = []
        for item in observed['decks']:
            available = item['status'] == 'observed_unvalidated'
            decks.append({'deck': item['deck'], 'available': available,
                          'trackPath': item['trackPath'] if available else '',
                          'positionSeconds': item['positionSeconds'] if available else None,
                          'playRequested': item['playRequested'] if available else False,
                          'normalRate': item['normalRateCandidate'] if available else None})
        self.sequence += 1
        return {'type': 'rekordbox_frame', 'mode': 'rekordbox', 'protocolVersion': 1,
                'epoch': self.epoch, 'sequence': self.sequence, 'positionOnly': True,
                'producerVerified': True, 'capturedNs': started, 'validUntilNs': started + TTL_NS,
                'decks': decks, 'fader': fader,
                'diagnostics': {'profile': observed['application'], 'process': observed['process'],
                                'clockMode': 'observed', 'captureNs': self.clock() - started,
                                'decks': {d['deck']: {'status': d['status'], 'error': d.get('error')}
                                          for d in observed['decks']},
                                'officialCompatibilityVerified': False}}


def status(error):
    descriptions = {'not_running': '请打开已准备的 rekordbox 测试副本（7.2.19）',
                    'process_identity_not_unique': '等待已准备的 rekordbox 测试副本，当前原版不会被读取',
                    'task_port_denied': 'rekordbox 读取权限未就绪，请查看连接诊断',
                    'unsupported_application': '当前 rekordbox 版本尚不支持，请使用已验证的 7.2.19 测试副本'}
    return {'type': 'rekordbox_status', 'mode': 'rekordbox', 'producerVerified': False,
            'detail': descriptions.get(error.code, 'rekordbox 状态暂不可用，歌词已隐藏；正在重试'),
            'diagnostics': {'code': error.code, 'detail': error.detail}}


def run(stop, emit, *, factory=RekordboxLiveReader, timer=time.monotonic):
    while not stop.is_set():
        try:
            with factory() as reader:
                # The native helper has a hard 300-second lifetime. Rotate our
                # owned attachment early, with a fresh epoch and identity check.
                deadline = timer() + 290
                while not stop.is_set() and timer() < deadline:
                    emit(reader.read_frame())
                    stop.wait(.025)
        except (ReaderError, OSError, ValueError, struct.error) as error:
            if not isinstance(error, ReaderError):
                error = ReaderError('observation_unavailable', str(error))
            emit(status(error))
            stop.wait(2)


def main():
    stop = threading.Event()
    for kind in (signal.SIGTERM, signal.SIGINT):
        signal.signal(kind, lambda *_: stop.set())
    run(stop, lambda frame: print(json.dumps(frame, ensure_ascii=False, allow_nan=False), flush=True))


if __name__ == '__main__':
    main()
