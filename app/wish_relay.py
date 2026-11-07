"""In-memory public wish relay. Run create_app under a WSGI server behind HTTPS."""
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from urllib.parse import urlsplit

from app.guest_inbox import MAX_BODY, MAX_NAME, MAX_QUEUE, MAX_TEXT, PAGE, clean_text

ROOM_IDLE_SECONDS = 45
MAX_ROOMS = 32
TOKEN_PATTERN = re.compile(r'[A-Za-z0-9_-]{24,64}')


def relay_origin(value):
    url = urlsplit(value.strip())
    if (url.scheme != 'https' or not url.hostname or url.username is not None
            or url.path not in ('', '/') or url.query or url.fragment
            or any(ch.isspace() or ch == '\\' for ch in url.netloc)
            or url.port == 0 or len(url.netloc) > 200):
        raise ValueError('需要填写 HTTPS 服务地址')
    return 'https://' + url.netloc


def valid_key(value):
    return bool(TOKEN_PATTERN.fullmatch(value))


def _authorized(env, key):
    value = env.get('HTTP_AUTHORIZATION', '')
    return hmac.compare_digest(value.encode('utf-8'), ('Bearer ' + key).encode('ascii'))


def _payload(env):
    try:
        length = int(env.get('CONTENT_LENGTH', '0'))
        if not 0 < length <= MAX_BODY:
            return None
        data = json.loads(env['wsgi.input'].read(length))
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _client_address(env):
    address = env.get('REMOTE_ADDR', '')
    if address in ('127.0.0.1', '::1'):
        # The HTTPS proxy must overwrite this header with the peer address.
        try:
            return str(ipaddress.ip_address(env.get('HTTP_X_REAL_IP', '')))
        except ValueError:
            pass
    return address


@dataclass
class Room:
    key: str
    seen: float
    sequence: int = 0
    pending: deque = field(default_factory=deque)
    recent: deque = field(default_factory=deque)


class WishRelay:
    def __init__(self, key, clock=time.monotonic):
        if not valid_key(key):
            raise ValueError('请配置由随机生成器创建的中继密钥')
        self.key = key
        self.clock = clock
        self.rooms = {}
        self.lock = threading.Lock()

    def route(self, env):
        method, path = env['REQUEST_METHOD'], env['PATH_INFO']
        create = path == '/api/rooms' and method == 'POST'
        guest = re.fullmatch(r'/m/([A-Za-z0-9_-]{24})', path)
        dj = re.fullmatch(r'/api/rooms/([A-Za-z0-9_-]{24})', path)
        if create and not _authorized(env, self.key):
            return 401, {}
        if not (create or guest or dj):
            return 404, {}
        # Read the bounded body before taking the shared lock.
        payload = _payload(env) if method == 'POST' and not create else None
        with self.lock:
            now = self.clock()
            for room_id in tuple(self.rooms):
                if now - self.rooms[room_id].seen >= ROOM_IDLE_SECONDS:
                    del self.rooms[room_id]
            if create:
                if len(self.rooms) >= MAX_ROOMS:
                    return 429, {}
                room_id, dj_key = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
                self.rooms[room_id] = Room(dj_key, now)
                return 201, {'room': room_id, 'key': dj_key}
            room_id = (guest or dj)[1]
            room = self.rooms.get(room_id)
            if guest:
                if not room:
                    message = '这次祝福已结束，请向 DJ 获取新的二维码。'
                    if method == 'GET':
                        return 410, '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><p>' + message + '</p>'
                    return 410, {'message': message}
                if method == 'GET':
                    return 200, PAGE
                if method == 'POST':
                    return self.accept(room, _client_address(env), payload, now)
            if dj:
                if not room or not _authorized(env, room.key):
                    return 404, {}
                if method == 'DELETE':
                    del self.rooms[room_id]
                    return 200, {}
                if method == 'POST':
                    after, limit = (payload or {}).get('after'), (payload or {}).get('limit')
                    if (type(after) is not int or not 0 <= after <= room.sequence
                            or type(limit) is not int or not 0 <= limit <= MAX_QUEUE):
                        return 400, {}
                    room.seen = now
                    while room.pending and room.pending[0]['seq'] <= after:
                        room.pending.popleft()
                    return 200, {'wishes': list(room.pending)[:limit]}
        return 405, {}

    @staticmethod
    def accept(room, client, payload, now):
        text = clean_text((payload or {}).get('text'), MAX_TEXT)
        name = clean_text((payload or {}).get('name'), MAX_NAME)
        if not text:
            return 400, {'message': '先写一句话'}
        while room.recent and now - room.recent[0][0] >= 20:
            room.recent.popleft()
        if (len(room.pending) >= MAX_QUEUE or len(room.recent) >= 60
                or sum(address == client for _, address in room.recent) >= 8):
            return 429, {'message': '发得太快或待审已满，请稍后再试'}
        room.sequence += 1
        room.recent.append((now, client))
        room.pending.append({'seq': room.sequence, 'text': text, 'name': name})
        return 200, {'message': '已提交，等待 DJ 审核'}

    def __call__(self, env, start_response):
        status, body = self.route(env)
        html = isinstance(body, str)
        data = (body if html else json.dumps(body, ensure_ascii=False)).encode('utf-8')
        start_response(f'{status} {HTTPStatus(status).phrase}', [
            ('Content-Type', ('text/html' if html else 'application/json') + '; charset=utf-8'),
            ('Content-Length', str(len(data))),
            ('Cache-Control', 'no-store'),
            ('Referrer-Policy', 'no-referrer'),
            ('X-Content-Type-Options', 'nosniff'),
            ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; "
             "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"),
        ])
        return [data]


def create_app():
    return WishRelay(os.environ.get('BAOFANG_WISH_RELAY_KEY', ''))
