import json
from datetime import UTC, datetime

import httpx

from wimcg.pipeline.connectors import coinbase, feargreed
from wimcg.pipeline.http import make_client


def test_coinbase_paginates_by_300_minutes_and_deduplicates(monkeypatch):
    monkeypatch.setattr(coinbase, "PAUSE_SECONDS", 0)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.params["start"])
        start = int(datetime.fromisoformat(request.url.params["start"]).timestamp())
        # Duas velas por página, da mais recente para a mais antiga; a primeira repete a da página anterior.
        rows = [[start + 60, 1, 3, 2, 2.5, 10], [start, 1, 3, 2, 2.5, 10]]
        return httpx.Response(200, content=json.dumps(rows))

    with make_client(transport=httpx.MockTransport(handler)) as client:
        df = coinbase.candles(client, datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, 10, tzinfo=UTC))
    assert len(requests) == 2  # 600 minutos = 2 páginas de 300
    assert df["time"].is_monotonic_increasing and df["time"].is_unique
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert df.iloc[0][["open", "high", "low", "close"]].tolist() == [2, 3, 1, 2.5]


def test_fear_greed_parses_and_filters_by_date():
    data = {"data": [
        {"value": "10", "value_classification": "Extreme Fear", "timestamp": "1794787200"},
        {"value": "30", "value_classification": "Fear", "timestamp": "1517443200"},
    ]}
    with make_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=json.dumps(data)))) as c:
        df = feargreed.fear_greed(c, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))
    assert df[["value", "classification"]].values.tolist() == [["10", "Extreme Fear"]]
