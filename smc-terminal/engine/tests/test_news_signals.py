"""News read aloud only after the AI analysed it: the article as the AI's main fact, the sentiment fallback, the
cache, and a signal per timeframe only where that timeframe has momentum (no network)."""

from types import SimpleNamespace

import numpy as np
import pytest

from app import ai, signals, training
from app.ai import common


def analysed(score, last_break_dir, bars_ago, n=100, tf="15m"):
    brk = SimpleNamespace(index=n - 1 - bars_ago, direction=last_break_dir, kind="CHoCH", scope="internal")
    structure = SimpleNamespace(breaks=[brk])
    return SimpleNamespace(bars=np.zeros(n), score=score, tf=SimpleNamespace(key=tf),
                           result=SimpleNamespace(internal=structure, swing=SimpleNamespace(breaks=[])))


SETUP = {"entry": 1.1, "stop": 1.09, "target": 1.12, "rr": 2.0, "level": "KETAT"}


@pytest.mark.parametrize("score,brk,ago,want,status", [
    (40, 1, 3, 1, "signal"),         # bias and a fresh break in the news direction
    (40, 1, 60, 1, "no_momentum"),   # bias ok, but the last break is old
    (5, 1, 3, 1, "no_momentum"),     # fresh break, but no clear bias
    (-40, -1, 3, 1, "against"),      # the timeframe moves the other way
    (40, -1, 2, 1, "against"),       # a fresh opposite break counts against
    (40, 1, 3, 0, "neutral"),        # a neutral news reading never makes a signal
])
def test_a_timeframe_signals_only_with_momentum_in_the_news_direction(score, brk, ago, want, status):
    row = signals.timeframe_signal(analysed(score, brk, ago), want, lambda: SETUP)
    assert row["status"] == status
    assert ("setup" in row) == (status == "signal")
    if status == "signal":
        assert row["setup"] is SETUP and row["direction"] == "bullish"


def test_without_a_virtual_trading_setup_the_smc_plan_is_used(monkeypatch):
    plan = {"direction": "bullish", "status": "pending", "entry": 10.0, "stop": 9.5, "targets": [11.0], "rr": [2.0], "source": "Order block internal"}
    monkeypatch.setattr(signals.analysis, "trade_plan", lambda a: plan)
    row = signals.timeframe_signal(analysed(40, 1, 3), 1, lambda: None)
    assert row["setup"]["entry"] == 10.0 and row["setup"]["target"] == 11.0 and row["setup"]["source"].startswith("plan SMC")
    plan["direction"] = "bearish"  # a plan the other way is not offered as this signal
    assert signals.timeframe_signal(analysed(40, 1, 3), 1, lambda: None)["setup"] is None


@pytest.fixture
def news_env(monkeypatch):
    inst = SimpleNamespace(symbol="EURUSD", market="forex", name="Euro / US Dollar", asset_class="forex")
    monkeypatch.setattr(ai.instruments, "resolve", lambda s: inst)
    monkeypatch.setattr(ai.voice, "article", lambda url, title, summary: {"text": f"{title}. Isi lengkap berita.", "source": "article"})
    seen = []
    monkeypatch.setattr(ai.signals, "for_direction", lambda i, d: seen.append(d) or [{"tf": "1h", "status": "no_momentum"}])
    monkeypatch.setattr(ai, "_news_cache", {})
    monkeypatch.setattr(ai, "_trigger", lambda inst: None)  # no network in tests
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return seen


ITEM = {"title": "ECB signals cuts", "link": "https://example.com/a", "source": "Example", "sentiment": -0.6,
        "symbols": [{"symbol": "EURUSD", "sign": 1}]}


def test_without_ai_the_direction_comes_from_the_tagged_sentiment(news_env):
    r = ai.analyze_news("EURUSD", ITEM, [], "openrouter", None)
    assert r["directionBasis"] == "sentiment" and r["direction"] == "turun" and r["aiError"]["code"] == "ai_not_configured"
    assert r["article"]["link"] == ITEM["link"] and r["article"]["fullText"] is True
    assert news_env == ["turun"]
    flipped = {**ITEM, "symbols": [{"symbol": "EURUSD", "sign": -1}]}  # good for the subject = bad for this pair
    assert ai.analyze_news("EURUSD", flipped, [], "openrouter", None)["direction"] == "naik"


def test_the_article_is_the_ai_main_fact_and_the_result_is_cached(news_env, monkeypatch):
    sent = {}

    def fake_run(system, user, key, model):
        sent["user"] = user
        return common.Answer(common.normalize({"direction": "naik", "confidence": 70, "horizon": "1-3 hari", "summary": "s"}), "m")

    base = {"economicReleases": [], "upcomingEconomicEvents": [], "headlines": [], "companyReports": None, "performance": None,
            "ruleModelMeasuredAccuracy": None, "dataNotes": []}
    monkeypatch.setattr(ai, "facts", lambda inst, news, focus=None: ({**base, "focusArticle": focus}, {"horizons": {}}))
    monkeypatch.setattr(ai.openrouter, "run", fake_run)
    r = ai.analyze_news("EURUSD", ITEM, [], "openrouter", "key")
    assert r["directionBasis"] == "ai" and r["direction"] == "naik"
    assert '"link": "https://example.com/a"' in sent["user"] and "Isi lengkap berita" in sent["user"]
    sent.clear()
    assert ai.analyze_news("EURUSD", ITEM, [], "openrouter", "key")["direction"] == "naik"
    assert not sent  # served from the cache: no second model call
    assert "focusArticle" in common.SYSTEM


def test_current_setup_reports_the_live_journal_setup_and_its_precision(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(training, "_market_of", lambda s: "forex")
    for state in (training._params, training._groups, training._evals):
        state.clear()
    training._conn = None
    try:
        db = training._db()
        db.execute("INSERT INTO signals(id, symbol, market, tf, direction, created_t, entry, stop, target, status, manage) "
                   "VALUES ('a', 'EURUSD', 'forex', '1h', 1, 100, 1.10, 1.09, 1.12, 'pending', 'partial')")
        db.commit()
        p = training.default_params("1h")
        training._params[("EURUSD", "1h")] = (3, p, {"outOfSample": {"trades": 40, "wins": 38, "expectancyR": 0.6}})
        inst = SimpleNamespace(symbol="EURUSD", market="forex")
        s = training.current_setup(inst, "1h", 1)
        assert (s["entry"], s["status"], s["live"], s["rr"], s["manage"]) == (1.10, "pending", True, 2.0, "partial")
        assert s["level"] == "SANGAT KETAT" and s["source"] == "virtual trading v3"  # 38/40: lower bound 0.90
        training._params[("EURUSD", "1h")] = (3, p, {"outOfSample": {"trades": 40, "wins": 34, "expectancyR": 0.6}})
        assert training.current_setup(inst, "1h", 1)["level"] == "KETAT"  # 34/40: lower bound 0.785
        assert training.current_setup(inst, "1h", 0) is None
    finally:
        training._conn.close()
        training._conn = None
        for state in (training._params, training._groups, training._evals):
            state.clear()
