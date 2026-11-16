"""Linha de comando: `wimcg update | backfill | moves | news | datasets`."""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

from wimcg import catalog
from wimcg.partitions import manifest_key, parquet_key, parse_time

log = logging.getLogger("wimcg")

# Até quanto tempo para trás o `update` recupera sozinho um buraco nos dados (ex.: execuções
# canceladas enquanto uma carga histórica ocupava o Actions). Acima disso, use `backfill`.
MAX_CATCHUP = timedelta(days=30)


def _error(message: str) -> None:
    """Loga o erro e, no GitHub Actions, também o publica como anotação da execução."""
    log.error(message)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error::{message}", flush=True)


def _warning(message: str) -> None:
    """Como _error, mas como aviso: aparece na execução sem marcá-la como falha."""
    log.warning(message)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning::{message}", flush=True)


def load_dataset(store, dataset_id: str):
    """Lê todas as partições Parquet de um dataset (vazio se ainda não houver dados)."""
    import pandas as pd

    raw = store.read(manifest_key(dataset_id))
    if not raw:
        return pd.DataFrame(columns=catalog.get(dataset_id).columns)
    frames = [pd.read_parquet(io.BytesIO(store.read(parquet_key(dataset_id, lbl)))) for lbl in json.loads(raw)["partitions"]]
    return pd.concat(frames, ignore_index=True)


def update_start(store, ds: catalog.Dataset, end: datetime) -> datetime:
    """Início da busca do `update`: as últimas `update_hours` ou, se houver um buraco, o último
    dado gravado (limitado a MAX_CATCHUP)."""
    start = end - timedelta(hours=ds.update_hours)
    raw = store.read(manifest_key(ds.id))
    last = json.loads(raw).get("end") if raw else None
    if not last:
        return start
    last_dt = parse_time(last)
    if last_dt < end - MAX_CATCHUP:
        _warning(
            f"{ds.id}: último dado em {last}, há mais de {MAX_CATCHUP.days} dias; "
            f"recuperando só os últimos {MAX_CATCHUP.days} (use `wimcg backfill` para o resto)"
        )
        return min(start, end - MAX_CATCHUP)
    if last_dt < start:
        log.info("%s: buraco desde %s; recuperando", ds.id, last)
    return min(start, last_dt)


def _run(datasets: list[catalog.Dataset], start: datetime | None, end: datetime) -> int:
    from wimcg import storage as storage_mod
    from wimcg.pipeline.connectors import CONNECTORS
    from wimcg.pipeline.http import make_client
    from wimcg.pipeline.writer import write_catalog, write_dataset

    try:
        store = storage_mod.from_env()
    except RuntimeError as exc:
        _error(f"configuração: {exc}")
        return 1

    failures, written = [], []
    with make_client() as client:
        for ds in datasets:
            try:
                ds_start = start or update_start(store, ds, end)
                result = write_dataset(store, ds, CONNECTORS[ds.id](client, ds_start, end), now=end)
            except Exception as exc:  # noqa: BLE001 - uma fonte fora do ar não deve parar as outras
                (_error if ds.critical else _warning)(f"{ds.id}: falhou: {exc}")
                failures.append(ds)
                continue
            log.info("%s: %d linhas em %s", ds.id, result.rows, ", ".join(result.partitions) or "-")
            if result.rows:
                written.append(ds.id)

    if written:
        try:
            write_catalog(store, list(catalog.DATASETS), now=end)
            store.commit(f"update {end:%Y-%m-%d %H:%M}Z: {', '.join(written)}")
        except Exception as exc:  # noqa: BLE001 - registra o motivo antes de falhar
            _error(f"falha ao gravar no armazenamento ({type(exc).__name__}): {exc}")
            return 1
    optional = [ds.id for ds in failures if not ds.critical]
    if optional:
        _warning(f"fontes auxiliares com falha (a coleta segue): {', '.join(optional)}")
    critical = [ds.id for ds in failures if ds.critical]
    if critical:
        _error(f"datasets com falha: {', '.join(critical)}")
        return 1
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    datasets = [d for d in catalog.DATASETS if d.schedule == "realtime"]
    if args.datasets:
        datasets = [catalog.get(n.strip()) for n in args.datasets.split(",") if n.strip()]
    return _run(datasets, None, datetime.now(UTC))


def cmd_backfill(args: argparse.Namespace) -> int:
    """Baixa o histórico em blocos, gravando cada bloco (dá para interromper e retomar)."""
    ds = catalog.get(args.dataset)
    end = parse_time(args.end) if args.end else datetime.now(UTC)
    cur = parse_time(args.start)
    status = 0
    while cur < end:
        nxt = min(cur + timedelta(days=ds.backfill_chunk_days), end)
        log.info("%s: %s a %s", ds.id, f"{cur:%Y-%m-%d}", f"{nxt:%Y-%m-%d}")
        status |= _run([ds], cur, nxt)
        cur = nxt
    return status


def cmd_moves(args: argparse.Namespace) -> int:
    from wimcg import storage as storage_mod
    from wimcg.moves import detect, hourly_close
    from wimcg.pipeline.writer import write_catalog, write_dataset

    store = storage_mod.from_env()
    candles = load_dataset(store, "btc-usd-1m")
    if candles.empty:
        _error("sem velas: rode `wimcg backfill btc-usd-1m --start 2015-07-20` antes")
        return 1
    moves = detect(hourly_close(candles))
    now = datetime.now(UTC)
    write_dataset(store, catalog.get("big-moves"), moves, now=now)
    write_catalog(store, list(catalog.DATASETS), now=now)
    store.commit(f"grandes movimentos até {candles['time'].max():%Y-%m-%d %H:%M}Z")
    for scale, g in moves.groupby("scale_hours"):
        log.info("escala %d h: %d movimentos (%d de alta, %d de baixa)", scale, len(g), (g.direction == "up").sum(), (g.direction == "down").sum())
    return 0


def cmd_news(args: argparse.Namespace) -> int:
    import pandas as pd

    from wimcg import storage as storage_mod
    from wimcg.news import news_for_move
    from wimcg.pipeline.http import make_client
    from wimcg.pipeline.writer import write_catalog, write_dataset

    store = storage_mod.from_env()
    moves = load_dataset(store, "big-moves")
    done = load_dataset(store, "move-news")
    done_keys = set(zip(pd.to_datetime(done["move_time"], utc=True), done["scale_hours"].astype(int), strict=True))
    todo = moves[pd.to_datetime(moves["time"], utc=True) >= parse_time(args.start)]
    todo = todo[[(t, int(s)) not in done_keys for t, s in zip(pd.to_datetime(todo["time"], utc=True), todo["scale_hours"], strict=True)]]
    if args.limit:
        todo = todo.sort_values("z", key=abs, ascending=False).head(args.limit)
    log.info("%d movimentos sem notícias", len(todo))
    ds = catalog.get("move-news")
    with make_client() as client:
        for i, (_, move) in enumerate(todo.iterrows(), 1):
            try:
                rows = news_for_move(client, move)
            except RuntimeError as exc:
                _warning(f"notícias: {exc}")
                break
            now = datetime.now(UTC)
            write_dataset(store, ds, rows, now=now)
            store.commit(f"notícias do movimento de {move['time']:%Y-%m-%d %H:%M}Z")
            log.info("[%d/%d] %s %+.1f%% (%d h): %d notícias", i, len(todo), f"{move['time']:%Y-%m-%d %H:%M}", move["return_pct"], move["scale_hours"], len(rows))
    write_catalog(store, list(catalog.DATASETS), now=datetime.now(UTC))
    return 0


def cmd_gkg_sql(args: argparse.Namespace) -> int:
    from wimcg import gkg

    print(gkg.sql(parse_time(args.start), parse_time(args.end)))
    return 0


def cmd_datasets(args: argparse.Namespace) -> int:
    for ds in catalog.DATASETS:
        print(f"{ds.id:<14} {ds.cadence:<8} {ds.provider}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wimcg", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("update", help="coleta os dados recentes de cada fonte")
    p.add_argument("--datasets", help="lista separada por vírgula (padrão: todos os de tempo real)")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("backfill", help="baixa o histórico de um dataset")
    p.add_argument("dataset")
    p.add_argument("--start", required=True, help="ex.: 2015-07-20")
    p.add_argument("--end", help="padrão: agora")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("moves", help="detecta os grandes movimentos a partir das velas")
    p.set_defaults(func=cmd_moves)

    p = sub.add_parser("news", help="busca as notícias de antes, durante e depois de cada grande movimento")
    p.add_argument("--start", default="2017-01-01", help="só movimentos a partir desta data")
    p.add_argument("--limit", type=int, help="processa só os N maiores movimentos pendentes")
    p.set_defaults(func=cmd_news)

    p = sub.add_parser("gkg-sql", help="imprime a consulta do BigQuery com as notícias por hora (GKG do GDELT)")
    p.add_argument("--start", required=True, help="início, inclusive (ex.: 2015-02-19)")
    p.add_argument("--end", required=True, help="fim, exclusive (ex.: 2021-01-01)")
    p.set_defaults(func=cmd_gkg_sql)

    p = sub.add_parser("datasets", help="lista os datasets do catálogo")
    p.set_defaults(func=cmd_datasets)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
