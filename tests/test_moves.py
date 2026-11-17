import json

import numpy as np
import pandas as pd

from wimcg import catalog, market
from wimcg.cli import load_dataset, main
from wimcg.moves import HISTORY_DAYS, detect, hourly_close
from wimcg.partitions import latest_key, manifest_key
from wimcg.pipeline.writer import write_dataset
from wimcg.storage import LocalStorage


def _prices(seed=0, hours=24 * 60):
    """Passeio aleatório com volatilidade horária de 0,5%."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=hours, freq="h", tz="UTC")
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.005, hours))), index=idx)


def test_hourly_close_uses_last_minute_of_each_hour():
    t = pd.date_range("2025-01-01T00:00Z", periods=120, freq="min")
    candles = pd.DataFrame({"time": t, "close": np.arange(120.0)})
    close = hourly_close(candles)
    assert close[pd.Timestamp("2025-01-01T01:00Z")] == 59  # hora 00:00–00:59 termina em 01:00
    assert close[pd.Timestamp("2025-01-01T02:00Z")] == 119


def test_detects_an_injected_crash_once_with_its_size():
    close = _prices()
    crash_hour = close.index[24 * 40]
    close[close.index >= crash_hour] *= 0.90  # queda de 10% numa hora (~20 sigmas)
    moves = detect(close, scales=(1,)).set_index("time")
    # O passeio aleatório também pode ter algum movimento de 3 sigmas por acaso; a queda tem de estar lá.
    m = moves.loc[crash_hour]
    assert m["start"] == crash_hour - pd.Timedelta(hours=1)
    assert m["direction"] == "down"
    assert m["return_pct"] < -9 and m["z"] < -10


def test_quiet_market_has_almost_no_big_moves():
    close = _prices(seed=1)
    moves = detect(close, scales=(1,))
    # 3 sigmas numa distribuição normal: ~0,3% das horas.
    assert len(moves) / len(close) < 0.01


def test_volatility_ignores_the_move_itself():
    close = _prices()
    crash_hour = close.index[24 * 40]
    close[close.index >= crash_hour] *= 0.90
    m = detect(close, scales=(1,)).set_index("time").loc[crash_hour]
    assert 0.3 < m["sigma_pct"] < 0.7  # a volatilidade típica continua ~0,5%


def test_hourly_close_leaves_out_the_hour_in_progress():
    # Velas até 01:14: a hora 01:00–01:59 ainda não fechou e não pode gerar movimento.
    t = pd.date_range("2025-01-01T00:00Z", periods=75, freq="min")
    close = hourly_close(pd.DataFrame({"time": t, "close": np.arange(75.0)}))
    assert list(close.index) == [pd.Timestamp("2025-01-01T01:00Z")]


def _store_with_candles(tmp_path, monkeypatch, close):
    """Grava uma vela por hora (no minuto 59) e aponta o armazenamento local para tmp_path."""
    monkeypatch.setenv("FS_STORAGE", "local")
    monkeypatch.setenv("FS_DATA_DIR", str(tmp_path))
    store = LocalStorage(tmp_path)
    candles = pd.DataFrame({"time": close.index - pd.Timedelta(minutes=1), "close": close.to_numpy()})
    for col in ("open", "high", "low"):
        candles[col] = candles["close"]
    candles["volume"] = 1.0
    write_dataset(store, catalog.get("btc-usd-1m"), candles)
    return store


def test_recent_run_matches_full_run_and_erases_stale_moves(tmp_path, monkeypatch):
    close = _prices(seed=3, hours=24 * 90)
    close[close.index >= close.index[-24 * 3]] *= 0.90  # queda há 3 dias (dentro do período recente)
    close[close.index >= close.index[24 * 30]] *= 1.10  # alta há 60 dias (fora dele)
    store = _store_with_candles(tmp_path, monkeypatch, close)

    assert main(["moves", "--full"]) == 0
    full = load_dataset(store, "big-moves")
    assert {close.index[-24 * 3], close.index[24 * 30]} <= set(full["time"])

    # Um movimento gravado numa rodada anterior e que não existe mais no recálculo.
    stale = full.tail(1).assign(time=close.index[-5], z=9.99)
    write_dataset(store, catalog.get("big-moves"), stale)
    assert len(load_dataset(store, "big-moves")) == len(full) + 1

    assert main(["moves"]) == 0  # só lê as velas recentes
    recent = load_dataset(store, "big-moves")
    assert list(zip(recent["time"], recent["scale_hours"], strict=True)) == list(zip(full["time"], full["scale_hours"], strict=True))
    assert np.allclose(recent["z"], full["z"], atol=0.011)


def test_recent_run_only_reads_recent_candles(tmp_path, monkeypatch):
    store = _store_with_candles(tmp_path, monkeypatch, _prices(seed=4, hours=24 * 90))
    last = pd.Timestamp(json.loads(store.read(manifest_key("btc-usd-1m")))["end"])
    recent = load_dataset(store, "btc-usd-1m", start=last - pd.Timedelta(days=HISTORY_DAYS), columns=["time", "close"])
    assert list(recent.columns) == ["time", "close"]
    assert recent["time"].min() >= last.floor("D") - pd.Timedelta(days=HISTORY_DAYS)
    assert len(recent) < 24 * (HISTORY_DAYS + 1)


def test_hourly_candles_aggregate_closed_hours_only():
    t = pd.date_range("2025-01-01T00:00Z", periods=135, freq="min")  # até 02:14
    price = np.arange(135.0) + 100
    candles = pd.DataFrame({"time": t, "open": price, "high": price + 1, "low": price - 1, "close": price + 0.5, "volume": 2.0})
    hourly = market.hourly_candles(candles)
    assert hourly["time"].tolist() == [pd.Timestamp("2025-01-01T00:00Z"), pd.Timestamp("2025-01-01T01:00Z")]
    first = hourly.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (100, 160, 99, 159.5, 120)


def _hourly(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"time": close.index, "open": close.to_numpy(), "high": close.to_numpy(), "low": close.to_numpy(), "close": close.to_numpy(), "volume": 1.0})


def test_state_flags_an_agitated_day_and_the_regime():
    rng = np.random.default_rng(7)
    hours = 24 * 400
    idx = pd.date_range("2024-01-01", periods=hours, freq="h", tz="UTC")
    shocks = rng.normal(0.0004, 0.003, hours)  # tendência de alta, mercado calmo
    shocks[-24:] = rng.normal(0, 0.03, 24)  # último dia: dez vezes mais agitado
    st = market.state(_hourly(pd.Series(100 * np.exp(np.cumsum(shocks)), index=idx))).set_index("time")
    last, before = st.iloc[-1], st.iloc[-30]
    assert last["volatility_state"] == "extreme" and last["volatility_percentile"] > 99
    assert before["volatility_state"] in ("calm", "normal", "volatile")
    assert before["regime"] == "bull" and before["ma_50d"] > before["ma_200d"]
    assert before["drawdown_pct"] <= 0 and before["high_365d"] >= before["close"]
    # Sem histórico suficiente, os campos ficam vazios em vez de inventar um valor.
    assert pd.isna(st.iloc[24 * 30]["volatility_percentile"]) and pd.isna(st.iloc[24 * 100]["regime"])


def test_state_calls_a_long_fall_a_bear_market():
    idx = pd.date_range("2024-01-01", periods=24 * 300, freq="h", tz="UTC")
    st = market.state(_hourly(pd.Series(100 * np.exp(np.linspace(0, -1, len(idx))), index=idx)))
    assert st.iloc[-1]["regime"] == "bear" and st.iloc[-1]["drawdown_pct"] < -50


def test_derive_recent_run_matches_full_run(tmp_path, monkeypatch):
    close = _prices(seed=5, hours=24 * 420)
    store = _store_with_candles(tmp_path, monkeypatch, close)
    assert main(["derive", "--full"]) == 0
    hourly_full, state_full = load_dataset(store, "btc-usd-1h"), load_dataset(store, "market-state")
    assert len(hourly_full) == len(close) and state_full["regime"].notna().sum() > 24 * 200

    assert main(["derive"]) == 0  # recalcula só os últimos dias, lendo um ano de velas de 1 hora
    hourly, state = load_dataset(store, "btc-usd-1h"), load_dataset(store, "market-state")
    pd.testing.assert_frame_equal(hourly, hourly_full)
    assert state["time"].equals(state_full["time"])
    numeric = [c for c in market.STATE_COLUMNS if c not in ("time", "volatility_state", "regime")]
    assert np.allclose(state[numeric].astype(float), state_full[numeric].astype(float), atol=0.011, equal_nan=True)
    assert state["regime"].fillna("").equals(state_full["regime"].fillna(""))
    assert json.loads(store.read(latest_key("market-state")))["data"][0]["regime"] in ("bull", "bear")
