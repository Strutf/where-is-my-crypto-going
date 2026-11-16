"""Catálogo de datasets: metadados compartilhados pela coleta, pela análise e pela futura API.

Este módulo não depende de pandas. Toda tabela tem uma coluna ``time`` (UTC) que define a
partição e a ordenação.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

FieldType = Literal["float", "int", "str", "bool", "time"]
Partition = Literal["day", "month", "year"]
# "derived": produzido pelo próprio projeto (ex.: grandes movimentos), não por um conector.
Schedule = Literal["realtime", "daily", "derived"]


@dataclass(frozen=True)
class Field:
    name: str
    type: FieldType
    unit: str | None
    description: str


@dataclass(frozen=True)
class Dataset:
    id: str
    title: str
    description: str
    provider: str
    source_url: str
    license: str
    cadence: str
    partition: Partition
    key: tuple[str, ...]
    fields: tuple[Field, ...]
    schedule: Schedule
    # Quantas horas para trás o comando `update` busca a cada execução.
    update_hours: int = 2
    # "last": última linha de cada grupo em `latest_group`; "snapshot": todas as linhas do último `time`.
    latest_mode: Literal["last", "snapshot"] = "last"
    latest_group: tuple[str, ...] = ()
    # True quando a fonte oferece histórico longo e `wimcg backfill` faz sentido.
    historical: bool = False
    # Tamanho de cada bloco do backfill (um commit por bloco): pequeno para dados de 1 minuto.
    backfill_chunk_days: int = 3650
    # False para datasets grandes demais para a API: só Parquet.
    json_partitions: bool = True
    latest_json: bool = True
    # False para fontes auxiliares: se falharem, a coleta segue e só registra um aviso.
    critical: bool = True
    notes: str = ""

    @property
    def columns(self) -> list[str]:
        return [f.name for f in self.fields]

    @property
    def time_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.type == "time"]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = list(self.key)
        d["latest_group"] = list(self.latest_group)
        return d


def _time(desc: str) -> Field:
    return Field("time", "time", None, desc)


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        id="btc-usd-1m",
        title="Bitcoin em dólar, velas de 1 minuto (Coinbase)",
        description="Abertura, máxima, mínima, fechamento e volume do par BTC-USD na Coinbase "
        "Exchange, minuto a minuto, desde julho de 2015. Minutos sem negociação não têm vela.",
        provider="Coinbase Exchange (API pública de mercado)",
        source_url="https://api.exchange.coinbase.com/products/BTC-USD/candles",
        license="Dados de mercado públicos da Coinbase; cite a fonte.",
        cadence="1min",
        partition="day",
        key=("time",),
        fields=(
            _time("Início do minuto (UTC)"),
            Field("open", "float", "USD", "Preço de abertura"),
            Field("high", "float", "USD", "Preço máximo"),
            Field("low", "float", "USD", "Preço mínimo"),
            Field("close", "float", "USD", "Preço de fechamento"),
            Field("volume", "float", "BTC", "Volume negociado"),
        ),
        schedule="realtime",
        historical=True,
        backfill_chunk_days=30,
        json_partitions=False,
    ),
    Dataset(
        id="fear-greed",
        title="Índice Fear & Greed de cripto (diário)",
        description="Índice de sentimento do mercado de cripto, de 0 (medo extremo) a 100 "
        "(ganância extrema), calculado diariamente pela alternative.me desde fevereiro de 2018.",
        provider="alternative.me",
        source_url="https://api.alternative.me/fng/",
        license="Uso livre com atribuição à alternative.me.",
        cadence="1d",
        partition="year",
        key=("time",),
        fields=(
            _time("Dia de referência (UTC)"),
            Field("value", "int", None, "Índice de 0 a 100"),
            Field("classification", "str", None, "Extreme Fear, Fear, Neutral, Greed ou Extreme Greed"),
        ),
        schedule="realtime",
        update_hours=72,
        historical=True,
        critical=False,
    ),
    Dataset(
        id="big-moves",
        title="Grandes movimentos do Bitcoin",
        description="Variações acima de 3 vezes a volatilidade típica dos 30 dias anteriores, "
        "nas escalas de 1 h e 24 h, detectadas a partir das velas de 1 minuto.",
        provider="Where Is My Crypto Going?",
        source_url="https://github.com/Strutf/where-is-my-crypto-going",
        license="MIT",
        cadence="evento",
        partition="year",
        key=("time", "scale_hours"),
        fields=(
            _time("Fim do movimento (UTC)"),
            Field("scale_hours", "int", "h", "Escala de tempo do movimento (1 ou 24)"),
            Field("start", "time", None, "Início do movimento (UTC)"),
            Field("direction", "str", None, "up ou down"),
            Field("return_pct", "float", "%", "Variação do preço no movimento"),
            Field("sigma_pct", "float", "%", "Volatilidade típica da escala nos 30 dias anteriores"),
            Field("z", "float", None, "Variação dividida pela volatilidade típica"),
            Field("price_start", "float", "USD", "Preço no início"),
            Field("price_end", "float", "USD", "Preço no fim"),
        ),
        schedule="derived",
    ),
    Dataset(
        id="move-news",
        title="Notícias em torno dos grandes movimentos (GDELT)",
        description="Notícias em inglês sobre bitcoin e cripto encontradas pelo GDELT nas 24 h "
        "antes, durante e nas 24 h depois de cada grande movimento, com a fase em relação ao "
        "movimento e categorias de causa por palavras-chave.",
        provider="GDELT Project (DOC 2.0 API)",
        source_url="https://api.gdeltproject.org/api/v2/doc/doc",
        license="GDELT: uso livre com citação. Os títulos pertencem aos veículos originais.",
        cadence="evento",
        partition="year",
        key=("move_time", "scale_hours", "url"),
        fields=(
            _time("Momento em que o GDELT viu a notícia (UTC)"),
            Field("move_time", "time", None, "Fim do movimento relacionado (chave em big-moves)"),
            Field("scale_hours", "int", "h", "Escala do movimento relacionado"),
            Field("phase", "str", None, "before, during ou after, em relação ao movimento"),
            Field("hours_from_start", "float", "h", "Horas entre o início do movimento e a notícia"),
            Field("title", "str", None, "Título da notícia"),
            Field("url", "str", None, "Endereço da notícia"),
            Field("domain", "str", None, "Veículo"),
            Field("source_country", "str", None, "País do veículo"),
            Field("categories", "str", None, "Categorias de causa por palavras-chave, separadas por vírgula"),
        ),
        schedule="derived",
        notes="O horário é quando o GDELT viu a notícia, que pode vir minutos depois da publicação.",
    ),
)

BY_ID: dict[str, Dataset] = {d.id: d for d in DATASETS}


def get(dataset_id: str) -> Dataset:
    try:
        return BY_ID[dataset_id]
    except KeyError:
        raise KeyError(f"dataset desconhecido: {dataset_id}") from None


__all__ = ["BY_ID", "DATASETS", "Dataset", "Field", "get"]
