"""Scan-to-send guest wishes over the room's local network.

Off by default. When the DJ turns it on, a tiny HTTP page is served on this
Mac's LAN address under a random token. Guests scan the QR code, type a short
wish, and it waits in the DJ's inbox. Nothing reaches the guest screen until
the DJ approves it. Text only, length capped, one message per phone every few
seconds, and the server stops when the DJ turns it off or the app quits.
"""
import json
import secrets
import socket
import threading
import time
import unicodedata
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_TEXT = 30
MAX_NAME = 8
MAX_BODY = 2048
MAX_QUEUE = 50
RATE_SECONDS = 20


def clean_text(value, limit):
    if not isinstance(value, str):
        return ''
    kept = ''.join(ch for ch in value if unicodedata.category(ch)[0] != 'C' or ch == ' ')
    return ' '.join(kept.split())[:limit]


def _private(address):
    import ipaddress
    try:
        value = ipaddress.ip_address(address)
    except ValueError:
        return False
    # 198.18.0.0/15 是测速保留段，常被代理/VPN 虚拟网卡占用，手机连不上
    return value.version == 4 and value.is_private and not value.is_loopback and not value.is_link_local \
        and value not in ipaddress.ip_network('198.18.0.0/15')


def lan_address():
    """A private address on a real network card that phones on the room Wi-Fi can reach."""
    candidates = []
    try:
        from PySide6.QtNetwork import QNetworkInterface
        for card in QNetworkInterface.allInterfaces():
            flags = card.flags()
            if not (flags & QNetworkInterface.InterfaceFlag.IsUp and flags & QNetworkInterface.InterfaceFlag.IsRunning) \
                    or flags & QNetworkInterface.InterfaceFlag.IsLoopBack or flags & QNetworkInterface.InterfaceFlag.IsPointToPoint:
                continue
            for entry in card.addressEntries():
                text = entry.ip().toString()
                if _private(text):
                    rank = 0 if text.startswith('192.168.') else 1 if text.startswith('10.') else 2
                    candidates.append((rank, card.name(), text))
    except Exception:
        pass
    if candidates:
        return sorted(candidates)[0][2]
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('10.255.255.255', 1))
        address = probe.getsockname()[0]
    except OSError:
        address = '127.0.0.1'
    finally:
        probe.close()
    return address if _private(address) else '127.0.0.1'


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>给包房送一句话</title>
<style>
body{margin:0;font-family:-apple-system,"PingFang SC",sans-serif;background:#1e1f23;color:#f0f1f3}
main{max-width:420px;margin:0 auto;padding:28px 20px}
h1{font-size:22px;margin:0 0 6px}p{color:#a3a6ae;margin:0 0 18px;font-size:14px}
input,textarea{width:100%;box-sizing:border-box;border:0;border-radius:10px;background:#303137;color:#fff;
font-size:17px;padding:12px;margin-bottom:12px}textarea{height:96px;resize:none}
button{width:100%;border:1px solid #b48cff;border-radius:10px;background:#2e2640;color:#d9c7ff;font-size:17px;padding:13px}
#s{margin-top:14px;font-size:15px;min-height:20px}
</style></head><body><main>
<h1>给包房送一句话</h1><p>DJ 看过后才会上屏。最多 {MAX_TEXT} 个字。</p>
<input id="n" maxlength="{MAX_NAME}" placeholder="你的名字（可不填）">
<textarea id="t" maxlength="{MAX_TEXT}" placeholder="比如：祝小王生日快乐！"></textarea>
<button id="b">发送</button><div id="s"></div>
<script>
document.getElementById('b').onclick=function(){
 var t=document.getElementById('t').value.trim(),s=document.getElementById('s');
 if(!t){s.textContent='先写一句话';return;}
 fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({text:t,name:document.getElementById('n').value})})
 .then(function(r){return r.json().then(function(j){s.textContent=j.message||'';
  if(r.ok)document.getElementById('t').value='';});})
 .catch(function(){s.textContent='没发出去，请再试一次';});
};
</script></main></body></html>""".replace('{MAX_TEXT}', str(MAX_TEXT)).replace('{MAX_NAME}', str(MAX_NAME))


class GuestInbox:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.queue = deque()
        self.last_seen = {}
        self.server = None
        self.thread = None
        self.token = ''
        self.url = ''

    @property
    def running(self):
        return self.server is not None

    def accept(self, client, payload):
        """Validate one wish. Returns (status, message)."""
        if not isinstance(payload, dict):
            return 400, '内容格式不对'
        text = clean_text(payload.get('text'), MAX_TEXT)
        name = clean_text(payload.get('name'), MAX_NAME)
        if not text:
            return 400, '先写一句话'
        now = self.clock()
        with self.lock:
            last = self.last_seen.get(client)
            if last is not None and now - last < RATE_SECONDS:
                return 429, f'发得太快了，{int(RATE_SECONDS - (now - last)) + 1} 秒后再试'
            if len(self.queue) >= MAX_QUEUE:
                return 429, 'DJ 这边消息太多，稍后再试'
            self.last_seen[client] = now
            if len(self.last_seen) > 500:
                self.last_seen = {k: v for k, v in self.last_seen.items() if now - v < RATE_SECONDS}
            self.queue.append({'id': secrets.token_hex(4), 'text': text, 'name': name,
                               'time': time.strftime('%H:%M')})
        return 200, '已送到 DJ，看过后上屏'

    def drain(self):
        with self.lock:
            rows = list(self.queue)
            self.queue.clear()
        return rows

    def start(self, host=None, port=0):
        if self.server:
            return self.url
        inbox = self
        token = secrets.token_urlsafe(6)
        path = '/m/' + token

        class Handler(BaseHTTPRequestHandler):
            server_version = 'Baofang'
            sys_version = ''

            def log_message(self, *_):
                pass

            def reply(self, status, body, kind='application/json; charset=utf-8'):
                data = body.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.split('?')[0] != path:
                    return self.reply(404, '{}')
                self.reply(200, PAGE, 'text/html; charset=utf-8')

            def do_POST(self):
                if self.path.split('?')[0] != path:
                    return self.reply(404, '{}')
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    length = -1
                if not 0 < length <= MAX_BODY:
                    return self.reply(413, json.dumps({'message': '内容太长'}, ensure_ascii=False))
                try:
                    payload = json.loads(self.rfile.read(length).decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    payload = None
                status, message = inbox.accept(self.client_address[0], payload)
                self.reply(status, json.dumps({'message': message}, ensure_ascii=False))

        host = host or lan_address()
        server = ThreadingHTTPServer((host, port), Handler)
        server.daemon_threads = True
        self.server, self.token = server, token
        self.url = f'http://{host}:{server.server_address[1]}{path}'
        self.thread = threading.Thread(target=server.serve_forever, name='guest-inbox', daemon=True)
        self.thread.start()
        return self.url

    def stop(self):
        server, self.server = self.server, None
        if server:
            server.shutdown()
            server.server_close()
        self.url, self.token = '', ''
        with self.lock:
            self.queue.clear()
            self.last_seen.clear()


def write_qr(url, destination, *, border=2):
    import segno
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    segno.make(url, error='m').save(str(destination), scale=10, border=border, dark='#000000', light='#ffffff')
    return destination
