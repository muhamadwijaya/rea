"""PC → phone: lets JARVIS on the user's PC (Mark LVII with the SHARX plugin) and SHARX desktop send requests to JARVIS
on this phone over the local network.

Off until the user turns it on (JARVIS tab → KONTROL PC → "Izinkan PC mengontrol HP"). While on, a small HTTP server
listens on port 8010 and accepts only private-network clients:
  POST /pair     {key}   one-time 8-character key shown on the phone (10 minutes, rate limited) → {token}
  POST /command  {text}  Bearer token → the request goes to the phone's JARVIS conversation (started if the app is open)
                         and the answer JARVIS speaks comes back as {reply}
  GET  /status           Bearer token → whether the conversation runs
Only the SHA-256 of each token is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from concurrent import futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger('sharx.jarvis.lan')

KEY_CHARS = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # Mark LVII's pairing alphabet (no O, I, L, 0, 1)
KEY_SECONDS = 600
_LOCK = threading.RLock()
_STATE = dict(server=None, port=None, key=None, keyUntil=0.0, failures={}, error=None)
_WAITERS: dict[str, futures.Future] = {}
PRIVATE = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '169.254.0.0/16')]


def port() -> int:
    return int(os.environ.get('SHARX_JARVIS_LAN_PORT') or 8010)


def _settings():
    from app.jarvis_phone import settings
    return settings()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def lan_address() -> str | None:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 53))  # no packet is sent: picks the outgoing interface
            ip = s.getsockname()[0]
            return None if ip.startswith('127.') else ip
    except OSError:
        return None


def status() -> dict:
    with _LOCK:
        running = _STATE['server'] is not None
        key = _STATE['key'] if running and time.time() < _STATE['keyUntil'] else None
        ip = lan_address() if running else None
        return dict(enabled=bool(_settings().values.get('allowPc')), running=running,
                    address=f'{ip}:{_STATE["port"]}' if ip else None, key=key,
                    keyMinutes=max(0, int((_STATE['keyUntil'] - time.time()) // 60)) if key else 0,
                    paired=len(_settings().values.get('pcTokens') or []), error=_STATE['error'])


def new_key() -> dict:
    with _LOCK:
        _STATE['key'] = ''.join(secrets.choice(KEY_CHARS) for _ in range(8))
        _STATE['keyUntil'] = time.time() + KEY_SECONDS
    return status()


def enable(on: bool, state=None) -> dict:
    _settings().set_internal('allowPc', bool(on))
    if on:
        start(state)
        new_key()
    else:
        stop()
    return status()


def forget_all() -> dict:
    _settings().set_internal('pcTokens', [])
    return status()


def start(state=None):
    with _LOCK:
        if _STATE['server'] is not None:
            return
        try:
            server = ThreadingHTTPServer(('0.0.0.0', port()), Handler)
        except OSError as e:
            _STATE['error'] = f'Port {port()} tidak bisa dipakai ({e.strerror or e}).'
            log.warning('JARVIS LAN listener: %s', _STATE['error'])
            return
        server.daemon_threads = True
        server.sharx_state = state
        _STATE.update(server=server, port=server.server_port, error=None)
        threading.Thread(target=server.serve_forever, name='jarvis-lan', daemon=True).start()


def stop():
    with _LOCK:
        server, _STATE['server'] = _STATE['server'], None
        _STATE['key'] = None
    if server is not None:
        server.shutdown()
        server.server_close()


def restore(state=None):
    """Starts the listener at app start when the user left it on."""
    if _settings().values.get('allowPc'):
        start(state)


def deliver_reply(reply_to: str, text: str):
    future = _WAITERS.pop(reply_to, None)
    if future and not future.done():
        future.set_result(text)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def reply(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def client_ok(self) -> bool:
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return ip.is_loopback or any(ip in net for net in PRIVATE)

    def body(self) -> dict:
        if 'chunked' in self.headers.get('Transfer-Encoding', '').lower():
            raw = bytearray()
            while True:  # clients that stream JSON send it in chunks, without a Content-Length
                size = int(self.rfile.readline(64).split(b';')[0].strip() or b'0', 16)
                if size == 0:
                    self.rfile.readline(64)
                    break
                if len(raw) + size > 65536:
                    raise ValueError('body too large')
                raw += self.rfile.read(size)
                self.rfile.readline(64)
        else:
            length = int(self.headers.get('Content-Length') or 0)
            if not 0 <= length <= 65536:
                raise ValueError('body too large')
            raw = self.rfile.read(length)
        data = json.loads(bytes(raw) or b'{}')
        if not isinstance(data, dict):
            raise ValueError('invalid body')
        return data

    def authorized(self) -> bool:
        token = self.headers.get('Authorization', '').removeprefix('Bearer ').strip()
        if not token:
            return False
        digest = _hash(token)
        return any(hmac.compare_digest(digest, h) for h in _settings().values.get('pcTokens') or [])

    def do_GET(self):
        if not self.client_ok():
            return self.reply(403, dict(error='forbidden'))
        if self.path.split('?')[0] != '/status':
            return self.reply(404, dict(error='not_found'))
        if not self.authorized():
            return self.reply(401, dict(error='unauthorized'))
        from app.jarvis_phone import active_session, settings
        return self.reply(200, dict(name=settings().values.get('assistantName'), sessionActive=active_session() is not None))

    def do_POST(self):
        if not self.client_ok():
            return self.reply(403, dict(error='forbidden'))
        path = self.path.split('?')[0]
        try:
            data = self.body()
        except ValueError as e:
            return self.reply(400, dict(error='invalid_argument', message=str(e)))
        if path == '/pair':
            return self.handle_pair(data)
        if path == '/command':
            if not self.authorized():
                return self.reply(401, dict(error='unauthorized', message='Pasangkan PC lagi dengan kode baru di HP.'))
            return self.handle_command(data)
        return self.reply(404, dict(error='not_found'))

    # (BaseHTTPRequestHandler already uses self.command for the HTTP method)
    def handle_pair(self, data: dict):
        who = self.client_address[0]
        now = time.time()
        with _LOCK:
            fails = [t for t in _STATE['failures'].get(who, []) if now - t < 300]
            if len(fails) >= 6:
                return self.reply(429, dict(error='too_many_attempts', message='Terlalu banyak percobaan. Coba lagi 5 menit lagi.'))
            key = str(data.get('key') or '').upper().replace('-', '').replace(' ', '')
            valid = _STATE['key'] and now < _STATE['keyUntil'] and hmac.compare_digest(key, _STATE['key'])
            if not valid:
                _STATE['failures'][who] = fails + [now]
                return self.reply(401, dict(error='invalid_key', message='Kode salah atau kedaluwarsa. Buat kode baru di HP.'))
            _STATE['key'] = None  # one-time
            _STATE['failures'].pop(who, None)
        token = secrets.token_urlsafe(32)
        tokens = (_settings().values.get('pcTokens') or [])[-9:] + [_hash(token)]
        _settings().set_internal('pcTokens', tokens)
        state = getattr(self.server, 'sharx_state', None)
        if state is not None:
            try:
                state.publish('jarvis', dict(type='pc_paired', client=who))
            except Exception:
                pass
        from app.jarvis_phone import settings
        return self.reply(200, dict(ok=True, token=token, name=settings().values.get('assistantName')))

    def handle_command(self, data: dict):
        text = ' '.join(str(data.get('text') or '').split())[:2000]
        if not text:
            return self.reply(400, dict(error='invalid_argument', message='Perintah kosong.'))
        state = getattr(self.server, 'sharx_state', None)
        reply_to = secrets.token_hex(8)
        future: futures.Future = futures.Future()
        _WAITERS[reply_to] = future
        try:
            # the open app starts JARVIS when needed and sends the request into its conversation (jarvis-phone.js)
            state.publish('jarvis', dict(type='pc_command', text=text, replyTo=reply_to))
        except Exception:
            _WAITERS.pop(reply_to, None)
            return self.reply(503, dict(error='unavailable', message='Aplikasi SHARX di HP belum siap.'))
        try:
            reply = future.result(timeout=float(data.get('timeout') or 45))
        except futures.TimeoutError:
            _WAITERS.pop(reply_to, None)
            return self.reply(200, dict(ok=False, reply=None, detail='JARVIS di HP belum menjawab: aplikasi SHARX mungkin '
                                                                       'tidak terbuka, atau Gemini API key belum diisi.'))
        return self.reply(200, dict(ok=True, reply=reply))
