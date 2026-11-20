"""JARVIS on Android ↔ Mark LVII (FatihMakes, CC BY-NC 4.0) on the user's PC.

The phone's own JARVIS (jarvis_phone) runs without the PC. When the PC runs Mark LVII, the phone reaches its dashboard
(port 8000) over the local network:
  - probe(): does an address answer as that dashboard, and why not when it does not;
  - pair(): signs in once with the one-time key of Mark LVII's "Remote Control" button (its own auto-login page) and
    keeps the device token it hands out (Android keystore vault), so later commands sign in by themselves until
    Mark LVII restarts;
  - pc_command(): sends a request the way Mark LVII's own phone page does (/api/command) and returns what JARVIS on the
    PC answered (its /ws log).
Only private-network addresses (home LAN, link-local, a VPN such as Tailscale) are used, so none of this can be pointed
at hosts on the internet.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
import time

import httpx

PORT = 8000
SOURCE = 'https://github.com/FatihMakes/Mark-LVII'
_PRIVATE = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '169.254.0.0/16')]
_ADDRESS = re.compile(r'^([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)(?::(\d{1,5}))?$')


def status() -> dict:
    return dict(mode='remote', source=SOURCE, license='CC BY-NC 4.0', port=PORT)


def parse(address) -> tuple[str, int]:
    """'192.168.1.5', '192.168.1.5:8000' or 'http://192.168.1.5:8000/' → (host, port)."""
    text = re.sub(r'^https?://', '', str(address or '').strip(), flags=re.I).split('/', 1)[0]
    match = _ADDRESS.match(text)
    if not match:
        raise ValueError('Alamat PC tidak valid. Contoh: 192.168.1.5:8000')
    port = int(match.group(2) or PORT)
    if not 0 < port < 65536:
        raise ValueError('Port harus 1–65535 (dashboard JARVIS memakai 8000).')
    return match.group(1), port


def resolve(host: str, port: int) -> str:
    """The IPv4 address of `host`, only when it is on a private network."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            raise ValueError(f'Nama {host} tidak ditemukan di jaringan ini. Pakai alamat IP PC, mis. 192.168.1.5.') from None
        ip = ipaddress.ip_address(infos[0][4][0])
    if ip.version != 4 or not any(ip in net for net in _PRIVATE):
        raise ValueError('Hanya alamat jaringan lokal (mis. 192.168.x.x, 10.x.x.x) atau VPN pribadi yang bisa dipakai.')
    return str(ip)


def probe(address) -> dict:
    host, port = parse(address)
    ip = resolve(host, port)
    shown = f'{ip}:{port}'
    url = f'http://{shown}/'
    result = dict(address=shown, url=None, state='offline', detail=None)
    try:
        with httpx.Client(timeout=httpx.Timeout(4.0, connect=3.0), follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', url + 'login', headers={'Accept': 'text/html'}) as response:
                if response.is_redirect and response.headers.get('location', '').lower().startswith('https://'):
                    result.update(state='https', detail='Dashboard JARVIS di PC memakai HTTPS.')
                    return result
                body = b''
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= 65536:
                        break
                if response.status_code == 200 and b'JARVIS' in body.upper():
                    result.update(state='online', url=url)
                else:
                    result.update(state='other', detail=f'{shown} menjawab HTTP {response.status_code}, bukan dashboard JARVIS.')
    except httpx.ConnectTimeout:
        result['detail'] = f'{shown} tidak menjawab dalam 3 detik: PC mati, beda Wi-Fi, atau firewall Windows memblokir JARVIS.'
    except httpx.ConnectError as e:
        text = str(e).lower()
        if 'refused' in text:
            result['detail'] = f'PC {ip} menolak sambungan di port {port}: JARVIS belum berjalan atau dashboard-nya tidak aktif.'
        elif 'unreachable' in text or 'no route' in text:
            result['detail'] = f'{ip} tidak terjangkau dari HP ini. Pastikan HP dan PC di Wi-Fi yang sama.'
        else:
            result['detail'] = f'Tidak bisa tersambung ke {shown} ({type(e).__name__}).'
    except httpx.HTTPError as e:
        result['detail'] = f'Sambungan ke {shown} gagal ({type(e).__name__}).'
    return result


# ------------------------------------------------------------------------------------------------- pairing, commands

KEY = re.compile(r'^[A-HJKMNP-Z2-9]{8}$')  # alphabet of Mark LVII pairing keys (no O, I, L, 0, 1)
TOKEN_TTL = 11 * 3600                        # Mark LVII drops tokens after 12 h
_PC = dict(address=None, token=None, tokenAt=0.0, online=None, checkedAt=0.0, lastError=None)
_PC_LOCK = threading.RLock()


class PcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _settings():
    from app.jarvis_phone import settings
    return settings()


def _address_file():
    from app.jarvis_phone import data_root
    return data_root() / 'jarvis-pc.json'


def _address() -> str | None:
    with _PC_LOCK:
        if _PC['address'] is None:
            try:
                _PC['address'] = json.loads(_address_file().read_text()).get('address')
            except (OSError, ValueError):
                _PC['address'] = ''
        return _PC['address'] or None


def pc_status(check: bool = False) -> dict:
    address = _address()
    paired = bool(address and _settings().secret('jarvis-pc'))
    if check and address and time.time() - _PC['checkedAt'] > 5:
        result = probe(address)
        with _PC_LOCK:
            _PC.update(online=result['state'] == 'online', checkedAt=time.time(),
                       lastError=None if result['state'] == 'online' else result.get('detail') or result['state'])
    with _PC_LOCK:
        return dict(paired=paired, address=address, online=_PC['online'] if address else None, lastError=_PC['lastError'],
                    url=f'http://{address}/' if address else None)


def pair(address, key) -> dict:
    host, port = parse(address)
    ip = resolve(host, port)
    key = re.sub(r'[\s-]', '', str(key or '')).upper()
    if not KEY.match(key):
        raise ValueError('Kode Remote Control terdiri dari 8 huruf/angka (tanpa O, I, L, 0, 1).')
    shown = f'{ip}:{port}'
    try:
        with httpx.Client(timeout=httpx.Timeout(6.0, connect=3.0), follow_redirects=False, trust_env=False) as http:
            page = http.get(f'http://{shown}/auto-login', params={'key': key}).text
    except httpx.HTTPError as e:
        raise ValueError(f'PC {shown} tidak bisa dihubungi ({type(e).__name__}). Pastikan JARVIS berjalan dan satu Wi-Fi.') from None
    token = re.search(r"setItem\('jarvis_token','([A-Za-z0-9_-]{8,200})'\)", page)
    device = re.search(r"setItem\('jarvis_device_token','([A-Za-z0-9_-]{8,200})'\)", page)
    if not token or not device:
        if 'expired' in page.lower():
            raise ValueError('Kode salah atau sudah kedaluwarsa. Tekan Remote Control lagi di HUD JARVIS PC.')
        raise ValueError(f'{shown} tidak menjawab sebagai dashboard JARVIS.')
    _settings().put_secret('jarvis-pc', device.group(1))
    tmp = _address_file().with_suffix('.tmp')
    tmp.write_text(json.dumps({'address': shown}))
    tmp.replace(_address_file())
    with _PC_LOCK:
        _PC.update(address=shown, token=token.group(1), tokenAt=time.time(), online=True, checkedAt=time.time(), lastError=None)
    return pc_status()


def unpair() -> dict:
    _settings().put_secret('jarvis-pc', '')
    try:
        _address_file().unlink()
    except OSError:
        pass
    with _PC_LOCK:
        _PC.update(address='', token=None, tokenAt=0.0, online=None, lastError=None)
    return pc_status()


def _token(http: httpx.Client, address: str, fresh: bool = False) -> str:
    with _PC_LOCK:
        if not fresh and _PC['token'] and time.time() - _PC['tokenAt'] < TOKEN_TTL:
            return _PC['token']
    device = _settings().secret('jarvis-pc')
    if not device:
        raise PcError('not_paired', 'JARVIS di PC belum dipasangkan dengan HP ini.')
    try:
        r = http.post(f'http://{address}/api/device-login', json={'device_token': device})
    except httpx.HTTPError as e:
        raise PcError('offline', f'PC {address} tidak menjawab ({type(e).__name__}).') from None
    if r.status_code == 401:
        raise PcError('pairing_lost', 'JARVIS di PC sudah dijalankan ulang; pasangkan lagi dengan kode Remote Control baru.')
    data = r.json() if r.status_code == 200 else {}
    if not data.get('token'):
        raise PcError('offline', f'PC {address} menolak sambungan (HTTP {r.status_code}).')
    with _PC_LOCK:
        _PC.update(token=data['token'], tokenAt=time.time())
    return data['token']


def _untrusted(text: str) -> str:
    text = str(text).replace('<<<', '‹‹‹').replace('>>>', '›››')
    return f'<<<UNTRUSTED source=pc>>>\n{text}\n<<<END UNTRUSTED>>>'


def pc_command(text: str, wait: bool = True, timeout: float = 30.0) -> dict:
    """Sends `text` to JARVIS on the PC; with `wait`, returns its spoken answer (its dashboard log) when it comes."""
    text = ' '.join(str(text or '').split())[:2000]
    if not text:
        return {'result': 'nothing to send'}
    address = _address()
    if not address or not _settings().secret('jarvis-pc'):
        return {'result': 'pc not paired', 'detail': 'Pair the PC first: JARVIS tab → KONTROL PC → address + Remote Control key.'}
    from websockets.exceptions import ConnectionClosed, WebSocketException
    ws = None
    try:
        with httpx.Client(timeout=httpx.Timeout(6.0, connect=3.0), follow_redirects=False, trust_env=False) as http:
            token = _token(http, address)
            if wait:
                from websockets.sync.client import connect
                ws = connect(f'ws://{address}/ws?token={token}', open_timeout=4, close_timeout=1, proxy=None, compression=None,
                             max_size=4 << 20, user_agent_header='SHARX-JARVIS')
                while True:  # the dashboard first replays its recent log; only what comes after the command counts
                    try:
                        ws.recv(timeout=0.4)
                    except TimeoutError:
                        break
            r = http.post(f'http://{address}/api/command', json={'text': text}, headers={'Authorization': f'Bearer {token}'})
            if r.status_code == 401:
                token = _token(http, address, fresh=True)
                r = http.post(f'http://{address}/api/command', json={'text': text}, headers={'Authorization': f'Bearer {token}'})
            if r.status_code != 200:
                return {'result': 'pc refused', 'detail': f'HTTP {r.status_code}'}
        with _PC_LOCK:
            _PC.update(online=True, checkedAt=time.time(), lastError=None)
        if ws is None:
            return {'result': 'sent to PC'}
        replies, deadline = [], time.time() + timeout
        while time.time() < deadline:
            try:
                raw = ws.recv(timeout=max(0.1, deadline - time.time()))
            except (TimeoutError, ConnectionClosed):
                break
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if event.get('type') == 'log' and event.get('speaker') == 'jarvis' and event.get('text'):
                replies.append(str(event['text'])[:2000])
                deadline = min(deadline, time.time() + 2.5)  # a follow-up sentence often comes right after
        if not replies:
            return {'result': 'sent to PC', 'pcReply': None,
                    'note': 'JARVIS on the PC has not answered yet; it may still be working or answering on the PC.'}
        return {'result': 'sent to PC', 'pcReply': _untrusted('\n'.join(replies))}
    except PcError as e:
        with _PC_LOCK:
            _PC.update(online=e.code != 'offline' and _PC['online'], lastError=str(e))
        return {'result': e.code, 'detail': str(e)}
    except (httpx.HTTPError, OSError, WebSocketException) as e:
        with _PC_LOCK:
            _PC.update(online=False, lastError=type(e).__name__)
        return {'result': 'offline', 'detail': f'PC {address} tidak menjawab ({type(e).__name__}).'}
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
