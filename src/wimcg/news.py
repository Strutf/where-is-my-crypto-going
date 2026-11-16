"""Notícias antes, durante e depois de cada grande movimento, pela DOC 2.0 API do GDELT.

Para cada movimento são feitas três consultas, uma por fase:
- before: nas 24 h antes do início (candidatas a causa: o que interessa para prever);
- during: entre o início e o fim;
- after: nas 24 h depois do fim (em geral, a imprensa explicando o que já aconteceu).

O GDELT aceita no máximo uma consulta a cada 5 s por IP e responde 429 (texto, não JSON)
acima disso; a coleta espera MIN_INTERVAL entre consultas e bem mais depois de um 429.
"""

from __future__ import annotations

import logging
import re
import time

import httpx
import pandas as pd

log = logging.getLogger(__name__)

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
QUERY = "(bitcoin OR cryptocurrency OR crypto) sourcelang:english"
MAX_RECORDS = 250
WINDOW = pd.Timedelta(hours=24)
MIN_INTERVAL = 6.0  # segundos entre consultas
RATE_LIMIT_WAIT = 60.0  # espera depois de um 429
RETRIES = 5

# Categorias de causa por palavras-chave no título (inglês). Uma notícia pode ter várias.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "macro": ("fed", "federal reserve", "powell", "interest rate", "rate cut", "rate hike", "inflation",
              "cpi", "jobs report", "payrolls", "treasury yield", "recession", "dollar index"),
    "regulation": ("sec", "regulator", "regulation", "regulatory", "lawsuit", "crackdown", "ban",
                   "legislation", "congress", "senate", "stablecoin bill", "cftc"),
    "etf_institutional": ("etf", "blackrock", "fidelity", "inflows", "outflows", "microstrategy",
                          "saylor", "institutional", "treasury company"),
    "security": ("hack", "hacked", "exploit", "stolen", "breach", "bankrupt", "bankruptcy",
                 "insolvency", "collapse", "fraud", "scam"),
    "geopolitics": ("war", "missile", "airstrike", "invasion", "attack", "sanction", "sanctions",
                    "ceasefire", "peace", "tariff", "tariffs", "conflict", "military"),
    "liquidation": ("liquidation", "liquidations", "liquidated", "leverage", "margin call",
                    "short squeeze", "open interest", "funding rate"),
}
_PATTERNS = {cat: re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b") for cat, words in CATEGORIES.items()}

_last_request = 0.0


def categorize(title: str) -> str:
    text = title.lower()
    return ",".join(cat for cat, pattern in _PATTERNS.items() if pattern.search(text))


def _gdelt_time(t: pd.Timestamp) -> str:
    return t.strftime("%Y%m%d%H%M%S")


def search(client: httpx.Client, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    """Artigos do GDELT entre start e end (no máximo MAX_RECORDS, os mais relevantes)."""
    global _last_request
    params = {
        "query": QUERY,
        "mode": "artlist",
        "format": "json",
        "maxrecords": MAX_RECORDS,
        "sort": "hybridrel",
        "startdatetime": _gdelt_time(start),
        "enddatetime": _gdelt_time(end),
    }
    for attempt in range(RETRIES):
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            resp = client.get(URL, params=params, timeout=90)
        except httpx.TransportError as exc:
            log.warning("GDELT: %s; nova tentativa", exc)
            time.sleep(RATE_LIMIT_WAIT)
            continue
        text = resp.text.strip()
        if resp.status_code == 429 or not text.startswith("{"):
            log.warning("GDELT limitou as consultas (tentativa %d); esperando %.0f s", attempt + 1, RATE_LIMIT_WAIT)
            time.sleep(RATE_LIMIT_WAIT)
            continue
        return resp.json().get("articles", [])
    raise RuntimeError("GDELT continua limitando as consultas; tente mais tarde")


def news_for_move(client: httpx.Client, move: pd.Series) -> pd.DataFrame:
    start, end = pd.Timestamp(move["start"]), pd.Timestamp(move["time"])
    windows = {"before": (start - WINDOW, start), "during": (start, end), "after": (end, end + WINDOW)}
    rows = []
    for phase, (lo, hi) in windows.items():
        for a in search(client, lo, hi):
            seen = pd.to_datetime(a["seendate"], format="%Y%m%dT%H%M%SZ", utc=True)
            rows.append(
                {
                    "time": seen,
                    "move_time": end,
                    "scale_hours": int(move["scale_hours"]),
                    "phase": phase,
                    "hours_from_start": round((seen - start) / pd.Timedelta(hours=1), 2),
                    "title": a.get("title", ""),
                    "url": a.get("url", ""),
                    "domain": a.get("domain", ""),
                    "source_country": a.get("sourcecountry", ""),
                    "categories": categorize(a.get("title", "")),
                }
            )
    return pd.DataFrame(rows)
