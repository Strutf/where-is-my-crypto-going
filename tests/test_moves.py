import numpy as np
import pandas as pd

from wimcg.moves import detect, hourly_close


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
