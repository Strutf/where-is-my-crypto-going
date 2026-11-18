"""Avaliação andando para a frente e medidas, sempre comparadas com referências simples.

Para cada mês de teste *m*: treina com as linhas dos `TRAIN_YEARS` anos anteriores e testa em
todas as horas de *m*. Uma linha de treino só entra se o alvo dela (até 24 h à frente) já era
conhecido no início de *m*: as últimas 24 h antes do mês ficam de fora (purga).

Referências:
- **direção:** 50%, "sempre alta" (o acerto de quem sempre diz que sobe, no próprio teste) e
  "inverter" (dizer o contrário do que aconteceu nas últimas *h* horas). A última foi incluída
  depois do primeiro relatório (17/11/2026): sozinha, ela já acertava 53,1% em 1 h, perto dos
  54% dos modelos;
- **probabilidade:** a taxa de alta da janela de treino (climatologia), pelo escore de Brier;
- **retorno:** prever retorno zero (passeio aleatório), pelo erro absoluto médio.

Intervalos de confiança: bootstrap por blocos de uma semana (168 h), que respeita a dependência
entre horas vizinhas e a sobreposição dos alvos de 4 h e 24 h.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from wimcg.ml.features import HORIZONS
from wimcg.ml.models import MODELS

log = logging.getLogger(__name__)

TRAIN_YEARS = 3
PURGE = pd.Timedelta(hours=max(HORIZONS))
BLOCK_HOURS = 168
BOOTSTRAP = 500


def months(first: str, last: str) -> pd.DatetimeIndex:
    return pd.date_range(first, last, freq="MS", tz="UTC")


def walk_forward(table: pd.DataFrame, features: list[str], model_name: str, first: str, last: str, seed: int = 0) -> pd.DataFrame:
    """Previsões fora da amostra, mês a mês. Uma linha por (hora, horizonte)."""
    model = MODELS[model_name](seed=seed)
    out = []
    for start in months(first, last):
        end = start + pd.offsets.MonthBegin(1)
        train = table[(table.index >= start - pd.DateOffset(years=TRAIN_YEARS)) & (table.index < start - PURGE)]
        test = table[(table.index >= start) & (table.index < end)]
        if test.empty:
            continue
        t0 = time.monotonic()
        model.fit(train, features, table)
        preds = model.predict(test)
        climatology = {h: float(np.nanmean(train[f"up_next_{h}h"])) for h in HORIZONS}
        for h, (prob, ret) in preds.items():
            out.append(
                pd.DataFrame(
                    {
                        "time": test.index,
                        "horizon": h,
                        "prob_up": prob,
                        "pred_ret": ret,
                        "climatology": climatology[h],
                        "ret": test[f"ret_next_{h}h"].to_numpy(),
                        "regime_bull": test["regime_bull"].to_numpy(),
                        "vol_percentile": test["vol_percentile"].to_numpy(),
                        # Escolha da regularização (só no gb_tuned), para ver se muda muito mês a mês.
                        "choice": (getattr(model, "choice", None) or {}).get(h),
                    }
                )
            )
        log.debug("%s %s: %d linhas de treino, %.1fs", model_name, f"{start:%Y-%m}", len(train), time.monotonic() - t0)
    preds = pd.concat(out, ignore_index=True)
    return preds[preds["ret"].notna()]


def _scores(g: pd.DataFrame) -> dict[str, float]:
    up = (g["ret"] > 0).to_numpy(float)
    prob = g["prob_up"].to_numpy()
    clim = g["climatology"].to_numpy()
    brier, brier_clim = np.mean((prob - up) ** 2), np.mean((clim - up) ** 2)
    mae, mae_zero = np.mean(np.abs(g["pred_ret"] - g["ret"])), np.mean(np.abs(g["ret"]))
    direction = np.where(prob > 0.5, 1.0, -1.0)
    out = {
        "accuracy": float(np.mean((prob > 0.5) == up)),
        "always_up": float(np.mean(up)),
        "brier_skill": float(1 - brier / brier_clim),
        "mae_skill": float(1 - mae / mae_zero),
        # Retorno médio de quem seguisse a direção prevista, em pontos-base, ANTES de qualquer custo.
        # Serve para medir se o acerto tem tamanho; não é uma estratégia.
        "return_bp": float(np.mean(direction * g["ret"]) * 1e4),
    }
    if "past_ret" in g:
        out["reversal"] = float(np.mean((g["past_ret"].to_numpy() < 0) == up))
    return out


def _auc(g: pd.DataFrame) -> float:
    from sklearn.metrics import roc_auc_score

    up = g["ret"] > 0
    return float(roc_auc_score(up, g["prob_up"])) if up.nunique() == 2 else np.nan


def summarize(preds: pd.DataFrame, seed: int = 0) -> dict:
    """Medidas de um modelo num horizonte, com intervalo de confiança de 95%."""
    g = preds.sort_values("time").reset_index(drop=True)
    point = _scores(g)
    blocks = (g["time"] - g["time"].iloc[0]) // pd.Timedelta(hours=BLOCK_HOURS)
    groups = [idx.to_numpy() for _, idx in g.groupby(blocks).groups.items()]
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(BOOTSTRAP):
        pick = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        boot.append(_scores(g.iloc[pick]))
    boot = pd.DataFrame(boot)
    result = {"n": len(g), "auc": _auc(g)}
    for k, v in point.items():
        lo, hi = np.percentile(boot[k], [2.5, 97.5])
        result[k] = {"value": v, "ci95": [float(lo), float(hi)]}
    # Ganhos sobre as referências também precisam de intervalo (a diferença, não cada um).
    for ref in ("always_up", "reversal"):
        if ref in point:
            diff = boot["accuracy"] - boot[ref]
            result[f"accuracy_vs_{ref}"] = {"value": point["accuracy"] - point[ref], "ci95": [float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5))]}
    return result


def slices(preds: pd.DataFrame) -> dict:
    """Acerto e taxa de alta por regime, por agitação do mercado e por ano."""
    g = preds.copy()
    g["hit"] = (g["prob_up"] > 0.5) == (g["ret"] > 0)
    g["up"] = g["ret"] > 0
    g["regime"] = g["regime_bull"].map({1.0: "bull", 0.0: "bear"}).fillna("?")
    g["agitation"] = pd.cut(g["vol_percentile"], [-1, 25, 75, 95, 101], labels=["calm", "normal", "volatile", "extreme"]).astype(str)
    g["year"] = g["time"].dt.year
    out = {}
    for key in ("regime", "agitation", "year"):
        t = g.groupby(key).agg(n=("hit", "size"), accuracy=("hit", "mean"), always_up=("up", "mean"))
        out[key] = {str(k): {c: (int(v) if c == "n" else round(float(v), 4)) for c, v in row.items()} for k, row in t.iterrows()}
    return out


def calibration(preds: pd.DataFrame, bins: int = 10) -> list[dict]:
    """Probabilidade prevista contra a frequência observada, por faixa de probabilidade."""
    g = preds.assign(up=preds["ret"] > 0, bin=pd.qcut(preds["prob_up"], bins, duplicates="drop"))
    t = g.groupby("bin", observed=True).agg(n=("up", "size"), predicted=("prob_up", "mean"), observed=("up", "mean"))
    return [{"n": int(r.n), "predicted": round(float(r.predicted), 4), "observed": round(float(r.observed), 4)} for r in t.itertuples()]


def _block_ci(stat, d: pd.DataFrame, seed: int = 0) -> list[float]:
    """Intervalo de 95% por bootstrap em blocos de BLOCK_HOURS para uma estatística da tabela."""
    d = d.sort_values("time").reset_index(drop=True)
    blocks = ((d["time"] - d["time"].iloc[0]) // pd.Timedelta(hours=BLOCK_HOURS)).to_numpy()
    groups = [np.flatnonzero(blocks == b) for b in np.unique(blocks)]
    rng = np.random.default_rng(seed)
    vals = [stat(d.iloc[np.concatenate([groups[k] for k in rng.integers(0, len(groups), len(groups))])]) for _ in range(BOOTSTRAP)]
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def research_slices(preds: pd.DataFrame, table: pd.DataFrame, h: int) -> dict:
    """Recortes da pesquisa de 18/11/2026 (registrados em docs/contexto.md antes de rodar).

    - seletivo: acerto e cobertura quando max(p, 1 − p) passa de um limiar, com a regra de inverter
      no mesmo subconjunto;
    - depois de um movimento grande: a última hora variou mais de 3 desvios-padrão dos retornos
      horários dos 7 dias anteriores (só o passado);
    - movimentos futuros grandes: |retorno futuro| > 2 × vol_24h × √h. Usa o futuro: é só descrição.
    """
    from wimcg.ml import intervals

    g = preds.copy()
    idx = pd.DatetimeIndex(g["time"])
    up = (g["ret"] > 0).to_numpy()
    hit = (g["prob_up"] > 0.5).to_numpy() == up
    rev = (table[f"ret_{h}h"].reindex(idx).to_numpy() < 0) == up
    conf = np.maximum(g["prob_up"], 1 - g["prob_up"]).to_numpy()
    out = {"selective": {}}
    for th in (0.52, 0.55, 0.60):
        m = conf > th
        out["selective"][str(th)] = {"coverage": float(m.mean()), "accuracy": float(hit[m].mean()) if m.any() else None, "reversal": float(rev[m].mean()) if m.any() else None}
    z = (table["ret_1h"] / table["vol_168h"]).reindex(idx).abs().to_numpy()
    m = z > 3
    out["after_big_move"] = {"coverage": float(m.mean()), "accuracy": float(hit[m].mean()) if m.any() else None, "reversal": float(rev[m].mean()) if m.any() else None}
    big = np.abs(g["ret"].to_numpy()) > 2 * table["vol_24h"].reindex(idx).to_numpy() * np.sqrt(h)
    out["big_future_descriptive"] = {"coverage": float(big.mean()), "accuracy": float(hit[big].mean()) if big.any() else None, "reversal": float(rev[big].mean()) if big.any() else None}

    # Calibração de 18/11/2026: λ dos 12 meses anteriores, de 2020-01 em diante.
    g["y"] = up.astype(float)
    parts = []
    for m0 in pd.date_range(pd.Timestamp("2020-01-01", tz="UTC"), g["time"].max(), freq="MS"):
        cur = g[(g["time"] >= m0) & (g["time"] < m0 + pd.offsets.MonthBegin(1))].copy()
        past = intervals.recent(g, m0)
        if cur.empty or len(past) < intervals.MIN_ROWS:
            continue
        lam = intervals.shrink_lambda(past["prob_up"], past["climatology"], past["y"])
        cur["prob_cal"] = intervals.calibrate(cur["prob_up"], cur["climatology"], lam)
        parts.append(cur)
    if parts:
        c = pd.concat(parts, ignore_index=True)

        def bss(d: pd.DataFrame, col: str) -> float:
            return float(1 - np.mean((d[col] - d["y"]) ** 2) / np.mean((d["climatology"] - d["y"]) ** 2))

        out["calibrated"] = {
            "from": "2020-01",
            "brier_skill_raw": bss(c, "prob_up"),
            "brier_skill_calibrated": bss(c, "prob_cal"),
            "diff_ci95": _block_ci(lambda d: bss(d, "prob_cal") - bss(d, "prob_up"), c),
        }
    if "choice" in g and g["choice"].notna().any():
        out["choices"] = g.groupby(g["time"].dt.strftime("%Y-%m"))["choice"].first().value_counts().to_dict()
    return out


def paired_diff(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Diferença de acerto e de Brier skill entre dois modelos nas mesmas horas (a − b), com IC."""
    m = a[["time", "prob_up", "ret", "climatology"]].merge(b[["time", "prob_up"]], on="time", suffixes=("", "_b"))
    m["y"] = (m["ret"] > 0).astype(float)

    def acc(d, col):
        return float(np.mean((d[col] > 0.5) == (d["y"] > 0.5)))

    def bss(d, col):
        return float(1 - np.mean((d[col] - d["y"]) ** 2) / np.mean((d["climatology"] - d["y"]) ** 2))

    return {
        "accuracy": {"value": acc(m, "prob_up") - acc(m, "prob_up_b"), "ci95": _block_ci(lambda d: acc(d, "prob_up") - acc(d, "prob_up_b"), m)},
        "brier_skill": {"value": bss(m, "prob_up") - bss(m, "prob_up_b"), "ci95": _block_ci(lambda d: bss(d, "prob_up") - bss(d, "prob_up_b"), m)},
    }
