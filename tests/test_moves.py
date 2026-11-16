import json

import numpy as np
import pandas as pd

from wimcg import catalog
from wimcg.cli import load_dataset, main
from wimcg.moves import HISTORY_DAYS, detect, hourly_close
from wimcg.partitions import manifest_key
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
