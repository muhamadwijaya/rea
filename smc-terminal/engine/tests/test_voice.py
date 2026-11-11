"""Voice assistant helpers that need no models or network: answer intent, read-aloud text, audio parsing, fallbacks."""

import io
import wave

import numpy as np
import pytest

from app import voice


@pytest.mark.parametrize("heard,expected", [
    ("Ya, baca kan.", "yes"), ("Ia boleh", "yes"), ("iya", "yes"), ("Oke lanjut", "yes"), ("Yes please", "yes"),
    ("Fidak nanti saja", "no"), ("tidak usah", "no"), ("nggak", "no"), ("No thanks", "no"), ("nanti aja", "no"),
    ("hmm", None), ("", None),
])
def test_spoken_answer_intent(heard, expected):
    assert voice.intent(heard) == expected


def test_text_is_made_readable_for_indonesian_voice():
    t = voice.normalize("Rupiah ke Rp 18.150, IHSG naik 1,02% & BBRI US$5", "id")
    assert "18150" in t and "1 koma 02 persen" in t and "rupiah 18150" in t and " dan " in t and "dolar AS 5" in t


def test_browser_audio_is_resampled_to_16k_mono():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes((np.sin(np.linspace(0, 100, 48000 * 2)) * 8000).astype(np.int16).tobytes())
    x = voice._samples_16k(buf.getvalue())
    assert x.dtype == np.float32 and abs(len(x) - 16000) < 5


def test_article_falls_back_to_summary_with_a_reason():
    r = voice.article("https://news.google.com/rss/articles/abc", "Judul", "Ringkasan")
    assert r["source"] == "summary" and r["text"] == "Judul Ringkasan" and "Google News" in r["reason"]
    assert voice.article("javascript:alert(1)", "J")["source"] == "summary"


def test_english_inside_indonesian_text_gets_the_english_voice():
    pytest.importorskip("wordfreq")
    segs = voice.segments("Saham Nvidia rebound di premarket setelah CoreWeave menaikkan outlook pendapatan.", "id")
    english = [t for lang, t in segs if lang == "en"]
    assert english == ["rebound", "premarket", "CoreWeave", "outlook"]
    assert segs[0] == ("id", "Saham Nvidia")  # words common to both languages follow the sentence
    assert voice.segments("Nvidia shares rebound as CoreWeave raises its outlook.", "en") == [
        ("en", "Nvidia shares rebound as CoreWeave raises its outlook.")]
    assert all(lang == "id" for lang, _ in voice.segments("IHSG ditutup naik didorong saham bank dan investor asing.", "id"))


def test_jarvis_revoicing_lowers_pitch_into_the_male_range():
    pyworld = pytest.importorskip("pyworld")
    sr = 22050
    t = np.arange(int(sr * 1.0)) / sr
    x = 0.4 * np.sign(np.sin(2 * np.pi * 250 * t)) * np.hanning(len(t))  # buzzy 250 Hz "voice"
    y = voice._world(x, sr, *voice.JARVIS_REVOICE["id"])

    def f0(sig):
        f, tt = pyworld.dio(sig, sr, frame_period=5.0)
        f = pyworld.stonemask(sig, f, tt, sr)
        return float(np.median(f[f > 0]))

    assert 100 <= f0(y) <= 140 and f0(x) > 200
