"""Avaliação da faixa provável e da calibração (método registrado em docs/contexto.md, 18/11/2026).

Usa as previsões fora da amostra da comparação de 17/11, guardadas em data/ml/ por
`wimcg ml-eval` (direção: gb só preço) e pela avaliação da volatilidade (gb, HAR):
`data/ml/vol_gb.parquet` e `data/ml/vol_har.parquet`.

Uso: python research/interval_eval.py   (da raiz do repositório, com o ambiente ativado)
Saída: research/results/interval_eval.md e .json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from wimcg.ml import intervals as iv

ROOT = Path(__file__).resolve().parents[1]
ML = ROOT / "data" / "ml"
OUT = ROOT / "research" / "results"
FIRST = "2020-01-01"
LAST = "2026-10-01"
HORIZONS = (1, 4, 24)
BLOCK = pd.Timedelta(hours=168)
BOOT = 500
Z90 = 1.6449
Z50 = 0.6745


def months():
    return pd.date_range(FIRST, LAST, freq="MS", tz="UTC")


def load():
    table = pd.read_parquet(ML / "table.parquet")
    gb = pd.read_parquet(ML / "vol_gb.parquet")
    har = pd.read_parquet(ML / "vol_har.parquet").rename(columns={"pred_vol": "har_vol"})
    direction = pd.read_parquet(ML / "preds" / "gb__price__s0.parquet")
    return table, gb, har, direction


def interval_rows(table, gb, har, h):
    d = gb[gb["horizon"] == h].merge(har[har["horizon"] == h][["time", "har_vol"]], on="time")
    d["ret"] = table[f"ret_next_{h}h"].reindex(pd.DatetimeIndex(d["time"])).to_numpy() * 100
    d = d[np.isfinite(d["ret"])].sort_values("time").reset_index(drop=True)
    out = []
    for m in months():
        cur = d[(d["time"] >= m) & (d["time"] < m + pd.offsets.MonthBegin(1))].copy()
        past = iv.recent(d, m)
        if cur.empty or len(past) < iv.MIN_ROWS:
            continue
        qz = iv.z_quantiles(past["ret"].to_numpy(), past["pred_vol"].to_numpy())
        qr = {q: float(np.quantile(past["ret"], q)) for q in iv.QUANTILES}  # referência (a): sem escalar
        for q, b in iv.band(cur["pred_vol"], qz).items():
            cur[f"m{q}"] = b
        for q in iv.QUANTILES:
            cur[f"r{q}"] = qr[q]
            # Referência (b): Normal(0, σ do HAR).
            cur[f"n{q}"] = cur["har_vol"] * {0.05: -Z90, 0.25: -Z50, 0.75: Z50, 0.95: Z90}[q]
        out.append(cur)
    return pd.concat(out, ignore_index=True)


def interval_scores(d, prefix):
    y = d["ret"].to_numpy()
    lo90, hi90, lo50, hi50 = (d[f"{prefix}{q}"].to_numpy() for q in (0.05, 0.95, 0.25, 0.75))
    return {
        "coverage_90": float(np.mean((y >= lo90) & (y <= hi90))),
        "coverage_50": float(np.mean((y >= lo50) & (y <= hi50))),
        "width_90": float(np.mean(hi90 - lo90)),
        "width_50": float(np.mean(hi50 - lo50)),
        "pinball": float(np.mean([iv.pinball(y, d[f"{prefix}{q}"], q) for q in iv.QUANTILES])),
    }


def calibration_rows(direction, h):
    d = direction[direction["horizon"] == h].sort_values("time").reset_index(drop=True)
    d["y"] = (d["ret"] > 0).astype(float)
    out = []
    for m in months():
        cur = d[(d["time"] >= m) & (d["time"] < m + pd.offsets.MonthBegin(1))].copy()
        past = iv.recent(d, m)
        if cur.empty or len(past) < iv.MIN_ROWS:
            continue
        lam = iv.shrink_lambda(past["prob_up"], past["climatology"], past["y"])
        cur["prob_cal"] = iv.calibrate(cur["prob_up"], cur["climatology"], lam)
        cur["lam"] = lam
        out.append(cur)
    return pd.concat(out, ignore_index=True)


def brier_skill(p, c, y):
    return 1 - np.mean((p - y) ** 2) / np.mean((c - y) ** 2)


def calibration_scores(d, seed=0):
    p, pc, c, y = (d[k].to_numpy() for k in ("prob_up", "prob_cal", "climatology", "y"))
    point = {
        "bss_raw": brier_skill(p, c, y), "bss_cal": brier_skill(pc, c, y),
        "acc_raw": float(np.mean((p > 0.5) == y)), "acc_cal": float(np.mean((pc > 0.5) == y)),
    }
    blocks = ((d["time"] - d["time"].iloc[0]) // BLOCK).to_numpy()
    groups = [np.flatnonzero(blocks == b) for b in np.unique(blocks)]
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(BOOT):
        i = np.concatenate([groups[k] for k in rng.integers(0, len(groups), len(groups))])
        diffs.append(brier_skill(pc[i], c[i], y[i]) - brier_skill(p[i], c[i], y[i]))
    point["bss_diff_ci95"] = [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))]
    lams = d.groupby(d["time"].dt.strftime("%Y-%m"))["lam"].first()
    point["lambda"] = {"min": float(lams.min()), "median": float(lams.median()), "max": float(lams.max()), "last": float(lams.iloc[-1])}
    bins = pd.qcut(pc, 10, duplicates="drop")
    t = pd.DataFrame({"p": pc, "y": y, "b": bins}).groupby("b", observed=True).agg(predicted=("p", "mean"), observed=("y", "mean"), n=("y", "size"))
    point["calibration"] = [{k: round(float(v), 4) for k, v in r.items()} for _, r in t.iterrows()]
    return point


def main():
    table, gb, har, direction = load()
    res = {"period": [FIRST[:7], LAST[:7]], "intervals": {}, "calibration": {}}
    for h in HORIZONS:
        d = interval_rows(table, gb, har, h)
        res["intervals"][h] = {name: interval_scores(d, p) for name, p in (("model", "m"), ("fixed", "r"), ("normal_har", "n"))}
        y = d["ret"].to_numpy()
        inside = (y >= d["m0.05"]) & (y <= d["m0.95"])
        res["intervals"][h]["model_by_year"] = {int(k): round(float(v), 4) for k, v in inside.groupby(d["time"].dt.year).mean().items()}
        state = pd.cut(table["vol_percentile"].reindex(pd.DatetimeIndex(d["time"])).to_numpy(), [-1, 25, 75, 95, 101], labels=["calm", "normal", "volatile", "extreme"])
        res["intervals"][h]["model_by_state"] = {str(k): round(float(v), 4) for k, v in inside.groupby(state, observed=True).mean().items()}
        res["calibration"][h] = calibration_scores(calibration_rows(direction, h))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "interval_eval.json").write_text(json.dumps(res, indent=1))
    (OUT / "interval_eval.md").write_text(markdown(res))
    print(markdown(res))


def markdown(res):
    pc = lambda v: f"{v * 100:.1f}%"
    lines = [
        "# Faixa provável e calibração",
        "",
        (f"Gerado por `python research/interval_eval.py`. Período: {res['period'][0]} a {res['period'][1]}. Método e critérios "
        "registrados antes em `docs/contexto.md` (18/11/2026). **Não é recomendação de investimento.**"),
        "",
        "## Faixa provável (retorno em %, log)",
        "",
        "| Horizonte | Faixa | Cobertura 90% | Cobertura 50% | Largura 90% | Largura 50% | Pinball |",
        "|---|---|---|---|---|---|---|",
    ]
    names = {"model": "volatilidade prevista × z", "fixed": "fixa no mês (referência)", "normal_har": "Normal com HAR (referência)"}
    for h, r in res["intervals"].items():
        for k, label in names.items():
            s = r[k]
            lines.append(f"| {h} h | {label} | {pc(s['coverage_90'])} | {pc(s['coverage_50'])} | {s['width_90']:.2f} | {s['width_50']:.2f} | {s['pinball']:.4f} |")
    lines += ["", "Cobertura da faixa de 90% do modelo, por ano e por agitação do mercado:", ""]
    for h, r in res["intervals"].items():
        lines.append(f"- {h} h: " + ", ".join(f"{y}: {pc(v)}" for y, v in r["model_by_year"].items()) + " | " + ", ".join(f"{k}: {pc(v)}" for k, v in r["model_by_state"].items()))
    lines += ["", "## Calibração da probabilidade de alta", "", "| Horizonte | Brier skill sem | com | Diferença (IC 95%) | Acerto sem | com | λ (mín · mediana · máx · último) |", "|---|---|---|---|---|---|---|"]
    for h, s in res["calibration"].items():
        lam = s["lambda"]
        lines.append(
            f"| {h} h | {s['bss_raw'] * 100:+.2f}% | {s['bss_cal'] * 100:+.2f}% | {(s['bss_cal'] - s['bss_raw']) * 100:+.2f} [{s['bss_diff_ci95'][0] * 100:+.2f}, {s['bss_diff_ci95'][1] * 100:+.2f}] "
            f"| {pc(s['acc_raw'])} | {pc(s['acc_cal'])} | {lam['min']:.2f} · {lam['median']:.2f} · {lam['max']:.2f} · {lam['last']:.2f} |"
        )
    lines += ["", "Confiabilidade depois da calibração (probabilidade prevista → frequência observada, por decil):", ""]
    for h, s in res["calibration"].items():
        lines.append(f"- {h} h: " + ", ".join(f"{c['predicted']:.2f}→{c['observed']:.2f}" for c in s["calibration"]))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
