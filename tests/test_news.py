import json

import httpx
import pandas as pd

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
