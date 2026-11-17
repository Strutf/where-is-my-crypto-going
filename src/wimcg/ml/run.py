"""Rodadas da comparação e relatório.

`run` treina e avalia cada combinação (modelo, conjunto de variáveis, semente) em paralelo e guarda
as previsões fora da amostra em `data/ml/preds/`. `report` junta tudo em `research/results/`:
um JSON com todos os números e um resumo em Markdown.

As redes neurais variam com a semente aleatória: o relatório usa a média das probabilidades e dos
retornos previstos das sementes rodadas (um pequeno conjunto), e o gradient boosting, que é
determinístico, roda uma vez só.
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from wimcg.ml import data, evaluate, features

log = logging.getLogger(__name__)

TABLE = Path("data/ml/table.parquet")
GROUPS_FILE = Path("data/ml/groups.json")
PREDS = Path("data/ml/preds")
RESULTS = Path("research/results")
FIRST_TEST = "2019-01-01"
# Atalhos dos conjuntos cumulativos de variáveis (ver features.feature_sets).
SETS = {"price": "price", "market": "price+market", "sentiment": "price+market+sentiment", "all": "price+market+sentiment+news"}


def last_full_month(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return (pd.Timestamp(now).tz_convert("UTC").floor("D").replace(day=1) - pd.offsets.MonthBegin(1)).strftime("%Y-%m-%d")


def build_table() -> tuple[pd.DataFrame, dict[str, list[str]]]:
    table, groups = features.build(data.load("btc-usd-1h"), data.load("market-state"), data.load("fear-greed"), data.load("gkg-hourly"))
    TABLE.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(TABLE)
    GROUPS_FILE.write_text(json.dumps({"groups": groups, "revision": data.revision()}, indent=1))
    return table, groups


def _job(model: str, set_name: str, seed: int, first: str, last: str, threads: int) -> tuple[str, float]:
    # Um núcleo por rodada. Com mais, as rodadas em paralelo disputam o processador e ficam muito
    # mais lentas (medido em 17/11/2026 num Apple M5: o gb leva 3,4 s por mês sozinho com 1 núcleo,
    # mas não terminou em 35 min com 5 rodadas de 2 núcleos; a gru não acelera com 4 núcleos).
    import sklearn.ensemble  # noqa: F401  (carrega o OpenMP do scikit-learn antes de limitá-lo)
    import torch
    from threadpoolctl import threadpool_limits

    torch.set_num_threads(threads)
    threadpool_limits(threads)
    table = pd.read_parquet(TABLE)
    groups = json.loads(GROUPS_FILE.read_text())["groups"]
    cols = features.feature_sets(groups)[SETS[set_name]]
    t0 = time.monotonic()
    preds = evaluate.walk_forward(table, cols, model, first, last, seed=seed)
    out = PREDS / f"{model}__{set_name}__s{seed}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    preds.to_parquet(out)
    return out.name, time.monotonic() - t0


def run(models: list[str], sets: list[str], seeds: int, first: str, last: str, jobs: int, threads: int = 1, redo: bool = False) -> None:
    combos = [(m, s, seed) for m in models for s in sets for seed in (range(1) if m == "gb" else range(seeds))]
    done = [c for c in combos if (PREDS / f"{c[0]}__{c[1]}__s{c[2]}.parquet").exists()]
    if done and not redo:
        # Retoma de onde parou. As previsões guardadas valem para o mesmo período e o mesmo código.
        log.info("%d rodadas já feitas (use --redo para refazer)", len(done))
        combos = [c for c in combos if c not in done]
    log.info("%d rodadas, de %s a %s, %d em paralelo", len(combos), first, last, jobs)
    # O limite de núcleos vai pelo ambiente, que os processos novos herdam: as bibliotecas de
    # paralelismo leem essas variáveis ao carregar, antes de qualquer código nosso.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[var] = str(threads)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_job, *c, first, last, threads): c for c in combos}
        for fut in as_completed(futures):
            name, seconds = fut.result()
            log.info("%s: %.0f s", name, seconds)


def _load_runs() -> dict[tuple[str, str], pd.DataFrame]:
    """Previsões por (modelo, conjunto), com a média das sementes."""
    runs: dict[tuple[str, str], list[pd.DataFrame]] = {}
    for f in sorted(PREDS.glob("*.parquet")):
        model, set_name, _ = f.stem.split("__")
        runs.setdefault((model, set_name), []).append(pd.read_parquet(f))
    out = {}
    for key, frames in runs.items():
        base = frames[0].copy()
        for col in ("prob_up", "pred_ret"):
            base[col] = sum(f[col].to_numpy() for f in frames) / len(frames)
        base.attrs["seeds"] = len(frames)
        out[key] = base
    return out


def report() -> dict:
    runs = _load_runs()
    meta = json.loads(GROUPS_FILE.read_text())
    table = pd.read_parquet(TABLE)
    results = []
    for (model, set_name), preds in sorted(runs.items()):
        for h in features.HORIZONS:
            p = preds[preds["horizon"] == h]
            # Variação das últimas h horas, para a referência "inverter".
            p = p.assign(past_ret=table[f"ret_{h}h"].reindex(pd.DatetimeIndex(p["time"])).to_numpy())
            results.append(
                {
                    "model": model,
                    "features": SETS[set_name],
                    "horizon_hours": h,
                    "seeds": preds.attrs["seeds"],
                    "test_start": str(p["time"].min()),
                    "test_end": str(p["time"].max()),
                    **evaluate.summarize(p),
                    "mean_abs_bp": float(p["ret"].abs().mean() * 1e4),
                    "slices": evaluate.slices(p),
                    "calibration": evaluate.calibration(p),
                }
            )
    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset_revision": meta.get("revision"),
        "method": {
            "train_years": evaluate.TRAIN_YEARS,
            "purge_hours": int(evaluate.PURGE / pd.Timedelta(hours=1)),
            "bootstrap": {"block_hours": evaluate.BLOCK_HOURS, "resamples": evaluate.BOOTSTRAP},
        },
        "results": results,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "direction_eval.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    (RESULTS / "direction_eval.md").write_text(markdown(payload))
    return payload


def _ci(m: dict, pct: bool = True, signed: bool = False) -> str:
    f = (lambda x: f"{x * 100:+.1f}") if signed else ((lambda x: f"{x * 100:.1f}") if pct else (lambda x: f"{x:+.2f}"))
    return f"{f(m['value'])} [{f(m['ci95'][0])}, {f(m['ci95'][1])}]"


def markdown(payload: dict) -> str:
    rows = payload["results"]
    first = rows[0]
    lines = [
        "# Direção do Bitcoin: comparação dos modelos",
        "",
        (f"Gerado por `wimcg ml-report` em {payload['generated_at']}, com o dataset na revisão "
        f"`{(payload['dataset_revision'] or '?')[:10]}`. Teste fora da amostra, mês a mês, de "
        f"{first['test_start'][:7]} a {first['test_end'][:7]}: treino nos {payload['method']['train_years']} anos "
        f"anteriores, sem as últimas {payload['method']['purge_hours']} h antes de cada mês. Intervalos de 95% por "
        f"bootstrap em blocos de {payload['method']['bootstrap']['block_hours']} h."),
        "",
        "**Não é recomendação de investimento.**",
        "",
        "Colunas:",
        "",
        "- **acerto:** % das horas em que a direção prevista (probabilidade > 50%) aconteceu;",
        "- **vs sempre alta:** acerto menos o de quem sempre diz que sobe, em pontos percentuais;",
        "- **vs inverter:** acerto menos o de quem diz o contrário do que aconteceu nas últimas *h* horas;",
        ("- **retorno (pb):** retorno médio por previsão na direção prevista, em pontos-base (0,01%), antes de "
        "qualquer custo de negociação. Mede o tamanho do acerto; não é estratégia;"),
        "- **Brier skill:** ganho da probabilidade sobre a taxa de alta da janela de treino (0 = igual, > 0 = melhor);",
        "- **AUC:** 0,5 = não separa altas de baixas;",
        "- **erro do retorno:** ganho sobre prever retorno zero (passeio aleatório), pelo erro absoluto médio.",
        "",
    ]
    for h in features.HORIZONS:
        lines += [
            f"## Horizonte de {h} h",
            "",
            "| Modelo | Variáveis | Sementes | Acerto (%) | vs sempre alta (p.p.) | vs inverter (p.p.) | Brier skill (%) | AUC | Erro do retorno (%) | Retorno (pb) |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in (r for r in rows if r["horizon_hours"] == h):
            lines.append(
                f"| {r['model']} | {r['features']} | {r['seeds']} | {_ci(r['accuracy'])} | {_ci(r['accuracy_vs_always_up'], signed=True)} | "
                f"{_ci(r['accuracy_vs_reversal'], signed=True)} | {_ci(r['brier_skill'], signed=True)} | {r['auc']:.3f} | "
                f"{_ci(r['mae_skill'], signed=True)} | {_ci(r['return_bp'], pct=False)} |"
            )
        ref = next(r for r in rows if r["horizon_hours"] == h)
        lines += [
            "",
            (f"Neste horizonte, sempre alta acerta {ref['always_up']['value'] * 100:.1f}% e inverter acerta "
            f"{ref['reversal']['value'] * 100:.1f}%. A variação média, em módulo, é de {ref['mean_abs_bp']:.0f} pb."),
            "",
        ]
    return "\n".join(lines)
