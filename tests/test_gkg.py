from datetime import UTC, datetime

import pandas as pd

from wimcg import gkg


def test_sql_has_every_column_and_bounds():
    q = gkg.sql(datetime(2015, 2, 19, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC))
    for col in gkg.columns()[1:]:
        assert f"AS {col}" in q
    # Lê um dia a mais de partição de cada lado e corta pelo DATE.
    assert "_PARTITIONTIME >= TIMESTAMP('2015-02-18')" in q
    assert "_PARTITIONTIME < TIMESTAMP('2021-01-02')" in q
    assert "time >= TIMESTAMP('2015-02-19 00:00:00') AND time < TIMESTAMP('2021-01-01 00:00:00')" in q


def test_aggregate_counts_by_hour_and_topic():
    records = pd.DataFrame(
        {
            "DATE": [20260205101500, 20260205104500, 20260205110000],
            "Themes": [
                "ECON_BITCOIN;ECON_BANKRUPTCY;TAX_FNCACT;",
                "ARMEDCONFLICT;ECON_INFLATION;",
                "ECON_BITCOIN;CYBER_ATTACK;",
            ],
            "V2Tone": ["-4,1,5,6", "2,3,1,4", "-1,0,1,1"],
        }
    )
    out = gkg.aggregate(records).set_index("time")
    assert list(out.reset_index().columns) == gkg.columns()
    h10, h11 = out.loc[pd.Timestamp("2026-02-05T10:00Z")], out.loc[pd.Timestamp("2026-02-05T11:00Z")]
    assert (h10["articles"], h10["btc_articles"], h10["tone"], h10["btc_tone"]) == (2, 1, -1.0, -4.0)
    assert (h10["btc_bankruptcy_articles"], h10["world_conflict_articles"], h10["world_monetary_articles"]) == (1, 1, 1)
    assert h10["btc_conflict_articles"] == 0  # o conflito não estava na notícia de bitcoin
    assert (h11["btc_articles"], h11["btc_cyber_articles"], h11["world_cyber_articles"]) == (1, 1, 1)


def test_aggregate_does_not_match_theme_prefixes():
    # "TERRORISM_X" não é "TERROR": a comparação é por tema inteiro, como no SQL.
    records = pd.DataFrame({"DATE": [20260205100000], "Themes": ["TERRORISM_X;"], "V2Tone": ["0,0"]})
    assert gkg.aggregate(records)["world_terror_articles"].iloc[0] == 0
