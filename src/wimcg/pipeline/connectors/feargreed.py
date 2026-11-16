"""Índice Fear & Greed de cripto da alternative.me (diário, desde 2018, sem chave)."""

from __future__ import annotations

from datetime import datetime

import httpx
import pandas as pd

from wimcg.pipeline.http import get_json

URL = "https://api.alternative.me/fng/"


def fear_greed(client: httpx.Client, start: datetime, end: datetime) -> pd.DataFrame:
    # limit=0 traz o histórico inteiro (~3 mil linhas); o writer descarta o que já existe.
    raw = get_json(client, URL, {"limit": 0, "format": "json"})["data"]
    df = pd.DataFrame(raw)
    df["time"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
    df = df.rename(columns={"value_classification": "classification"})
    df = df[(df["time"] >= pd.Timestamp(start).floor("D")) & (df["time"] <= pd.Timestamp(end))]
    return df[["time", "value", "classification"]].sort_values("time").reset_index(drop=True)
