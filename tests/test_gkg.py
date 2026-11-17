import gzip
from datetime import UTC, datetime

import pandas as pd
import pytest

from wimcg import catalog, gkg
from wimcg.cli import main
from wimcg.partitions import manifest_key, parquet_key
from wimcg.storage import LocalStorage


def _bq_csv(times: list[str]) -> str:
    """CSV no formato exportado pelo BigQuery, com valores fictícios."""
    lines = [",".join(gkg.columns())]
    for i, t in enumerate(times):
        values = [str(100 + i), "-1.5", "3", "0.25"] + [str(i)] * (len(gkg.columns()) - 5)
        lines.append(",".join([f"{t} UTC", *values]))
    return "\n".join(lines) + "\n"


def test_catalog_fields_follow_the_query():
    assert catalog.get("gkg-hourly").columns == gkg.columns()


def test_read_bigquery_csv_parses_utc_and_gzip(tmp_path):
    path = tmp_path / "a.csv.gz"
    path.write_bytes(gzip.compress(_bq_csv(["2015-02-19 00:00:00", "2015-02-19 01:00:00"]).encode()))
    df = gkg.read_bigquery_csv(path)
    assert df["time"].iloc[1] == pd.Timestamp("2015-02-19T01:00Z")
    assert df["articles"].tolist() == [100, 101]


def test_read_bigquery_csv_rejects_other_columns(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text(_bq_csv(["2015-02-19 00:00:00"]).replace("btc_tone", "x_tone"))
    with pytest.raises(ValueError, match="colunas"):
        gkg.read_bigquery_csv(path)


def test_pack_rejects_overlapping_files(tmp_path):
    (tmp_path / "a.csv").write_text(_bq_csv(["2020-12-31 23:00:00"]))
    (tmp_path / "b.csv").write_text(_bq_csv(["2020-12-31 23:00:00", "2021-01-01 00:00:00"]))
    frames = [gkg.read_bigquery_csv(tmp_path / n) for n in ("a.csv", "b.csv")]
    with pytest.raises(ValueError, match="repetidas"):
        gkg.pack(frames)


def test_pack_and_import_write_monthly_partitions(tmp_path, monkeypatch):
    (tmp_path / "b.csv").write_text(_bq_csv(["2021-01-01 00:00:00"]))
    (tmp_path / "a.csv").write_text(_bq_csv(["2020-12-31 22:00:00", "2020-12-31 23:00:00"]))
    out = tmp_path / "history" / "gkg.parquet"
    assert main(["gkg-pack", str(tmp_path / "b.csv"), str(tmp_path / "a.csv"), "--out", str(out)]) == 0

    monkeypatch.setenv("FS_STORAGE", "local")
    monkeypatch.setenv("FS_DATA_DIR", str(tmp_path / "data"))
    assert main(["gkg-import", str(out)]) == 0
    store = LocalStorage(tmp_path / "data")
    assert b'"partitions":["2020-12","2021-01"]' in store.read(manifest_key("gkg-hourly"))
    dec = pd.read_parquet(tmp_path / "data" / parquet_key("gkg-hourly", "2020-12"))
    assert dec["time"].tolist() == [pd.Timestamp("2020-12-31T22:00Z"), pd.Timestamp("2020-12-31T23:00Z")]
    assert dec["btc_tone"].tolist() == [0.25, 0.25]


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


def test_aggregate_counts_the_15_min_files_of_each_feed():
    records = pd.DataFrame(
        {
            "DATE": [20260205100000, 20260205100000, 20260205101500, 20260205100000],
            "Themes": ["ECON_BITCOIN;", "TERROR;", "TERROR;", "TERROR;"],
            "V2Tone": ["1", "1", "1", "1"],
            "translated": [False, False, False, True],
        }
    )
    row = gkg.aggregate(records).iloc[0]
    assert (row["articles"], row["en_files"], row["translated_files"]) == (4, 2, 1)


def test_files_sql_reads_only_the_small_columns():
    q = gkg.files_sql(datetime(2015, 2, 19, tzinfo=UTC), datetime(2026, 11, 17, tzinfo=UTC))
    assert "AS en_files" in q and "AS translated_files" in q
    assert "Themes" not in q and "V2Tone" not in q
    assert "_PARTITIONTIME >= TIMESTAMP('2015-02-18')" in q


def test_old_extraction_without_file_columns_is_accepted_and_filled_later(tmp_path):
    old = _bq_csv(["2020-12-31 22:00:00", "2020-12-31 23:00:00"])
    old = "\n".join(line.rsplit(",", 2)[0] for line in old.splitlines()) + "\n"  # sem as duas últimas colunas
    (tmp_path / "old.csv").write_text(old)
    df = gkg.read_bigquery_csv(tmp_path / "old.csv")
    assert list(df.columns) == gkg.columns() and df["en_files"].isna().all()

    (tmp_path / "files.csv").write_text(
        "time,en_files,translated_files\n2020-12-31 22:00:00 UTC,4,3\n2020-12-31 23:00:00 UTC,4,4\n"
    )
    out = tmp_path / "h.parquet"
    assert main(["gkg-pack", str(tmp_path / "old.csv"), "--files", str(tmp_path / "files.csv"), "--out", str(out)]) == 0
    packed = gkg.read_history(out)
    assert packed["translated_files"].tolist() == [3, 4] and packed["en_files"].tolist() == [4, 4]

    # Contagens que não cobrem todas as horas são recusadas.
    (tmp_path / "short.csv").write_text("time,en_files,translated_files\n2020-12-31 22:00:00 UTC,4,3\n")
    assert main(["gkg-pack", str(tmp_path / "old.csv"), "--files", str(tmp_path / "short.csv"), "--out", str(out)]) == 1
