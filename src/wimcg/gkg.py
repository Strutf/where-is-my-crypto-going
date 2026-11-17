"""Séries horárias de notícias a partir do GKG do GDELT (temas e tom de cada notícia).

As mesmas definições geram:
- a consulta do BigQuery para o histórico desde 2015 (`wimcg gkg-sql`);
- a agregação dos arquivos brutos de 15 min na coleta ao vivo (`aggregate`).
Assim o modelo recebe ao vivo exatamente os números com que foi treinado.

Uma notícia entra num assunto se tiver pelo menos um dos temas dele. Os temas foram escolhidos
olhando dias de 2015 a 2026 no BigQuery (ver docs/contexto.md): só temas `ECON_*` e afins que
já existiam em 2015, para não criar degraus artificiais. A exceção é `EPU_UNCERTAINTY`, que
surge entre 2015 e 2017; antes disso a série deve ser tratada como ausente.

Cada linha é uma hora (início da hora, UTC, pelo campo DATE do GKG). O GDELT publica dois
arquivos a cada 15 min: as notícias em inglês e as traduzidas de outros idiomas. As colunas
`en_files` e `translated_files` dizem quantos desses arquivos entraram na hora (4 = completa).
Quando falta um arquivo, as contagens da hora ficam menores sem que o mundo tenha mudado: no
BigQuery faltam arquivos de traduções em várias horas (ver docs/contexto.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # o catálogo importa TOPICS sem carregar o pandas
    import pandas as pd

TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
BITCOIN = "ECON_BITCOIN"
# Quantos arquivos de 15 min entraram na hora, em inglês e traduzidos.
FILE_COLUMNS = ("en_files", "translated_files")


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
    return cols + list(FILE_COLUMNS)


def _in_list(themes: tuple[str, ...]) -> str:
    return ", ".join(f"'{t}'" for t in themes)


# Notícias traduzidas têm um T no identificador (ex.: 20150218230000-T12). Cada arquivo de 15 min
# tem um único valor de DATE, então contar os valores distintos é contar os arquivos.
_FILES_SQL = """COUNT(DISTINCT IF(NOT translated, stamp, NULL)) AS en_files,
  COUNT(DISTINCT IF(translated, stamp, NULL)) AS translated_files"""
_ROW_SQL = """TIMESTAMP_TRUNC(PARSE_TIMESTAMP('%Y%m%d%H%M%S', CAST(`DATE` AS STRING)), HOUR) AS time,
      `DATE` AS stamp,
      REGEXP_CONTAINS(GKGRECORDID, r'-T') AS translated"""


def _bounds(start: datetime, end: datetime) -> tuple[str, str]:
    # A partição é o dia de entrada no GDELT, que pode diferir do DATE perto da meia-noite:
    # lê um dia a mais de cada lado e corta pelo DATE, para dois intervalos vizinhos não se
    # sobreporem.
    p_start = (start - timedelta(days=1)).strftime("%Y-%m-%d")
    p_end = (end + timedelta(days=1)).strftime("%Y-%m-%d")
    partitions = f"_PARTITIONTIME >= TIMESTAMP('{p_start}') AND _PARTITIONTIME < TIMESTAMP('{p_end}')"
    hours = f"time >= TIMESTAMP('{start:%Y-%m-%d %H:%M:%S}') AND time < TIMESTAMP('{end:%Y-%m-%d %H:%M:%S}')"
    return partitions, hours


def sql(start: datetime, end: datetime) -> str:
    """Consulta do BigQuery com as séries por hora em [start, end) (UTC)."""
    flags = ",\n    ".join(
        f"EXISTS(SELECT 1 FROM UNNEST(themes) AS t WHERE t IN ({_in_list(tp.themes)})) AS is_{tp.name}" for tp in TOPICS
    )
    counts = [f"COUNTIF(btc AND is_{t.name}) AS btc_{t.name}_articles" for t in TOPICS if t.bitcoin]
    counts += [f"COUNTIF(is_{t.name}) AS world_{t.name}_articles" for t in TOPICS if t.world]
    counts_sql = ",\n  ".join(counts)
    partitions, hours = _bounds(start, end)
    return f"""-- Gerado por `wimcg gkg-sql --start {start:%Y-%m-%d} --end {end:%Y-%m-%d}` (src/wimcg/gkg.py).
-- Notícias por hora no GKG do GDELT: total, tom médio, bitcoin e assuntos.
SELECT
  time,
  COUNT(*) AS articles,
  ROUND(AVG(tone), 4) AS tone,
  COUNTIF(btc) AS btc_articles,
  ROUND(AVG(IF(btc, tone, NULL)), 4) AS btc_tone,
  {counts_sql},
  {_FILES_SQL}
FROM (
  SELECT
    time,
    stamp,
    translated,
    themes,
    tone,
    '{BITCOIN}' IN UNNEST(themes) AS btc,
    {flags}
  FROM (
    SELECT
      {_ROW_SQL},
      SPLIT(Themes, ';') AS themes,
      SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(0)] AS FLOAT64) AS tone
    FROM `{TABLE}`
    WHERE {partitions}
  )
  WHERE {hours}
)
GROUP BY time
ORDER BY time
"""


def files_sql(start: datetime, end: datetime) -> str:
    """Consulta leve: só quantos arquivos de 15 min o BigQuery tem em cada hora.

    Lê apenas duas colunas pequenas (DATE e GKGRECORDID), sem os temas. Serve para marcar as
    horas incompletas de um histórico que foi extraído antes de `sql` ter essas colunas.
    """
    partitions, hours = _bounds(start, end)
    return f"""-- Gerado por `wimcg gkg-sql --files --start {start:%Y-%m-%d} --end {end:%Y-%m-%d}` (src/wimcg/gkg.py).
-- Arquivos de 15 min do GKG presentes em cada hora (4 = hora completa), em inglês e traduzidos.
SELECT
  time,
  {_FILES_SQL}
FROM (
  SELECT
      {_ROW_SQL}
  FROM `{TABLE}`
  WHERE {partitions}
)
WHERE {hours}
GROUP BY time
ORDER BY time
"""


def aggregate(records: pd.DataFrame) -> pd.DataFrame:
    """Mesmas séries de `sql`, a partir de linhas do GKG.

    Colunas esperadas: DATE, Themes, V2Tone e, opcionalmente, `translated` (True para as linhas
    do arquivo de traduções; sem a coluna, todas contam como em inglês).
    """
    import pandas as pd

    if records.empty:
        return pd.DataFrame(columns=columns())
    stamp = records["DATE"].astype("int64")
    time = pd.to_datetime(stamp.astype(str), format="%Y%m%d%H%M%S", utc=True).dt.floor("h")
    themes = records["Themes"].fillna("").astype(str).map(lambda s: set(s.split(";")))
    tone = pd.to_numeric(records["V2Tone"].fillna("").astype(str).str.split(",").str[0], errors="coerce")
    btc = themes.map(lambda s: BITCOIN in s)
    translated = records["translated"].astype(bool) if "translated" in records else pd.Series(False, index=records.index)
    df = pd.DataFrame({"time": time, "tone": tone, "btc": btc, "stamp": stamp, "translated": translated})
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
    out["en_files"] = df["stamp"].where(~df["translated"]).groupby(df["time"]).nunique()
    out["translated_files"] = df["stamp"].where(df["translated"]).groupby(df["time"]).nunique()
    out[["tone", "btc_tone"]] = out[["tone", "btc_tone"]].round(4)
    return out.reset_index()[columns()]


def _check(df: pd.DataFrame, where: str) -> pd.DataFrame:
    """Confere as colunas. As de arquivos são opcionais: extrações antigas não as têm (ficam vazias)."""
    import pandas as pd

    for col in FILE_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    # Um arquivo de outra versão da consulta gravaria colunas trocadas.
    if sorted(df.columns) != sorted(columns()):
        raise ValueError(f"{where}: colunas diferentes das de gkg.columns() (consulta de outra versão?)")
    if df["time"].duplicated().any():
        raise ValueError(f"{where}: horas repetidas")
    return df[columns()]


def _bigquery_time(col: pd.Series) -> pd.Series:
    """O BigQuery exporta "2015-02-19 00:00:00 UTC"."""
    import pandas as pd

    return pd.to_datetime(col.astype(str).str.removesuffix(" UTC"), utc=True, format="%Y-%m-%d %H:%M:%S")


def read_bigquery_csv(path) -> pd.DataFrame:
    """Lê o resultado de `sql` exportado do BigQuery como CSV (aceita .csv.gz)."""
    import pandas as pd

    df = pd.read_csv(path)
    df["time"] = _bigquery_time(df["time"])
    return _check(df, str(path))


def read_files_csv(path) -> pd.DataFrame:
    """Lê o resultado de `files_sql` exportado do BigQuery como CSV."""
    import pandas as pd

    df = pd.read_csv(path)
    if list(df.columns) != ["time", *FILE_COLUMNS]:
        raise ValueError(f"{path}: esperava as colunas time, {', '.join(FILE_COLUMNS)}")
    df["time"] = _bigquery_time(df["time"])
    return df


def pack(frames: list[pd.DataFrame], files: list[pd.DataFrame] | None = None) -> pd.DataFrame:
    """Junta resultados de `sql` em uma tabela ordenada, recusando horas repetidas entre eles.

    `files` são resultados de `files_sql`: preenchem as colunas de arquivos por hora.
    """
    import pandas as pd

    df = pd.concat(frames, ignore_index=True).sort_values("time", ignore_index=True)
    df = _check(df, "arquivos juntos")
    if files:
        counts = pd.concat(files, ignore_index=True)
        if counts["time"].duplicated().any():
            raise ValueError("contagens de arquivos: horas repetidas")
        missing = ~df["time"].isin(counts["time"])
        if missing.any():
            raise ValueError(f"contagens de arquivos: faltam {int(missing.sum())} horas (a primeira é {df['time'][missing].iloc[0]})")
        df = df.drop(columns=list(FILE_COLUMNS)).merge(counts, on="time", how="left", validate="one_to_one")[columns()]
    counts_cols = [c for c in columns() if c.endswith("articles")]
    df[counts_cols] = df[counts_cols].astype("int32")  # cabem com folga e o arquivo fica menor
    df[list(FILE_COLUMNS)] = df[list(FILE_COLUMNS)].astype("Int8")  # aceita vazio
    return df


def write_history(df: pd.DataFrame, path) -> None:
    """Grava o histórico em Parquet compacto (zstd no nível máximo útil): ~3 MB para 2015-2026."""
    df.to_parquet(path, index=False, compression="zstd", compression_level=19)


def read_history(path) -> pd.DataFrame:
    import pandas as pd

    return _check(pd.read_parquet(path), str(path))
