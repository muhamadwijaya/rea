"""JARVIS on the phone: the voice assistant of Mark LVII (FatihMakes, https://github.com/FatihMakes/Mark-LVII,
CC BY-NC 4.0, non-commercial use only) running inside SHARX Android, without the PC.

Mark LVII is a desktop program (Qt HUD, PortAudio, Windows automation), so it cannot run unchanged on a phone. What
runs here is its conversation core, kept as in the original:
  - the same Gemini Live models, tried in Mark LVII's order, with its voices, transcripts, session resumption and
    sliding-window context;
  - its persona prompt (core/prompt.txt) and long-term memory (memory/memory_manager.py, same file format), bundled
    unmodified from the pinned Mark LVII commit in mark_lvii/; only the prompt's [SELF] section is replaced for the
    phone at runtime;
  - save_memory, recall_memory, weather_report, reminder and shutdown_jarvis with Mark LVII's names and parameters.
Desktop-only tools are replaced by phone tools (Java bridge JarvisHooks), SHARX market tools, and pc_command, which
hands a request to Mark LVII on the user's PC when one is paired (jarvis_remote).

The WebView never sees the Gemini API key (kept in the Android keystore vault): it streams microphone audio to
/api/jarvis/live on this local server, and this module relays it to the Live API and the replies back.
"""
from __future__ import annotations

import base64
import concurrent.futures as futures
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import struct
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

log = logging.getLogger('sharx.jarvis')

LIVE_URL = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent'
# Mark LVII core/gemini.py LIVE_MODELS, in its order
LIVE_MODELS = ('models/gemini-3.1-flash-live-preview', 'models/gemini-2.5-flash-native-audio-preview-12-2025')
# Mark LVII memory/config_manager.py AVAILABLE_VOICES / DEFAULT_VOICE
VOICES = ('Charon', 'Puck', 'Kore', 'Fenrir', 'Aoede')
MARK_DIR = Path(__file__).resolve().parent.parent / 'mark_lvii'
SOURCE = 'https://github.com/FatihMakes/Mark-LVII'
_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
_LOCK = threading.RLock()
_SESSIONS: list['LiveSession'] = []
_SETTINGS: 'Settings | None' = None
_REMINDERS: 'Reminders | None' = None


def live_url() -> str:
    return os.environ.get('SHARX_GEMINI_LIVE_URL') or LIVE_URL  # tests point this at a local fake server


def data_root() -> Path:
    root = Path(os.environ.get('SHARX_DATA_DIR') or '.') / 'jarvis'
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------------------------------------- settings

class Settings:
    """Names, voice and options in jarvis-settings.json; the Gemini key in the Android keystore vault."""
    defaults = dict(assistantName='JARVIS', userName='', voice='Charon', duplex=False, googleSearch=True,
                    allowPc=False, pcTokens=[])  # PC → phone (jarvis_lan): switch, SHA-256 of paired PC tokens
    internal = ('allowPc', 'pcTokens')

    def __init__(self, root: Path):
        self.path = root / 'jarvis-settings.json'
        self.values = dict(self.defaults)
        self.keys: dict[str, str] = {}  # without the vault (tests): kept in memory only
        self.vault = None
        try:
            from java import jclass
            self.vault = jclass('com.sharx.invest.SecretVault')
        except ImportError:
            pass
        try:
            self.values.update({k: v for k, v in json.loads(self.path.read_text()).items() if k in self.defaults})
        except (OSError, ValueError):
            pass

    def secret(self, name: str) -> str | None:
        value = self.vault.get(name) if self.vault else self.keys.get(name)
        value = str(value or '').strip()
        return value or None

    def put_secret(self, name: str, value: str) -> None:
        if self.vault:
            self.vault.put(name, value)
        elif value:
            self.keys[name] = value
        else:
            self.keys.pop(name, None)

    def key(self) -> str | None:
        return self.secret('gemini')

    def view(self) -> dict:
        shown = {k: v for k, v in self.values.items() if k not in self.internal}
        return dict(shown, configured=bool(self.key()), encrypted=bool(self.vault))

    def set_internal(self, name: str, value) -> None:
        """Values the app manages itself (never from the page's settings form)."""
        if name not in self.internal:
            raise KeyError(name)
        with _LOCK:
            self.values = dict(self.values, **{name: value})
            self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.values))
        tmp.replace(self.path)

    def update(self, body: dict) -> dict:
        with _LOCK:
            values = dict(self.values)
            for name in ('assistantName', 'userName'):
                if name in body and body[name] is not None:
                    text = ' '.join(str(body[name]).split())[:40]
                    if any(ord(c) < 32 for c in text):
                        raise ValueError('Nama tidak valid.')
                    values[name] = text or self.defaults[name]
            if 'voice' in body:
                if body['voice'] not in VOICES:
                    raise ValueError('Suara JARVIS tidak dikenal.')
                values['voice'] = body['voice']
            for name in ('duplex', 'googleSearch'):
                if name in body:
                    values[name] = bool(body[name])
            key = str(body.get('apiKey') or '').strip()
            if key:
                if len(key) < 20 or len(key) > 200 or any(c.isspace() or ord(c) < 33 for c in key):
                    raise ValueError('Format Gemini API key tidak valid (biasanya diawali AIza…).')
                self.put_secret('gemini', key)
            if body.get('clearKey'):
                self.put_secret('gemini', '')
            self.values = values
            self._save()
            return self.view()


def settings() -> Settings:
    global _SETTINGS
    with _LOCK:
        if _SETTINGS is None:
            _SETTINGS = Settings(data_root())
        return _SETTINGS


# ------------------------------------------------------------------------------------------------ memory and prompt

def memory():
    """Mark LVII's own memory manager, storing in the SHARX data folder (app code is replaced on every update)."""
    from mark_lvii import memory_manager as mm
    path = data_root() / 'memory' / 'long_term.json'
    if Path(mm.MEMORY_PATH) != path:
        path.parent.mkdir(parents=True, exist_ok=True)
        mm.MEMORY_PATH = path
    return mm


PHONE_SELF = """[SELF]
You are {assistant_name}, a voice assistant running inside the SHARX INVEST app on the user's Android phone
({platform}). The glowing orb on the JARVIS screen is your presence while you listen and speak. On the phone you
cannot see the screen or the camera.
The user's PC may run your desktop self. When it is paired, pc_command hands it a request: use it for anything that
has to happen on the computer (its apps, files, browser, keyboard, mouse or screen) and tell the user what the PC
answered. When the PC is not connected, say so in one clause and do what the phone can do.
SHARX INVEST is the user's market app (forex, gold, indices, crypto, US and Indonesian stocks, Smart Money Concept
analysis). Use the market tools for prices, analysis, news and the economic calendar instead of guessing, and never
present an analysis as a certainty or as financial advice.
Some tools named elsewhere in these instructions exist only on the desktop (undo, dev_agent, restart, shutdown,
toggle_wifi, screen and camera tools). On the phone they are not in [WHAT YOU CAN DO]: hand such requests to the PC
with pc_command.
[REMINDER] messages come from the app when a reminder the user set is due: tell them in one short sentence, in the
language of the conversation.
"""


def _prompt_template() -> str:
    try:
        text = (MARK_DIR / 'prompt.txt').read_text(encoding='utf-8')
    except OSError:
        return PHONE_SELF
    # the file's own '#' comments are notes for whoever edits it
    text = '\n'.join(line for line in text.splitlines() if not line.startswith('#'))
    replaced, n = re.subn(r'\[SELF\]\n.*?(?=\n\[[A-Z ]+\]\n)', PHONE_SELF.rstrip('\n'), text, count=1, flags=re.S)
    return replaced if n else PHONE_SELF + '\n' + text


def _limits() -> str:
    return '\n'.join([
        '- Only the tools listed above act in the world. For anything else, say in one clause that it is out of '
        'reach and offer the closest thing you can do; never describe an action as done unless its tool said so.',
        '- You act on this phone, and on the user\'s PC only through pc_command while it is paired. Other devices, '
        'accounts and hardware are out of reach.',
        '- Calls, text messages, WhatsApp and e-mail only open prefilled on the phone; the user presses send. Say '
        'that they are ready to send, never that they were sent.',
        '- On the phone you have no sight: no screen capture and no camera.',
        '- You remember what is in the memory block and this session. Anything else must be saved with save_memory '
        'to be remembered.',
        '- While the microphone is muted you hear nothing, and you cannot unmute it yourself.',
    ])


def system_prompt(declarations: list[dict]) -> str:
    s = settings().values
    now = datetime.now().astimezone()
    parts = [
        '[CURRENT DATE & TIME]\nRight now it is: ' + now.strftime('%A, %d %B %Y — %H:%M') +
        f' ({now.tzname()}).\nUse this to work out exact times for reminders.\n',
        '[IDENTITY]\nYour name is {0}. Always refer to yourself as {0}.\n'.format(s['assistantName']) +
        (f"ADDRESS: Always call the user '{s['userName']}'.\n" if s['userName'] else
         'ADDRESS: Use the ordinary respectful way of addressing the user in the language you are speaking.\n'),
    ]
    try:
        mm = memory()
        block = mm.format_memory_for_prompt(mm.load_memory())
        if block:
            parts.append(block)
    except Exception as e:  # the assistant still works without its memory file
        log.warning('JARVIS memory unavailable: %s', type(e).__name__)
    capabilities = '\n'.join(f"- {d['name']}: {' '.join(d['description'].split())[:150]}" for d in declarations)
    platform = ('Android ' + _android_release()).strip()
    prompt = _prompt_template()
    for token, value in (('assistant_name', s['assistantName']), ('platform', platform),
                         ('capabilities', capabilities), ('limits', _limits())):
        prompt = prompt.replace('{' + token + '}', value)
    parts.append(prompt)
    return '\n'.join(parts)


def _android_release() -> str:
    try:
        from java import jclass
        return str(jclass('android.os.Build$VERSION').RELEASE)
    except Exception:
        return ''


# ------------------------------------------------------------------------------------------------------------ tools

def _obj(properties: dict | None = None, required: tuple = ()) -> dict:
    schema = {'type': 'OBJECT', 'properties': properties or {}}
    if required:
        schema['required'] = list(required)
    return schema


S = {'type': 'STRING'}
I = {'type': 'INTEGER'}


def _s(description: str, enum: tuple | None = None) -> dict:
    out = dict(S, description=description)
    if enum:
        out['enum'] = list(enum)
    return out


def _untrusted(source: str, text: str) -> str:
    text = str(text).replace('<<<', '‹‹‹').replace('>>>', '›››')
    return f'<<<UNTRUSTED source={source}>>>\n{text}\n<<<END UNTRUSTED>>>'


class Tool:
    def __init__(self, name, description, parameters, handler=None, client=False):
        self.name, self.description, self.parameters, self.handler, self.client = name, description, parameters, handler, client

    def declaration(self) -> dict:
        return dict(name=self.name, description=self.description, parameters=self.parameters)


def _java():
    try:
        from java import jclass
        return jclass('com.sharx.invest.JarvisHooks')
    except Exception:
        return None


def _phone(method: str, *args) -> dict:
    hooks = _java()
    if hooks is None:
        return {'result': 'not available', 'detail': 'Phone actions need the Android app.'}
    try:
        text = str(getattr(hooks, method)(*args))
    except Exception as e:
        return {'result': 'failed', 'detail': str(e)[:200]}
    try:
        return json.loads(text)
    except ValueError:
        return {'result': text}


# memory (Mark LVII names and parameters)

def t_save_memory(a, ctx):
    category = str(a.get('category') or 'notes')
    key, value = str(a.get('key') or '').strip(), str(a.get('value') or '').strip()
    if key and value:
        memory().update_memory({category: {key: {'value': value}}})
    return {'result': 'ok', 'silent': True}


def t_recall_memory(a, ctx):
    return {'result': memory().search_memory(str(a.get('query') or ''))}


# time, weather, web

def t_datetime(a, ctx):
    now = datetime.now().astimezone()
    return {'date': now.strftime('%Y-%m-%d'), 'time': now.strftime('%H:%M'), 'weekday': now.strftime('%A'),
            'timezone': str(now.tzname()), 'utcOffset': now.strftime('%z')}


_WMO = {0: 'clear sky', 1: 'mainly clear', 2: 'partly cloudy', 3: 'overcast', 45: 'fog', 48: 'rime fog',
        51: 'light drizzle', 53: 'drizzle', 55: 'dense drizzle', 61: 'light rain', 63: 'rain', 65: 'heavy rain',
        71: 'light snow', 73: 'snow', 75: 'heavy snow', 80: 'light showers', 81: 'showers', 82: 'violent showers',
        95: 'thunderstorm', 96: 'thunderstorm with hail', 99: 'thunderstorm with heavy hail'}


def t_weather(a, ctx):
    import httpx
    city = str(a.get('city') or '').strip()
    if not city:
        return {'result': 'city missing'}
    with httpx.Client(timeout=15) as http:
        geo = http.get('https://geocoding-api.open-meteo.com/v1/search', params={'name': city, 'count': 1, 'language': 'en'}).json()
        if not geo.get('results'):
            return {'result': f'no place called {city} was found'}
        place = geo['results'][0]
        data = http.get('https://api.open-meteo.com/v1/forecast', params={
            'latitude': place['latitude'], 'longitude': place['longitude'], 'timezone': 'auto', 'forecast_days': 2,
            'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation',
            'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code'}).json()
    cur, day = data.get('current', {}), data.get('daily', {})
    return {
        'place': ', '.join(x for x in (place.get('name'), place.get('admin1'), place.get('country')) if x),
        'now': {'conditions': _WMO.get(cur.get('weather_code'), 'unknown'), 'temperatureC': cur.get('temperature_2m'),
                'feelsLikeC': cur.get('apparent_temperature'), 'humidityPct': cur.get('relative_humidity_2m'),
                'windKmh': cur.get('wind_speed_10m'), 'precipitationMm': cur.get('precipitation')},
        'days': [{'date': d, 'conditions': _WMO.get(c, 'unknown'), 'minC': lo, 'maxC': hi, 'rainChancePct': p}
                 for d, c, lo, hi, p in zip(day.get('time', []), day.get('weather_code', []), day.get('temperature_2m_min', []),
                                            day.get('temperature_2m_max', []), day.get('precipitation_probability_max', []))],
        'source': 'Open-Meteo',
    }


def _public_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('only http(s) pages can be read')
    for info in socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError('pages on the local network are not read')
    return url


def t_read_webpage(a, ctx):
    import httpx
    url = _public_url(str(a.get('url') or '').strip())
    with httpx.Client(timeout=20, follow_redirects=True, headers={'User-Agent': 'Mozilla/5.0 (Android) SHARX-JARVIS'}) as http:
        r = http.get(url)
    html = r.text[:2_000_000]
    text = ''
    try:
        import trafilatura
        text = trafilatura.extract(html, url=str(r.url)) or ''
    except Exception:
        text = ''
    if not text:
        from bs4 import BeautifulSoup
        text = BeautifulSoup(html, 'lxml').get_text(' ', strip=True)
    return {'url': str(r.url), 'status': r.status_code, 'text': _untrusted('web', text[:8000])}


# reminders (Mark LVII's reminder tool: date, time, message)

class Reminders:
    def __init__(self, root: Path):
        self.path = root / 'reminders.json'
        try:
            self.items = [r for r in json.loads(self.path.read_text()) if isinstance(r, dict)]
        except (OSError, ValueError):
            self.items = []
        self.state = None
        threading.Thread(target=self.loop, name='jarvis-reminders', daemon=True).start()

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.items))
        tmp.replace(self.path)

    def add(self, when: datetime, message: str) -> dict:
        item = dict(id=uuid.uuid4().hex[:8], at=when.timestamp(), local=when.strftime('%Y-%m-%d %H:%M'), message=message[:300])
        with _LOCK:
            self.items.append(item)
            self.save()
        return item

    def cancel(self, rid: str) -> bool:
        with _LOCK:
            before = len(self.items)
            self.items = [r for r in self.items if r['id'] != rid]
            self.save()
            return len(self.items) < before

    def upcoming(self) -> list[dict]:
        with _LOCK:
            return sorted(self.items, key=lambda r: r['at'])

    def loop(self):
        while True:
            time.sleep(5)
            now = time.time()
            with _LOCK:
                due = [r for r in self.items if r['at'] <= now]
                if due:
                    self.items = [r for r in self.items if r['at'] > now]
                    self.save()
            for item in due:
                self.fire(item)

    def fire(self, item: dict):
        text = item['message']
        if self.state is not None:
            try:
                self.state.publish('jarvis', dict(type='reminder', message=text, at=item['local']))
            except Exception:
                pass
        session = active_session()
        if session and session.inject(f'[REMINDER] Due now ({item["local"]}): {text}'):
            return
        try:  # no conversation running: the phone's own voice says it
            from app import android_tts
            android_tts.say({'owner': 'jarvis-reminder', 'token': int(time.time()), 'text': f'Pengingat: {text}', 'lang': 'id'})
        except Exception as e:
            log.warning('JARVIS reminder not spoken: %s', type(e).__name__)


def reminders(state=None) -> Reminders:
    global _REMINDERS
    with _LOCK:
        if _REMINDERS is None:
            _REMINDERS = Reminders(data_root())
        if state is not None:
            _REMINDERS.state = state
        return _REMINDERS


def t_reminder(a, ctx):
    try:
        when = datetime.strptime(f"{a.get('date')} {a.get('time')}", '%Y-%m-%d %H:%M').astimezone()
    except (TypeError, ValueError):
        return {'result': 'invalid date or time; use YYYY-MM-DD and HH:MM (24h)'}
    if when.timestamp() <= time.time():
        return {'result': 'that time has already passed'}
    item = reminders().add(when, str(a.get('message') or 'Reminder'))
    return {'result': 'reminder set', 'at': item['local'], 'id': item['id'],
            'note': 'It fires while SHARX runs on this phone (spoken, and shown in the app).'}


def t_reminders_list(a, ctx):
    return {'reminders': [{'id': r['id'], 'at': r['local'], 'message': r['message']} for r in reminders().upcoming()]}


# SHARX market tools

def t_market_analysis(a, ctx):
    from app import analysis, instruments, market_data
    tf = str(a.get('timeframe') or '1h').lower()
    if tf not in ('5m', '15m', '1h', '4h', '1d'):
        tf = '1h'
    inst = instruments.resolve(str(a.get('symbol') or ''))
    market_data.timeframe(tf)
    result = analysis.run(inst, tf)
    j = analysis.to_json(result, bars_out=50, include_series=False)
    plan = j.get('plan') or {}
    summary = analysis.summary(result)
    out = {
        'symbol': inst.symbol, 'name': inst.name, 'timeframe': tf, 'price': j.get('price'),
        'bias': j.get('bias', {}).get('label'), 'score': j.get('bias', {}).get('score'),
        'swingTrend': summary.get('swingTrend'), 'internalTrend': summary.get('internalTrend'),
        'lastStructureBreak': summary.get('lastEvent'), 'zone': summary.get('zone'), 'rsi': summary.get('rsi'),
        'plan': {k: plan.get(k) for k in ('direction', 'status', 'entry', 'stop', 'targets', 'rr', 'note')} if plan else None,
        'dataAsOf': datetime.fromtimestamp(j['asOf']).astimezone().strftime('%Y-%m-%d %H:%M') if j.get('asOf') else None,
        'source': j.get('source'),
        'note': 'Rule-based Smart Money Concept reading, not a forecast or financial advice.',
    }
    return out


def t_market_quote(a, ctx):
    from app import background, instruments
    inst = instruments.resolve(str(a.get('symbol') or ''))
    row = next((r for r in background.quotes('all').get('rows', []) if r.get('symbol') == inst.symbol), None)
    if not row or row.get('last') is None:
        return {'symbol': inst.symbol, 'result': 'no price yet; try market_analysis for the latest candle'}
    return {'symbol': inst.symbol, 'name': inst.name, 'last': row.get('last'), 'changePct': row.get('changePct'),
            'open': row.get('open'), 'updated': row.get('updated')}


def t_market_news(a, ctx):
    state = ctx['state']
    query = {'limit': max(1, min(10, int(a.get('limit') or 6))), 'maxAgeMinutes': 24 * 60}
    if a.get('symbol'):
        from app import instruments
        query['symbol'] = instruments.resolve(str(a['symbol'])).symbol
    items = state.news_query(query)
    rows = [f"{i.get('published', '')[:16]} · {i.get('source')} · {i.get('sentimentLabel') or 'neutral'} · {i.get('title')}" for i in items]
    return {'count': len(rows), 'headlines': _untrusted('news', '\n'.join(rows) or 'no headlines in the last 24 hours')}


def t_calendar(a, ctx):
    state = ctx['state']
    hours = max(1, min(168, int(a.get('hours') or 24)))
    now = time.time()
    rows = []
    for e in getattr(state, 'events', []) or []:
        try:
            t = datetime.fromisoformat(str(e['date']).replace('Z', '+00:00'))
        except (KeyError, ValueError):
            continue
        if now - 3600 <= t.timestamp() <= now + hours * 3600 and str(e.get('impact', '')).lower() in ('high', 'medium'):
            rows.append({'time': t.astimezone().strftime('%a %d %b %H:%M'), 'currency': e.get('currency'), 'event': e.get('title'),
                         'impact': e.get('impact'), 'forecast': e.get('forecast'), 'previous': e.get('previous'), 'actual': e.get('actual')})
    return {'hours': hours, 'events': rows[:25]}


# phone (Java bridge)

APPS = {
    'whatsapp': 'com.whatsapp', 'wa': 'com.whatsapp', 'whatsapp business': 'com.whatsapp.w4b', 'youtube': 'com.google.android.youtube',
    'chrome': 'com.android.chrome', 'gmail': 'com.google.android.gm', 'maps': 'com.google.android.apps.maps',
    'google maps': 'com.google.android.apps.maps', 'google': 'com.google.android.googlequicksearchbox',
    'play store': 'com.android.vending', 'spotify': 'com.spotify.music', 'telegram': 'org.telegram.messenger',
    'instagram': 'com.instagram.android', 'facebook': 'com.facebook.katana', 'tiktok': 'com.zhiliaoapp.musically',
    'x': 'com.twitter.android', 'twitter': 'com.twitter.android', 'calendar': 'com.google.android.calendar',
    'kalender': 'com.google.android.calendar', 'clock': 'com.google.android.deskclock', 'jam': 'com.google.android.deskclock',
    'calculator': 'com.google.android.calculator', 'kalkulator': 'com.google.android.calculator',
    'photos': 'com.google.android.apps.photos', 'foto': 'com.google.android.apps.photos', 'drive': 'com.google.android.apps.docs',
    'tradingview': 'com.tradingview.tradingviewapp', 'metatrader 4': 'net.metaquotes.metatrader4', 'mt4': 'net.metaquotes.metatrader4',
    'metatrader 5': 'net.metaquotes.metatrader5', 'mt5': 'net.metaquotes.metatrader5', 'binance': 'com.binance.dev',
    'gojek': 'com.gojek.app', 'grab': 'com.grabtaxi.passenger', 'shopee': 'com.shopee.id', 'tokopedia': 'com.tokopedia.tkpd',
    'netflix': 'com.netflix.mediaclient', 'zoom': 'us.zoom.videomeetings', 'meet': 'com.google.android.apps.tachyon',
}


def t_phone_control(a, ctx):
    action = str(a.get('action') or '')
    level = int(a.get('level') if a.get('level') is not None else -1)
    if action in ('volume_set', 'volume_up', 'volume_down', 'mute', 'unmute'):
        return _phone('volume', action, level)
    if action in ('flashlight_on', 'flashlight_off'):
        return _phone('torch', action == 'flashlight_on')
    if action == 'battery':
        return _phone('battery')
    if action == 'device_info':
        return _phone('device')
    if action in ('keep_screen_on', 'allow_screen_off'):
        return _phone('keepScreenOn', action == 'keep_screen_on')
    if action.startswith('open_settings'):
        return _phone('settings', action.removeprefix('open_settings').strip('_') or 'main')
    return {'result': f'unknown action {action}'}


def t_open_app(a, ctx):
    name = ' '.join(str(a.get('name') or '').lower().split())
    package = APPS.get(name) or (name if re.fullmatch(r'[a-z][a-z0-9_]*(\.[a-z0-9_]+)+', name) else None)
    if not package:
        return {'result': f'unknown app {name}', 'known': sorted(set(APPS))[:40]}
    return _phone('openApp', package)


def t_open_link(a, ctx):
    url = str(a.get('url') or '').strip()
    if urlsplit(url).scheme not in ('http', 'https'):
        return {'result': 'only http(s) links'}
    return _phone('openUri', url)


def t_compose(a, ctx):
    kind = str(a.get('kind') or '')
    to = re.sub(r'[^\d+*#,;@.\w-]', '', str(a.get('to') or ''))
    text = str(a.get('text') or '')[:2000]
    if kind == 'call':
        uri = 'tel:' + quote(to)
    elif kind == 'sms':
        uri = 'smsto:' + quote(to)
    elif kind == 'email':
        uri = 'mailto:' + quote(to) + '?subject=' + quote(str(a.get('subject') or '')) + '&body=' + quote(text)
    elif kind == 'whatsapp':
        number = re.sub(r'\D', '', to)
        if number.startswith('0'):
            number = '62' + number[1:]
        uri = 'https://wa.me/' + number + ('?text=' + quote(text) if text else '')
    else:
        return {'result': f'unknown kind {kind}'}
    out = _phone('compose', kind, uri, text)
    out.setdefault('note', 'Opened prefilled on the phone; the user presses send or call.')
    return out


def t_open_map(a, ctx):
    place = str(a.get('destination') or '').strip()
    if not place:
        return {'result': 'destination missing'}
    if a.get('navigate'):
        mode = {'walking': 'w', 'transit': 'r', 'motorcycle': 'l'}.get(str(a.get('mode') or ''), 'd')
        return _phone('openUri', f'google.navigation:q={quote(place)}&mode={mode}')
    return _phone('openUri', f'geo:0,0?q={quote(place)}')


def t_pc_command(a, ctx):
    from app import jarvis_remote
    return jarvis_remote.pc_command(str(a.get('command') or ''), wait=True)


def t_pc_status(a, ctx):
    from app import jarvis_remote
    return jarvis_remote.pc_status(check=True)


TOOLS = [
    Tool('save_memory', 'Save a fact about the user for future conversations (name, preferences, people, projects, wishes). '
         'Call silently whenever something worth remembering comes up.',
         _obj({'category': _s('Kind of fact', ('identity', 'preferences', 'projects', 'relationships', 'wishes', 'notes')),
               'key': _s('Short English key, e.g. name, sister, favorite_pair'), 'value': _s('The fact to remember')},
              ('category', 'key', 'value')), t_save_memory),
    Tool('recall_memory', 'Search everything stored about the user, including entries not shown in the memory block.',
         _obj({'query': _s('What to look for, in English')}, ('query',)), t_recall_memory),
    Tool('get_datetime', 'Current local date, time and time zone of the phone.', _obj(), t_datetime),
    Tool('weather_report', 'Current weather and two-day forecast for a city.', _obj({'city': _s('City name')}, ('city',)), t_weather),
    Tool('read_webpage', 'Fetch a public web page and return its readable text.', _obj({'url': _s('Full http(s) URL')}, ('url',)), t_read_webpage),
    Tool('reminder', 'Set a timed reminder on the phone.',
         _obj({'date': _s('Date in YYYY-MM-DD format'), 'time': _s('Time in HH:MM format (24h)'),
               'message': _s('Reminder message text')}, ('date', 'time', 'message')), t_reminder),
    Tool('list_reminders', 'List the reminders that are still pending.', _obj(), t_reminders_list),
    Tool('market_analysis', 'Smart Money Concept analysis of a market symbol in SHARX INVEST: bias, structure, zone and '
         'the current trade plan (entry, stop, targets).',
         _obj({'symbol': _s('Symbol, e.g. EURUSD, XAUUSD, BTCUSD, AAPL, BBCA'),
               'timeframe': _s('Chart timeframe', ('5m', '15m', '1h', '4h', '1d'))}, ('symbol',)), t_market_analysis),
    Tool('market_quote', 'Latest price and daily change of a market symbol.', _obj({'symbol': _s('Symbol')}, ('symbol',)), t_market_quote),
    Tool('market_news', 'Latest market headlines with sentiment, optionally for one symbol.',
         _obj({'symbol': _s('Optional symbol'), 'limit': dict(I, description='How many headlines, 1-10')}), t_market_news),
    Tool('economic_calendar', 'Upcoming high and medium impact economic events (forecast, previous, actual).',
         _obj({'hours': dict(I, description='How many hours ahead, default 24')}), t_calendar),
    Tool('open_chart', 'Show the SHARX chart and analysis page of a symbol on the phone screen.',
         _obj({'symbol': _s('Symbol'), 'timeframe': _s('Timeframe', ('5m', '15m', '1h', '4h', '1d'))}, ('symbol',)), client=True),
    Tool('phone_control', 'Control this phone: media volume, flashlight, battery level, device info, keeping the screen '
         'awake, or opening a settings screen.',
         _obj({'action': _s('What to do', ('volume_set', 'volume_up', 'volume_down', 'mute', 'unmute', 'flashlight_on',
                                          'flashlight_off', 'battery', 'device_info', 'keep_screen_on', 'allow_screen_off',
                                          'open_settings', 'open_settings_wifi', 'open_settings_bluetooth',
                                          'open_settings_display', 'open_settings_sound')),
               'level': dict(I, description='Volume percent 0-100 for volume_set')}, ('action',)), t_phone_control),
    Tool('open_app', 'Open an app installed on this phone by its common name (WhatsApp, YouTube, Maps, MT5, ...) or '
         'Android package name.', _obj({'name': _s('App name or package')}, ('name',)), t_open_app),
    Tool('open_link', 'Open a web link on the phone (browser or the app that handles it).', _obj({'url': _s('http(s) URL')}, ('url',)), t_open_link),
    Tool('phone_compose', 'Prepare a phone call, SMS, WhatsApp or e-mail on the phone. It opens prefilled; the user '
         'presses send or call. Announce it before calling.',
         _obj({'kind': _s('What to prepare', ('call', 'sms', 'whatsapp', 'email')), 'to': _s('Phone number or e-mail'),
               'subject': _s('E-mail subject'), 'text': _s('Message text')}, ('kind',)), t_compose),
    Tool('open_map', 'Show a place on the map, or start navigation to it.',
         _obj({'destination': _s('Place or address'), 'navigate': {'type': 'BOOLEAN', 'description': 'Start turn-by-turn navigation'},
               'mode': _s('Travel mode', ('driving', 'motorcycle', 'walking', 'transit'))}, ('destination',)), t_open_map),
    Tool('pc_command', 'Hand a request to JARVIS on the user\'s PC (its apps, files, browser, keyboard, mouse, screen) and '
         'return what the PC answered. Works only while the PC is paired and running.',
         _obj({'command': _s('The request, in the user\'s own words')}, ('command',)), t_pc_command),
    Tool('pc_status', 'Whether JARVIS on the user\'s PC is paired and reachable right now.', _obj(), t_pc_status),
    Tool('shutdown_jarvis', 'End this conversation and stop listening. Only when the user explicitly ends the session.',
         _obj(), client=True),
]
TOOL_BY_NAME = {t.name: t for t in TOOLS}


# ---------------------------------------------------------------------------------------------- WebView WebSocket

def _unmask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    if not n:
        return b''
    key = int.from_bytes((mask * (n // 4 + 1))[:n], 'big')
    return (int.from_bytes(data, 'big') ^ key).to_bytes(n, 'big')


class Browser:
    """Server end of the WebView's WebSocket (RFC 6455) on the request handler's socket."""
    MAX = 1 << 20

    def __init__(self, handler):
        key = handler.headers.get('Sec-WebSocket-Key', '')
        if not key or len(key) > 128:
            raise ValueError('invalid WebSocket key')
        accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        handler.send_response(101)
        handler.send_header('Upgrade', 'websocket')
        handler.send_header('Connection', 'Upgrade')
        handler.send_header('Sec-WebSocket-Accept', accept)
        handler.end_headers()
        handler.wfile.flush()
        handler.close_connection = True
        self.sock = handler.connection
        self.sock.settimeout(None)
        self.lock = threading.Lock()
        self.closed = False

    def _send(self, opcode: int, payload: bytes) -> bool:
        n = len(payload)
        header = bytes([0x80 | opcode, n]) if n < 126 else bytes([0x80 | opcode, 126]) + struct.pack('!H', n) if n < 65536 \
            else bytes([0x80 | opcode, 127]) + struct.pack('!Q', n)
        with self.lock:
            if self.closed:
                return False
            try:
                self.sock.sendall(header + payload)
                return True
            except OSError:
                self.closed = True
                return False

    def send(self, message: dict) -> bool:
        return self._send(1, json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode())

    def send_audio(self, pcm: bytes) -> bool:
        return self._send(2, pcm)

    def _read(self, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise ConnectionError('closed')
            out.extend(chunk)
        return bytes(out)

    def recv(self):
        """(kind, payload) with kind 'text' or 'binary'; None once the page closed the socket."""
        message, kind = bytearray(), None
        try:
            while True:
                b0, b1 = self._read(2)
                opcode, length = b0 & 15, b1 & 127
                if not b1 & 128:
                    raise ValueError('unmasked client frame')
                if length == 126:
                    length = struct.unpack('!H', self._read(2))[0]
                elif length == 127:
                    length = struct.unpack('!Q', self._read(8))[0]
                if length > self.MAX:
                    raise ValueError('frame too large')
                mask = self._read(4)
                payload = _unmask(self._read(length), mask)
                if opcode == 8:
                    self._send(8, payload[:2])
                    return None
                if opcode == 9:
                    self._send(10, payload)
                    continue
                if opcode == 10:
                    continue
                if opcode in (1, 2):
                    kind = 'text' if opcode == 1 else 'binary'
                message.extend(payload)
                if len(message) > self.MAX:
                    raise ValueError('message too large')
                if b0 & 0x80:
                    return kind, bytes(message)
        except (OSError, ConnectionError, ValueError):
            return None

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        try:
            self.sock.sendall(b'\x88\x02\x03\xe8')
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------ Gemini Live relay

class SetupRejected(Exception):
    def __init__(self, code, reason):
        super().__init__(f'{code} {reason}')
        self.code, self.reason = code, reason or ''


def _classify(e: SetupRejected) -> str:
    text = e.reason.lower()
    if 'api key' in text or 'api_key' in text or 'permission' in text or 'unauthenticated' in text or 'credential' in text:
        return 'key'
    if 'quota' in text or 'exhausted' in text or 'rate' in text:
        return 'quota'
    if 'search' in text or 'tool' in text:
        return 'extras'
    return 'model'


class LiveSession:
    """One conversation: the WebView end, the Live API end, and the tools in between."""

    def __init__(self, state, browser: Browser):
        self.state, self.browser = state, browser
        self.settings = settings()
        self.up = None
        self.up_lock = threading.Lock()
        self.model = None
        self.handle = None  # session resumption handle from the server
        self.extras = True
        self.stopping = False
        self.pending: dict[str, futures.Future] = {}
        self.pool = futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix='jarvis-tool')
        self.cancelled: set[str] = set()
        self.rate = 24000
        self.reply_to = None   # a request from the PC (jarvis_lan) waits for the answer of this turn
        self.reply_text: list[str] = []

    # -- upstream

    def _setup(self, model: str) -> dict:
        declarations = [t.declaration() for t in TOOLS]
        tools = [{'functionDeclarations': declarations}]
        if self.extras and self.settings.values.get('googleSearch', True):
            tools.append({'googleSearch': {}})
        setup = {
            'model': model,
            'generationConfig': {
                'responseModalities': ['AUDIO'],
                'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': self.settings.values.get('voice', 'Charon')}}},
            },
            'systemInstruction': {'parts': [{'text': system_prompt(declarations)}]},
            'tools': tools,
            'inputAudioTranscription': {},
            'outputAudioTranscription': {},
            'contextWindowCompression': {'slidingWindow': {}},
            'sessionResumption': {'handle': self.handle} if self.handle else {},
        }
        return {'setup': setup}

    def _open(self, model: str):
        from websockets.sync.client import connect
        from websockets.exceptions import ConnectionClosed
        url = live_url()
        context = None
        if url.startswith('wss:'):
            try:
                import certifi
                context = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                context = ssl.create_default_context()
        ws = connect(url, additional_headers={'x-goog-api-key': self.settings.key()}, ssl=context, open_timeout=15,
                     close_timeout=3, max_size=32 << 20, compression=None, ping_interval=20, ping_timeout=30,
                     user_agent_header='SHARX-JARVIS')
        try:
            ws.send(json.dumps(self._setup(model)))
            while True:
                raw = ws.recv(timeout=20)
                message = json.loads(raw)
                if 'setupComplete' in message:
                    return ws
        except ConnectionClosed as e:
            code = getattr(e.rcvd, 'code', None) if e.rcvd else None
            reason = getattr(e.rcvd, 'reason', '') if e.rcvd else ''
            raise SetupRejected(code, reason) from None
        except TimeoutError:
            ws.close()
            raise SetupRejected(None, 'no setupComplete within 20 s') from None

    def connect(self, resuming=False) -> bool:
        if not self.settings.key():
            self.browser.send({'type': 'error', 'code': 'no_key', 'message': 'Gemini API key belum diisi. Buka PENGATURAN JARVIS.'})
            return False
        self.browser.send({'type': 'state', 'state': 'reconnecting' if resuming else 'connecting'})
        order = [self.model] + [m for m in LIVE_MODELS if m != self.model] if self.model else list(LIVE_MODELS)
        last = None
        for model in order:
            for attempt in range(2):
                try:
                    ws = self._open(model)
                except SetupRejected as e:
                    last = e
                    kind = _classify(e)
                    log.warning('JARVIS Live setup rejected (%s): %s %s', model, e.code, e.reason[:120])
                    if kind == 'key':
                        self.browser.send({'type': 'error', 'code': 'key', 'message': 'Gemini API key ditolak. Periksa key di PENGATURAN JARVIS.'})
                        return False
                    if kind == 'extras' and self.extras:
                        self.extras = False  # Google Search grounding not accepted with this model: plain tools
                        continue
                    if self.handle and attempt == 0:
                        self.handle = None  # an expired resumption handle: start a fresh session
                        continue
                    break
                except (OSError, TimeoutError) as e:
                    last = SetupRejected(None, f'{type(e).__name__}: {e}')
                    log.warning('JARVIS Live connect failed: %s', type(e).__name__)
                    break
                with self.up_lock:
                    self.up, self.model = ws, model
                self.browser.send({'type': 'ready', 'model': model.removeprefix('models/'), 'resumed': bool(resuming and self.handle),
                                   'googleSearch': self.extras and self.settings.values.get('googleSearch', True)})
                return True
        kind = _classify(last) if last else 'model'
        message = {'quota': 'Kuota Gemini Live akun ini habis untuk sementara. Coba lagi nanti.',
                   }.get(kind, 'Gemini Live tidak bisa dihubungi: ' + (last.reason[:160] if last else 'tanpa alasan') + '.')
        self.browser.send({'type': 'error', 'code': kind, 'message': message})
        return False

    def send_up(self, message: dict) -> bool:
        with self.up_lock:
            ws = self.up
            if ws is None:
                return False
            try:
                ws.send(json.dumps(message, separators=(',', ':')))
                return True
            except Exception:
                return False

    def inject(self, text: str) -> bool:
        return self.send_up({'clientContent': {'turns': [{'role': 'user', 'parts': [{'text': text}]}], 'turnComplete': True}})

    def pump(self):
        """Live API → WebView, reconnecting with the resumption handle when the server goes away."""
        from websockets.exceptions import ConnectionClosed
        while not self.stopping:
            ws = self.up
            try:
                raw = ws.recv()
            except (ConnectionClosed, OSError, TimeoutError) as e:
                if self.stopping:
                    return
                log.info('JARVIS Live connection ended: %s', type(e).__name__)
                with self.up_lock:
                    if self.up is ws:
                        self.up = None
                time.sleep(0.5)
                if not self.connect(resuming=True):
                    self.browser.send({'type': 'closed', 'reason': 'disconnected'})
                    return
                continue
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8', 'replace')
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            self.handle_up(message, ws)

    def handle_up(self, m: dict, ws):
        content = m.get('serverContent')
        if content:
            for part in (content.get('modelTurn') or {}).get('parts') or []:
                blob = part.get('inlineData')
                if blob and str(blob.get('mimeType', '')).startswith('audio/pcm'):
                    rate = int((re.search(r'rate=(\d+)', blob['mimeType']) or [0, 24000])[1])
                    if rate != self.rate:
                        self.rate = rate
                        self.browser.send({'type': 'audio_format', 'rate': rate})
                    self.browser.send_audio(base64.b64decode(blob.get('data') or ''))
            if (content.get('inputTranscription') or {}).get('text'):
                self.browser.send({'type': 'transcript', 'role': 'user', 'text': content['inputTranscription']['text']})
            if (content.get('outputTranscription') or {}).get('text'):
                self.browser.send({'type': 'transcript', 'role': 'model', 'text': content['outputTranscription']['text']})
                if self.reply_to:
                    self.reply_text.append(content['outputTranscription']['text'])
            if content.get('interrupted'):
                self.browser.send({'type': 'interrupted'})
            if content.get('turnComplete'):
                self.browser.send({'type': 'turn_complete'})
                if self.reply_to:
                    from app import jarvis_lan
                    jarvis_lan.deliver_reply(self.reply_to, ' '.join(''.join(self.reply_text).split()))
                    self.reply_to, self.reply_text = None, []
        if 'toolCall' in m:
            calls = m['toolCall'].get('functionCalls') or []
            self.pool.submit(self.run_tools, calls)
        if 'toolCallCancellation' in m:
            self.cancelled.update(m['toolCallCancellation'].get('ids') or [])
        update = m.get('sessionResumptionUpdate')
        if update and update.get('resumable') and update.get('newHandle'):
            self.handle = update['newHandle']
        if 'goAway' in m:
            # the server is about to end this connection: continue on a new one with the resumption handle
            log.info('JARVIS Live goAway %s', m['goAway'].get('timeLeft'))
            threading.Thread(target=self._close_soon, args=(ws,), daemon=True).start()

    def _close_soon(self, ws):
        time.sleep(1.0)
        try:
            ws.close()
        except Exception:
            pass

    def run_tools(self, calls: list[dict]):
        responses = []
        self.browser.send({'type': 'state', 'state': 'thinking'})
        for call in calls:
            cid, name, args = call.get('id'), call.get('name'), call.get('args') or {}
            tool = TOOL_BY_NAME.get(name)
            self.browser.send({'type': 'tool', 'phase': 'start', 'id': cid, 'name': name, 'args': args})
            started = time.monotonic()
            try:
                if tool is None:
                    result = {'result': f'unknown tool {name}'}
                elif tool.client:
                    result = self.client_tool(cid, name, args)
                else:
                    result = tool.handler(args, {'state': self.state})
            except Exception as e:
                log.warning('JARVIS tool %s failed: %s', name, type(e).__name__)
                result = {'result': 'failed', 'detail': f'{type(e).__name__}: {str(e)[:200]}'}
            if not isinstance(result, dict):
                result = {'result': str(result)}
            self.browser.send({'type': 'tool', 'phase': 'done', 'id': cid, 'name': name,
                               'ms': int((time.monotonic() - started) * 1000), 'ok': result.get('result') not in ('failed',)})
            if cid in self.cancelled:
                continue
            responses.append({'id': cid, 'name': name, 'response': result})
        if responses:
            self.send_up({'toolResponse': {'functionResponses': responses}})

    def client_tool(self, cid, name, args) -> dict:
        future: futures.Future = futures.Future()
        self.pending[cid] = future
        self.browser.send({'type': 'client_tool', 'id': cid, 'name': name, 'args': args})
        try:
            return future.result(timeout=20)
        except futures.TimeoutError:
            return {'result': 'the app did not answer'}
        finally:
            self.pending.pop(cid, None)

    # -- WebView end

    def run(self):
        first = self.browser.recv()
        if not first or first[0] != 'text':
            return
        try:
            start = json.loads(first[1])
        except ValueError:
            return
        if start.get('type') != 'start':
            return
        with _LOCK:
            _SESSIONS.append(self)
        try:
            if not self.connect():
                return
            threading.Thread(target=self.pump, name='jarvis-live', daemon=True).start()
            while True:
                frame = self.browser.recv()
                if frame is None:
                    break
                kind, payload = frame
                if kind == 'binary':
                    self.send_up({'realtimeInput': {'audio': {'data': base64.b64encode(payload).decode(), 'mimeType': 'audio/pcm;rate=16000'}}})
                    continue
                try:
                    message = json.loads(payload)
                except ValueError:
                    continue
                kind = message.get('type')
                if kind == 'text' and str(message.get('text') or '').strip():
                    text = str(message['text'])[:4000]
                    if message.get('replyTo'):
                        self.reply_to, self.reply_text = str(message['replyTo']), []
                        text = f'(Sent from the user\'s PC) {text}'
                    self.inject(text)
                elif kind == 'audio_end':
                    self.send_up({'realtimeInput': {'audioStreamEnd': True}})
                elif kind == 'tool_result' and message.get('id') in self.pending:
                    result = message.get('result')
                    self.pending[message['id']].set_result(result if isinstance(result, dict) else {'result': str(result)})
                elif kind == 'stop':
                    break
        finally:
            self.close()

    def close(self):
        self.stopping = True
        with _LOCK:
            if self in _SESSIONS:
                _SESSIONS.remove(self)
        with self.up_lock:
            ws, self.up = self.up, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        for future in list(self.pending.values()):
            if not future.done():
                future.set_result({'result': 'conversation ended'})
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.browser.close()


def active_session() -> LiveSession | None:
    with _LOCK:
        return next((s for s in reversed(_SESSIONS) if s.up is not None and not s.stopping), None)


def live(handler):
    """GET /api/jarvis/live (WebSocket), after the host checked the session cookie, Host and Origin."""
    try:
        browser = Browser(handler)
    except ValueError:
        return handler.reply(400, dict(error='invalid_websocket'))
    reminders(handler.server.state)
    LiveSession(handler.server.state, browser).run()


# ------------------------------------------------------------------------------------------------------------- API

def status(state=None) -> dict:
    from app import jarvis_lan, jarvis_remote
    session = active_session()
    return dict(
        mode='phone', source=SOURCE, license='CC BY-NC 4.0', markCommit=_mark_commit(),
        settings=settings().view(), voices=list(VOICES), models=[m.removeprefix('models/') for m in LIVE_MODELS],
        session=dict(active=session is not None, model=(session.model or '').removeprefix('models/') if session else None),
        reminders=[{k: r[k] for k in ('id', 'local', 'message')} for r in reminders(state).upcoming()],
        pc=jarvis_remote.pc_status(check=False),
        lan=jarvis_lan.status(),
        tools=[dict(name=t.name, client=t.client) for t in TOOLS],
    )


def _mark_commit() -> str | None:
    try:
        return (MARK_DIR / 'COMMIT').read_text().strip()[:40] or None
    except OSError:
        return None


def dispatch(state, method: str, p: str, q: dict, b: dict):
    from app import jarvis_lan, jarvis_remote
    post = method == 'POST'
    if p == '/jarvis/status':
        return status(state)
    if p == '/jarvis/lan':
        # PC → phone: the user switches the listener on or off, makes a new one-time key, or forgets every PC
        action = b.get('action') if post else None
        if action == 'enable':
            return jarvis_lan.enable(True, state)
        if action == 'disable':
            return jarvis_lan.enable(False, state)
        if action == 'key':
            return jarvis_lan.new_key()
        if action == 'forget':
            return jarvis_lan.forget_all()
        return jarvis_lan.status()
    if p == '/jarvis/settings':
        if post:
            settings().update(b)
        return status(state)
    if p == '/jarvis/memory':
        mm = memory()
        if post and b.get('action') == 'forget':
            mm.forget(str(b.get('key') or ''), str(b.get('category') or 'notes'))
        return dict(entries=mm.all_entries_for_ui())
    if p == '/jarvis/reminders':
        if post and b.get('action') == 'cancel':
            reminders(state).cancel(str(b.get('id') or ''))
        return dict(reminders=[{k: r[k] for k in ('id', 'local', 'message')} for r in reminders(state).upcoming()])
    if not post and p in ('/jarvis/probe', '/jarvis/pc/pair', '/jarvis/pc/unpair', '/jarvis/pc/command'):
        raise ValueError('Gunakan POST.')
    if p == '/jarvis/probe':
        return jarvis_remote.probe(b.get('address'))
    if p == '/jarvis/pc/pair':
        return jarvis_remote.pair(b.get('address'), b.get('key'))
    if p == '/jarvis/pc/unpair':
        return jarvis_remote.unpair()
    if p == '/jarvis/pc/command':
        return jarvis_remote.pc_command(str(b.get('text') or ''), wait=bool(b.get('wait', True)))
    if p == '/jarvis/pc':
        return jarvis_remote.pc_status(check=True)
    raise ValueError('Endpoint JARVIS tidak dikenal.')
