"""Velas de 1 minuto do BTC-USD na API pública da Coinbase Exchange (sem chave).

A API devolve no máximo 300 velas por requisição, da mais recente para a mais antiga, no
formato [time, low, high, open, close, volume]. Minutos sem negociação não têm vela.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import httpx
import pandas as pd

from wimcg.pipeline.http import get_json

URL = "https://api.exchange.coinbase.com/products/{product}/candles"
MAX_CANDLES = 300
PAUSE_SECONDS = 0.15  # o limite público é de ~10 requisições por segundo


def candles(
    client: httpx.Client, start: datetime, end: datetime, product: str = "BTC-USD", granularity: int = 60
) -> pd.DataFrame:
    step = timedelta(seconds=granularity * MAX_CANDLES)
    frames = []
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        raw = get_json(
            client,
            URL.format(product=product),
            {"granularity": granularity, "start": cur.isoformat(), "end": nxt.isoformat()},
        )
        if raw:
            frames.append(pd.DataFrame(raw, columns=["time", "low", "high", "open", "close", "volume"]))
        cur = nxt
        if cur < end:
            time.sleep(PAUSE_SECONDS)
    if not frames:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    # Os intervalos pedidos se tocam nas pontas: a mesma vela pode vir duas vezes.
    df = df.drop_duplicates("time").sort_values("time")
    return df[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def btc_usd_1m(client: httpx.Client, start: datetime, end: datetime) -> pd.DataFrame:
    return candles(client, start, end)
