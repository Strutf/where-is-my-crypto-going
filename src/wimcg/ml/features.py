"""Tabela de treino por hora: variáveis conhecidas no fim de cada hora e o que aconteceu depois.

Convenção de tempo: cada linha é a hora com início em `time`. A decisão é tomada no fim dela
(`time + 1 h`), quando a vela fecha. Só entra o que já se sabia nesse instante:

- **preço e estado do mercado:** velas até a hora `time`, inclusive (já fechada);
- **Fear & Greed:** o valor do dia anterior. O índice do dia *d* é publicado perto da meia-noite
  UTC, e usar o do próprio dia arriscaria usar um valor que ainda não existia;
- **notícias:** a hora anterior (`time − 1 h`). Uma hora do GDELT só fica completa cerca de 20 min
  depois de terminar, então a própria hora `time` ainda não está pronta no fim dela.

Alvos, para cada horizonte *h* (1, 4 e 24 h): o retorno em log do fechamento da hora `time` até o
fechamento da hora `time + h`, e a direção (1 se subiu, 0 se não subiu).

As variáveis ficam em grupos (`GROUPS`) para medir o ganho de cada um, com e sem.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from wimcg import gkg

HORIZONS = (1, 4, 24)
# Muda quando as variáveis mudam: entra no nome da versão dos modelos publicados.
FEATURE_VERSION = 1
HOUR = pd.Timedelta(hours=1)
# Horas sem negociação ficam com o último preço conhecido (como no estado do mercado).
MAX_FILL_HOURS = 6
MONTH_HOURS = 30 * 24


def _log_return(close: pd.Series, hours: int) -> pd.Series:
    return np.log(close) - np.log(close.shift(hours))


def _zscore(x: pd.Series, window: int = MONTH_HOURS) -> pd.Series:
    """Desvio do valor em relação aos `window` valores anteriores (sem incluir o próprio)."""
    past = x.shift(1).rolling(window, min_periods=window // 2)
    return (x - past.mean()) / past.std()


def price_features(hourly: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Variáveis de preço numa grade horária completa. Devolve (variáveis, fechamento usado)."""
    h = hourly.set_index("time").sort_index()
    grid = pd.date_range(h.index[0], h.index[-1], freq="h")
    h = h.reindex(grid)
    close = h["close"].ffill(limit=MAX_FILL_HOURS)
    high = h["high"].fillna(close)
    low = h["low"].fillna(close)
    ret = _log_return(close, 1)

    f = pd.DataFrame(index=grid)
    for hours in (1, 2, 4, 8, 24, 72, 168, 720):
        f[f"ret_{hours}h"] = _log_return(close, hours)
    f["vol_24h"] = ret.rolling(24, min_periods=20).std()
    f["vol_168h"] = ret.rolling(168, min_periods=140).std()
    f["vol_ratio"] = f["vol_24h"] / f["vol_168h"]
    f["range_1h"] = np.log(high / low)
    f["range_24h"] = f["range_1h"].rolling(24, min_periods=20).mean()
    f["from_high_24h"] = np.log(close / high.rolling(24, min_periods=20).max())
    f["from_low_24h"] = np.log(close / low.rolling(24, min_periods=20).min())
    f["volume_z"] = _zscore(np.log1p(h["volume"].fillna(0)))
    decision = grid + HOUR
    f["hour_of_day"] = decision.hour
    f["day_of_week"] = decision.day_of_week
    # A hora em si não tem vela (só o preço repetido): a linha não serve nem para treino.
    f["has_candle"] = h["close"].notna()
    return f, close


def market_features(state: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    s = state.set_index("time").sort_index().reindex(index)
    f = pd.DataFrame(index=index)
    f["vol_percentile"] = s["volatility_percentile"]
    f["drawdown"] = s["drawdown_pct"] / 100
    f["vs_ma_50d"] = np.log(s["close"] / s["ma_50d"])
    f["vs_ma_200d"] = np.log(s["close"] / s["ma_200d"])
    f["regime_bull"] = s["regime"].map({"bull": 1.0, "bear": 0.0}).astype(float)
    return f


def sentiment_features(fear_greed: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    fg = fear_greed[["time", "value"]].copy()
    fg["value"] = pd.to_numeric(fg["value"], errors="coerce")
    # Disponível a partir do dia seguinte ao do valor (ver o comentário do módulo).
    fg["available"] = fg["time"] + pd.Timedelta(days=1)
    fg = fg.sort_values("available")
    fg["change_7d"] = fg["value"] - fg["value"].shift(7)
    decision = pd.DataFrame({"decision": index + HOUR})
    merged = pd.merge_asof(decision, fg[["available", "value", "change_7d"]], left_on="decision", right_on="available")
    f = pd.DataFrame(index=index)
    f["fear_greed"] = merged["value"].to_numpy()
    f["fear_greed_change_7d"] = merged["change_7d"].to_numpy()
    return f


def news_features(news: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Notícias da hora anterior e das 24 h anteriores, em proporções (ver a lacuna do histórico)."""
    n = news.set_index("time").sort_index()
    grid = pd.date_range(min(n.index[0], index[0] - HOUR), index[-1], freq="h")
    n = n.reindex(grid)
    articles = n["articles"].astype(float)
    btc = n["btc_articles"].astype(float)

    def day(x: pd.Series) -> pd.Series:
        return x.rolling(24, min_periods=12).sum()


    f = pd.DataFrame(index=grid)
    f["btc_share"] = btc / articles
    f["btc_share_z"] = _zscore(f["btc_share"])
    f["btc_share_24h"] = day(btc) / day(articles)
    f["btc_share_24h_z"] = _zscore(f["btc_share_24h"])
    f["tone"] = n["tone"].astype(float)
    f["tone_z"] = _zscore(f["tone"])
    # Tom das notícias de bitcoin nas últimas 24 h, ponderado pelo número de notícias.
    f["btc_tone_24h"] = day(n["btc_tone"].astype(float).fillna(0) * btc) / day(btc)
    for tp in gkg.TOPICS:
        if tp.bitcoin:
            f[f"btc_{tp.name}_24h"] = day(n[f"btc_{tp.name}_articles"].astype(float)) / day(btc)
        if tp.world:
            share = day(n[f"world_{tp.name}_articles"].astype(float)) / day(articles)
            f[f"world_{tp.name}_z"] = _zscore(share)
    # Hora anterior: a própria hora ainda não está completa no fim dela.
    return f.shift(1).reindex(index)


def targets(close: pd.Series) -> pd.DataFrame:
    t = pd.DataFrame(index=close.index)
    for h in HORIZONS:
        t[f"ret_next_{h}h"] = np.log(close.shift(-h)) - np.log(close)
        t[f"up_next_{h}h"] = (t[f"ret_next_{h}h"] > 0).astype(float).where(t[f"ret_next_{h}h"].notna())
    return t


def build(hourly: pd.DataFrame, state: pd.DataFrame, fear_greed: pd.DataFrame, news: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Tabela de treino (uma linha por hora com vela) e os nomes das variáveis de cada grupo."""
    price, close = price_features(hourly)
    groups = {
        "price": [c for c in price.columns if c != "has_candle"],
        "market": [],
        "sentiment": [],
        "news": [],
    }
    parts = [price]
    for name, part in (
        ("market", market_features(state, price.index)),
        ("sentiment", sentiment_features(fear_greed, price.index)),
        ("news", news_features(news, price.index)),
    ):
        groups[name] = list(part.columns)
        parts.append(part)
    # O fechamento não é variável (os modelos usam retornos); fica na tabela como preço de partida.
    table = pd.concat([*parts, targets(close), close.rename("close")], axis=1)
    table = table[table.pop("has_candle")]
    table = table.replace([np.inf, -np.inf], np.nan)
    return table.rename_axis("time"), groups


# Ordem em que os grupos entram nas comparações (cada conjunto inclui os anteriores).
GROUPS = ("price", "market", "sentiment", "news")


def feature_sets(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """Conjuntos cumulativos: só preço; + estado do mercado; + Fear & Greed; + notícias."""
    sets, cols = {}, []
    for name in GROUPS:
        cols = cols + groups[name]
        sets["+".join(GROUPS[: GROUPS.index(name) + 1])] = cols
    return sets
