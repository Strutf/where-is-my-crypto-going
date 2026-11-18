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
    from threadpoolctl import threadpool_limits

    if model in ("mlp", "gru"):
        # Só as redes neurais usam o PyTorch, que fica fora das dependências do Actions.
        import torch

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


def report(name: str = "direction_eval", baseline: str | None = None) -> dict:
    """Junta as previsões guardadas. `baseline` (ex.: "gb_noes") liga a comparação pareada com ele."""
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
                    "research": evaluate.research_slices(p, table, h),
                }
            )
            if baseline and (baseline, set_name) in runs and model != baseline:
                b = runs[(baseline, set_name)]
                results[-1]["vs_baseline"] = {"baseline": baseline, **evaluate.paired_diff(p, b[b["horizon"] == h])}
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
    payload["variants_tested"] = sorted({f"{m}/{SETS[s_]}" for m, s_ in runs})
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    (RESULTS / f"{name}.md").write_text(markdown(payload))
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
        lines += research_markdown([r for r in rows if r["horizon_hours"] == h])
        lines += [
            "",
            (f"Neste horizonte, sempre alta acerta {ref['always_up']['value'] * 100:.1f}% e inverter acerta "
            f"{ref['reversal']['value'] * 100:.1f}%. A variação média, em módulo, é de {ref['mean_abs_bp']:.0f} pb."),
            "",
        ]
    return "\n".join(lines)


def research_markdown(rows: list[dict]) -> list[str]:
    """Tabelas dos recortes da pesquisa de 18/11/2026, quando existem."""
    rows = [r for r in rows if "research" in r]
    if not rows:
        return []
    pc = lambda v: "—" if v is None else f"{v * 100:.1f}"
    out = ["", "Recortes (acerto do modelo / da regra de inverter, % das horas):", "",
           "| Modelo | Variáveis | p > 0,52 | p > 0,55 | p > 0,60 | Depois de movimento grande | Movimento futuro grande (descritivo) |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        x = r["research"]
        cell = lambda d: f"{pc(d['accuracy'])} / {pc(d['reversal'])} ({pc(d['coverage'])})"
        out.append(f"| {r['model']} | {r['features']} | " + " | ".join(cell(x["selective"][k]) for k in ("0.52", "0.55", "0.6")) + f" | {cell(x['after_big_move'])} | {cell(x['big_future_descriptive'])} |")
    out += ["", "Brier skill sem e com a calibração de 18/11 (de 2020-01), e comparação pareada com a base:", "",
            "| Modelo | Variáveis | Brier skill sem | com | Diferença (IC 95%) | Acerto − base (IC 95%) | Brier skill − base (IC 95%) | Escolhas (gb_tuned) |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        c = r["research"].get("calibrated")
        vb = r.get("vs_baseline")
        f = lambda m: f"{m['value'] * 100:+.2f} [{m['ci95'][0] * 100:+.2f}, {m['ci95'][1] * 100:+.2f}]"
        cal = f"{c['brier_skill_raw'] * 100:+.2f}% | {c['brier_skill_calibrated'] * 100:+.2f}% | {(c['brier_skill_calibrated'] - c['brier_skill_raw']) * 100:+.2f} [{c['diff_ci95'][0] * 100:+.2f}, {c['diff_ci95'][1] * 100:+.2f}]" if c else "— | — | —"
        base = f"{f(vb['accuracy'])} | {f(vb['brier_skill'])}" if vb else "— | —"
        ch = ", ".join(f"{k}: {v}" for k, v in (r["research"].get("choices") or {}).items()) or "—"
        out.append(f"| {r['model']} | {r['features']} | {cal} | {base} | {ch} |")
    return out


def vol_eval(first: str, last: str) -> dict:
    """Volatilidade: persistência, HAR, gb e GARCH, mês a mês, com a diferença de erro e IC."""
    from wimcg.ml import volatility

    table = volatility.add_columns(pd.read_parquet(TABLE))
    groups = json.loads(GROUPS_FILE.read_text())["groups"]
    cols = groups["price"] + groups["market"]
    preds = {}
    for name in ("persistence", "har", "gb", "garch"):
        t0 = time.monotonic()
        preds[name] = volatility.walk_forward(table, cols, name, first, last, evaluate.TRAIN_YEARS, evaluate.PURGE)
        preds[name].to_parquet(TABLE.parent / f"vol_{name}.parquet")
        log.info("volatilidade %s: %.0f s", name, time.monotonic() - t0)
    out = {"period": [first[:7], last[:7]], "horizons": {}}
    for h in features.HORIZONS:
        merged = None
        for name, p in preds.items():
            q = p[p["horizon"] == h][["time", "pred_vol", "vol"]].rename(columns={"pred_vol": name})
            merged = q if merged is None else merged.merge(q[["time", name]], on="time")
        res = {"n": len(merged)}
        for name in preds:
            res[name] = {"mae": float((merged[name] - merged["vol"]).abs().mean())}
        for name in ("gb", "garch"):
            for ref in ("har", "persistence", "gb"):
                if name == ref:
                    continue
                stat = lambda d, a=name, b=ref: float((d[a] - d["vol"]).abs().mean() / (d[b] - d["vol"]).abs().mean() - 1)
                res[name][f"vs_{ref}"] = {"value": stat(merged), "ci95": evaluate._block_ci(stat, merged)}
        out["horizons"][str(h)] = res
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "volatility_eval.json").write_text(json.dumps(out, indent=1))
    lines = ["# Volatilidade: persistência, HAR, gradient boosting e GARCH", "",
             (f"Gerado por `wimcg ml-vol-eval`. Teste mês a mês de {out['period'][0]} a {out['period'][1]}, treino nos 3 anos anteriores. "
             "Erro absoluto médio da volatilidade das próximas h horas, em pontos percentuais; diferenças relativas com IC de 95% "
             "(bootstrap em blocos de 168 h). Negativo = erra menos."), "",
             "| Horizonte | Persistência | HAR | gb | GARCH | gb vs HAR | GARCH vs HAR | GARCH vs gb |", "|---|---|---|---|---|---|---|---|"]
    f = lambda m: f"{m['value'] * 100:+.1f}% [{m['ci95'][0] * 100:+.1f}, {m['ci95'][1] * 100:+.1f}]"
    for h, r in out["horizons"].items():
        lines.append(f"| {h} h | {r['persistence']['mae']:.3f} | {r['har']['mae']:.3f} | {r['gb']['mae']:.3f} | {r['garch']['mae']:.3f} | {f(r['gb']['vs_har'])} | {f(r['garch']['vs_har'])} | {f(r['garch']['vs_gb'])} |")
    (RESULTS / "volatility_eval.md").write_text("\n".join(lines) + "\n")
    return out
