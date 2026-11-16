"""Detecção dos grandes movimentos do preço a partir das velas de 1 minuto.

Um movimento na escala de s horas termina na hora t quando a variação entre t − s e t passa
de K vezes a volatilidade típica daquela escala. A volatilidade típica é o desvio-padrão dos
retornos horários nos 30 dias que terminam no **início** do movimento, multiplicado por √s
(não usa nada do próprio movimento). Horas seguidas acima do limiar, no mesmo sentido,
formam um único movimento, representado pela hora de maior variação relativa.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SCALES_HOURS = (1, 24)
K_SIGMA = 3.0
WINDOW_DAYS = 30
MIN_HISTORY_DAYS = 20
# No dia a dia, só os movimentos que terminaram nos últimos RECENT_DAYS são recalculados (um
# movimento em andamento ainda pode mudar de hora representativa). Os mais antigos não mudam.
RECENT_DAYS = 14
# Velas necessárias para recalcular esse período: a janela da volatilidade típica, mais a maior
# escala (24 h) e uma folga.
HISTORY_DAYS = RECENT_DAYS + WINDOW_DAYS + 2


def hourly_close(candles: pd.DataFrame) -> pd.Series:
    """Preço de fechamento ao fim de cada hora (índice = fim da hora, UTC)."""
    close = candles.set_index("time")["close"].sort_index()
    hourly = close.resample("h", label="right", closed="left").last()
    # A hora em andamento fica de fora: só entra depois de fechada (última vela = minuto 59).
    hourly = hourly[hourly.index <= close.index[-1] + pd.Timedelta(minutes=1)]
    # Horas sem negociação ficam com o último preço conhecido (até 6 h).
    return hourly.ffill(limit=6)


def _clusters(flagged: pd.Series, gap: pd.Timedelta) -> list[list[pd.Timestamp]]:
    """Agrupa horas sinalizadas seguidas (no mesmo sentido, sem buraco maior que `gap`)."""
    groups: list[list[pd.Timestamp]] = []
    last_t, last_sign = None, 0
    for t, sign in flagged.items():
        if groups and sign == last_sign and t - last_t <= gap:
            groups[-1].append(t)
        else:
            groups.append([t])
        last_t, last_sign = t, sign
    return groups


def detect(
    close: pd.Series,
    scales: tuple[int, ...] = SCALES_HOURS,
    k: float = K_SIGMA,
    window_days: int = WINDOW_DAYS,
) -> pd.DataFrame:
    log_price = np.log(close)
    hourly_returns = log_price.diff()
    window = window_days * 24
    rows = []
    for s in scales:
        move = log_price - log_price.shift(s)
        sigma = hourly_returns.rolling(window, min_periods=MIN_HISTORY_DAYS * 24).std().shift(s) * np.sqrt(s)
        z = move / sigma
        flagged = np.sign(z[z.abs() >= k])
        for group in _clusters(flagged, pd.Timedelta(hours=s)):
            end = max(group, key=lambda t: abs(z[t]))
            start = end - pd.Timedelta(hours=s)
            rows.append(
                {
                    "time": end,
                    "scale_hours": s,
                    "start": start,
                    "direction": "up" if move[end] > 0 else "down",
                    "return_pct": round(float(np.expm1(move[end]) * 100), 3),
                    "sigma_pct": round(float(sigma[end] * 100), 3),
                    "z": round(float(z[end]), 2),
                    "price_start": float(close[start]),
                    "price_end": float(close[end]),
                }
            )
    columns = ["time", "scale_hours", "start", "direction", "return_pct", "sigma_pct", "z", "price_start", "price_end"]
    return pd.DataFrame(rows, columns=columns).sort_values(["time", "scale_hours"]).reset_index(drop=True)
