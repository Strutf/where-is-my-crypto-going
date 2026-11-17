"""Grava DataFrames no armazenamento em partições Parquet (treino) + JSON (API).

Cada gravação lê a partição existente, mescla com os dados novos e remove duplicatas
pela chave do dataset, mantendo o valor mais recente. Assim o pipeline é idempotente:
rodar duas vezes não duplica nada, e valores revisados (ex.: uma vela corrigida pela
corretora) substituem os anteriores.
"""

from __future__ import annotations

import io
import json
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from wimcg import card
from wimcg.catalog import Dataset
from wimcg.partitions import (
    CATALOG_KEY,
    TIME_FORMAT,
    format_time,
    json_key,
    label,
    latest_key,
    manifest_key,
    parquet_key,
)
from wimcg.storage import Storage

_PARTITION_FORMAT = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}
_PANDAS_TYPES = {"float": "float64", "int": "Int64", "str": "string", "bool": "boolean"}


@dataclass
class WriteResult:
    dataset: str
    rows: int
    partitions: list[str]


def coerce(df: pd.DataFrame, ds: Dataset) -> pd.DataFrame:
    """Força o esquema do catálogo: colunas, ordem e tipos. Linhas sem `time` são descartadas."""
    out = pd.DataFrame(index=df.index)
    for f in ds.fields:
        col = df[f.name] if f.name in df.columns else pd.Series(pd.NA, index=df.index)
        if f.type == "time":
            out[f.name] = pd.to_datetime(col, utc=True, errors="coerce", format="ISO8601")
        elif f.type in ("float", "int"):
            num = pd.to_numeric(col, errors="coerce")
            out[f.name] = num.round() if f.type == "int" else num
            out[f.name] = out[f.name].astype(_PANDAS_TYPES[f.type])
        else:
            out[f.name] = col.astype(_PANDAS_TYPES[f.type])
    return out.dropna(subset=["time"]).reset_index(drop=True)


def to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression="zstd")
    return buf.getvalue()


def read_parquet(storage: Storage, key: str) -> pd.DataFrame | None:
    data = storage.read(key)
    return pd.read_parquet(io.BytesIO(data)) if data else None


def to_records(df: pd.DataFrame, ds: Dataset) -> list[dict]:
    out = df.copy()
    for name in ds.time_fields:
        out[name] = out[name].dt.strftime(TIME_FORMAT)
    out = out.astype(object).where(out.notna(), None)
    return out.to_dict("records")


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _latest(df: pd.DataFrame, ds: Dataset) -> pd.DataFrame:
    if ds.latest_mode == "snapshot":
        return df[df["time"] == df["time"].max()]
    if ds.latest_group:
        return df.groupby(list(ds.latest_group), dropna=False).tail(1)
    return df.tail(1)


def _merge(old: pd.DataFrame | None, new: pd.DataFrame, ds: Dataset) -> pd.DataFrame:
    if old is not None and not old.empty:
        with warnings.catch_warnings():
            # Colunas totalmente vazias em um dos lados geram FutureWarning no concat.
            warnings.simplefilter("ignore", FutureWarning)
            new = pd.concat([coerce(old, ds), new], ignore_index=True)
    key = list(ds.key)
    return new.drop_duplicates(subset=key, keep="last").sort_values(key).reset_index(drop=True)


def write_dataset(
    storage: Storage,
    ds: Dataset,
    df: pd.DataFrame,
    now: datetime | None = None,
    replace_from: datetime | None = None,
) -> WriteResult:
    """Mescla `df` com o que já está gravado.

    `replace_from` serve a dados derivados que são recalculados: as linhas já gravadas com
    `time >= replace_from` são trocadas pelas de `df`, e as que sumiram do recálculo são apagadas.
    """
    now = now or datetime.now(UTC)
    df = coerce(df, ds)
    raw = storage.read(manifest_key(ds.id))
    manifest = json.loads(raw) if raw else {"dataset": ds.id, "partitions": []}

    labels = df["time"].dt.strftime(_PARTITION_FORMAT[ds.partition])
    parts = {lbl: part for lbl, part in df.groupby(labels, sort=True)}
    if replace_from is not None:
        # Partições já gravadas que podem ter linhas a apagar, mesmo sem linhas novas.
        first = label(replace_from, ds.partition)
        for lbl in manifest["partitions"]:
            if lbl >= first:
                parts.setdefault(lbl, df.iloc[0:0])
    if not parts:
        return WriteResult(ds.id, 0, [])

    merged: dict[str, pd.DataFrame] = {}
    for lbl in sorted(parts):
        old = read_parquet(storage, parquet_key(ds.id, lbl))
        if old is not None and replace_from is not None:
            old = old[pd.to_datetime(old["time"], utc=True) < replace_from]
        full = _merge(old, parts[lbl], ds)
        storage.write(parquet_key(ds.id, lbl), to_parquet_bytes(full))
        if ds.json_partitions:
            storage.write(json_key(ds.id, lbl), _json_bytes(to_records(full, ds)))
        merged[lbl] = full

    partitions = sorted(set(manifest["partitions"]) | set(merged))
    starts = [manifest["start"]] if manifest.get("start") else []
    ends = [manifest["end"]] if manifest.get("end") else []
    if not df.empty:
        starts.append(format_time(df["time"].min()))
        ends.append(format_time(df["time"].max()))
    manifest.update(
        partitions=partitions,
        partition=ds.partition,
        start=min(starts, default=None),
        end=max(ends, default=None),
        updated_at=format_time(now),
    )
    storage.write(manifest_key(ds.id), _json_bytes(manifest))

    newest = partitions[-1]
    if ds.latest_json and newest in merged:
        latest = {"dataset": ds.id, "updated_at": format_time(now)}
        latest["data"] = to_records(_latest(merged[newest], ds), ds)
        storage.write(latest_key(ds.id), _json_bytes(latest))

    return WriteResult(ds.id, len(df), sorted(merged))


def write_catalog(storage: Storage, datasets: list[Dataset], now: datetime | None = None) -> None:
    """catalog.json com metadados + cobertura de cada dataset (evita N leituras na API)."""
    entries = []
    for ds in datasets:
        raw = storage.read(manifest_key(ds.id))
        manifest = json.loads(raw) if raw else {}
        entry = ds.to_dict()
        entry["coverage"] = {k: manifest.get(k) for k in ("start", "end", "updated_at")}
        entries.append(entry)
    payload = {"updated_at": format_time(now or datetime.now(UTC)), "datasets": entries}
    storage.write(CATALOG_KEY, _json_bytes(payload))
    # A página do dataset acompanha o catálogo; só é regravada quando o texto muda.
    text = card.render(datasets, {e["id"]: e["coverage"]["start"] for e in entries}).encode()
    if storage.read(card.KEY) != text:
        storage.write(card.KEY, text)
