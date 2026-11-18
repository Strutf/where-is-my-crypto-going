"""Faixa provável do preço e calibração da probabilidade de alta.

As duas usam as previsões fora da amostra dos 12 meses anteriores (com o resultado já conhecido),
nunca o período em que vão ser aplicadas.

**Faixa provável.** O modelo de volatilidade prevê o tamanho do movimento, sem sinal. A faixa sai
de quanto o retorno real costuma ficar em relação a essa volatilidade prevista: z = retorno (log,
em %) ÷ volatilidade prevista (em %). Os quantis de z no passado recente, vezes a volatilidade
prevista agora, dão os limites. Ex.: quantis de 5% e 95% de z = −1,7 e +1,6 e volatilidade
prevista de 2% dão uma faixa de 90% entre −3,4% e +3,2%.

**Calibração.** O gradient boosting exagera a confiança em 4 h e 24 h. A probabilidade é encolhida
em direção à taxa de alta da janela de treino (c): p' = c + λ·(p − c), com o λ que minimiza o
erro de Brier no passado recente: λ = Σ(p − c)(y − c) / Σ(p − c)², limitado a [0, 1].
"""

from __future__ import annotations

import numpy as np
import pandas as pd

QUANTILES = (0.05, 0.25, 0.75, 0.95)
LOOKBACK_MONTHS = 12
PURGE = pd.Timedelta(hours=24)
# Menos que isso no passado recente e a estimativa não é feita (fica vazia).
MIN_ROWS = 24 * 90


def z_quantiles(ret_pct: np.ndarray, vol_pct: np.ndarray) -> dict[float, float]:
    """Quantis de z = retorno ÷ volatilidade prevista."""
    ok = np.isfinite(ret_pct) & np.isfinite(vol_pct) & (vol_pct > 0)
    z = ret_pct[ok] / vol_pct[ok]
    return {q: float(np.quantile(z, q)) for q in QUANTILES}


def band(vol_pct, qs: dict[float, float]) -> dict[float, np.ndarray]:
    """Limites da faixa, em retorno (log, %), para cada quantil."""
    return {q: np.asarray(vol_pct, dtype=float) * z for q, z in qs.items()}


def to_price(base, ret_log_pct):
    return np.asarray(base, dtype=float) * np.exp(np.asarray(ret_log_pct, dtype=float) / 100)


def shrink_lambda(p, c, y) -> float:
    """λ do encolhimento até a climatologia (mínimo do Brier), limitado a [0, 1]."""
    p, c, y = (np.asarray(a, dtype=float) for a in (p, c, y))
    d = p - c
    den = float(np.sum(d * d))
    if den <= 0:
        return 0.0
    return float(np.clip(np.sum(d * (y - c)) / den, 0.0, 1.0))


def calibrate(p, c, lam: float):
    return np.asarray(c, dtype=float) + lam * (np.asarray(p, dtype=float) - np.asarray(c, dtype=float))


def pinball(y, pred, q: float) -> float:
    diff = np.asarray(y, dtype=float) - np.asarray(pred, dtype=float)
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def recent(df: pd.DataFrame, month_start: pd.Timestamp) -> pd.DataFrame:
    """Linhas dos 12 meses antes do mês, só as com resultado conhecido no início dele."""
    t = pd.to_datetime(df["time"], utc=True)
    return df[(t >= month_start - pd.DateOffset(months=LOOKBACK_MONTHS)) & (t < month_start - PURGE)]
