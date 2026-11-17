import json
import sys
from datetime import UTC, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from wimcg import catalog
from wimcg.api.app import create_app
from wimcg.pipeline.writer import write_catalog, write_dataset
from wimcg.storage import LocalStorage

NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)


@pytest.fixture
def client(tmp_path):
    store = LocalStorage(tmp_path)
    hours = pd.date_range("2026-10-30T00:00Z", "2026-11-02T23:00Z", freq="h")  # atravessa a virada do mês
    candles = pd.DataFrame({"time": hours, "open": 1.0, "high": 2.0, "low": 0.5, "close": range(len(hours)), "volume": 3.0})
    write_dataset(store, catalog.get("btc-usd-1h"), candles, now=NOW)
    minutes = pd.DataFrame({"time": hours[:2], "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 3.0})
    write_dataset(store, catalog.get("btc-usd-1m"), minutes, now=NOW)
    write_catalog(store, list(catalog.DATASETS), now=NOW)
    return TestClient(create_app(store, rate_limit_per_minute=0))


def test_datasets_lists_the_catalog_with_coverage_and_the_disclaimer(client):
    body = client.get("/v1/datasets").json()
    assert "Não é recomendação de investimento" in body["disclaimer"]
    by_id = {d["id"]: d for d in body["datasets"]}
    assert set(by_id) == {d.id for d in catalog.DATASETS}
    assert by_id["btc-usd-1h"]["coverage"]["start"] == "2026-10-30T00:00:00Z"


def test_data_returns_the_range_across_partitions(client):
    r = client.get("/v1/datasets/btc-usd-1h/data", params={"start": "2026-10-31T22:00", "end": "2026-11-01T02:00", "fields": "close"})
    body = r.json()
    assert r.status_code == 200 and "s-maxage=60" in r.headers["cache-control"]
    assert [row["time"] for row in body["data"]] == ["2026-10-31T22:00:00Z", "2026-10-31T23:00:00Z", "2026-11-01T00:00:00Z", "2026-11-01T01:00:00Z"]
    assert body["count"] == 4 and set(body["data"][0]) == {"time", "close"}


def test_latest_returns_the_last_row(client):
    body = client.get("/v1/datasets/btc-usd-1h/latest").json()
    assert body["data"][0]["time"] == "2026-11-02T23:00:00Z"


def test_bad_requests_are_refused_with_a_clear_message(client):
    assert client.get("/v1/datasets/nope/data").status_code == 404
    assert client.get("/v1/datasets/market-state/latest").status_code == 404  # ainda sem dados
    assert "Parquet" in client.get("/v1/datasets/btc-usd-1m/data").json()["detail"]  # grande demais para a API
    assert client.get("/v1/datasets/btc-usd-1h/data", params={"start": "ontem"}).status_code == 400
    assert client.get("/v1/datasets/btc-usd-1h/data", params={"start": "2020-01-01", "end": "2026-01-01"}).status_code == 400
    assert client.get("/v1/datasets/btc-usd-1h/data", params={"fields": "senha"}).status_code == 400
    assert client.get("/v1/datasets", params={"x": "1"}).status_code == 400  # parâmetro desconhecido


def test_rate_limit_answers_429(tmp_path):
    limited = TestClient(create_app(LocalStorage(tmp_path), rate_limit_per_minute=2))
    assert [limited.get("/v1/datasets").status_code for _ in range(3)] == [200, 200, 429]


def test_the_api_does_not_need_pandas():
    """Na Vercel só há fastapi + httpx: nada do que a API importa pode puxar o pandas."""
    code = "import sys; sys.modules['pandas'] = None; sys.modules['pyarrow'] = None; import wimcg.api.app; print('ok')"
    import subprocess

    out = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr[-500:]


def test_vercel_entry_point_restores_the_rewritten_path(tmp_path, monkeypatch):
    monkeypatch.setenv("FS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FS_HF_REPO", raising=False)
    monkeypatch.delenv("FS_DATA_URL", raising=False)
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("vercel_index", Path(__file__).parents[1] / "api" / "index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    r = TestClient(module.app).get("/api/index", params={"__path": "v1/datasets"})
    assert r.status_code == 200 and json.loads(r.text)["datasets"]


def test_model_and_verification_endpoints(tmp_path):
    from wimcg.ml import serve

    store = LocalStorage(tmp_path)
    c = TestClient(create_app(store, rate_limit_per_minute=0))
    assert c.get("/v1/verification/summary").status_code == 404
    assert c.get("/v1/models/current").status_code == 404
    store.write(serve.SUMMARY_KEY, json.dumps({"windows": {"7d": []}}).encode())
    store.write(serve.MODELS_POINTER, json.dumps({"model_version": "v1-20261201", "bundle": "x", "report": "y", "summary": {"evaluation": {}}}).encode())
    c = TestClient(create_app(store, rate_limit_per_minute=0))  # a API guarda as leituras por 60 s
    assert c.get("/v1/verification/summary").json() == {"windows": {"7d": []}}
    body = c.get("/v1/models/current").json()
    assert body["model_version"] == "v1-20261201" and "disclaimer" in body


def test_api_keys_match_the_serving_module():
    from wimcg.api import app as api
    from wimcg.ml import serve

    assert (api.MODELS_POINTER, api.MODELS_INDEX, api.SUMMARY_KEY) == (serve.MODELS_POINTER, serve.MODELS_INDEX, serve.SUMMARY_KEY)
