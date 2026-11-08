"""Asynchronous HTTPS polling for the DJ's public wish session."""
import json
import os
import re
import time
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from app.guest_inbox import MAX_NAME, MAX_QUEUE, MAX_TEXT, clean_text
from app.wish_relay import ROOM_IDLE_SECONDS, relay_origin, valid_key

ROOM_ID = re.compile(r'[A-Za-z0-9_-]{24}')
MAX_REPLY = 16384
REQUEST_TIMEOUT_MS = 6000


@dataclass
class ClosingRoom:
    key: str
    deadline: float
    reply: QNetworkReply | None = None


class WishRelayClient(QObject):
    changed = Signal()
    received = Signal(list)

    def __init__(self, parent, free_slots):
        super().__init__(parent)
        self.free_slots = free_slots
        self.origin, self.key = '', os.environ.get('BAOFANG_WISH_RELAY_KEY', '')
        try:
            if valid_key(self.key):
                self.origin = relay_origin(os.environ.get('BAOFANG_WISH_RELAY_URL', ''))
        except ValueError:
            pass
        self.network = QNetworkAccessManager(self)
        self.timer = QTimer(self, interval=2000)
        self.timer.timeout.connect(self.poll)
        self._close_timer = QTimer(self, interval=2000)
        self._close_timer.timeout.connect(self._retry_closures)
        self._closing = {}
        self._close_result = ''
        self._shutting_down = False
        self.reply = None
        self.room, self.room_key = '', ''
        self.phase, self.detail = 'idle', ''
        self.cursor = 0

    @property
    def active(self):
        return self.phase in ('starting', 'ready', 'retry')

    @property
    def url(self):
        return self.origin + '/m/' + self.room if self.room else ''

    @property
    def close_status(self):
        if self._closing:
            return '旧公网二维码关闭待确认，正在重试。旧码可能暂时可用。'
        return self._close_result

    def _status(self, phase, detail=''):
        if (self.phase, self.detail) != (phase, detail):
            self.phase, self.detail = phase, detail
            self.changed.emit()

    def _request(self, path, key, callback, payload=None):
        request = QNetworkRequest(QUrl(self.origin + path))
        request.setRawHeader(b'Authorization', ('Bearer ' + key).encode('ascii'))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(5000)
        if payload is None:
            reply = self.network.deleteResource(request)
        else:
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json')
            reply = self.network.post(request, json.dumps(payload).encode('ascii'))
        reply.setReadBufferSize(MAX_REPLY + 1)
        reply.readyRead.connect(lambda: reply.abort() if reply.bytesAvailable() > MAX_REPLY else None)
        # Bound the total time too, including a server that trickles bytes.
        timeout = QTimer(reply, singleShot=True, interval=REQUEST_TIMEOUT_MS)
        timeout.timeout.connect(reply.abort)
        timeout.start()

        def finished():
            timeout.stop()
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if reply.error() != QNetworkReply.NetworkError.NoError and status in (200, 201, 204):
                status = None
            try:
                data = json.loads(bytes(reply.readAll())) if reply.bytesAvailable() <= MAX_REPLY else None
            except (ValueError, RecursionError):
                data = None
            callback(reply, status, data if isinstance(data, dict) else {})
            reply.deleteLater()

        reply.finished.connect(finished)
        return reply

    def start(self):
        if self.active or not self.origin:
            return
        self.cursor = 0
        self._status('starting', '正在开启公网扫码…')
        self.reply = self._request('/api/rooms', self.key, self._opened, {})

    def _opened(self, reply, status, data):
        current = reply is self.reply
        if current:
            self.reply = None
        room, key = data.get('room'), data.get('key')
        if (status == 201 and isinstance(room, str) and ROOM_ID.fullmatch(room)
                and isinstance(key, str) and valid_key(key)):
            if not current:
                if not self._shutting_down:
                    self._queue_close(room, key)
                return
            self.room, self.room_key = room, key
            self.timer.start()
            self._status('ready', '公网扫码已开启，客人可用手机流量发送')
        elif current:
            detail = {401: '公网扫码授权失败，请检查中继配置',
                      429: '公网扫码服务繁忙，请稍后重试'}.get(status, '公网扫码未开启，请检查电脑网络或服务配置')
            self._status('error', detail)

    def poll(self):
        if self.reply is None and self.room:
            limit = max(0, min(MAX_QUEUE, self.free_slots()))
            self.reply = self._request('/api/rooms/' + self.room, self.room_key,
                                       self._polled, {'after': self.cursor, 'limit': limit})

    def _polled(self, reply, status, data):
        if reply is not self.reply:
            return
        self.reply = None
        if status == 404:
            self.stop('error', '这次公网扫码已结束，请重新开启并更换二维码', close_room=False)
            return
        wishes = data.get('wishes')
        if status != 200 or not isinstance(wishes, list) or len(wishes) > MAX_QUEUE:
            self._status('retry', '公网扫码连接中断，正在重连')
            return
        rows = []
        cursor = self.cursor
        for wish in wishes:
            if not isinstance(wish, dict) or type(wish.get('seq')) is not int or wish['seq'] != cursor + 1:
                self._status('retry', '公网扫码返回异常，正在重连')
                return
            cursor = wish['seq']
            text = clean_text(wish.get('text'), MAX_TEXT)
            if text:
                rows.append({'id': self.room + '-' + str(cursor), 'text': text,
                             'name': clean_text(wish.get('name'), MAX_NAME), 'time': time.strftime('%H:%M')})
        if rows:
            self.received.emit(rows)
        self.cursor = cursor
        self._status('ready', '公网扫码已开启，客人可用手机流量发送')

    def _queue_close(self, room, key, grace=0):
        self._closing[room] = ClosingRoom(key, time.monotonic() + ROOM_IDLE_SECONDS + grace)
        self._close_result = ''
        self._retry_closures()
        if not self._close_timer.isActive():
            self._close_timer.start()
        self.changed.emit()

    def _retry_closures(self):
        now = time.monotonic()
        expired = False
        for room, closing in tuple(self._closing.items()):
            if now >= closing.deadline:
                del self._closing[room]
                if closing.reply:
                    closing.reply.abort()
                expired = True
            elif closing.reply is None:
                closing.reply = self._request('/api/rooms/' + room, closing.key, partial(self._closed, room))
        if not self._closing:
            self._close_timer.stop()
        if expired:
            self._close_result = '已到旧公网二维码的过期时间，关闭未获远端确认。'
            self.changed.emit()

    def _closed(self, room, reply, status, _data):
        closing = self._closing.get(room)
        if closing is None or reply is not closing.reply:
            return
        closing.reply = None
        if status in (200, 204, 404):
            del self._closing[room]
            if not self._closing:
                self._close_timer.stop()
                if not self._close_result:
                    self._close_result = '旧公网二维码已关闭。'
                self.changed.emit()

    def stop(self, phase='idle', detail='', *, notify=True, close_room=True):
        self.timer.stop()
        opening = self.phase == 'starting'
        reply, self.reply = self.reply, None
        if reply and (not opening or not notify):
            reply.abort()
        room, key = self.room, self.room_key
        self.room, self.room_key = '', ''
        if notify:
            self._status(phase, detail)
            if room and close_room:
                # A poll that reached the relay before cancellation can renew the lease.
                self._queue_close(room, key, REQUEST_TIMEOUT_MS / 1000 if reply else 0)
        else:
            self._shutting_down = True
            self.phase, self.detail = phase, detail
            self._close_timer.stop()
            closing, self._closing = self._closing, {}
            for pending in closing.values():
                if pending.reply:
                    pending.reply.abort()
            if room and close_room:
                self._request('/api/rooms/' + room, key, lambda *_: None)
