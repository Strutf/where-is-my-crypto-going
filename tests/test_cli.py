import json
from datetime import UTC, datetime, timedelta

from wimcg import catalog
from wimcg.cli import MAX_CATCHUP, update_start
from wimcg.partitions import format_time, manifest_key
from wimcg.storage import LocalStorage

END = datetime(2026, 11, 16, 12, tzinfo=UTC)
DS = catalog.get("btc-usd-1m")


def _store(tmp_path, last: datetime | None) -> LocalStorage:
    store = LocalStorage(tmp_path)
    if last is not None:
        store.write(manifest_key(DS.id), json.dumps({"end": format_time(last)}).encode())
    return store


def test_update_start_without_data_uses_update_hours(tmp_path):
    assert update_start(_store(tmp_path, None), DS, END) == END - timedelta(hours=DS.update_hours)


def test_update_start_recent_data_keeps_the_usual_window(tmp_path):
    # Dados de 10 min atrás: busca as últimas update_hours (pega velas revisadas).
    store = _store(tmp_path, END - timedelta(minutes=10))
    assert update_start(store, DS, END) == END - timedelta(hours=DS.update_hours)


def test_update_start_fills_a_gap_from_the_last_stored_time(tmp_path):
    last = END - timedelta(hours=5)
    assert update_start(_store(tmp_path, last), DS, END) == last


def test_update_start_caps_very_old_gaps(tmp_path):
    store = _store(tmp_path, END - timedelta(days=200))
    assert update_start(store, DS, END) == END - MAX_CATCHUP


def test_update_start_of_append_only_data_continues_after_the_last_row(tmp_path):
    # Uma hora de notícias já gravada não muda: não relê as últimas update_hours.
    news = catalog.get("gkg-hourly")
    store = LocalStorage(tmp_path)
    last = END - timedelta(minutes=30)
    store.write(manifest_key(news.id), json.dumps({"end": format_time(last)}).encode())
    assert update_start(store, news, END) == last + timedelta(seconds=1)


def test_dataset_card_lists_every_table_and_does_not_change_between_runs(tmp_path):
    from wimcg.pipeline.writer import write_catalog

    store = LocalStorage(tmp_path)
    store.write(manifest_key("btc-usd-1m"), json.dumps({"start": "2015-07-20T00:00:00Z", "end": format_time(END)}).encode())
    write_catalog(store, list(catalog.DATASETS), now=END)
    text = store.read("README.md").decode()
    assert text.startswith("---\n") and "Não é recomendação de investimento" in text
    for ds in catalog.DATASETS:
        assert f"### `{ds.id}`" in text
    # O visualizador só recebe as tabelas que já têm dados.
    assert 'data_files: "parquet/btc-usd-1m/*.parquet"' in text and "parquet/move-news/" not in text.split("---")[1]
    assert "**Desde:** 2015-07-20" in text and "**Desde:** ainda sem dados" in text

    # Uma coleta mais tarde muda a cobertura, mas não a página (ela não cita o fim dos dados).
    store.write(manifest_key("btc-usd-1m"), json.dumps({"start": "2015-07-20T00:00:00Z", "end": format_time(END + timedelta(hours=1))}).encode())
    write_catalog(store, list(catalog.DATASETS), now=END + timedelta(hours=1))
    assert store.read("README.md").decode() == text
