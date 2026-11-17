import json

import httpx
import pandas as pd
import pytest

from wimcg import news


def test_categorize_by_title_keywords():
    assert news.categorize("Bitcoin falls as Fed signals rate hike") == "macro"
    assert news.categorize("Exchange hacked, $200M stolen") == "security"
    assert news.categorize("Crypto liquidations top $1B as war fears grow") == "geopolitics,liquidation"
    assert news.categorize("Bitcoin price today") == ""
    assert news.categorize("Analyst warns of correction") == ""  # "war" só como palavra inteira


def test_news_for_move_labels_phases(monkeypatch):
    monkeypatch.setattr(news, "MIN_INTERVAL", 0)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = request.url.params["startdatetime"]
        calls.append(start)
        seen = {"20260204190000": "20260205T050000Z", "20260205190000": "20260205T193000Z",
                "20260205200000": "20260205T210000Z"}[start]
        art = {"title": "Crypto crash accelerates", "url": f"https://x/{start}", "domain": "x.com",
               "sourcecountry": "US", "seendate": seen}
        return httpx.Response(200, content=json.dumps({"articles": [art]}))

    move = pd.Series({"time": pd.Timestamp("2026-02-05T20:00Z"), "start": pd.Timestamp("2026-02-05T19:00Z"),
                      "scale_hours": 1})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        rows = news.news_for_move(client, move)
    assert rows["phase"].tolist() == ["before", "during", "after"]
    assert rows["hours_from_start"].tolist() == [-14.0, 0.5, 2.0]
    assert len(calls) == 3


def test_search_waits_and_retries_after_rate_limit(monkeypatch):
    monkeypatch.setattr(news, "MIN_INTERVAL", 0)
    monkeypatch.setattr(news, "RATE_LIMIT_WAIT", 0)
    responses = iter([httpx.Response(429, text="Please limit requests to one every 5 seconds"),
                      httpx.Response(200, content=json.dumps({"articles": [{"title": "ok"}]}))])
    with httpx.Client(transport=httpx.MockTransport(lambda r: next(responses))) as client:
        assert news.search(client, pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-01-02", tz="UTC")) == [
            {"title": "ok"}
        ]


def test_title_mentions_crypto_in_several_languages():
    for title in ("Bitcoin Dips Below US$65,000 on Rate Fears", "BTC falls again", "比特幣單月跌逾兩成", "비트코인 급락", "Курс биткоина упал", "Criptomoedas em queda"):
        assert news.title_mentions_crypto(title), title
    for title in ("Savannah Guthrie renews NBC contract", "The FTC Says Older Americans Reported $3 Billion in Fraud Losses", "Something about method", None, ""):
        assert not news.title_mentions_crypto(title), title


def test_quick_search_gives_up_on_the_first_refusal(monkeypatch):
    monkeypatch.setattr(news, "MIN_INTERVAL", 0)
    refuse = httpx.MockTransport(lambda r: httpx.Response(429, text="Please limit requests"))
    with httpx.Client(transport=refuse) as client, pytest.raises(RuntimeError, match="recusou"):
        news.search(client, pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-01-02", tz="UTC"), patient=False)


def test_news_command_resumes_phase_by_phase(tmp_path, monkeypatch):
    """A busca grava cada fase assim que ela chega e continua de onde parou na execução seguinte."""
    from wimcg import catalog
    from wimcg.cli import NEWS_DONE_KEY, load_dataset, main
    from wimcg.pipeline import http as http_mod
    from wimcg.pipeline.writer import write_dataset
    from wimcg.storage import LocalStorage

    monkeypatch.setattr(news, "MIN_INTERVAL", 0)
    monkeypatch.setenv("FS_STORAGE", "local")
    monkeypatch.setenv("FS_DATA_DIR", str(tmp_path))
    store = LocalStorage(tmp_path)
    moves = pd.DataFrame(
        {
            "time": [pd.Timestamp("2026-02-05T20:00Z"), pd.Timestamp("2026-03-01T10:00Z")],
            "scale_hours": [1, 1],
            "start": [pd.Timestamp("2026-02-05T19:00Z"), pd.Timestamp("2026-03-01T09:00Z")],
            "direction": ["down", "up"],
            "return_pct": [-5.0, 2.0],
            "sigma_pct": [1.0, 0.6],
            "z": [-5.0, 3.3],
            "price_start": [100.0, 100.0],
            "price_end": [95.0, 102.0],
        }
    )
    write_dataset(store, catalog.get("big-moves"), moves)

    answers = []  # o que o GDELT falso responde, em ordem

    def handler(request: httpx.Request) -> httpx.Response:
        kind = answers.pop(0)
        if kind == "refuse":
            return httpx.Response(429, text="Please limit requests")
        arts = [] if kind == "empty" else [{"title": "Bitcoin crash", "url": f"https://x/{request.url.params['startdatetime']}", "domain": "x.com", "sourcecountry": "US", "seendate": "20260205T193000Z"}]
        return httpx.Response(200, content=json.dumps({"articles": arts}))

    monkeypatch.setattr(http_mod, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))

    # 1ª execução: a primeira fase do maior movimento chega, a segunda é recusada.
    answers[:] = ["news", "refuse"]
    assert main(["news", "--limit", "1", "--quick"]) == 0
    assert json.loads(store.read(NEWS_DONE_KEY)) == ["2026-02-05T20:00Z|1|before"]
    assert load_dataset(store, "move-news")["phase"].tolist() == ["before"]

    # 2ª execução: continua da segunda fase; uma fase sem notícias também conta como feita.
    answers[:] = ["news", "empty"]
    assert main(["news", "--limit", "1", "--quick"]) == 0
    assert len(json.loads(store.read(NEWS_DONE_KEY))) == 3 and not answers
    assert sorted(load_dataset(store, "move-news")["phase"]) == ["before", "during"]

    # 3ª execução: o maior movimento está completo, então passa para o seguinte.
    answers[:] = ["news", "news", "news"]
    assert main(["news", "--limit", "1", "--quick"]) == 0
    assert len(json.loads(store.read(NEWS_DONE_KEY))) == 6
