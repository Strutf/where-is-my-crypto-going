"""Testes do treino: nenhuma variável pode depender do que acontece depois da decisão."""

import numpy as np
import pandas as pd
import pytest

from wimcg import gkg, market
from wimcg.ml import evaluate, features

HOURS = 24 * 400


def _inputs(seed: int = 0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=HOURS, freq="h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, HOURS)))
    hourly = pd.DataFrame({"time": idx, "open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": rng.uniform(1, 2, HOURS)})
    days = pd.date_range("2024-01-01", periods=HOURS // 24, freq="D", tz="UTC")
    fear_greed = pd.DataFrame({"time": days, "value": rng.integers(0, 100, len(days)), "classification": "x"})
    news = pd.DataFrame({"time": idx})
    for col in gkg.columns()[1:]:
        news[col] = rng.integers(1, 1000, HOURS).astype(float)
    news["tone"] = rng.normal(-1, 1, HOURS)
    news["btc_tone"] = rng.normal(-1, 1, HOURS)
    return hourly, fear_greed, news


def _table(hourly, fear_greed, news):
    return features.build(hourly, market.state(hourly), fear_greed, news)


def _feature_columns(groups):
    return [c for g in groups.values() for c in g]


def test_changing_the_future_does_not_change_past_features():
    hourly, fear_greed, news = _inputs()
    before, groups = _table(hourly, fear_greed, news)
    cut = hourly["time"].iloc[HOURS // 2]  # tudo a partir desta hora muda

    h2, fg2, n2 = hourly.copy(), fear_greed.copy(), news.copy()
    later = h2["time"] >= cut
    h2.loc[later, ["open", "high", "low", "close"]] *= 3.0
    h2.loc[later, "volume"] *= 10
    n2.loc[n2["time"] >= cut, gkg.columns()[1:]] = 5.0
    # O Fear & Greed do dia da hora de corte em diante.
    fg2.loc[fg2["time"] >= cut.floor("D"), "value"] = 99
    after, _ = _table(h2, fg2, n2)

    cols = _feature_columns(groups)
    known = before.index < cut  # decisões tomadas antes de qualquer mudança
    pd.testing.assert_frame_equal(before.loc[known, cols], after.loc[known, cols])
    # Os alvos, ao contrário, olham para a frente e precisam mudar perto do corte.
    last = before.index[known][-1]
    assert before.loc[last, "ret_next_24h"] != after.loc[last, "ret_next_24h"]


def test_news_of_the_hour_itself_is_not_used():
    hourly, fear_greed, news = _inputs(1)
    before, groups = _table(hourly, fear_greed, news)
    hour = hourly["time"].iloc[HOURS // 2]
    n2 = news.copy()
    n2.loc[n2["time"] == hour, "btc_articles"] = 999_999  # notícia da própria hora
    after, _ = _table(hourly, fear_greed, n2)
    cols = groups["news"]
    pd.testing.assert_frame_equal(before.loc[[hour], cols], after.loc[[hour], cols])
    assert not before.loc[[hour + pd.Timedelta(hours=1)], cols].equals(after.loc[[hour + pd.Timedelta(hours=1)], cols])


def test_fear_greed_of_the_same_day_is_not_used():
    hourly, fear_greed, news = _inputs(2)
    before, _ = _table(hourly, fear_greed, news)
    day = fear_greed["time"].iloc[200]
    fg2 = fear_greed.copy()
    fg2.loc[fg2["time"] == day, "value"] = -1
    after, _ = _table(hourly, fg2, news)
    same_day = (before.index >= day) & (before.index + pd.Timedelta(hours=1) < day + pd.Timedelta(days=1))
    assert (before.loc[same_day, "fear_greed"] == after.loc[same_day, "fear_greed"]).all()
    next_day = (before.index + pd.Timedelta(hours=1) >= day + pd.Timedelta(days=1)) & (before.index < day + pd.Timedelta(days=2) - pd.Timedelta(hours=1))
    assert (after.loc[next_day, "fear_greed"] == -1).all()


def test_targets_are_future_log_returns():
    hourly, fear_greed, news = _inputs(3)
    table, _ = _table(hourly, fear_greed, news)
    close = hourly.set_index("time")["close"]
    t = table.index[1000]
    for h in features.HORIZONS:
        expected = np.log(close[t + pd.Timedelta(hours=h)] / close[t])
        assert table.loc[t, f"ret_next_{h}h"] == pytest.approx(expected)
        assert table.loc[t, f"up_next_{h}h"] == float(expected > 0)


def test_training_rows_never_overlap_the_test_month():
    """Toda linha de treino tem o alvo conhecido antes do início do mês de teste."""
    hourly, fear_greed, news = _inputs(4)
    table, groups = _table(hourly, fear_greed, news)
    seen = []

    class Spy:
        def fit(self, train, cols, history):
            seen.append(train.index.max())

        def predict(self, test):
            n = len(test)
            return {h: (np.full(n, 0.5), np.zeros(n)) for h in features.HORIZONS}

    evaluate.MODELS["spy"] = lambda **_: Spy()
    try:
        preds = evaluate.walk_forward(table, groups["price"], "spy", "2024-11-01", "2024-12-01")
    finally:
        del evaluate.MODELS["spy"]
    starts = evaluate.months("2024-11-01", "2024-12-01")
    for last_train, start in zip(seen, starts, strict=True):
        # O alvo mais longo (24 h) da última linha de treino termina antes do mês de teste.
        assert last_train + pd.Timedelta(hours=max(features.HORIZONS)) < start
    assert set(preds["horizon"]) == set(features.HORIZONS)


def test_summary_of_a_coin_flip_is_not_significant():
    rng = np.random.default_rng(5)
    n = 24 * 200
    preds = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
            "prob_up": rng.uniform(0, 1, n),
            "pred_ret": np.zeros(n),
            "climatology": 0.5,
            "ret": rng.normal(0, 0.01, n),
        }
    )
    s = evaluate.summarize(preds)
    lo, hi = s["accuracy"]["ci95"]
    assert lo < 0.5 < hi
    assert s["mae_skill"]["value"] == pytest.approx(0.0)


def test_calibration_shrinks_toward_climatology():
    from wimcg.ml import intervals

    assert intervals.calibrate(0.75, 0.52, 0.3) == pytest.approx(0.589)
    # Modelo confiante demais: diz 30% ou 70%, mas a frequência real é 45% e 55%.
    rng = np.random.default_rng(6)
    p = rng.choice([0.3, 0.7], 20000)
    y = (rng.uniform(size=p.size) < np.where(p > 0.5, 0.55, 0.45)).astype(float)
    lam = intervals.shrink_lambda(p, np.full(p.size, 0.5), y)
    assert 0.15 < lam < 0.35  # o certo é 0,25: 0,5 + 0,25 × 0,2 = 0,55
    assert intervals.shrink_lambda(p, np.full(p.size, 0.5), 1 - y) == 0.0  # sinal invertido: não usa o modelo


def test_band_covers_what_it_promises():
    from wimcg.ml import intervals

    rng = np.random.default_rng(7)
    vol = rng.uniform(0.5, 3, 40000)
    ret = vol * rng.standard_t(4, vol.size) / np.sqrt(2)  # caudas mais grossas que a normal
    q = intervals.z_quantiles(ret[:20000], vol[:20000])
    b = intervals.band(vol[20000:], q)
    inside90 = np.mean((ret[20000:] >= b[0.05]) & (ret[20000:] <= b[0.95]))
    inside50 = np.mean((ret[20000:] >= b[0.25]) & (ret[20000:] <= b[0.75]))
    assert abs(inside90 - 0.90) < 0.01 and abs(inside50 - 0.50) < 0.015
    assert intervals.to_price(100.0, 10.0) == pytest.approx(100 * np.exp(0.1))


def test_recent_rows_end_before_the_month_with_purge():
    from wimcg.ml import intervals

    times = pd.date_range("2024-01-01", "2025-03-01", freq="h", tz="UTC")
    df = pd.DataFrame({"time": times})
    month = pd.Timestamp("2025-02-01", tz="UTC")
    past = intervals.recent(df, month)
    assert past["time"].max() < month - pd.Timedelta(hours=24)
    assert past["time"].min() >= month - pd.DateOffset(months=12)
