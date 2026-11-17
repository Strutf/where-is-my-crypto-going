"""Linha de comando: `wimcg update | backfill | derive | moves | news | gkg-sql | gkg-pack | gkg-import | train | predict | verify | ml-data | ml-eval | ml-report | datasets`."""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

from wimcg import catalog
from wimcg.partitions import label, manifest_key, parquet_key, parse_time

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


def load_dataset(store, dataset_id: str, start: datetime | None = None, columns: list[str] | None = None):
    """Lê as partições Parquet de um dataset (vazio se ainda não houver dados).

    Com `start`, lê só a partir da partição que contém esse instante.
    """
    import pandas as pd

    ds = catalog.get(dataset_id)
    raw = store.read(manifest_key(dataset_id))
    labels = json.loads(raw)["partitions"] if raw else []
    if start is not None:
        labels = [lbl for lbl in labels if lbl >= label(start, ds.partition)]
    if not labels:
        return pd.DataFrame(columns=columns or ds.columns)
    frames = [pd.read_parquet(io.BytesIO(store.read(parquet_key(dataset_id, lbl))), columns=columns) for lbl in labels]
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
    if ds.append_only:
        return last_dt + timedelta(seconds=1)
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


def _last_time(store, dataset_id: str) -> datetime | None:
    raw = store.read(manifest_key(dataset_id))
    last = json.loads(raw).get("end") if raw else None
    return parse_time(last) if last else None


def _derive_hourly(store, full: bool, now: datetime) -> None:
    """Velas de 1 hora: o histórico inteiro, ou só as últimas horas no dia a dia."""
    from wimcg import market
    from wimcg.pipeline.writer import write_dataset

    ds = catalog.get("btc-usd-1h")
    full = full or _last_time(store, ds.id) is None
    start = None if full else _last_time(store, "btc-usd-1m") - timedelta(days=market.RECENT_DAYS)
    hourly = market.hourly_candles(load_dataset(store, "btc-usd-1m", start=start))
    result = write_dataset(store, ds, hourly, now=now)
    log.info("%s: %d horas (%s)", ds.id, result.rows, "histórico inteiro" if full else "período recente")


def _derive_state(store, full: bool, now: datetime) -> None:
    """Estado do mercado, a partir das velas de 1 hora."""
    from wimcg import market
    from wimcg.pipeline.writer import write_dataset

    ds = catalog.get("market-state")
    last = _last_time(store, "btc-usd-1h")
    if last is None:
        return
    full = full or _last_time(store, ds.id) is None
    start = None if full else last - timedelta(days=market.HISTORY_DAYS)
    state = market.state(load_dataset(store, "btc-usd-1h", start=start))
    if not full:
        state = state[state["time"] >= last - timedelta(days=market.RECENT_DAYS)]
    result = write_dataset(store, ds, state, now=now)
    if not state.empty:
        row = state.iloc[-1]
        log.info("%s: %d horas; agora: volatilidade %s (percentil %s), regime %s, %s%% do pico", ds.id, result.rows, row["volatility_state"], row["volatility_percentile"], row["regime"], row["drawdown_pct"])


def _derive_moves(store, full: bool, now: datetime) -> None:
    """Grandes movimentos: os recentes no dia a dia, todos com `full`."""
    import pandas as pd

    from wimcg.moves import HISTORY_DAYS, RECENT_DAYS, detect, hourly_close
    from wimcg.pipeline.writer import write_dataset

    ds = catalog.get("big-moves")
    last = _last_time(store, "btc-usd-1m")
    # Sem nenhum movimento gravado ainda, calcula o histórico inteiro.
    full = full or not store.read(manifest_key(ds.id))
    cutoff = None if full else last - timedelta(days=RECENT_DAYS)
    start = None if full else last - timedelta(days=HISTORY_DAYS)
    candles = load_dataset(store, "btc-usd-1m", start=start, columns=["time", "close"])
    moves = detect(hourly_close(candles))
    if cutoff is not None:
        moves = moves[moves["time"] >= cutoff]
    write_dataset(store, ds, moves, now=now, replace_from=cutoff or pd.Timestamp(0, tz="UTC"))
    log.info("velas desde %s (%s)", f"{candles['time'].min():%Y-%m-%d}", "histórico inteiro" if full else f"movimentos dos últimos {RECENT_DAYS} dias")
    for scale, g in moves.groupby("scale_hours"):
        log.info("escala %d h: %d movimentos (%d de alta, %d de baixa)", scale, len(g), (g.direction == "up").sum(), (g.direction == "down").sum())


def _derive(args: argparse.Namespace, steps) -> int:
    from wimcg import storage as storage_mod
    from wimcg.pipeline.writer import write_catalog

    store = storage_mod.from_env()
    last = _last_time(store, "btc-usd-1m")
    if last is None:
        _error("sem velas: rode `wimcg backfill btc-usd-1m --start 2015-07-20` antes")
        return 1
    now = datetime.now(UTC)
    for step in steps:
        step(store, args.full, now)
    write_catalog(store, list(catalog.DATASETS), now=now)
    store.commit(f"derivados até {last:%Y-%m-%d %H:%M}Z")
    return 0


def cmd_moves(args: argparse.Namespace) -> int:
    """Recalcula os grandes movimentos: os recentes no dia a dia, todos com --full."""
    return _derive(args, [_derive_moves])


def cmd_derive(args: argparse.Namespace) -> int:
    """Tudo o que é calculado das velas: velas de 1 hora, grandes movimentos e estado do mercado."""
    return _derive(args, [_derive_hourly, _derive_moves, _derive_state])


NEWS_DONE_KEY = "json/move-news/done.json"


def cmd_news(args: argparse.Namespace) -> int:
    """Busca as notícias dos grandes movimentos, do maior para o menor, uma fase por consulta.

    As fases já consultadas ficam em NEWS_DONE_KEY (inclusive as que não acharam nada), então a
    busca continua de onde parou. Com --quick, para na primeira recusa do GDELT.
    """
    import pandas as pd

    from wimcg import storage as storage_mod
    from wimcg.news import PHASES, news_for_phase
    from wimcg.pipeline.http import make_client
    from wimcg.pipeline.writer import write_catalog, write_dataset

    store = storage_mod.from_env()
    moves = load_dataset(store, "big-moves")
    raw = store.read(NEWS_DONE_KEY)
    done = set(json.loads(raw)) if raw else set()

    def key(move, phase: str) -> str:
        return f"{pd.Timestamp(move['time']):%Y-%m-%dT%H:%MZ}|{int(move['scale_hours'])}|{phase}"

    todo = moves[pd.to_datetime(moves["time"], utc=True) >= parse_time(args.start)]
    todo = todo[[any(key(m, ph) not in done for ph in PHASES) for _, m in todo.iterrows()]]
    todo = todo.sort_values("z", key=abs, ascending=False)
    log.info("%d movimentos com notícias a buscar", len(todo))
    if args.limit:
        todo = todo.head(args.limit)
    ds = catalog.get("move-news")
    refused = False
    with make_client() as client:
        for _, move in todo.iterrows():
            for phase in PHASES:
                if key(move, phase) in done:
                    continue
                try:
                    rows = news_for_phase(client, move, phase, patient=not args.quick)
                except RuntimeError as exc:
                    # Na coleta de 15 em 15 minutos a recusa é esperada: tenta de novo na próxima.
                    (log.info if args.quick else _warning)(f"notícias: {exc}")
                    refused = True
                    break
                now = datetime.now(UTC)
                write_dataset(store, ds, rows, now=now)
                done.add(key(move, phase))
                store.write(NEWS_DONE_KEY, json.dumps(sorted(done)).encode())
                write_catalog(store, list(catalog.DATASETS), now=now)
                store.commit(f"notícias do movimento de {move['time']:%Y-%m-%d %H:%M}Z ({phase})")
                log.info("%s %+.1f%% (%d h), %s: %d notícias", f"{move['time']:%Y-%m-%d %H:%M}", move["return_pct"], move["scale_hours"], phase, len(rows))
            if refused:
                break
    return 0


def cmd_gkg_sql(args: argparse.Namespace) -> int:
    from wimcg import gkg

    query = gkg.files_sql if args.files else gkg.sql
    print(query(parse_time(args.start), parse_time(args.end)))
    return 0


def cmd_gkg_pack(args: argparse.Namespace) -> int:
    """Junta os CSVs exportados do BigQuery (consulta de `gkg-sql`) num Parquet compacto."""
    from wimcg import gkg

    try:
        df = gkg.pack([gkg.read_bigquery_csv(p) for p in args.csv], [gkg.read_files_csv(p) for p in args.files])
    except (OSError, ValueError) as exc:
        _error(f"gkg-pack: {exc}")
        return 1
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    gkg.write_history(df, args.out)
    first, last = df["time"].min(), df["time"].max()
    missing = int((last - first) / timedelta(hours=1)) + 1 - len(df)
    log.info("%s: %d horas, de %s a %s (%d horas sem dados no GDELT)", args.out, len(df), f"{first:%Y-%m-%d %H}h", f"{last:%Y-%m-%d %H}h", missing)
    return 0


def cmd_gkg_import(args: argparse.Namespace) -> int:
    """Grava no armazenamento o histórico gerado por `gkg-pack`."""
    from wimcg import gkg
    from wimcg import storage as storage_mod
    from wimcg.pipeline.writer import write_catalog, write_dataset

    try:
        df = gkg.read_history(args.file)
    except (OSError, ValueError) as exc:
        _error(f"gkg-import: {exc}")
        return 1
    store = storage_mod.from_env()
    now = datetime.now(UTC)
    result = write_dataset(store, catalog.get("gkg-hourly"), df, now=now)
    write_catalog(store, list(catalog.DATASETS), now=now)
    store.commit(f"gkg-hourly: {df['time'].min():%Y-%m-%d} a {df['time'].max():%Y-%m-%d %H}h, {len(df)} horas")
    log.info("gkg-hourly: %d horas em %d partições", result.rows, len(result.partitions))
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    """Treina os modelos publicados, avalia mês a mês e publica (ou não, pela trava)."""
    import io

    import joblib

    from wimcg import storage as storage_mod
    from wimcg.ml import serve
    from wimcg.ml import train as train_mod

    store = storage_mod.from_env()
    # Revisão lida antes dos dados: as coletas não gravam ao mesmo tempo (grupo hf-writes).
    revision = store.data_revision()
    table, groups = train_mod.load_table(store)
    from wimcg.ml.run import last_full_month

    bundle, summary = train_mod.train(table, groups, args.last or last_full_month(), args.evaluate_from)
    summary["reproducibility"]["data"] = {"storage": os.environ.get("FS_STORAGE", "local"), "repository": os.environ.get("FS_HF_REPO"), "revision": revision}
    for kind in ("direction", "volatility"):
        for h, m in summary["evaluation"][kind].items():
            log.info("%s %s h: %s", kind, h, json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in m.items()}))
    buf = io.BytesIO()
    joblib.dump(bundle, buf)

    publish, reason = args.publish, "publicação forçada"
    if args.publish_if_better:
        current = serve.current_report(store)
        if current is None:
            publish, reason = True, "nenhum modelo publicado ainda"
        else:
            publish, reason = train_mod.compare(summary, current)
            reason = f"comparado com {current['model_version']}: {reason}"
        log.info("publicação %s: %s", "aprovada" if publish else "recusada", reason)
    if publish:
        serve.publish(store, buf.getvalue(), summary)
        store.commit(f"modelos {summary['model_version']}")
        log.info("publicado como %s", summary["model_version"])
    elif args.publish_if_better:
        serve.record_rejected(store, summary, reason)
        store.commit(f"treino {summary['model_version']} não publicado")
        _warning(f"modelo novo não publicado ({reason})")
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    from wimcg import storage as storage_mod
    from wimcg.ml.serve import NoModelPublished, predict
    from wimcg.pipeline.writer import write_catalog, write_dataset

    store = storage_mod.from_env()
    try:
        rows = predict(store)
    except NoModelPublished as exc:
        log.warning("previsão ignorada: %s", exc)
        return 0
    except RuntimeError as exc:
        _error(f"previsão: {exc}")
        return 1
    if rows.empty:
        log.info("a última hora fechada já foi prevista")
        return 0
    now = datetime.now(UTC)
    write_dataset(store, catalog.get("predictions"), rows, now=now)
    write_catalog(store, list(catalog.DATASETS), now=now)
    store.commit(f"previsões {rows['time'].iloc[0]:%Y-%m-%d %H:%M}Z")
    for r in rows.itertuples():
        log.info("+%d h: %s (alta %.0f%%), retorno %+.2f%%, volatilidade %.2f%%", r.horizon_hours, r.direction, 100 * r.prob_up, r.pred_return_pct, r.pred_volatility_pct)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from wimcg import storage as storage_mod
    from wimcg.ml.serve import SUMMARY_KEY, summarize, verify
    from wimcg.pipeline.writer import write_catalog, write_dataset

    store = storage_mod.from_env()
    now = datetime.now(UTC)
    rows = verify(store, now=now, days=args.days)
    if rows.empty:
        log.info("nenhuma previsão com resultado conhecido ainda")
        return 0
    ds = catalog.get("verification")
    write_dataset(store, ds, rows, now=now)
    summary = summarize(load_dataset(store, ds.id), now)
    store.write(SUMMARY_KEY, json.dumps(summary, ensure_ascii=False).encode())
    write_catalog(store, list(catalog.DATASETS), now=now)
    store.commit(f"verificação {now:%Y-%m-%d %H:%M}Z")
    for g in summary["windows"]["7d"]:
        log.info(
            "+%d h (7 dias, n=%d): acerto %.1f%% | sempre alta %.1f%% | inverter %.1f%% | volatilidade: erro %.2f p.p. (persistência %.2f)",
            g["horizon_hours"], g["n"], 100 * g["accuracy"], 100 * g["always_up_accuracy"], 100 * g["reversal_accuracy"],
            g["volatility_mae_pct"], g["volatility_mae_persistence_pct"],
        )
    return 0


def cmd_ml_data(args: argparse.Namespace) -> int:
    """Baixa do Hugging Face as tabelas de treino e monta a tabela por hora."""
    from wimcg.ml import data, run

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    revision = data.download()
    table, groups = run.build_table()
    log.info("dataset %s: %d horas, %s", revision[:10], len(table), ", ".join(f"{k} {len(v)}" for k, v in groups.items()))
    return 0


def cmd_ml_eval(args: argparse.Namespace) -> int:
    """Avaliação andando para a frente de cada modelo e conjunto de variáveis."""
    from wimcg.ml import run

    if not run.TABLE.exists():
        _error("sem tabela de treino: rode `wimcg ml-data` antes")
        return 1
    run.run(args.models.split(","), args.sets.split(","), args.seeds, args.first, args.last or run.last_full_month(), args.jobs, redo=args.redo)
    return 0


def cmd_ml_report(args: argparse.Namespace) -> int:
    from wimcg.ml import run

    run.report()
    print((run.RESULTS / "direction_eval.md").read_text())
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
    p.add_argument("--full", action="store_true", help="recalcula o histórico inteiro (padrão: só o período recente)")
    p.set_defaults(func=cmd_moves)

    p = sub.add_parser("derive", help="recalcula o que sai das velas: velas de 1 hora, grandes movimentos e estado do mercado")
    p.add_argument("--full", action="store_true", help="recalcula o histórico inteiro (padrão: só o período recente)")
    p.set_defaults(func=cmd_derive)

    p = sub.add_parser("news", help="busca as notícias de antes, durante e depois de cada grande movimento")
    p.add_argument("--start", default="2017-01-01", help="só movimentos a partir desta data")
    p.add_argument("--limit", type=int, help="processa só os N maiores movimentos pendentes")
    p.add_argument("--quick", action="store_true", help="para na primeira recusa do GDELT, sem esperar (para a coleta de 15 em 15 minutos)")
    p.set_defaults(func=cmd_news)

    p = sub.add_parser("gkg-sql", help="imprime a consulta do BigQuery com as notícias por hora (GKG do GDELT)")
    p.add_argument("--start", required=True, help="início, inclusive (ex.: 2015-02-19)")
    p.add_argument("--end", required=True, help="fim, exclusive (ex.: 2021-01-01)")
    p.add_argument("--files", action="store_true", help="consulta leve: só os arquivos de 15 min presentes em cada hora")
    p.set_defaults(func=cmd_gkg_sql)

    p = sub.add_parser("gkg-pack", help="junta os CSVs exportados do BigQuery (resultado de gkg-sql) num Parquet")
    p.add_argument("csv", nargs="+", help="arquivos .csv ou .csv.gz")
    p.add_argument("--files", nargs="+", default=[], help="CSVs de `gkg-sql --files`, para marcar as horas incompletas")
    p.add_argument("--out", default="history/gkg-hourly.parquet")
    p.set_defaults(func=cmd_gkg_pack)

    p = sub.add_parser("gkg-import", help="grava o Parquet de gkg-pack no dataset gkg-hourly")
    p.add_argument("file", nargs="?", default="history/gkg-hourly.parquet")
    p.set_defaults(func=cmd_gkg_import)

    p = sub.add_parser("train", help="treina os modelos publicados (direção e volatilidade), com avaliação mês a mês")
    p.add_argument("--publish", action="store_true", help="publica sempre")
    p.add_argument("--publish-if-better", action="store_true", help="publica só se a avaliação não piorar em relação ao modelo atual")
    p.add_argument("--evaluate-from", default="2019-01-01", help="primeiro mês da avaliação")
    p.add_argument("--last", help="último mês da avaliação (padrão: o último mês completo)")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("predict", help="emite as previsões da última hora fechada")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("verify", help="compara as previsões passadas com o que aconteceu")
    p.add_argument("--days", type=int, default=35, help="quantos dias de previsões verificar")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("ml-data", help="baixa as tabelas de treino do Hugging Face e monta a tabela por hora")
    p.set_defaults(func=cmd_ml_data)

    p = sub.add_parser("ml-eval", help="avalia os modelos mês a mês (gb, mlp, gru) e guarda as previsões")
    p.add_argument("--models", default="gb,mlp,gru")
    p.add_argument("--sets", default="price,market,sentiment,all", help="conjuntos de variáveis: price, market, sentiment, all")
    p.add_argument("--seeds", type=int, default=3, help="sementes por rede neural (o gb roda uma vez)")
    p.add_argument("--first", default="2019-01-01", help="primeiro mês de teste")
    p.add_argument("--last", help="último mês de teste (padrão: o último mês completo)")
    p.add_argument("--jobs", type=int, default=4, help="rodadas em paralelo, com 1 núcleo cada")
    p.add_argument("--redo", action="store_true", help="refaz as rodadas que já têm previsões guardadas")
    p.set_defaults(func=cmd_ml_eval)

    p = sub.add_parser("ml-report", help="resume as previsões guardadas em research/results/")
    p.set_defaults(func=cmd_ml_report)

    p = sub.add_parser("datasets", help="lista os datasets do catálogo")
    p.set_defaults(func=cmd_datasets)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpx2", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
