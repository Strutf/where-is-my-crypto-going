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
