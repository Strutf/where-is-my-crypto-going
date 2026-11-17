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
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import datetime, timedelta

import httpx
import pandas as pd

from wimcg import gkg
from wimcg.pipeline.http import get

log = logging.getLogger(__name__)

BASE = "https://storage.googleapis.com/data.gdeltproject.org/gdeltv2/"
# (lista do último arquivo publicado, sufixo do arquivo), para inglês e para traduções.
ENGLISH = ("lastupdate.txt", ".gkg.csv.zip")
TRANSLATED = ("lastupdate-translation.txt", ".translation.gkg.csv.zip")
QUARTERS = (0, 15, 30, 45)
PATIENCE = timedelta(hours=2)
# Posição dos campos no GKG 2.1 (27 campos separados por tabulação, sem cabeçalho).
N_FIELDS, DATE, THEMES, TONE = 27, 1, 7, 15


def latest(client: httpx.Client, listing: str) -> datetime:
    """Horário do último arquivo do GKG publicado, segundo a lista do GDELT."""
    found = re.search(r"/(\d{14})\.(?:translation\.)?gkg\.csv\.zip", get(client, BASE + listing).text)
    if not found:
        raise RuntimeError(f"GDELT: {listing} sem arquivo do GKG")
    return pd.Timestamp(datetime.strptime(found.group(1), "%Y%m%d%H%M%S"), tz="UTC").to_pydatetime()


def parse(content: bytes, translated: bool) -> pd.DataFrame:
    """Linhas de um arquivo .gkg.csv.zip, só com os campos usados em `gkg.aggregate`."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        raw = z.read(z.namelist()[0])
    rows = []
    for line in raw.split(b"\n"):
        fields = line.split(b"\t")
        # Algumas notícias trazem quebras de linha no último campo; os pedaços soltos não são linhas.
        if len(fields) == N_FIELDS:
            rows.append((fields[DATE], fields[THEMES], fields[TONE]))
    df = pd.DataFrame(rows, columns=["DATE", "Themes", "V2Tone"])
    for col in df.columns:
        df[col] = df[col].str.decode("utf-8", errors="replace")
    df["translated"] = translated
    return df


def _file(client: httpx.Client, stamp: datetime, feed: tuple[str, str]) -> pd.DataFrame | None:
    try:
        resp = get(client, f"{BASE}{stamp:%Y%m%d%H%M%S}{feed[1]}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None  # o GDELT não publicou este arquivo
        raise
    return parse(resp.content, translated=feed is TRANSLATED)


def gkg_hourly(client: httpx.Client, start: datetime, end: datetime) -> pd.DataFrame:
    """Horas fechadas com início em [start, end)."""
    last_en, last_tr = latest(client, ENGLISH[0]), latest(client, TRANSLATED[0])
    hour = pd.Timestamp(start).ceil("h").to_pydatetime()
    frames = []
    while hour + timedelta(hours=1) <= end:
        last_quarter = hour + timedelta(minutes=QUARTERS[-1])
        if last_en < last_quarter:
            break
        if last_tr < last_quarter and end < hour + timedelta(hours=1) + PATIENCE:
            break
        files = [_file(client, hour + timedelta(minutes=m), feed) for feed in (ENGLISH, TRANSLATED) for m in QUARTERS]
        files = [f for f in files if f is not None and not f.empty]
        if files:
            out = gkg.aggregate(pd.concat(files, ignore_index=True))
            out = out[out["time"] == pd.Timestamp(hour)]
            if (out[list(gkg.FILE_COLUMNS)] < len(QUARTERS)).any(axis=None):
                log.warning("gkg-hourly: %s incompleta (%s)", f"{hour:%Y-%m-%d %H}h", out[list(gkg.FILE_COLUMNS)].iloc[0].to_dict())
            frames.append(out)
        hour += timedelta(hours=1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=gkg.columns())
