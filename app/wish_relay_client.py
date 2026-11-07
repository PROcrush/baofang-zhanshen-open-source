"""Asynchronous HTTPS polling for the DJ's public wish session."""
import json
import os
import re
import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from app.guest_inbox import MAX_NAME, MAX_QUEUE, MAX_TEXT, clean_text
from app.wish_relay import relay_origin, valid_key

ROOM_ID = re.compile(r'[A-Za-z0-9_-]{24}')
MAX_REPLY = 16384


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
        timeout = QTimer(reply, singleShot=True, interval=6000)
        timeout.timeout.connect(reply.abort)
        timeout.start()

        def finished():
            timeout.stop()
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if reply.error() != QNetworkReply.NetworkError.NoError and status in (200, 201):
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
        if reply is not self.reply:
            return
        self.reply = None
        room, key = data.get('room'), data.get('key')
        if (status == 201 and isinstance(room, str) and ROOM_ID.fullmatch(room)
                and isinstance(key, str) and valid_key(key)):
            self.room, self.room_key = room, key
            self.timer.start()
            self._status('ready', '公网扫码已开启，客人可用手机流量发送')
        else:
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
            self.stop('error', '这次公网扫码已结束，请重新开启并更换二维码')
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

    def stop(self, phase='idle', detail='', *, notify=True, close_room=True):
        self.timer.stop()
        reply, self.reply = self.reply, None
        if reply:
            reply.abort()
        if self.room and close_room:
            self._request('/api/rooms/' + self.room, self.room_key, lambda *_: None)
        self.room, self.room_key = '', ''
        if notify:
            self._status(phase, detail)
        else:
            self.phase, self.detail = phase, detail
