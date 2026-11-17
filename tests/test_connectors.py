import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd

from wimcg.pipeline.connectors import coinbase, feargreed, gdelt_gkg
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


def _gkg_zip(stamp: str, themes: list[str], translated: bool = False) -> bytes:
    """Arquivo .gkg.csv.zip com uma linha por tema e um pedaço solto (quebra de linha no último campo)."""
    lines = []
    for i, theme in enumerate(themes):
        fields = [""] * gdelt_gkg.N_FIELDS
        fields[0], fields[gdelt_gkg.DATE], fields[gdelt_gkg.THEMES], fields[gdelt_gkg.TONE] = f"{stamp}-{i}", stamp, theme, "-2,1,3"
        fields[gdelt_gkg.DOMAIN], fields[gdelt_gkg.URL] = "example.com", f"https://example.com/{stamp}/{i}"
        fields[gdelt_gkg.EXTRAS] = "<PAGE_LINKS>x</PAGE_LINKS><PAGE_TITLE>Bitcoin falls &#x2013; again</PAGE_TITLE>"
        if translated:
            fields[gdelt_gkg.TRANSLATION] = "srclc:por;eng:GT-POR 1.0"
        lines.append("\t".join(fields))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{stamp}.gkg.csv", "\n".join(lines) + "\npedaço solto de uma notícia\n")
    return buf.getvalue()


def _gdelt(last_en: str, last_tr: str, missing: tuple[str, ...] = ()):
    """Servidor falso do GDELT: as listas do último arquivo e um arquivo por pedido."""
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        requested.append(name)
        if name == "lastupdate.txt":
            return httpx.Response(200, text=f"1 abc http://data.gdeltproject.org/gdeltv2/{last_en}.gkg.csv.zip\n")
        if name == "lastupdate-translation.txt":
            return httpx.Response(200, text=f"1 abc http://data.gdeltproject.org/gdeltv2/{last_tr}.translation.gkg.csv.zip\n")
        if name in missing:
            return httpx.Response(404)
        return httpx.Response(200, content=_gkg_zip(name[:14], ["ECON_BITCOIN;CYBER_ATTACK;", "TERROR;"], "translation" in name))

    return handler, requested


def test_gkg_hourly_aggregates_only_closed_hours():
    # Último arquivo publicado: 11:45 em inglês e 11:30 nas traduções. Só a hora das 10 fechou.
    handler, requested = _gdelt("20260205114500", "20260205113000")
    start, end = datetime(2026, 2, 5, 9, 0, 1, tzinfo=UTC), datetime(2026, 2, 5, 12, 7, tzinfo=UTC)
    with make_client(transport=httpx.MockTransport(handler)) as client:
        df = gdelt_gkg.gkg_hourly(client, start, end)
    assert df["time"].tolist() == [pd.Timestamp("2026-02-05T10:00Z")]  # a das 9 já estava gravada
    row = df.iloc[0]
    assert (row["articles"], row["btc_articles"], row["btc_cyber_articles"], row["world_terror_articles"]) == (16, 8, 8, 8)
    assert (row["en_files"], row["translated_files"], row["tone"]) == (4, 4, -2.0)
    assert len(requested) == 2 + 8  # as duas listas e os 8 arquivos da hora das 10


def test_gkg_hourly_marks_files_gdelt_never_published():
    handler, _ = _gdelt("20260205120000", "20260205120000", missing=("20260205103000.translation.gkg.csv.zip",))
    with make_client(transport=httpx.MockTransport(handler)) as client:
        df = gdelt_gkg.gkg_hourly(client, datetime(2026, 2, 5, 10, tzinfo=UTC), datetime(2026, 2, 5, 11, 30, tzinfo=UTC))
    row = df.iloc[0]
    assert (row["articles"], row["en_files"], row["translated_files"]) == (14, 4, 3)


def test_gkg_hourly_stops_waiting_for_late_translations():
    # As traduções pararam às 09:45. A hora das 10 só é gravada sem elas depois de PATIENCE.
    handler, _ = _gdelt("20260205140000", "20260205094500", missing=tuple(f"202602051{h}{m}00.translation.gkg.csv.zip" for h in "0123" for m in ("00", "15", "30", "45")))
    start = datetime(2026, 2, 5, 10, tzinfo=UTC)
    with make_client(transport=httpx.MockTransport(handler)) as client:
        soon = gdelt_gkg.gkg_hourly(client, start, datetime(2026, 2, 5, 11, 30, tzinfo=UTC))
        later = gdelt_gkg.gkg_hourly(client, start, start + timedelta(hours=1) + gdelt_gkg.PATIENCE)
    assert soon.empty
    assert later["time"].tolist() == [pd.Timestamp("2026-02-05T10:00Z")]
    assert (later.iloc[0]["en_files"], later.iloc[0]["translated_files"]) == (4, 0)


def test_btc_news_reuses_the_files_read_for_the_hourly_counts():
    handler, requested = _gdelt("20260205114500", "20260205114500")
    start, end = datetime(2026, 2, 5, 10, tzinfo=UTC), datetime(2026, 2, 5, 12, 7, tzinfo=UTC)
    with make_client(transport=httpx.MockTransport(handler)) as client:
        gdelt_gkg.gkg_hourly(client, start, end)
        downloads = len(requested)
        news = gdelt_gkg.btc_news(client, start, end)
        assert len(requested) == downloads + 2  # só as duas listas; nenhum arquivo de novo
    assert not gdelt_gkg._news_cache  # o que foi entregue sai da memória
    assert len(news) == 2 * 8  # uma notícia de bitcoin por arquivo, 8 arquivos por hora, 2 horas
    first = news.sort_values(["time", "language"]).iloc[0]
    assert first["time"] == pd.Timestamp("2026-02-05T10:00Z")
    assert (first["title"], first["domain"], first["language"]) == ("Bitcoin falls \u2013 again", "example.com", "eng")
    assert first["in_title"]
    assert (first["url"], first["tone"], first["topics"]) == ("https://example.com/20260205100000/0", -2.0, "cyber")
    assert set(news["language"]) == {"eng", "por"}


def test_btc_news_downloads_by_itself_when_the_hour_was_not_read():
    handler, requested = _gdelt("20260205114500", "20260205114500")
    with make_client(transport=httpx.MockTransport(handler)) as client:
        news = gdelt_gkg.btc_news(client, datetime(2026, 2, 5, 10, tzinfo=UTC), datetime(2026, 2, 5, 11, 30, tzinfo=UTC))
    assert len(requested) == 2 + 8 and len(news) == 8
