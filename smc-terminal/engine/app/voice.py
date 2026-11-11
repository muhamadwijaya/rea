"""Voice for the news assistant: offline neural text-to-speech, short yes/no answers by voice, and the full
text of a news article to read aloud.

- Text-to-speech: Piper (offline, free, no usage limit). Voices: Indonesian `id_ID-news_tts-medium` and
  English `en_US-lessac-medium`, downloaded once from the Piper voice repository and checked against their
  published MD5 digests. Voice model licences follow their datasets (personal / non-commercial use).
- Speech-to-text: faster-whisper `tiny` (multilingual, offline) for the short answer after a question.
  Audio arrives as 16 kHz mono WAV from the browser and is passed to the model as samples.
- Article text: the article page as served to a normal reader, extracted with trafilatura. Sites that
  refuse automated readers (403/429, bot checkpoints) or redirect links that need a browser are not worked
  around: the caller then gets the feed summary with the reason.

The packages are optional (requirements-voice.txt); without them the endpoints say what is missing.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
import time
import wave
from collections import OrderedDict

import httpx
import numpy as np

from . import paths
from .errors import EngineError

log = logging.getLogger("smc.voice")

VOICE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
VOICES = {
    "id": {"label": "Indonesia (Piper news_tts, medium)", "stem": "id_ID-news_tts-medium", "dir": "id/id_ID/news_tts/medium/",
           "files": {".onnx": (62950044, "17de01db7ac654655436b6e509893c72"),
                     ".onnx.json": (5050, "c023cfa031cc9ed4cf5dd8d2ef1e48ed")}},
    "en": {"label": "English (Piper lessac, medium)", "stem": "en_US-lessac-medium", "dir": "en/en_US/lessac/medium/",
           "files": {".onnx": (63201294, "2fc642b535197b6305c7c8f92dc8b24f"),
                     ".onnx.json": (4885, "c1f2b7bddefe113f3255ff9ef234cfd3")}},
}
STT_MODEL = "tiny"
STT_SIZE = 75_000_000
MAX_TTS_CHARS = 1200
MAX_ARTICLE_CHARS = 12_000
USER_AGENT = "Mozilla/5.0 (compatible; SharxInvest/3.0; local news reader)"

YES = {"ya", "iya", "ia", "iyah", "yah", "yes", "yep", "yup", "boleh", "oke", "ok", "okay", "okey", "lanjut", "lanjutkan",
       "baca", "bacakan", "mau", "silakan", "silahkan", "betul", "benar", "sip", "gas", "sure", "go", "please", "read"}
NO = {"tidak", "tdk", "nggak", "ngga", "gak", "ga", "enggak", "engga", "ndak", "no", "nope", "jangan", "nanti", "skip",
      "lewati", "lewat", "batal", "stop", "cukup", "tak", "bukan", "later", "cancel"}

_lock = threading.Lock()
_downloads: dict[str, dict] = {}
_voices: dict[str, object] = {}
_voice_locks: dict[str, threading.Lock] = {k: threading.Lock() for k in VOICES}
_stt = None
_stt_lock = threading.Lock()
_tts_cache: OrderedDict[str, bytes] = OrderedDict()


class VoiceUnavailable(EngineError):
    status = 503
    code = "voice_unavailable"


def _voice_dir():
    d = paths.data_dir() / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stt_dir():
    d = paths.data_dir() / "stt"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _has(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:  # missing or broken optional package
        return False


def _voice_ready(key: str) -> bool:
    v = VOICES[key]
    return all((_voice_dir() / (v["stem"] + ext)).exists() for ext in v["files"])


def _stt_ready() -> bool:
    return any(_stt_dir().glob("**/model.bin"))


def status() -> dict:
    with _lock:
        downloads = {k: dict(v) for k, v in _downloads.items()}
    return {
        "packages": {"piper": _has("piper"), "whisper": _has("faster_whisper"), "trafilatura": _has("trafilatura")},
        "tts": {k: {"label": v["label"], "ready": _voice_ready(k), "sizeBytes": sum(s for s, _ in v["files"].values()),
                    "download": downloads.get(k)} for k, v in VOICES.items()},
        "stt": {"model": f"whisper-{STT_MODEL}", "ready": _stt_ready(), "sizeBytes": STT_SIZE, "download": downloads.get("stt")},
    }


# ------------------------------------------------------------------ downloads


def setup(items: list[str]) -> dict:
    """Start downloading the requested models (voice keys and/or "stt") in the background."""
    for item in items:
        if item not in VOICES and item != "stt":
            raise VoiceUnavailable(f"Model tidak dikenal: {item}")
        with _lock:
            if _downloads.get(item, {}).get("status") == "downloading":
                continue
            _downloads[item] = {"status": "downloading", "done": 0, "total": STT_SIZE if item == "stt" else
                                sum(s for s, _ in VOICES[item]["files"].values()), "error": None, "started": int(time.time())}
        threading.Thread(target=_download, args=(item,), daemon=True, name=f"voice-{item}").start()
    return status()


def _progress(item: str, **kw) -> None:
    with _lock:
        _downloads.setdefault(item, {}).update(kw)


def _download(item: str) -> None:
    try:
        if item == "stt":
            if not _has("faster_whisper"):
                raise VoiceUnavailable("Paket faster-whisper belum terpasang (jalankan setup.bat).")
            _load_stt()
        else:
            _download_voice(item)
        _progress(item, status="ready", finished=int(time.time()))
    except Exception as exc:  # reported in status, never fatal for the engine
        log.warning("voice download %s failed: %s", item, exc)
        _progress(item, status="error", error=str(getattr(exc, "message", exc))[:200])


def _download_voice(key: str) -> None:
    v = VOICES[key]
    done = 0
    for ext, (size, md5) in v["files"].items():
        target = _voice_dir() / (v["stem"] + ext)
        if target.exists() and target.stat().st_size == size:
            done += size
            _progress(key, done=done)
            continue
        part = target.with_suffix(target.suffix + ".part")
        digest = hashlib.md5()
        with httpx.stream("GET", VOICE_BASE + v["dir"] + v["stem"] + ext, follow_redirects=True, timeout=60.0) as r:
            r.raise_for_status()
            with open(part, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    _progress(key, done=done)
        if digest.hexdigest() != md5:
            part.unlink(missing_ok=True)
            raise VoiceUnavailable(f"Unduhan {target.name} rusak (checksum tidak cocok); coba lagi.")
        part.replace(target)


# ------------------------------------------------------------------ text-to-speech

_NUM_GROUPS = re.compile(r"(?<=\d)\.(?=\d{3}\b)")


def normalize(text: str, lang: str) -> str:
    """Make prices, percents and symbols readable aloud."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = t.replace("&", " dan " if lang == "id" else " and ")
    if lang == "id":
        t = _NUM_GROUPS.sub("", t)  # 18.150 → 18150 (Indonesian thousands separator)
        t = re.sub(r"(\d),(\d)", r"\1 koma \2", t)
        t = re.sub(r"(\d)\s?%", r"\1 persen", t)
        t = re.sub(r"\bRp\.?\s?", "rupiah ", t)
        t = re.sub(r"US\$\s?|\$\s?", "dolar AS ", t)
    else:
        t = re.sub(r"(\d)\s?%", r"\1 percent", t)
    return t[:MAX_TTS_CHARS]


def _voice(lang: str):
    key = lang if lang in VOICES else "id"
    if not _voice_ready(key):
        key = "id" if _voice_ready("id") else "en" if _voice_ready("en") else None
    if key is None:
        raise VoiceUnavailable("Suara belum diunduh: buka pengaturan suara dan tekan UNDUH SUARA.")
    if key not in _voices:
        try:
            from piper import PiperVoice
        except Exception as exc:
            raise VoiceUnavailable("Paket piper-tts belum terpasang (jalankan setup.bat).") from exc
        _voices[key] = PiperVoice.load(str(_voice_dir() / (VOICES[key]["stem"] + ".onnx")))
    return key, _voices[key]


def speak(text: str, lang: str = "id", rate: float = 1.0) -> bytes:
    """WAV bytes of `text` read by the Piper voice for `lang` (falls back to the other installed voice)."""
    from piper import SynthesisConfig

    rate = max(0.6, min(1.8, float(rate or 1.0)))
    key, voice = _voice(lang)
    clean = normalize(text, key)
    if not clean:
        raise VoiceUnavailable("Teks kosong.")
    cache_key = hashlib.sha1(f"{key}|{rate:.2f}|{clean}".encode()).hexdigest()
    with _lock:
        hit = _tts_cache.get(cache_key)
        if hit:
            _tts_cache.move_to_end(cache_key)
            return hit
    buf = io.BytesIO()
    with _voice_locks[key], wave.open(buf, "wb") as w:
        voice.synthesize_wav(clean, w, syn_config=SynthesisConfig(length_scale=1.0 / rate))
    data = buf.getvalue()
    with _lock:
        _tts_cache[cache_key] = data
        while len(_tts_cache) > 60:
            _tts_cache.popitem(last=False)
    return data


# ------------------------------------------------------------------ speech-to-text (short answers)


def _load_stt():
    global _stt
    with _stt_lock:
        if _stt is None:
            from faster_whisper import WhisperModel

            _stt = WhisperModel(STT_MODEL, device="cpu", compute_type="int8", download_root=str(_stt_dir()))
    return _stt


def _samples_16k(wav_bytes: bytes) -> np.ndarray:
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            if w.getsampwidth() != 2:
                raise VoiceUnavailable("Audio harus WAV PCM 16-bit.")
            sr, channels = w.getframerate(), w.getnchannels()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    except (wave.Error, EOFError) as exc:
        raise VoiceUnavailable("Audio tidak bisa dibaca (harus WAV).") from exc
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if sr != 16000 and len(x):
        n = int(len(x) * 16000 / sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x[: 16000 * 15]  # an answer, not a speech


def intent(text: str) -> str | None:
    """'yes' / 'no' / None from a short spoken answer (Indonesian or English, tolerant of small mishearings)."""
    words = re.findall(r"[a-z]+", (text or "").lower())

    def near(word: str, vocab: set[str]) -> bool:
        if word in vocab:
            return True
        return len(word) >= 4 and any(len(v) >= 4 and _edits(word, v) <= 1 for v in vocab)

    if any(near(w, NO) for w in words):  # "nanti saja", "tidak usah" → no, even with other words
        return "no"
    if any(near(w, YES) for w in words):
        return "yes"
    return None


def _edits(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 1:
        return 2
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def transcribe(wav_bytes: bytes, lang: str = "id") -> dict:
    if not _stt_ready() and _stt is None:
        raise VoiceUnavailable("Pengenal suara belum diunduh: buka pengaturan suara dan tekan UNDUH.")
    try:
        model = _load_stt()
    except ImportError as exc:
        raise VoiceUnavailable("Paket faster-whisper belum terpasang (jalankan setup.bat).") from exc
    audio = _samples_16k(wav_bytes)
    if len(audio) < 1600:
        return {"text": "", "intent": None}
    started = time.perf_counter()
    segments, _ = model.transcribe(audio, language=lang if lang in ("id", "en") else None, beam_size=1,
                                   without_timestamps=True, initial_prompt="Jawaban singkat: ya atau tidak.")
    text = " ".join(s.text for s in segments).strip()
    return {"text": text, "intent": intent(text), "elapsedMs": int((time.perf_counter() - started) * 1000)}


# ------------------------------------------------------------------ article text

_BLOCKED = re.compile(r"(security checkpoint|captcha|access denied|enable javascript|are you a robot|verify you are human|"
                      r"just a moment|attention required)", re.I)


def article(url: str, title: str = "", summary: str = "") -> dict:
    """Full article text when the site serves it to a reader; otherwise the feed summary and why."""
    fallback = {"text": " ".join(x for x in (title, summary) if x).strip(), "source": "summary"}
    if not url.startswith(("http://", "https://")):
        return {**fallback, "reason": "tautan berita tidak valid"}
    if "news.google.com/" in url:
        return {**fallback, "reason": "tautan Google News perlu dibuka di browser"}
    try:
        import trafilatura
    except ImportError:
        return {**fallback, "reason": "paket trafilatura belum terpasang"}
    try:
        r = httpx.get(url, timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept-Language": "id,en;q=0.8"})
    except httpx.HTTPError as exc:
        return {**fallback, "reason": f"situs tidak dapat dihubungi ({type(exc).__name__})"}
    if r.status_code in (401, 402, 403, 429, 451) or r.status_code >= 500:
        return {**fallback, "reason": f"situs menolak pembaca otomatis (HTTP {r.status_code})"}
    text = trafilatura.extract(r.text, url=str(r.url), include_comments=False, include_tables=False, favor_precision=True) or ""
    if len(text) < 300 or _BLOCKED.search(text[:400]):
        return {**fallback, "reason": "isi lengkap tidak tersedia untuk pembaca otomatis (paywall / verifikasi)"}
    if title and not text.startswith(title[:30]):
        text = f"{title}. {text}"
    return {"text": text[:MAX_ARTICLE_CHARS], "source": "article", "url": str(r.url), "chars": len(text),
            "truncated": len(text) > MAX_ARTICLE_CHARS}
