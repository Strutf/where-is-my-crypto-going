"""Ciclo completo com dados sintéticos: treinar, publicar, prever uma vez por hora e verificar."""

import copy

import numpy as np
import pandas as pd
import pytest

from tests.test_ml import _inputs
from wimcg import catalog, market
from wimcg.cli import load_dataset, main
from wimcg.ml import serve
from wimcg.ml import train as train_mod
from wimcg.pipeline.writer import write_dataset
from wimcg.storage import LocalStorage

HOURS = 24 * 400


def _write(store, hourly, fear_greed, news):
    write_dataset(store, catalog.get("btc-usd-1h"), hourly)
    write_dataset(store, catalog.get("market-state"), market.state(hourly))
    write_dataset(store, catalog.get("fear-greed"), fear_greed)
    write_dataset(store, catalog.get("gkg-hourly"), news)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from wimcg.ml import models

    monkeypatch.setattr(models, "TUNE_GRID", [(20, 200), (40, 1000)])  # grade pequena: testes rápidos
    monkeypatch.setenv("FS_STORAGE", "local")
    monkeypatch.setenv("FS_DATA_DIR", str(tmp_path))
    store = LocalStorage(tmp_path)
    hourly, fear_greed, news = _inputs(7)
    # Guarda as últimas 30 horas para "acontecerem" depois da previsão.
    _write(store, hourly.iloc[:-30], fear_greed, news)
    store.later = hourly.iloc[-30:]
    return store


def test_train_predict_once_and_verify(store, monkeypatch):
    from wimcg.ml import intervals

    monkeypatch.setattr(intervals, "MIN_ROWS", 100)  # só 2 meses de avaliação nos dados sintéticos
    assert main(["train", "--publish", "--evaluate-from", "2024-12-01", "--last", "2025-01-01"]) == 0
    report = serve.current_report(store)
    assert report["evaluation"]["period"] == ["2024-12", "2025-01"]
    # Revisão 2: regularização escolhida por horizonte, registrada no relatório.
    assert report["model_version"].startswith("v1.2-")
    assert set(report["reproducibility"]["hyperparameters"]["direction"]["choice"]) == {"1", "4", "24"}
    assert set(report["evaluation"]["direction"]) == {"1", "4", "24"}

    last_hour = load_dataset(store, "btc-usd-1h")["time"].max()
    now = last_hour + pd.Timedelta(hours=1, minutes=5)
    rows = serve.predict(store, now=now)
    assert rows["time"].unique().tolist() == [last_hour + pd.Timedelta(hours=1)]
    assert rows["horizon_hours"].tolist() == [1, 4, 24]
    assert ((rows["prob_up"] >= 0) & (rows["prob_up"] <= 1)).all() and (rows["pred_volatility_pct"] > 0).all()
    # Faixa provável em preços, em volta do preço de partida, com a de 50% dentro da de 90%.
    assert (rows["range_low_90"] < rows["range_low_50"]).all() and (rows["range_low_50"] < rows["range_high_50"]).all()
    assert (rows["range_high_50"] < rows["range_high_90"]).all()
    assert (rows["range_low_90"] < rows["base_price"]).all() and (rows["base_price"] < rows["range_high_90"]).all()
    # Probabilidade publicada = a calibrada; a do modelo fica guardada ao lado.
    report = serve.current_report(store)
    for r in rows.itertuples():
        cal = report["calibration"][str(r.horizon_hours)]
        assert r.prob_up == pytest.approx(cal["climatology"] + cal["lambda"] * (r.prob_up_raw - cal["climatology"]), abs=1e-4)
    write_dataset(store, catalog.get("predictions"), rows)
    # A mesma hora não é prevista de novo (a tabela de previsões nunca é reescrita).
    assert serve.predict(store, now=now + pd.Timedelta(minutes=15)).empty

    # O tempo passa: chegam as velas seguintes, e as previsões vencidas são conferidas.
    write_dataset(store, catalog.get("btc-usd-1h"), store.later)
    checked = serve.verify(store, now=now + pd.Timedelta(days=2)).set_index("horizon_hours")
    assert sorted(checked.index) == [1, 4, 24]
    close = pd.concat([load_dataset(store, "btc-usd-1h")]).set_index("time")["close"]
    for h in (1, 4, 24):
        expected = (close[last_hour + pd.Timedelta(hours=h)] / close[last_hour] - 1) * 100
        assert checked.loc[h, "observed_return_pct"] == pytest.approx(expected, abs=1e-3)
        assert checked.loc[h, "hit"] == (checked.loc[h, "direction"] == ("up" if expected > 0 else "down"))
        final = close[last_hour + pd.Timedelta(hours=h)]
        assert checked.loc[h, "in_range_90"] == (checked.loc[h, "range_low_90"] <= final <= checked.loc[h, "range_high_90"])
    summary = serve.summarize(checked.reset_index(), now + pd.Timedelta(days=2))
    assert {g["horizon_hours"] for g in summary["windows"]["all"]} == {1, 4, 24}
    assert all(g["range_n"] == 1 and g["range_coverage_90"] in (0.0, 1.0) for g in summary["windows"]["all"])


def test_old_models_without_bands_still_predict(store, monkeypatch):
    """O modelo publicado antes de 18/11/2026 não tem faixa nem calibração: a previsão sai sem elas."""
    assert main(["train", "--publish", "--evaluate-from", "2024-12-01", "--last", "2025-01-01"]) == 0
    bundle = serve.load_bundle(store)
    old = {k: v for k, v in bundle.items() if k not in ("bands", "calibration")}
    monkeypatch.setattr(serve, "load_bundle", lambda _store: old)
    last_hour = load_dataset(store, "btc-usd-1h")["time"].max()
    rows = serve.predict(store, now=last_hour + pd.Timedelta(hours=1, minutes=5))
    assert (rows["prob_up"] == rows["prob_up_raw"]).all() and "range_low_90" not in rows


def test_stale_data_is_not_predicted(store):
    last_hour = load_dataset(store, "btc-usd-1h")["time"].max()
    with pytest.raises(RuntimeError, match="parados"):
        serve.predict(store, now=last_hour + pd.Timedelta(hours=6))


def test_live_features_match_the_training_table():
    """A hora prevista ao vivo, com só 75 dias de histórico, tem as mesmas variáveis do treino."""
    from wimcg.ml import features, volatility

    hourly, fear_greed, news = _inputs(8)
    full, groups = features.build(hourly, market.state(hourly), fear_greed, news)
    full = volatility.add_columns(full)
    issue = hourly["time"].iloc[-1]
    start = issue - serve.HISTORY
    cut = lambda df: df[df["time"] >= start]
    state = market.state(hourly)
    part, _ = features.build(cut(hourly), cut(state), fear_greed, cut(news))
    part = volatility.add_columns(part)
    cols = [c for g in groups.values() for c in g] + [c for c in full.columns if c.startswith("rv_past")]
    pd.testing.assert_series_equal(full.loc[issue, cols], part.loc[issue, cols], check_names=False)


def test_publication_gate():
    months = lambda acc, mae: [
        {"month": "2026-01", "horizon_hours": h, "kind": "direction", "n": 100, "hits": acc, "always_up_hits": 50, "reversal_hits": 52, "brier": 25.0, "brier_climatology": 25.0, "abs_error_return": 1.0, "abs_return": 1.0}
        for h in (1, 4, 24)
    ] + [{"month": "2026-01", "horizon_hours": h, "kind": "volatility", "n": 100, "abs_error_gb": mae, "abs_error_har": 1.0, "abs_error_persistence": 1.0} for h in (1, 4, 24)]
    old = {"evaluation": {"months": months(54, 50.0)}}
    same = copy.deepcopy(old)
    assert train_mod.compare(same, old)[0]
    assert not train_mod.compare({"evaluation": {"months": months(52, 50.0)}}, old)[0]  # direção pior
    assert not train_mod.compare({"evaluation": {"months": months(54, 60.0)}}, old)[0]  # volatilidade pior
    assert train_mod.compare({"evaluation": {"months": [dict(m, month="2027-01") for m in months(40, 99.0)]}}, old)[0]  # sem meses em comum
    assert np.isfinite(train_mod.totals(old["evaluation"]["months"], "direction", 1)["accuracy"])
