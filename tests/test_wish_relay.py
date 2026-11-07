import io
import json
import unittest

from app.wish_relay import ROOM_IDLE_SECONDS, WishRelay, relay_origin


class WishRelayTest(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.operator = 'x' * 32
        self.relay = WishRelay(self.operator, clock=lambda: self.now)
        _, room = self.call('POST', '/api/rooms', key=self.operator)
        self.guest = '/m/' + room['room']
        self.dj = '/api/rooms/' + room['room']
        self.dj_key = room['key']

    def call(self, method, path, payload=None, key='', ip='203.0.113.8'):
        body = json.dumps(payload).encode() if payload is not None else b''
        return self.relay.route({
            'REQUEST_METHOD': method, 'PATH_INFO': path, 'HTTP_AUTHORIZATION': 'Bearer ' + key,
            'REMOTE_ADDR': ip, 'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body),
        })

    def test_guest_can_send_but_cannot_read_or_close_inbox(self):
        self.assertEqual(self.call('POST', '/api/rooms')[0], 401)
        self.assertEqual(self.call('GET', self.guest)[0], 200)
        self.assertEqual(self.call('POST', self.guest, {'text': '生日快乐！'})[0], 200)
        self.assertEqual(self.call('DELETE', self.dj, key=self.operator)[0], 404)
        self.assertEqual(self.call('POST', self.dj, {'after': 0, 'limit': 1})[0], 404)
        payload = {'after': 0, 'limit': 1}
        expected = {'wishes': [{'seq': 1, 'text': '生日快乐！', 'name': ''}]}
        self.assertEqual(self.call('POST', self.dj, payload, key=self.dj_key), (200, expected))
        self.assertEqual(self.call('POST', self.dj, payload, key=self.dj_key), (200, expected))
        self.assertEqual(self.call('POST', self.dj, dict(payload, after=1), key=self.dj_key), (200, {'wishes': []}))
        self.assertEqual(self.call('DELETE', self.dj, key=self.dj_key)[0], 200)
        self.assertEqual(self.call('POST', self.guest, {'text': '还在吗'})[0], 410)

    def test_inactive_room_expires_even_if_guests_visit(self):
        self.now += ROOM_IDLE_SECONDS - 1
        self.assertEqual(self.call('GET', self.guest)[0], 200)
        self.now += 1
        self.assertEqual(self.call('GET', self.guest)[0], 410)
        self.assertEqual(self.call('POST', self.dj, {'after': 0, 'limit': 1}, key=self.dj_key)[0], 404)

    def test_rate_limit_and_invalid_input(self):
        self.assertEqual(self.call('POST', self.guest, {'text': '\n\t'})[0], 400)
        for _ in range(8):
            self.assertEqual(self.call('POST', self.guest, {'text': '干杯'})[0], 200)
        self.assertEqual(self.call('POST', self.guest, {'text': '干杯'})[0], 429)
        self.assertEqual(self.call('POST', self.guest, {'text': '干杯'}, ip='203.0.113.9')[0], 200)

    def test_requires_https_origin_without_credentials_or_paths(self):
        self.assertEqual(relay_origin('https://wish.example:8443/'), 'https://wish.example:8443')
        for value in ('http://wish.example', 'https://user@wish.example', 'https://wish.example/path',
                      'https://wish.example?key=123', 'https://wish.example:abc'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                relay_origin(value)
