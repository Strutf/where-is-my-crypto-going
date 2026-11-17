"""Treino mensal dos modelos publicados, com avaliação e trava de publicação.

Dois conjuntos de modelos, escolhidos na comparação de 17/11/2026 (research/results/):
- **direção:** gradient boosting só com as variáveis de preço (as outras não acrescentaram nada);
- **volatilidade:** gradient boosting com preço, estado do mercado e as volatilidades passadas.

Cada treino faz duas coisas:
1. **avaliação:** o mesmo procedimento da publicação, repetido mês a mês desde `EVAL_START` (treina
   nos 3 anos anteriores a cada mês, testa no mês). Os resultados de cada mês ficam no relatório,
   junto com os das referências (sempre alta, inverter, persistência, HAR);
2. **modelo publicado:** treinado nos 3 anos mais recentes, com tudo o que já tem alvo conhecido.

Trava (`compare`): o modelo novo só substitui o atual se, nos meses avaliados pelos dois, o acerto
da direção não cair mais que `ACCURACY_TOLERANCE` e o erro da volatilidade não subir mais que
`VOLATILITY_TOLERANCE`. Como o procedimento é o mesmo, diferenças só aparecem quando o código, as
variáveis ou os dados mudam; é isso que a trava protege.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from wimcg.ml import evaluate, features, models, volatility
from wimcg.ml.models import GradientBoosting

log = logging.getLogger(__name__)

EVAL_START = "2019-01-01"
ACCURACY_TOLERANCE = 0.005  # meio ponto percentual
VOLATILITY_TOLERANCE = 1.02  # erro até 2% maior


def feature_columns(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    return {"direction": groups["price"], "volatility": groups["price"] + groups["market"]}


def load_table(store) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Tabela de treino completa a partir do armazenamento (no Actions, o Hugging Face)."""
    from wimcg.cli import load_dataset

    table, groups = features.build(
        load_dataset(store, "btc-usd-1h"),
        load_dataset(store, "market-state"),
        load_dataset(store, "fear-greed"),
        load_dataset(store, "gkg-hourly"),
    )
    return volatility.add_columns(table), groups


def _month_rows(direction: pd.DataFrame, vols: dict[str, pd.DataFrame], table: pd.DataFrame) -> list[dict]:
    """Somas por (mês, horizonte): com elas dá para recalcular qualquer medida num conjunto de meses."""
    rows = []
    d = direction.assign(month=direction["time"].dt.strftime("%Y-%m"))
    for (month, h), g in d.groupby(["month", "horizon"]):
        up = (g["ret"] > 0).to_numpy()
        past = table[f"ret_{h}h"].reindex(pd.DatetimeIndex(g["time"])).to_numpy()
        prob = g["prob_up"].to_numpy()
        rows.append(
            {
                "month": month,
                "horizon_hours": int(h),
                "kind": "direction",
                "n": len(g),
                "hits": int(np.sum((prob > 0.5) == up)),
                "always_up_hits": int(np.sum(up)),
                "reversal_hits": int(np.sum((past < 0) == up)),
                "brier": float(np.sum((prob - up) ** 2)),
                "brier_climatology": float(np.sum((g["climatology"].to_numpy() - up) ** 2)),
                "abs_error_return": float(np.sum(np.abs(g["pred_ret"] - g["ret"]))),
                "abs_return": float(np.sum(np.abs(g["ret"]))),
            }
        )
    merged = None
    for name, p in vols.items():
        p = p.rename(columns={"pred_vol": name})
        merged = p if merged is None else merged.merge(p[["time", "horizon", name]], on=["time", "horizon"])
    merged["month"] = merged["time"].dt.strftime("%Y-%m")
    for (month, h), g in merged.groupby(["month", "horizon"]):
        row = {"month": month, "horizon_hours": int(h), "kind": "volatility", "n": len(g)}
        for name in vols:
            row[f"abs_error_{name}"] = float(np.sum(np.abs(g[name] - g["vol"])))
        rows.append(row)
    return rows


def totals(months: list[dict], kind: str, h: int, keep: set[str] | None = None) -> dict:
    """Medidas de um horizonte somando os meses (todos, ou só os de `keep`)."""
    sel = [m for m in months if m["kind"] == kind and m["horizon_hours"] == h and (keep is None or m["month"] in keep)]
    n = sum(m["n"] for m in sel)
    if not n:
        return {"n": 0}

    def s(key: str) -> float:
        return sum(m[key] for m in sel)

    if kind == "direction":
        return {
            "n": n,
            "accuracy": s("hits") / n,
            "always_up": s("always_up_hits") / n,
            "reversal": s("reversal_hits") / n,
            "brier_skill": 1 - s("brier") / s("brier_climatology"),
            "return_error_skill": 1 - s("abs_error_return") / s("abs_return"),
        }
    return {"n": n, **{k.removeprefix("abs_error_") + "_mae": s(k) / n for k in sel[0] if k.startswith("abs_error_")}}


def evaluate_monthly(table: pd.DataFrame, groups: dict[str, list[str]], first: str, last: str) -> list[dict]:
    cols = feature_columns(groups)
    direction = evaluate.walk_forward(table, cols["direction"], "gb", first, last)
    vols = {
        name: volatility.walk_forward(table, cols["volatility"], name, first, last, evaluate.TRAIN_YEARS, evaluate.PURGE)
        for name in ("gb", "har", "persistence")
    }
    return _month_rows(direction, vols, table)


def fit_final(table: pd.DataFrame, groups: dict[str, list[str]]) -> dict:
    """Modelos publicados: os 3 anos mais recentes com alvo conhecido."""
    cols = feature_columns(groups)
    recent = table[table.index >= table.index.max() - pd.DateOffset(years=evaluate.TRAIN_YEARS)]
    direction = GradientBoosting()
    direction.fit(recent, cols["direction"], recent)
    vol = volatility.GradientBoosting()
    vol.fit(recent, cols["volatility"])
    return {"direction": direction, "volatility": vol, "train_rows": len(recent), "train_start": str(recent.index.min())}


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def code_version() -> dict:
    commit = os.environ.get("GITHUB_SHA") or _git("rev-parse", "HEAD")
    status = None if os.environ.get("GITHUB_SHA") else _git("status", "--porcelain", "--untracked-files=no")
    return {"commit": commit, "uncommitted_changes": bool(status), "repository": "https://github.com/Strutf/where-is-my-crypto-going"}


def train(table: pd.DataFrame, groups: dict[str, list[str]], last_test: str, evaluate_from: str = EVAL_START) -> tuple[dict, dict]:
    """Devolve (pacote com os modelos, relatório)."""
    import sklearn

    months = evaluate_monthly(table, groups, evaluate_from, last_test)
    fitted = fit_final(table, groups)
    now = datetime.now(UTC)
    version = f"v{features.FEATURE_VERSION}-{now:%Y%m%d}"
    cols = feature_columns(groups)
    summary = {
        "model_version": version,
        "feature_version": features.FEATURE_VERSION,
        "trained_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_end": str(table.index.max()),
        "published_model": {"train_start": fitted["train_start"], "train_rows": fitted["train_rows"], "train_years": evaluate.TRAIN_YEARS},
        "features": cols,
        "evaluation": {
            "method": f"mês a mês desde {evaluate_from}: treino nos {evaluate.TRAIN_YEARS} anos anteriores, sem as últimas "
            f"{int(evaluate.PURGE / pd.Timedelta(hours=1))} h",
            "period": [months[0]["month"], months[-1]["month"]] if months else None,
            "direction": {str(h): totals(months, "direction", h) for h in features.HORIZONS},
            "volatility": {str(h): totals(months, "volatility", h) for h in features.HORIZONS},
            "months": months,
        },
        "reproducibility": {
            "code": code_version(),
            "hyperparameters": {"direction": models.GB_PARAMS, "volatility": volatility.GB_PARAMS},
            "environment": {"python": platform.python_version(), "scikit-learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
        },
    }
    bundle = {"model_version": version, "features": cols, "direction": fitted["direction"], "volatility": fitted["volatility"]}
    return bundle, summary


def compare(new: dict, old: dict) -> tuple[bool, str]:
    """Trava de publicação: compara os dois relatórios nos meses avaliados pelos dois."""
    new_m, old_m = new["evaluation"]["months"], old["evaluation"]["months"]
    common = {m["month"] for m in new_m} & {m["month"] for m in old_m}
    if not common:
        return True, "nenhum mês avaliado em comum com o modelo atual"
    acc_drop, vol_ratio = [], []
    for h in features.HORIZONS:
        a, b = totals(new_m, "direction", h, common), totals(old_m, "direction", h, common)
        if a["n"] and b["n"]:
            acc_drop.append(b["accuracy"] - a["accuracy"])
        a, b = totals(new_m, "volatility", h, common), totals(old_m, "volatility", h, common)
        if a["n"] and b["n"]:
            vol_ratio.append(a["gb_mae"] / b["gb_mae"])
    drop, ratio = float(np.mean(acc_drop)), float(np.mean(vol_ratio))
    ok = drop <= ACCURACY_TOLERANCE and ratio <= VOLATILITY_TOLERANCE
    return ok, f"{len(common)} meses em comum: acerto {-drop * 100:+.2f} p.p., erro da volatilidade {ratio:.3f}x o atual"
