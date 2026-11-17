"""Previsões ao vivo e verificação, como no Future Space.

**Modelos no armazenamento:**
- `models/<versão>/bundle.joblib` e `models/<versão>/report.json`: modelos e relatório de cada versão;
- `models/current.json`: a versão em uso;
- `models/index.json`: todos os treinos, publicados ou recusados pela trava, com as medidas;
- `models/rejected/<versão>-<horário>/report.json`: relatório dos treinos recusados.

**Previsão** (a cada coleta): para a última hora fechada, a direção (com a probabilidade), o retorno
e a volatilidade em 1, 4 e 24 h, mais as referências conhecidas no mesmo instante (inverter a última
direção e a persistência da volatilidade). Cada hora é prevista uma única vez: a tabela
`predictions` nunca é reescrita.

**Verificação** (a cada coleta): cada previsão cujo resultado já existe é comparada com o que
aconteceu e vai para a tabela `verification`. Um resumo dos últimos 7 e 30 dias e de todo o
histórico vai para `json/verification/summary.json`, lido pela API e pelo site.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from wimcg.ml import features, volatility
from wimcg.partitions import format_time, manifest_key, parse_time
from wimcg.storage import Storage

log = logging.getLogger(__name__)

MODELS_POINTER = "models/current.json"
MODELS_INDEX = "models/index.json"
SUMMARY_KEY = "json/verification/summary.json"
HOUR = pd.Timedelta(hours=1)
# Histórico lido para montar as variáveis de uma hora: a maior janela (720 h) mais o desvio do
# volume em relação aos 30 dias anteriores, com folga.
HISTORY = timedelta(days=75)
# Sem uma vela de 1 hora nova há mais que isso, a previsão não é emitida (dados parados).
MAX_STALENESS = timedelta(hours=3)
WINDOWS_DAYS = (7, 30)


class NoModelPublished(RuntimeError):
    pass


def _read_json(store: Storage, key: str):
    raw = store.read(key)
    return json.loads(raw) if raw else None


def _write_json(store: Storage, key: str, payload) -> None:
    store.write(key, json.dumps(payload, indent=1, ensure_ascii=False).encode())


def _index_entry(summary: dict, status: str, report_key: str, reason: str | None) -> dict:
    ev = summary["evaluation"]
    return {
        "id": report_key.split("/")[-2],
        "model_version": summary["model_version"],
        "status": status,
        "reason": reason,
        "trained_at": summary["trained_at"],
        "data_end": summary["data_end"],
        "code_commit": summary["reproducibility"]["code"]["commit"],
        "data_revision": (summary["reproducibility"].get("data") or {}).get("revision"),
        "report": report_key,
        "evaluation_period": ev["period"],
        "direction": ev["direction"],
        "volatility": ev["volatility"],
    }


def _record(store: Storage, entry: dict) -> None:
    index = _read_json(store, MODELS_INDEX) or {"models": []}
    index["models"] = [m for m in index["models"] if m["id"] != entry["id"]] + [entry]
    if entry["status"] == "published":
        index["current"] = entry["model_version"]
    _write_json(store, MODELS_INDEX, index)


def _public_report(summary: dict) -> dict:
    """O relatório sem as somas por mês, que só servem à trava (ficam no relatório completo)."""
    out = json.loads(json.dumps(summary))
    out["evaluation"].pop("months", None)
    return out


def publish(store: Storage, bundle_bytes: bytes, summary: dict) -> None:
    base = f"models/{summary['model_version']}"
    store.write(f"{base}/bundle.joblib", bundle_bytes)
    _write_json(store, f"{base}/report.json", summary)
    _record(store, _index_entry(summary, "published", f"{base}/report.json", None))
    _write_json(store, MODELS_POINTER, {"model_version": summary["model_version"], "bundle": f"{base}/bundle.joblib", "report": f"{base}/report.json", "summary": _public_report(summary)})


def record_rejected(store: Storage, summary: dict, reason: str) -> None:
    stamp = summary["trained_at"].replace("-", "").replace(":", "")
    key = f"models/rejected/{summary['model_version']}-{stamp}/report.json"
    _write_json(store, key, summary)
    _record(store, _index_entry(summary, "rejected", key, reason))


def current_report(store: Storage) -> dict | None:
    pointer = _read_json(store, MODELS_POINTER)
    return _read_json(store, pointer["report"]) if pointer else None


def load_bundle(store: Storage) -> dict:
    import joblib

    pointer = _read_json(store, MODELS_POINTER)
    if not pointer:
        raise NoModelPublished("nenhum modelo publicado; rode o workflow train")
    # Arquivos joblib executam código ao carregar: só carregamos do nosso próprio armazenamento.
    return joblib.load(io.BytesIO(store.read(pointer["bundle"])))


def _last_time(store: Storage, dataset_id: str) -> pd.Timestamp | None:
    raw = _read_json(store, manifest_key(dataset_id))
    return pd.Timestamp(parse_time(raw["end"])) if raw and raw.get("end") else None


def recent_table(store: Storage, issue: pd.Timestamp) -> pd.DataFrame:
    """A tabela de variáveis com o histórico necessário para a hora `issue` (mesma função do treino)."""
    from wimcg.cli import load_dataset

    start = (issue - HISTORY).to_pydatetime()
    table, _ = features.build(
        load_dataset(store, "btc-usd-1h", start=start),
        load_dataset(store, "market-state", start=start),
        load_dataset(store, "fear-greed"),
        load_dataset(store, "gkg-hourly", start=start),
    )
    return volatility.add_columns(table)


def predict(store: Storage, now: datetime | None = None) -> pd.DataFrame:
    """Previsões da última hora fechada; vazio se ela já foi prevista."""
    now = pd.Timestamp(now or datetime.now(UTC))
    issue = _last_time(store, "btc-usd-1h")  # início da última hora fechada
    if issue is None:
        raise RuntimeError("sem velas de 1 hora")
    if now - (issue + HOUR) > MAX_STALENESS:
        raise RuntimeError(f"última vela de 1 hora em {issue:%Y-%m-%d %H}h UTC; previsão não emitida com dados parados")
    last = _last_time(store, "predictions")
    if last is not None and last >= issue + HOUR:
        return pd.DataFrame()  # esta hora já foi prevista: a tabela nunca é reescrita

    bundle = load_bundle(store)
    table = recent_table(store, issue)
    if issue not in table.index:
        raise RuntimeError(f"hora {issue} sem vela na tabela de variáveis")
    row = table.loc[[issue]]
    direction = bundle["direction"].predict(row)
    vol = bundle["volatility"].predict(row)
    rows = []
    for h in features.HORIZONS:
        prob, ret = float(direction[h][0][0]), float(direction[h][1][0])
        past = float(row[f"ret_{h}h"].iloc[0])
        rows.append(
            {
                "time": issue + HOUR,
                "horizon_hours": h,
                "target_time": issue + HOUR + pd.Timedelta(hours=h),
                "base_price": float(row["close"].iloc[0]),
                "prob_up": round(prob, 4),
                "direction": "up" if prob > 0.5 else "down",
                "pred_return_pct": round((np.exp(ret) - 1) * 100, 4),
                "pred_volatility_pct": round(float(vol[h][0]), 4),
                "reversal_direction": "up" if past < 0 else "down",
                "persistence_volatility_pct": round(float(row[f"rv_past_{h}h"].iloc[0]) * np.sqrt(h), 4),
                "model_version": bundle["model_version"],
            }
        )
    return pd.DataFrame(rows)


def verify(store: Storage, now: datetime | None = None, days: int = 35) -> pd.DataFrame:
    """Compara as previsões dos últimos `days` dias com o que aconteceu depois."""
    from wimcg.cli import load_dataset

    now = pd.Timestamp(now or datetime.now(UTC))
    start = (now - pd.Timedelta(days=days)).to_pydatetime()
    preds = load_dataset(store, "predictions", start=start)
    if preds.empty:
        return pd.DataFrame()
    hourly = load_dataset(store, "btc-usd-1h", start=start - timedelta(days=2), columns=["time", "close"])
    close = hourly.set_index("time")["close"].sort_index()
    close = close.reindex(pd.date_range(close.index[0], close.index[-1], freq="h")).ffill(limit=features.MAX_FILL_HOURS)
    log_close = np.log(close)
    r2 = log_close.diff() ** 2

    rows = []
    for p in preds.itertuples():
        issue = p.time - HOUR  # hora de base: o preço de partida é o fechamento dela
        target = issue + pd.Timedelta(hours=int(p.horizon_hours))
        if target not in close.index or pd.isna(close.get(target)) or pd.isna(close.get(issue)):
            continue  # resultado ainda não existe (ou faltou vela)
        window = r2[(r2.index > issue) & (r2.index <= target)]
        if window.isna().any() or len(window) != p.horizon_hours:
            continue
        observed = float(close[target] / close[issue] - 1) * 100
        observed_vol = float(np.sqrt(window.sum())) * 100
        observed_dir = "up" if observed > 0 else "down"
        rows.append(
            {
                "time": p.time,
                "horizon_hours": int(p.horizon_hours),
                "target_time": p.target_time,
                "base_price": float(close[issue]),
                "observed_price": float(close[target]),
                "direction": p.direction,
                "prob_up": p.prob_up,
                "observed_direction": observed_dir,
                "hit": p.direction == observed_dir,
                "always_up_hit": observed_dir == "up",
                "reversal_hit": p.reversal_direction == observed_dir,
                "pred_return_pct": p.pred_return_pct,
                "observed_return_pct": round(observed, 4),
                "pred_volatility_pct": p.pred_volatility_pct,
                "observed_volatility_pct": round(observed_vol, 4),
                "persistence_volatility_pct": p.persistence_volatility_pct,
                "model_version": p.model_version,
            }
        )
    return pd.DataFrame(rows)


def _group_summary(g: pd.DataFrame) -> dict:
    def r(x: float) -> float:
        return round(float(x), 4)

    return {
        "n": len(g),
        "accuracy": r(g["hit"].mean()),
        "always_up_accuracy": r(g["always_up_hit"].mean()),
        "reversal_accuracy": r(g["reversal_hit"].mean()),
        "return_mae_pct": r((g["pred_return_pct"] - g["observed_return_pct"]).abs().mean()),
        "return_mae_zero_pct": r(g["observed_return_pct"].abs().mean()),
        "volatility_mae_pct": r((g["pred_volatility_pct"] - g["observed_volatility_pct"]).abs().mean()),
        "volatility_mae_persistence_pct": r((g["persistence_volatility_pct"] - g["observed_volatility_pct"]).abs().mean()),
    }


def summarize(rows: pd.DataFrame, now: datetime) -> dict:
    """Medidas por horizonte nos últimos 7 e 30 dias e em todo o histórico de verificação."""
    now = pd.Timestamp(now)
    windows = {}
    for label, since in [*[(f"{d}d", now - pd.Timedelta(days=d)) for d in WINDOWS_DAYS], ("all", None)]:
        recent = rows if since is None else rows[rows["target_time"] >= since]
        windows[label] = [{"horizon_hours": int(h), **_group_summary(g)} for h, g in recent.groupby("horizon_hours")]
    first = rows["time"].min() if not rows.empty else None
    return {"updated_at": format_time(now), "since": format_time(first) if first is not None else None, "windows": windows, "disclaimer": "Projeto de estudo e pesquisa. Não é recomendação de investimento."}
