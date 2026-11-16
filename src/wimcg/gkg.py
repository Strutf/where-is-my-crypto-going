"""Séries horárias de notícias a partir do GKG do GDELT (temas e tom de cada notícia).

As mesmas definições geram:
- a consulta do BigQuery para o histórico desde 2015 (`wimcg gkg-sql`);
- a agregação dos arquivos brutos de 15 min na coleta ao vivo (`aggregate`).
Assim o modelo recebe ao vivo exatamente os números com que foi treinado.

Uma notícia entra num assunto se tiver pelo menos um dos temas dele. Os temas foram escolhidos
olhando dias de 2015 a 2026 no BigQuery (ver docs/contexto.md): só temas `ECON_*` e afins que
já existiam em 2015, para não criar degraus artificiais. A exceção é `EPU_UNCERTAINTY`, que
surge entre 2015 e 2017; antes disso a série deve ser tratada como ausente.

Cada linha é uma hora (início da hora, UTC, pelo campo DATE do GKG). Uma hora só está completa
depois que o GDELT publica o último arquivo de 15 min dela.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # o catálogo importa TOPICS sem carregar o pandas
    import pandas as pd

TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
BITCOIN = "ECON_BITCOIN"


@dataclass(frozen=True)
class Topic:
    name: str
    themes: tuple[str, ...]
    world: bool  # conta em todas as notícias do mundo
    bitcoin: bool  # conta nas notícias de bitcoin


TOPICS: tuple[Topic, ...] = (
    Topic("monetary", ("ECON_CENTRALBANK", "ECON_INTEREST_RATES", "ECON_INFLATION"), world=True, bitcoin=True),
    Topic("markets", ("ECON_STOCKMARKET", "WB_2936_GOLD", "ECON_WORLDCURRENCIES_DOLLAR"), world=True, bitcoin=True),
    Topic("regulation", ("LEGISLATION", "WB_840_JUSTICE"), world=False, bitcoin=True),
    Topic("cyber", ("CYBER_ATTACK",), world=True, bitcoin=True),
    Topic("bankruptcy", ("ECON_BANKRUPTCY",), world=False, bitcoin=True),
    Topic("volatility", ("WB_1150_VOLATILITY",), world=False, bitcoin=True),
    Topic("conflict", ("ARMEDCONFLICT", "MILITARY"), world=True, bitcoin=True),
    Topic("terror", ("TERROR",), world=True, bitcoin=False),
    Topic("disaster", ("NATURAL_DISASTER",), world=True, bitcoin=False),
    Topic("peace", ("CEASEFIRE", "PEACEKEEPING"), world=True, bitcoin=False),
    Topic("sanctions", ("SANCTIONS",), world=True, bitcoin=False),
    Topic("uncertainty", ("EPU_UNCERTAINTY",), world=True, bitcoin=False),
)


def columns() -> list[str]:
    """Colunas da tabela horária, na ordem da consulta."""
    cols = ["time", "articles", "tone", "btc_articles", "btc_tone"]
    cols += [f"btc_{t.name}_articles" for t in TOPICS if t.bitcoin]
    cols += [f"world_{t.name}_articles" for t in TOPICS if t.world]
    return cols


def _in_list(themes: tuple[str, ...]) -> str:
    return ", ".join(f"'{t}'" for t in themes)


def sql(start: datetime, end: datetime) -> str:
    """Consulta do BigQuery com as séries por hora em [start, end) (UTC)."""
    flags = ",\n    ".join(
        f"EXISTS(SELECT 1 FROM UNNEST(themes) AS t WHERE t IN ({_in_list(tp.themes)})) AS is_{tp.name}" for tp in TOPICS
    )
    counts = [f"COUNTIF(btc AND is_{t.name}) AS btc_{t.name}_articles" for t in TOPICS if t.bitcoin]
    counts += [f"COUNTIF(is_{t.name}) AS world_{t.name}_articles" for t in TOPICS if t.world]
    counts_sql = ",\n  ".join(counts)
    # A partição é o dia de entrada no GDELT, que pode diferir do DATE perto da meia-noite:
    # lê um dia a mais de cada lado e corta pelo DATE, para dois intervalos vizinhos não se
    # sobreporem.
    p_start = (start - timedelta(days=1)).strftime("%Y-%m-%d")
    p_end = (end + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"""-- Gerado por `wimcg gkg-sql --start {start:%Y-%m-%d} --end {end:%Y-%m-%d}` (src/wimcg/gkg.py).
-- Notícias por hora no GKG do GDELT: total, tom médio, bitcoin e assuntos.
SELECT
  time,
  COUNT(*) AS articles,
  ROUND(AVG(tone), 4) AS tone,
  COUNTIF(btc) AS btc_articles,
  ROUND(AVG(IF(btc, tone, NULL)), 4) AS btc_tone,
  {counts_sql}
FROM (
  SELECT
    time,
    themes,
    tone,
    '{BITCOIN}' IN UNNEST(themes) AS btc,
    {flags}
  FROM (
    SELECT
      TIMESTAMP_TRUNC(PARSE_TIMESTAMP('%Y%m%d%H%M%S', CAST(`DATE` AS STRING)), HOUR) AS time,
      SPLIT(Themes, ';') AS themes,
      SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(0)] AS FLOAT64) AS tone
    FROM `{TABLE}`
    WHERE _PARTITIONTIME >= TIMESTAMP('{p_start}') AND _PARTITIONTIME < TIMESTAMP('{p_end}')
  )
  WHERE time >= TIMESTAMP('{start:%Y-%m-%d %H:%M:%S}') AND time < TIMESTAMP('{end:%Y-%m-%d %H:%M:%S}')
)
GROUP BY time
ORDER BY time
"""


def aggregate(records: pd.DataFrame) -> pd.DataFrame:
    """Mesmas séries de `sql`, a partir de linhas do GKG com as colunas DATE, Themes e V2Tone."""
    import pandas as pd

    if records.empty:
        return pd.DataFrame(columns=columns())
    time = pd.to_datetime(records["DATE"].astype("int64").astype(str), format="%Y%m%d%H%M%S", utc=True).dt.floor("h")
    themes = records["Themes"].fillna("").astype(str).map(lambda s: set(s.split(";")))
    tone = pd.to_numeric(records["V2Tone"].fillna("").astype(str).str.split(",").str[0], errors="coerce")
    btc = themes.map(lambda s: BITCOIN in s)
    df = pd.DataFrame({"time": time, "tone": tone, "btc": btc})
    for tp in TOPICS:
        wanted = set(tp.themes)
        df[f"is_{tp.name}"] = themes.map(lambda s, w=wanted: not s.isdisjoint(w))

    g = df.groupby("time")
    out = pd.DataFrame({"articles": g.size(), "tone": g["tone"].mean()})
    out["btc_articles"] = g["btc"].sum()
    out["btc_tone"] = df["tone"].where(df["btc"]).groupby(df["time"]).mean()
    for tp in TOPICS:
        if tp.bitcoin:
            out[f"btc_{tp.name}_articles"] = (df["btc"] & df[f"is_{tp.name}"]).groupby(df["time"]).sum()
    for tp in TOPICS:
        if tp.world:
            out[f"world_{tp.name}_articles"] = g[f"is_{tp.name}"].sum()
    out[["tone", "btc_tone"]] = out[["tone", "btc_tone"]].round(4)
    return out.reset_index()[columns()]


def _check(df: pd.DataFrame, where: str) -> None:
    # Um arquivo de uma versão antiga da consulta gravaria colunas trocadas.
    if list(df.columns) != columns():
        raise ValueError(f"{where}: colunas diferentes das de gkg.columns() (consulta de outra versão?)")
    if df["time"].duplicated().any():
        raise ValueError(f"{where}: horas repetidas")


def read_bigquery_csv(path) -> pd.DataFrame:
    """Lê o resultado de `sql` exportado do BigQuery como CSV (aceita .csv.gz)."""
    import pandas as pd

    df = pd.read_csv(path)
    # O BigQuery exporta "2015-02-19 00:00:00 UTC".
    df["time"] = pd.to_datetime(df["time"].astype(str).str.removesuffix(" UTC"), utc=True, format="%Y-%m-%d %H:%M:%S")
    _check(df, str(path))
    return df


def pack(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Junta resultados de `sql` em uma tabela ordenada, recusando horas repetidas entre eles."""
    import pandas as pd

    df = pd.concat(frames, ignore_index=True).sort_values("time", ignore_index=True)
    _check(df, "arquivos juntos")
    counts = [c for c in columns() if c.endswith("_articles") or c == "articles"]
    df[counts] = df[counts].astype("int32")  # cabem com folga e o arquivo fica menor
    return df


def write_history(df: pd.DataFrame, path) -> None:
    """Grava o histórico em Parquet compacto (zstd no nível máximo útil): ~3 MB para 2015-2026."""
    df.to_parquet(path, index=False, compression="zstd", compression_level=19)


def read_history(path) -> pd.DataFrame:
    import pandas as pd

    df = pd.read_parquet(path)
    _check(df, str(path))
    return df
