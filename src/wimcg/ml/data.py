"""Tabelas do dataset público no Hugging Face usadas no treino, baixadas para `data/hf/`."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

TABLES = ("btc-usd-1h", "market-state", "fear-greed", "gkg-hourly")
DEFAULT_REPO = "Strutf/where-is-my-crypto-going"
DEFAULT_DIR = Path("data/hf")


def download(dest: Path = DEFAULT_DIR, repo: str | None = None) -> str:
    """Baixa (ou atualiza) as tabelas de treino. Devolve o commit do dataset, para reproduzir."""
    from huggingface_hub import HfApi, snapshot_download

    repo = repo or os.environ.get("FS_HF_REPO") or DEFAULT_REPO
    revision = HfApi().repo_info(repo, repo_type="dataset").sha
    snapshot_download(
        repo,
        repo_type="dataset",
        revision=revision,
        local_dir=dest,
        allow_patterns=[f"parquet/{t}/*" for t in TABLES],
    )
    (dest / "REVISION").write_text(revision + "\n")
    return revision


def revision(dest: Path = DEFAULT_DIR) -> str | None:
    path = dest / "REVISION"
    return path.read_text().strip() if path.exists() else None


def load(table: str, dest: Path = DEFAULT_DIR) -> pd.DataFrame:
    files = sorted((dest / "parquet" / table).glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"{table}: nada em {dest}; rode `wimcg ml-data` antes")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
