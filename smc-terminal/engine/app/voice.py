"""Voice for the news assistant: offline neural text-to-speech, short yes/no answers by voice, and the full
text of a news article to read aloud.

- Text-to-speech, natural (default): Supertonic 3 (Supertone; code MIT, weights BigScience OpenRAIL-M) — one
  multilingual on-device model, so Indonesian and English are read by the same human-like voice (10 preset
  voices). Offline, free, no usage limit. Downloaded once from a pinned Hugging Face revision, each file checked
  against its SHA-256.
- Text-to-speech, fallback: Piper. Voices: Indonesian `id_ID-news_tts-medium` and English
  `en_US-lessac-medium` / `en_GB-vctk-medium`, checked against their published MD5 digests. Voice model
  licences follow their datasets (personal / non-commercial use).
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
    "engb": {"label": "English British pria (VCTK p226) — suara JARVIS", "stem": "en_GB-vctk-medium", "dir": "en/en_GB/vctk/medium/",
             "speaker": "p226",
             "files": {".onnx": (76952753, "573025290fdc68812543b7438ace0c29"),
                       ".onnx.json": (6637, "68e82dee4c9a723e804f9fc0b14f1802")}},
    "en": {"label": "English (Piper lessac, medium) — suara natural", "stem": "en_US-lessac-medium", "dir": "en/en_US/lessac/medium/",
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
# Supertonic 3 at the revision the supertonic 1.3 SDK pins; (bytes, SHA-256) per file.
NATURAL = "st3"
NATURAL_BASE = "https://huggingface.co/Supertone/supertonic-3/resolve/724fb5abbf5502583fb520898d45929e62f02c0b/"
NATURAL_FILES = {
    "config.json": (174, "4099082b107a9d4029849ac76b89eca65e03732660969c2babe5bf308c7357f2"),
    "onnx/tts.json": (8253, "42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09"),
    "onnx/unicode_indexer.json": (277676, "9bf7346e43883a81f8645c81224f786d43c5b57f3641f6e7671a7d6c493cb24f"),
    "onnx/duration_predictor.onnx": (3700147, "c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db"),
    "onnx/text_encoder.onnx": (36416150, "c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff"),
    "onnx/vector_estimator.onnx": (256534781, "883ac868ea0275ef0e991524dc64f16b3c0376efd7c320af6b53f5b780d7c61c"),
    "onnx/vocoder.onnx": (101424195, "085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba"),
    "voice_styles/F1.json": (292046, "bbdec6ee00231c2c742ad05483df5334cab3b52fda3ba38e6a07059c4563dbc2"),
    "voice_styles/F2.json": (292423, "7c722c6a72707b1a77f035d67f0d1351ba187738e06f7683e8c72b1df3477fc6"),
    "voice_styles/F3.json": (290794, "12f6ef2573baa2defa1128069cb59f203e3ab67c92af77b42df8a0e3a2f7c6ab"),
    "voice_styles/F4.json": (291808, "c2fa764c1225a76dfc3e2c73e8aa4f70d9ee48793860eb34c295fff01c2e032b"),
    "voice_styles/F5.json": (291479, "45966e73316415626cf41a7d1c6f3b4c70dbc1ba2bee5c1978ef0ce33244fc8d"),
    "voice_styles/M1.json": (291748, "e35604687f5d23694b8e91593a93eec0e4eca6c0b02bb8ed69139ab2ea6b0a5b"),
    "voice_styles/M2.json": (292055, "b76cbf62bac707c710cf0ae5aba5e31eea1a6339a9734bfae33ab98499534a50"),
    "voice_styles/M3.json": (290198, "ea1ac35ccb91b0d7ecad533a2fbd0eec10c91513d8951e3b25fbba99954e159b"),
    "voice_styles/M4.json": (291522, "ca8eefad4fcd989c9379032ff3e50738adc547eeb5e221b82593a6d7b3bac303"),
    "voice_styles/M5.json": (291469, "dd22b92740314321f8ae11c5e87f8dd60d060f15dd3a632b5adf77f471f77af2"),
}
NATURAL_VOICES = ("M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5")
NATURAL_SIZE = sum(size for size, _ in NATURAL_FILES.values())

_downloads: dict[str, dict] = {}
_voices: dict[str, object] = {}
_voice_locks: dict[str, threading.Lock] = {k: threading.Lock() for k in VOICES}
_stt = None
_stt_lock = threading.Lock()
_tts_cache: OrderedDict[str, bytes] = OrderedDict()
_natural = None
_natural_styles: dict[str, object] = {}
_natural_lock = threading.Lock()


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


def _natural_dir():
    return _voice_dir() / "supertonic-3"


def _natural_ready() -> bool:
    root = _natural_dir()
    return all((root / path).is_file() and (root / path).stat().st_size == size for path, (size, _) in NATURAL_FILES.items())


def _stt_ready() -> bool:
    return any(_stt_dir().glob("**/model.bin"))


def status() -> dict:
    with _lock:
        downloads = {k: dict(v) for k, v in _downloads.items()}
    return {
        "packages": {"piper": _has("piper"), "whisper": _has("faster_whisper"), "trafilatura": _has("trafilatura"),
                     "pyworld": _has("pyworld"), "wordfreq": _has("wordfreq"), "supertonic": _has("supertonic")},
        "natural": {"label": "Supertonic 3 — suara natural Indonesia + English", "ready": _natural_ready(),
                    "sizeBytes": NATURAL_SIZE, "voices": list(NATURAL_VOICES), "download": downloads.get(NATURAL)},
        "tts": {k: {"label": v["label"], "ready": _voice_ready(k), "sizeBytes": sum(s for s, _ in v["files"].values()),
                    "download": downloads.get(k)} for k, v in VOICES.items()},
        "stt": {"model": f"whisper-{STT_MODEL}", "ready": _stt_ready(), "sizeBytes": STT_SIZE, "download": downloads.get("stt")},
    }


# ------------------------------------------------------------------ downloads


def setup(items: list[str]) -> dict:
    """Start downloading the requested models (voice keys and/or "stt") in the background."""
    for item in items:
        if item not in VOICES and item not in ("stt", NATURAL):
            raise VoiceUnavailable(f"Model tidak dikenal: {item}")
        total = STT_SIZE if item == "stt" else NATURAL_SIZE if item == NATURAL else sum(s for s, _ in VOICES[item]["files"].values())
        with _lock:
            if _downloads.get(item, {}).get("status") == "downloading":
                continue
            _downloads[item] = {"status": "downloading", "done": 0, "total": total, "error": None, "started": int(time.time())}
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
        elif item == NATURAL:
            _download_natural()
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


def _download_natural() -> None:
    if not _has("supertonic"):
        raise VoiceUnavailable("Paket supertonic belum terpasang (jalankan setup.bat).")
    root = _natural_dir()
    done = 0
    for path, (size, sha256) in NATURAL_FILES.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == size:
            done += size
            _progress(NATURAL, done=done)
            continue
        part = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        with httpx.stream("GET", NATURAL_BASE + path, follow_redirects=True, timeout=60.0) as r:
            r.raise_for_status()
            with open(part, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    _progress(NATURAL, done=done)
        if digest.hexdigest() != sha256:
            part.unlink(missing_ok=True)
            raise VoiceUnavailable(f"Unduhan {path} rusak (checksum tidak cocok); coba lagi.")
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


# --- languages inside one text: English words and phrases in Indonesian news are read by the English voice

_EN_SPELLING = re.compile(r"(th|sh|ph|ck|ee|oo|ea|ou|ow|igh|tion|ness|ment|[^aeiou]y$|ing$|ed$|q|x|w[^aeiouy])", re.I)
_TOKEN = re.compile(r"\S+|\s+")


def word_lang(word: str) -> str | None:
    """'en' / 'id' for a word, None when it carries no language (numbers, acronyms, tickers, unknown names)."""
    core = re.sub(r"^[^\w]+|[^\w]+$", "", word)
    if not core or not re.search(r"[A-Za-z]", core) or (core.isupper() and len(core) <= 5):
        return None
    w = core.lower().replace("'s", "")
    try:
        from wordfreq import zipf_frequency
    except ImportError:
        zipf_frequency = None
    if zipf_frequency is not None:
        en, idn = zipf_frequency(w, "en"), zipf_frequency(w, "id")
        if en >= 2.0 and en - idn >= 0.15:
            return "en"  # rebound, outlook, trading, market ...
        if idn >= 2.0 and idn - en >= 0.15:
            return "id"  # saham, naik, investor
        if en >= 2.0 and idn >= 2.0:
            return None  # equally common in both (data, Nvidia): follows the sentence
        if en > 0 or idn > 0:
            return "en" if en > idn else "id"
    return "en" if _EN_SPELLING.search(w) else None  # unknown names: English spelling patterns (CoreWeave)


def segments(text: str, base: str = "id") -> list[tuple[str, str]]:
    """Split a text into runs of one language; words without a language follow their neighbours."""
    tokens = _TOKEN.findall(text or "")
    words = [(i, word_lang(t)) for i, t in enumerate(tokens) if not t.isspace()]
    labels: dict[int, str] = {}
    for n, (i, lang) in enumerate(words):
        if lang:
            labels[i] = lang
            continue
        prev = next((lg for _, lg in reversed(words[:n]) if lg), None)
        nxt = next((lg for _, lg in words[n + 1:] if lg), None)
        labels[i] = prev if prev and prev == nxt else (prev if nxt is None and prev else base)
    out: list[list] = []
    current = base
    for i, tok in enumerate(tokens):
        lang = labels.get(i, current)
        if out and out[-1][0] == lang or (tok.isspace() and out):
            out[-1][1] += tok
        else:
            out.append([lang, tok])
        current = lang
    return [(lang, t.strip()) for lang, t in out if t.strip()]


# --- voices and personas

# Which voice reads each language for a persona. "jarvis" is an original calm, deep AI-assistant style (not a
# copy of any actor's voice): a British male voice for English, the Indonesian voice re-voiced deeper.
PERSONAS = {"jarvis": {"id": "id", "en": "engb"}, "natural": {"id": "id", "en": "en"}}
# JARVIS re-voicing per voice: (F0 ratio, formant ratio). The Indonesian (≈250 Hz) and lessac (≈200 Hz) voices
# are female and need a large shift; VCTK p226 (≈110 Hz) is already a deep male voice.
JARVIS_REVOICE = {"id": (0.47, 0.85), "en": (0.58, 0.86), "engb": (0.93, 0.97)}


def _voice_key(lang: str, persona: str) -> str | None:
    wanted = PERSONAS.get(persona, PERSONAS["jarvis"]).get(lang, "id")
    for key in (wanted, "engb" if lang == "en" else "id", "en" if lang == "en" else "id", "id", "en", "engb"):
        if key in VOICES and _voice_ready(key):
            return key
    return None


def _load_voice(key: str):
    if key not in _voices:
        try:
            from piper import PiperVoice
        except Exception as exc:
            raise VoiceUnavailable("Paket piper-tts belum terpasang (jalankan setup.bat).") from exc
        _voices[key] = PiperVoice.load(str(_voice_dir() / (VOICES[key]["stem"] + ".onnx")))
    return _voices[key]


def _synth(text: str, key: str, rate: float) -> tuple[np.ndarray, int]:
    from piper import SynthesisConfig

    voice = _load_voice(key)
    speaker = VOICES[key].get("speaker")
    config = SynthesisConfig(length_scale=1.0 / rate,
                             speaker_id=voice.config.speaker_id_map.get(speaker) if speaker else None)
    buf = io.BytesIO()
    with _voice_locks[key], wave.open(buf, "wb") as w:
        voice.synthesize_wav(text, w, syn_config=config)
    buf.seek(0)
    with wave.open(buf) as r:
        sr = r.getframerate()
        x = np.frombuffer(r.readframes(r.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0
    return _trim(x, sr), sr


def _trim(x: np.ndarray, sr: int) -> np.ndarray:
    if not len(x):
        return x
    loud = np.flatnonzero(np.abs(x) > max(0.01, 0.02 * float(np.max(np.abs(x)))))
    if not len(loud):
        return x[:0]
    pad = int(0.02 * sr)
    return x[max(0, loud[0] - pad): loud[-1] + pad]


def _world(x: np.ndarray, sr: int, f0_ratio: float, formant: float) -> np.ndarray:
    """Re-voice with the WORLD vocoder: pitch × f0_ratio, formants × formant (a longer, deeper vocal tract)."""
    if (abs(f0_ratio - 1) < 0.01 and abs(formant - 1) < 0.01) or len(x) < sr // 10:
        return x
    try:
        import pyworld
    except ImportError:
        return x  # without pyworld the voice stays as synthesized
    f0, t = pyworld.dio(x, sr, frame_period=5.0)
    f0 = pyworld.stonemask(x, f0, t, sr)
    sp = pyworld.cheaptrick(x, f0, t, sr)
    ap = pyworld.d4c(x, f0, t, sr)
    if abs(formant - 1) >= 0.01:
        bins = np.arange(sp.shape[1], dtype=np.float64)
        src = np.clip(bins / formant, 0, sp.shape[1] - 1)
        sp = np.array([np.interp(src, bins, frame) for frame in sp])
        ap = np.array([np.interp(src, bins, frame) for frame in ap])
    y = pyworld.synthesize(f0 * f0_ratio, sp, ap, sr, frame_period=5.0)
    return y[: len(x)] if len(y) > len(x) else y


def _delay(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    d = int(sr * ms / 1000)
    return np.concatenate([np.zeros(d), x[:-d]]) if 0 < d < len(x) else np.zeros_like(x)


def _ai_colour(x: np.ndarray, sr: int) -> np.ndarray:
    """Subtle 'AI assistant' colour: a light metallic resonance, short room reflections and a crisp top end."""
    y = x + 0.20 * _delay(x, sr, 6) + 0.10 * _delay(x, sr, 11)
    y = y + 0.10 * _delay(y, sr, 23) + 0.07 * _delay(y, sr, 37) + 0.05 * _delay(y, sr, 53)
    low = np.convolve(y, np.ones(9) / 9, mode="same")
    y = y + 0.18 * (y - low)  # presence
    y = y - np.convolve(y, np.ones(int(sr / 90)) / int(sr / 90), mode="same")  # rumble below ~90 Hz
    y = np.tanh(1.3 * y) / np.tanh(1.3)
    peak = float(np.max(np.abs(y))) or 1.0
    return y * (0.89 / peak)


def _resample(x: np.ndarray, sr: int, target: int) -> np.ndarray:
    if sr == target or not len(x):
        return x
    n = int(len(x) * target / sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)


def _load_natural():
    global _natural
    if _natural is None:
        try:
            from supertonic import TTS
        except Exception as exc:
            raise VoiceUnavailable("Paket supertonic belum terpasang (jalankan setup.bat).") from exc
        _natural = TTS(model_dir=_natural_dir(), auto_download=False)
    return _natural


def phrases(parts: list[tuple[str, str]], base: str, min_words: int = 2) -> list[tuple[str, str]]:
    """Language runs for a multilingual voice: a lone foreign word stays inside the surrounding sentence (the model
    reads loanwords such as "rebound" or "outlook" well in context, while one-word utterances lose the sentence's
    intonation); phrases of `min_words` or more keep their own language ("wait and see", "the Fed")."""
    relabelled = [(base if lang != base and len(text.split()) < min_words else lang, text) for lang, text in parts]
    out: list[list[str]] = []
    for lang, text in relabelled:
        if out and out[-1][0] == lang:
            out[-1][1] += " " + text
        else:
            out.append([lang, text])
    return [(lang, text) for lang, text in out]


def _speak_natural(parts: list[tuple[str, str]], rate: float, voice: str) -> tuple[np.ndarray, int]:
    """Supertonic 3: every run of the text in its own language, all with the same voice."""
    with _natural_lock:
        tts = _load_natural()
        name = voice if voice in NATURAL_VOICES else NATURAL_VOICES[0]
        if name not in _natural_styles:
            _natural_styles[name] = tts.get_voice_style(voice_name=name)
        style, sr = _natural_styles[name], int(tts.sample_rate)
        speed = max(0.7, min(2.0, 1.05 * rate))
        pieces: list[np.ndarray] = []
        previous = ""
        for seg_lang, seg_text in parts:
            clean = normalize(seg_text, seg_lang)
            if not clean:
                continue
            wav, _ = tts.synthesize(clean, voice_style=style, lang=seg_lang, speed=speed)
            x = _trim(np.asarray(wav, dtype=np.float64).reshape(-1), sr)
            if pieces:  # a sentence end gets a breath, a language switch inside a sentence only a short gap
                pieces.append(np.zeros(int(sr * (0.28 if re.search(r"[.!?]$", previous) else 0.05))))
            pieces.append(x)
            previous = seg_text
    if not pieces:
        raise VoiceUnavailable("Teks kosong.")
    return np.concatenate(pieces), sr


def speak(text: str, lang: str = "id", rate: float = 1.0, persona: str = "jarvis", mixed: bool = True,
          engine: str = "piper", voice: str = "M1") -> bytes:
    """WAV of `text`. `lang` is the text's main language; with `mixed`, English words / phrases inside it are
    read in English. `engine`: "supertonic" (natural, same voice for both languages; Piper when it is not
    installed yet) or "piper". `persona`: "jarvis" (calm AI-assistant colour) or "natural" (no effect)."""
    rate = max(0.6, min(1.8, float(rate or 1.0)))
    persona = persona if persona in PERSONAS else "jarvis"
    base = "en" if lang == "en" else "id"
    parts = segments(text, base) if mixed else [(base, text)]
    natural = engine == "supertonic" and _natural_ready() and _has("supertonic")
    used = f"st3:{voice}" if natural else "piper"
    cache_key = hashlib.sha1(f"{used}|{persona}|{rate:.2f}|{mixed}|{base}|{text}".encode()).hexdigest()
    with _lock:
        hit = _tts_cache.get(cache_key)
        if hit:
            _tts_cache.move_to_end(cache_key)
            return hit
    if natural:
        audio, out_sr = _speak_natural(phrases(parts, base) if mixed else parts, rate, voice)
        return _finish(audio, out_sr, persona, cache_key)
    out_sr = 22050
    pieces: list[np.ndarray] = []
    previous = ""
    for seg_lang, seg_text in parts:
        key = _voice_key(seg_lang, persona)
        if key is None:
            raise VoiceUnavailable("Suara belum diunduh: buka pengaturan suara dan tekan UNDUH SEMUA.")
        clean = normalize(seg_text, "en" if key in ("en", "engb") else "id")
        if not clean:
            continue
        x, sr = _synth(clean, key, rate)
        f0_ratio, formant = JARVIS_REVOICE.get(key, (1.0, 1.0)) if persona == "jarvis" else (1.0, 1.0)
        x = _resample(_world(x, sr, f0_ratio, formant), sr, out_sr)
        if pieces:  # a sentence end gets a breath, a language switch inside a sentence only a short gap
            pieces.append(np.zeros(int(out_sr * (0.28 if re.search(r"[.!?]$", previous) else 0.06))))
        pieces.append(x)
        previous = seg_text
    if not pieces:
        raise VoiceUnavailable("Teks kosong.")
    return _finish(np.concatenate(pieces), out_sr, persona, cache_key)


def _finish(audio: np.ndarray, out_sr: int, persona: str, cache_key: str) -> bytes:
    if persona == "jarvis":
        audio = _ai_colour(audio, out_sr)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(out_sr)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
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
