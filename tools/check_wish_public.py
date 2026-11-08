"""Real Qt and HTTPS check with a temporary loopback relay and synthetic wishes."""
import json
import os
import secrets
import ssl
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from socketserver import ThreadingMixIn
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from tools.check_live_extras import find, wait_for
from tools.ui_state_smoke import click
from PySide6.QtCore import QPointF, QUrl
from PySide6.QtNetwork import QNetworkReply, QSslCertificate, QSslConfiguration
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.controller import Controller
from app.wish_relay import WishRelay


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_):
        pass


class TestServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

    def finish_request(self, request, address):
        request.settimeout(5)
        try:
            with self.tls.wrap_socket(request, server_side=True) as secured:
                super().finish_request(secured, address)
        except OSError:
            pass


def check(root, temp, evidence, origin, context, unavailable):
    controller = Controller(root, temp / 'runtime')
    network_errors = []
    controller.wish_relay.network.finished.connect(
        lambda reply: network_errors.append(reply.errorString()) if reply.error() else None)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    engine.rootContext().setContextProperty('controller', controller)
    engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
    assert engine.rootObjects(), 'Main.qml failed to load'
    window = engine.rootObjects()[0]
    window.resize(1000, 700)

    def press(name):
        assert wait_for(lambda: find(window, name) and find(window, name).isVisible()), name
        item = find(window, name)
        assert item and item.isVisible() and item.isEnabled(), name
        scroll = find(window, 'screenContentScroll')
        if scroll and scroll.isAncestorOf(item):
            flick = scroll.property('contentItem')
            center = item.mapToItem(flick, QPointF(item.width() / 2, item.height() / 2))
            limit = flick.property('contentHeight') - flick.height()
            flick.setProperty('contentY', max(0, min(limit, flick.property('contentY') + center.y() - flick.height() / 2)))
            QTest.qWait(80)
        click(window, item)

    def qr():
        return controller.state['guestQr']

    def send(url, text):
        body = json.dumps({'name': '阿杰', 'text': text}).encode()
        request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, context=context, timeout=3) as response:
            assert '等待 DJ 审核' in json.load(response)['message']

    try:
        assert wait_for(lambda: not controller.state['stale'])
        press('liveTab-message')
        press('screenWishesTab')
        press('wishPublicToggle')
        assert wait_for(lambda: qr()['running']), (controller.state['guestRelayStatus'], network_errors)
        url = qr()['url']
        assert url.startswith(origin + '/m/')
        assert not find(window, 'wishServerToggle').isEnabled()
        assert not controller.inbox.running
        press('wishQrSettings')
        press('wishQrOnScreen')
        assert qr()['onScreen']
        with urllib.request.urlopen(url, context=context, timeout=3) as response:
            assert '给包房送一句话' in response.read().decode()
        send(url, '生日快乐！')
        assert wait_for(lambda: len(controller.state['guestInbox']) == 1)
        assert not controller.state['guestMessage']['visible']
        window.grabWindow().save(str(evidence / '公网祝福待审.png'))
        press('wishApprove-' + controller.state['guestInbox'][0]['id'])
        assert controller.state['guestMessage']['text'] == '阿杰：生日快乐！'
        assert not controller.state['guestInbox']

        unavailable.set()
        controller.wish_relay.poll()
        assert wait_for(lambda: not qr()['running'])
        assert find(window, 'wishPublicToggle').property('checked')
        assert not find(window, 'wishQrImage').isVisible()
        send(url, '第二条祝福')
        unavailable.clear()
        controller.wish_relay.poll()
        assert wait_for(lambda: qr()['running'] and len(controller.state['guestInbox']) == 1)
        assert qr()['url'] == url and qr()['onScreen']
        assert controller.state['guestInbox'][0]['text'] == '第二条祝福'
        press('wishReject-' + controller.state['guestInbox'][0]['id'])
        assert not controller.state['guestInbox']
        QTest.qWait(2200)
        assert not controller.state['guestInbox'], 'a dismissed wish was delivered twice'

        unavailable.set()
        errors_before_close = len(network_errors)
        press('wishPublicToggle')
        assert not qr()['running'] and not qr()['url']
        assert wait_for(lambda: len(network_errors) > errors_before_close)
        pending = find(window, 'wishPublicCloseStatus')
        assert pending.isVisible() and '关闭待确认' in pending.property('text')
        assert controller.wish_relay.room_key == '' and not controller.wish_relay.timer.isActive()
        with urllib.request.urlopen(url, context=context, timeout=3) as response:
            assert response.status == 200, 'the simulated failed DELETE must leave the old URL reachable'

        with patch('app.guest_inbox.lan_address', return_value='127.0.0.1'):
            press('wishServerToggle')
        assert controller.inbox.running and qr()['running']
        lan_url = qr()['url']
        QTest.qWait(2200)
        assert controller.inbox.running and qr()['url'] == lan_url
        press('wishServerToggle')

        press('wishPublicToggle')
        assert wait_for(lambda: qr()['running'] and qr()['url'] != url)
        new_url = qr()['url']
        unavailable.clear()
        assert wait_for(lambda: '已关闭' in controller.state['guestRelayCloseStatus'])
        assert qr()['running'] and qr()['url'] == new_url
        try:
            urllib.request.urlopen(url, context=context, timeout=3)
            raise AssertionError('old QR still accepts visits')
        except urllib.error.HTTPError as exc:
            assert exc.code == 410, exc

        unavailable.set()
        press('wishPublicToggle')
        assert '关闭待确认' in controller.state['guestRelayCloseStatus']
        with patch('app.guest_inbox.lan_address', return_value='127.0.0.1'):
            press('wishServerToggle')
        lan_url = qr()['url']
        unavailable.clear()
        assert wait_for(lambda: '已关闭' in controller.state['guestRelayCloseStatus'])
        assert controller.inbox.running and qr()['url'] == lan_url
        assert wait_for(lambda: not controller.wish_relay.network.findChildren(QNetworkReply))
        press('wishServerToggle')
        controller.setOfflineOnly(True)
        assert not find(window, 'wishPublicToggle').isEnabled()
        assert find(window, 'wishServerToggle').isEnabled()
        assert not warnings, warnings
        print('HTTPS 提交、审核、断线恢复、关场失败重试、切换局域网和新场次、旧码失效及离线模式通过；QML 警告 0')
    finally:
        controller.shutdown()
        window.close()
        engine.deleteLater()


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ.get('BAOFANG_UI_CHECK_DIR', root / 'runtime/wish-public-check'))
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    QQuickStyle.setStyle('Basic')
    key = secrets.token_urlsafe(32)
    relay = WishRelay(key)
    unavailable = threading.Event()

    def service(env, start):
        if unavailable.is_set() and env['PATH_INFO'].startswith('/api/rooms/'):
            start('503 Service Unavailable', [('Content-Length', '0')])
            return [b'']
        return relay(env, start)

    with tempfile.TemporaryDirectory(prefix='baofang-wish-') as folder:
        temp = Path(folder)
        def openssl(*args):
            subprocess.run(['openssl', *args], cwd=temp, check=True, capture_output=True)

        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=Wish Test CA',
                '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
                '-keyout', 'ca.key', '-out', 'ca.pem')
        openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost',
                '-keyout', 'tls.key', '-out', 'tls.csr')
        (temp / 'tls.ext').write_text('subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n')
        openssl('x509', '-req', '-days', '1', '-in', 'tls.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key',
                '-CAcreateserial', '-extfile', 'tls.ext', '-out', 'tls.pem')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(temp / 'tls.pem', temp / 'tls.key')
        trust = ssl.create_default_context(cafile=str(temp / 'ca.pem'))
        qt_trust = QSslConfiguration.defaultConfiguration()
        qt_trust.setCaCertificates(QSslCertificate.fromPath(str(temp / 'ca.pem')))
        QSslConfiguration.setDefaultConfiguration(qt_trust)
        with make_server('127.0.0.1', 0, service, server_class=TestServer, handler_class=QuietHandler) as server:
            server.tls = context
            origin = f'https://localhost:{server.server_port}'
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, {'BAOFANG_WISH_RELAY_KEY': key, 'BAOFANG_WISH_RELAY_URL': origin}):
                    check(root, temp, evidence, origin, trust, unavailable)
            finally:
                server.shutdown()
                thread.join()


if __name__ == '__main__':
    main()
