"""Estima em quantas horas o histórico de notícias (BigQuery) está incompleto.

Contexto: o histórico de `gkg-hourly` até 15/11/2026 veio do BigQuery, e o BigQuery está sem
arquivos inteiros de 15 min em várias horas (ver docs/contexto.md). Sem cota para perguntar isso
ao próprio BigQuery, este script compara, hora a hora:

- `articles` do histórico (history/gkg-hourly.parquet);
- o tamanho dos arquivos que o GDELT publicou naquela hora, pelas listas mestras
  (masterfilelist.txt e masterfilelist-translation.txt, ~270 MB, baixadas uma vez).

O número de notícias por byte muda devagar, então em cada mês se ajusta
`articles ≈ a × bytes_em_inglês + b × bytes_traduzidos` usando só as horas que parecem completas.
A razão entre o `articles` do histórico e esse valor esperado fica perto de 1 quando o BigQuery
tem todos os arquivos e cai quando falta algum (um arquivo de traduções é ~17% da hora).

É uma estimativa, não uma contagem: o limiar `THRESHOLD` separa bem as 10 horas conferidas
arquivo por arquivo (lista `CHECKED`), mas horas pouco abaixo de 1 podem ser só ruído.

Uso:  python research/gkg_gaps.py            (da raiz do repositório, com o ambiente ativado)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history" / "gkg-hourly.parquet"
CACHE = ROOT / "data" / "gdelt-master"  # data/ não vai para o repositório
BASE = "https://storage.googleapis.com/data.gdeltproject.org/gdeltv2/"
LISTS = ("masterfilelist.txt", "masterfilelist-translation.txt")
THRESHOLD = 0.93

# Horas conferidas em 16/11/2026 baixando os 8 arquivos e contando as linhas: o arquivo que
# falta no BigQuery ("-" = nenhum, a contagem do BigQuery é igual à dos arquivos).
CHECKED = {
    "2015-06-10T08": "-",
    "2018-01-16T10": "-",
    "2021-05-19T13": "traduções 13:30",
    "2024-03-12T15": "-",
    "2026-02-05T12": "-",
    "2026-11-10T09": "traduções 09:45",
    "2026-11-15T20": "traduções 20:45",
    "2026-11-15T21": "traduções 21:15",
    "2026-11-15T22": "traduções 22:00",
    "2026-11-15T23": "-",
}

_LINE = re.compile(rb"^(\d+) \S+ \S+/(\d{14})\.(translation\.)?gkg\.csv\.zip\s*$")
_QUARTERS = (b"0000", b"1500", b"3000", b"4500")


def download_lists() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for name in LISTS:
        path = CACHE / name
        if path.exists():
            continue
        print(f"baixando {name}...", file=sys.stderr)
        with httpx.stream("GET", BASE + name, timeout=300, follow_redirects=True) as resp:
            resp.raise_for_status()
            with path.open("wb") as out:
                for chunk in resp.iter_bytes():
                    out.write(chunk)


def source_sizes() -> pd.DataFrame:
    """Por hora: bytes e número de arquivos do GKG publicados, em inglês e traduzidos."""
    rows = []
    for name in LISTS:
        with (CACHE / name).open("rb") as lines:
            for line in lines:
                m = _LINE.match(line)
                if m and m.group(2)[10:] in _QUARTERS:
                    rows.append((m.group(2).decode(), bool(m.group(3)), int(m.group(1))))
    files = pd.DataFrame(rows, columns=["stamp", "translated", "size"]).drop_duplicates(["stamp", "translated"])
    files["time"] = pd.to_datetime(files["stamp"].str[:10], format="%Y%m%d%H", utc=True)
    g = files.groupby(["time", "translated"])["size"].agg(["sum", "count"]).unstack("translated").fillna(0)
    return pd.DataFrame(
        {
            "en_bytes": g[("sum", False)],
            "translated_bytes": g[("sum", True)],
            "en_files": g[("count", False)].astype(int),
            "translated_files": g[("count", True)].astype(int),
        }
    )


def ratios(history: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
    """Razão entre `articles` do histórico e o esperado pelo tamanho dos arquivos na fonte."""
    df = source.join(history.set_index("time")[["articles"]], how="inner")
    df["ratio"] = np.nan
    for _, part in df.groupby(df.index.strftime("%Y-%m")):
        x = part[["en_bytes", "translated_bytes"]].to_numpy(float)
        y = part["articles"].to_numpy(float)
        keep = np.ones(len(part), bool)
        for _ in range(4):  # reajusta só com as horas que parecem completas
            coef, *_ = np.linalg.lstsq(x[keep], y[keep], rcond=None)
            ratio = y / np.maximum(x @ coef, 1)
            keep = ratio > np.median(ratio) * 0.97
        df.loc[part.index, "ratio"] = ratio
    return df


def main() -> None:
    download_lists()
    source = source_sizes()
    df = ratios(pd.read_parquet(HISTORY), source)

    full = ((source["en_files"] >= 4) & (source["translated_files"] >= 4)).mean()
    print(f"Na fonte (GDELT): {len(source)} horas com arquivos; {full:.1%} delas com os 8 arquivos.")
    print(f"No histórico (BigQuery): {len(df)} horas.\n")

    print("Horas conferidas arquivo por arquivo:")
    for hour, missing in CHECKED.items():
        print(f"  {hour}h  razão {df.loc[pd.Timestamp(hour, tz='UTC'), 'ratio']:.2f}  falta no BigQuery: {missing}")

    low = df["ratio"] < THRESHOLD
    print(f"\nHoras com razão abaixo de {THRESHOLD} (provavelmente incompletas no BigQuery):")
    by_year = pd.DataFrame({"horas": low.groupby(df.index.year).sum(), "% do ano": (low.groupby(df.index.year).mean() * 100).round(1)})
    print(by_year.to_string())
    print(f"total: {low.sum()} de {len(df)} horas ({low.mean():.1%})")

    bins = [0, 0.7, 0.8, 0.9, THRESHOLD, 1.07, 99]
    labels = ["falta mais de 30%", "falta de 20% a 30%", "falta de 10% a 20%", f"falta de {1 - THRESHOLD:.0%} a 10%", "completa", "acima do esperado"]
    print("\nHoras por tamanho da falta:")
    print(pd.cut(df["ratio"], bins, labels=labels).value_counts().reindex(labels).to_string())


if __name__ == "__main__":
    main()
