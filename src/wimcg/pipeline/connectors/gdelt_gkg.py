"""Notícias por hora a partir dos arquivos brutos do GKG do GDELT (sem chave e sem limite).

A cada 15 min o GDELT publica dois arquivos: as notícias em inglês (`.gkg.csv.zip`) e as
traduzidas de outros idiomas (`.translation.gkg.csv.zip`), de 2 a 5 MB cada. O nome do arquivo é
o DATE das linhas dele, então a hora H é formada pelos arquivos de H:00, H:15, H:30 e H:45.

Uma hora só é gravada depois de fechada, isto é, quando o GDELT já publicou o arquivo de H:45
das duas listas. As traduções costumam sair 15 min depois; se atrasarem mais que `PATIENCE`, a
hora é gravada sem elas e `translated_files` mostra quantos arquivos entraram. Um arquivo que o
GDELT nunca publicou (404) fica de fora do mesmo jeito.

Os arquivos são lidos direto do armazenamento do GDELT (o balde `data.gdeltproject.org` no Google
Cloud Storage), e não do endereço público https://data.gdeltproject.org. O endereço público guarda
por 1 hora a resposta "não existe" de quem pediu um arquivo antes de ele ser publicado: em
17/11/2026, dois arquivos já publicados e listados ainda davam 404 por lá, e a hora seria gravada
incompleta. No armazenamento o 404 não é guardado, então ali ele quer dizer que o arquivo não existe.

Conferido em 16/11/2026: a agregação dos arquivos bate com `articles` do BigQuery nas horas em
que o BigQuery tem os 8 arquivos (ver docs/contexto.md).

Os mesmos arquivos alimentam dois datasets: `gkg-hourly` (contagens por hora) e `btc-news` (as
notícias de bitcoin, uma a uma, com título e endereço). Para não baixar tudo duas vezes, as
notícias de bitcoin de cada hora lida ficam guardadas na memória até o outro conector pedi-las.
"""

from __future__ import annotations

import html
import io
import logging
import re
import zipfile
from datetime import datetime, timedelta

import httpx
import pandas as pd

from wimcg import gkg
from wimcg.news import title_mentions_crypto
from wimcg.pipeline.http import get

log = logging.getLogger(__name__)

BASE = "https://storage.googleapis.com/data.gdeltproject.org/gdeltv2/"
# (lista do último arquivo publicado, sufixo do arquivo), para inglês e para traduções.
ENGLISH = ("lastupdate.txt", ".gkg.csv.zip")
TRANSLATED = ("lastupdate-translation.txt", ".translation.gkg.csv.zip")
QUARTERS = (0, 15, 30, 45)
PATIENCE = timedelta(hours=2)
# Posição dos campos no GKG 2.1 (27 campos separados por tabulação, sem cabeçalho).
N_FIELDS, DATE, DOMAIN, URL, THEMES, TONE, TRANSLATION, EXTRAS = 27, 1, 3, 4, 7, 15, 25, 26
NEWS_COLUMNS = ["time", "url", "title", "in_title", "domain", "language", "tone", "topics"]
MAX_TITLE = 300
_TITLE = re.compile(rb"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.DOTALL)
_LANGUAGE = re.compile(rb"srclc:([a-z]+)")
_BITCOIN = gkg.BITCOIN.encode()
# Notícias de bitcoin das horas já lidas nesta execução, para o segundo conector não baixar de novo.
_news_cache: dict[datetime, pd.DataFrame] = {}


def latest(client: httpx.Client, listing: str) -> datetime:
    """Horário do último arquivo do GKG publicado, segundo a lista do GDELT."""
    found = re.search(r"/(\d{14})\.(?:translation\.)?gkg\.csv\.zip", get(client, BASE + listing).text)
    if not found:
        raise RuntimeError(f"GDELT: {listing} sem arquivo do GKG")
    return pd.Timestamp(datetime.strptime(found.group(1), "%Y%m%d%H%M%S"), tz="UTC").to_pydatetime()


def _text(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def parse(content: bytes, translated: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lê um arquivo .gkg.csv.zip.

    Devolve (linhas para `gkg.aggregate`, notícias de bitcoin com as colunas de `btc-news`).
    """
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        raw = z.read(z.namelist()[0])
    rows, news = [], []
    for line in raw.split(b"\n"):
        fields = line.split(b"\t")
        # Algumas notícias trazem quebras de linha no último campo; os pedaços soltos não são linhas.
        if len(fields) != N_FIELDS:
            continue
        rows.append((_text(fields[DATE]), _text(fields[THEMES]), _text(fields[TONE])))
        themes = set(fields[THEMES].split(b";"))
        if _BITCOIN in themes:
            names = {t.decode() for t in themes}
            found = _TITLE.search(fields[EXTRAS])
            # O título vem no idioma original, com entidades HTML (&#x2013;).
            title = html.unescape(_text(found.group(1))).strip()[:MAX_TITLE] if found else None
            language = _LANGUAGE.search(fields[TRANSLATION])
            news.append(
                {
                    "time": pd.to_datetime(_text(fields[DATE]), format="%Y%m%d%H%M%S", utc=True),
                    "url": _text(fields[URL]),
                    "title": title,
                    "in_title": title_mentions_crypto(title),
                    "domain": _text(fields[DOMAIN]),
                    "language": _text(language.group(1)) if language else "eng",
                    "tone": pd.to_numeric(_text(fields[TONE]).split(",")[0], errors="coerce"),
                    "topics": ",".join(tp.name for tp in gkg.TOPICS if tp.bitcoin and not names.isdisjoint(tp.themes)),
                }
            )
    records = pd.DataFrame(rows, columns=["DATE", "Themes", "V2Tone"])
    records["translated"] = translated
    return records, pd.DataFrame(news, columns=NEWS_COLUMNS)


def _file(client: httpx.Client, stamp: datetime, feed: tuple[str, str]) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    try:
        resp = get(client, f"{BASE}{stamp:%Y%m%d%H%M%S}{feed[1]}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None  # o GDELT não publicou este arquivo
        raise
    return parse(resp.content, translated=feed is TRANSLATED)


def _closed_hours(client: httpx.Client, start: datetime, end: datetime) -> list[datetime]:
    """Horas com início em [start, end) que o GDELT já terminou de publicar."""
    last_en, last_tr = latest(client, ENGLISH[0]), latest(client, TRANSLATED[0])
    hour = pd.Timestamp(start).ceil("h").to_pydatetime()
    hours = []
    while hour + timedelta(hours=1) <= end:
        last_quarter = hour + timedelta(minutes=QUARTERS[-1])
        if last_en < last_quarter:
            break
        if last_tr < last_quarter and end < hour + timedelta(hours=1) + PATIENCE:
            break
        hours.append(hour)
        hour += timedelta(hours=1)
    return hours


def _read_hour(client: httpx.Client, hour: datetime) -> pd.DataFrame:
    """Baixa os 8 arquivos da hora: devolve as linhas para a agregação e guarda as notícias de bitcoin."""
    files = [_file(client, hour + timedelta(minutes=m), feed) for feed in (ENGLISH, TRANSLATED) for m in QUARTERS]
    files = [f for f in files if f is not None]
    _news_cache[hour] = pd.concat([n for _, n in files], ignore_index=True) if files else pd.DataFrame(columns=NEWS_COLUMNS)
    return pd.concat([r for r, _ in files], ignore_index=True) if files else pd.DataFrame(columns=["DATE", "Themes", "V2Tone", "translated"])


def gkg_hourly(client: httpx.Client, start: datetime, end: datetime) -> pd.DataFrame:
    """Contagens por hora, para as horas fechadas com início em [start, end)."""
    frames = []
    for hour in _closed_hours(client, start, end):
        records = _read_hour(client, hour)
        if records.empty:
            continue
        out = gkg.aggregate(records)
        out = out[out["time"] == pd.Timestamp(hour)]
        if (out[list(gkg.FILE_COLUMNS)] < len(QUARTERS)).any(axis=None):
            log.warning("gkg-hourly: %s incompleta (%s)", f"{hour:%Y-%m-%d %H}h", out[list(gkg.FILE_COLUMNS)].iloc[0].to_dict())
        frames.append(out)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=gkg.columns())


def btc_news(client: httpx.Client, start: datetime, end: datetime) -> pd.DataFrame:
    """Notícias de bitcoin (tema ECON_BITCOIN), uma por linha, das horas fechadas com início em [start, end)."""
    frames = []
    for hour in _closed_hours(client, start, end):
        if hour not in _news_cache:
            _read_hour(client, hour)
        frames.append(_news_cache.pop(hour))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=NEWS_COLUMNS)
