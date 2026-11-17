"""Velas por hora e estado do mercado, derivados das velas de 1 minuto.

O estado descreve o momento, não prevê nada. Cada medida usa só o passado:

- **Volatilidade de 24 h:** desvio-padrão dos últimos 24 retornos horários (em log), vezes √24,
  em % (a variação típica de um dia nesse ritmo).
- **Percentil da volatilidade:** posição desse valor entre os valores dos 365 dias anteriores
  (0 = o mais calmo do ano, 100 = o mais agitado). Dele sai o rótulo: `calm` (abaixo de 25),
  `normal`, `volatile` (acima de 75) e `extreme` (acima de 95).
- **Médias de 50 e de 200 dias** do fechamento horário.
- **Regime:** `bull` quando a média de 50 dias está acima da de 200 dias, `bear` quando está
  abaixo. É uma convenção comum e simples, que só confirma uma virada semanas depois de ela
  acontecer; não é previsão.
- **Queda desde o pico:** distância do fechamento até a máxima dos 365 dias anteriores, em %.

Todas as janelas têm tamanho fixo, então recalcular só o período recente dá o mesmo resultado
que recalcular o histórico inteiro, desde que se leiam `HISTORY_DAYS` de velas por hora.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HOUR = pd.Timedelta(hours=1)
VOL_HOURS = 24
YEAR_HOURS = 365 * 24
MA_SHORT_HOURS = 50 * 24
MA_LONG_HOURS = 200 * 24
# Uma janela vale quando tem pelo menos esta fração das horas (a Coinbase já ficou fora do ar).
MIN_FRACTION = 0.9
# O percentil já é publicado com 90 dias de histórico.
MIN_PERCENTILE_HOURS = 90 * 24
# O pico já é publicado com 30 dias de histórico (no começo dos dados, é o pico do que existe).
MIN_PEAK_HOURS = 30 * 24
CALM, VOLATILE, EXTREME = 25.0, 75.0, 95.0
# Horas sem negociação ficam com o último preço conhecido (como na detecção dos movimentos).
MAX_FILL_HOURS = 6
# No dia a dia, as últimas RECENT_DAYS são recalculadas (a corretora pode revisar velas recentes).
RECENT_DAYS = 2
# Velas por hora necessárias para recalcular o estado desse período: o ano do percentil, o dia
# da volatilidade e uma folga.
HISTORY_DAYS = 365 + RECENT_DAYS + 3

HOURLY_COLUMNS = ["time", "open", "high", "low", "close", "volume"]
STATE_COLUMNS = [
    "time",
    "close",
    "volatility_24h",
    "volatility_percentile",
    "volatility_state",
    "ma_50d",
    "ma_200d",
    "regime",
    "high_365d",
    "drawdown_pct",
]


def hourly_candles(candles: pd.DataFrame) -> pd.DataFrame:
    """Velas de 1 minuto agrupadas por hora (`time` = início da hora). Só horas fechadas."""
    if candles.empty:
        return pd.DataFrame(columns=HOURLY_COLUMNS)
    c = candles.sort_values("time").set_index("time")
    out = c.resample("h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    out = out.dropna(subset=["close"])  # horas sem nenhuma negociação não têm vela
    # A hora em andamento fica de fora: só entra depois da vela do minuto 59.
    out = out[out.index + HOUR <= c.index[-1] + pd.Timedelta(minutes=1)]
    return out.reset_index()[HOURLY_COLUMNS]


def state(hourly: pd.DataFrame) -> pd.DataFrame:
    """Estado do mercado ao fim de cada hora (`time` = início da hora, como nas velas)."""
    if hourly.empty:
        return pd.DataFrame(columns=STATE_COLUMNS)
    h = hourly.sort_values("time").set_index("time")
    index = pd.date_range(h.index[0], h.index[-1], freq="h")
    close = h["close"].reindex(index).ffill(limit=MAX_FILL_HOURS)
    high = h["high"].reindex(index).fillna(close)

    def window(hours: int) -> dict:
        return {"window": hours, "min_periods": int(hours * MIN_FRACTION)}

    returns = np.log(close).diff()
    vol = returns.rolling(**window(VOL_HOURS)).std() * np.sqrt(VOL_HOURS) * 100
    percentile = vol.rolling(YEAR_HOURS, min_periods=MIN_PERCENTILE_HOURS).rank(pct=True) * 100
    ma_short = close.rolling(**window(MA_SHORT_HOURS)).mean()
    ma_long = close.rolling(**window(MA_LONG_HOURS)).mean()
    peak = high.rolling(YEAR_HOURS, min_periods=MIN_PEAK_HOURS).max()

    vol_state = pd.Series(pd.NA, index=index, dtype="string")
    vol_state[percentile.notna()] = "normal"
    vol_state[percentile < CALM] = "calm"
    vol_state[percentile > VOLATILE] = "volatile"
    vol_state[percentile > EXTREME] = "extreme"
    regime = pd.Series(pd.NA, index=index, dtype="string")
    known = ma_short.notna() & ma_long.notna()
    regime[known & (ma_short > ma_long)] = "bull"
    regime[known & (ma_short <= ma_long)] = "bear"

    out = pd.DataFrame(
        {
            "close": close.round(2),
            "volatility_24h": vol.round(3),
            "volatility_percentile": percentile.round(1),
            "volatility_state": vol_state,
            "ma_50d": ma_short.round(2),
            "ma_200d": ma_long.round(2),
            "regime": regime,
            "high_365d": peak.round(2),
            "drawdown_pct": ((close / peak - 1) * 100).round(2),
        }
    )
    out = out[out["close"].notna()]
    return out.rename_axis("time").reset_index()[STATE_COLUMNS]
