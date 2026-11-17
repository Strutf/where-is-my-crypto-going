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
# "manual": carregado por um comando próprio (ex.: histórico exportado do BigQuery), sem coleta
# automática ainda.
Schedule = Literal["realtime", "daily", "derived", "manual"]


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
    # True quando uma linha gravada não muda mais: o `update` busca só o que vem depois da última,
    # em vez de reler as últimas `update_hours`.
    append_only: bool = False
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


def _gkg_fields() -> tuple[Field, ...]:
    """Campos de gkg-hourly, gerados das mesmas definições da consulta (wimcg.gkg.TOPICS)."""
    from wimcg.gkg import TOPICS

    fields = [
        _time("Início da hora (UTC), pelo campo DATE do GKG"),
        Field("articles", "int", "notícias", "Notícias no GKG na hora (todas, do mundo inteiro)"),
        Field("tone", "float", None, "Tom médio das notícias (V2Tone, de -100 a +100; na prática entre -10 e +10)"),
        Field("btc_articles", "int", "notícias", "Notícias com o tema ECON_BITCOIN"),
        Field("btc_tone", "float", None, "Tom médio das notícias de bitcoin (vazio se não houve nenhuma)"),
    ]
    for tp in TOPICS:
        if tp.bitcoin:
            fields.append(Field(f"btc_{tp.name}_articles", "int", "notícias", f"Notícias de bitcoin com algum destes temas: {', '.join(tp.themes)}"))
    for tp in TOPICS:
        if tp.world:
            fields.append(Field(f"world_{tp.name}_articles", "int", "notícias", f"Notícias do mundo com algum destes temas: {', '.join(tp.themes)}"))
    fields += [
        Field("en_files", "int", "arquivos", "Arquivos de 15 min de notícias em inglês que entraram na hora (4 = completa)"),
        Field("translated_files", "int", "arquivos", "Arquivos de 15 min de notícias traduzidas que entraram na hora (4 = completa)"),
    ]
    return tuple(fields)


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
        id="btc-usd-1h",
        title="Bitcoin em dólar, velas de 1 hora",
        description="As velas de 1 minuto da Coinbase agrupadas por hora: abertura, máxima, "
        "mínima, fechamento e volume. É o que o gráfico do site lê. Só entram horas fechadas.",
        provider="Where Is My Crypto Going? (a partir de btc-usd-1m)",
        source_url="https://github.com/Strutf/where-is-my-crypto-going",
        license="Dados de mercado públicos da Coinbase; cite a fonte.",
        cadence="1h",
        partition="month",
        key=("time",),
        fields=(
            _time("Início da hora (UTC)"),
            Field("open", "float", "USD", "Preço de abertura"),
            Field("high", "float", "USD", "Preço máximo"),
            Field("low", "float", "USD", "Preço mínimo"),
            Field("close", "float", "USD", "Preço de fechamento"),
            Field("volume", "float", "BTC", "Volume negociado"),
        ),
        schedule="derived",
        notes="Horas sem nenhuma negociação não têm vela.",
    ),
    Dataset(
        id="market-state",
        title="Estado do mercado: volatilidade e regime",
        description="Para cada hora, medidas que descrevem o momento do Bitcoin usando só o "
        "passado: se o mercado está mais agitado ou mais calmo que o normal, se o regime é de "
        "alta ou de baixa e a distância até o pico do último ano. Descreve, não prevê.",
        provider="Where Is My Crypto Going? (a partir de btc-usd-1h)",
        source_url="https://github.com/Strutf/where-is-my-crypto-going",
        license="MIT",
        cadence="1h",
        partition="month",
        key=("time",),
        fields=(
            _time("Início da hora (UTC); o estado vale para o fim dela"),
            Field("close", "float", "USD", "Preço de fechamento da hora"),
            Field("volatility_24h", "float", "%", "Desvio-padrão dos últimos 24 retornos horários, vezes raiz de 24"),
            Field("volatility_percentile", "float", None, "Posição da volatilidade entre os 365 dias anteriores (0 a 100)"),
            Field("volatility_state", "str", None, "calm (percentil < 25), normal, volatile (> 75) ou extreme (> 95)"),
            Field("ma_50d", "float", "USD", "Média dos fechamentos horários dos últimos 50 dias"),
            Field("ma_200d", "float", "USD", "Média dos fechamentos horários dos últimos 200 dias"),
            Field("regime", "str", None, "bull se a média de 50 dias está acima da de 200 dias; bear se está abaixo"),
            Field("high_365d", "float", "USD", "Preço máximo dos últimos 365 dias"),
            Field("drawdown_pct", "float", "%", "Distância do fechamento até esse máximo (0 = no pico)"),
        ),
        schedule="derived",
        notes="O regime é uma convenção (cruzamento das médias de 50 e 200 dias) e só confirma "
        "uma virada semanas depois de ela acontecer. Campos vazios = histórico ainda insuficiente "
        "(30 dias para o pico, 90 para o percentil, 180 para a média longa). Não é recomendação "
        "de investimento.",
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
    Dataset(
        id="gkg-hourly",
        title="Notícias por hora: bitcoin e assuntos do mundo (GDELT GKG)",
        description="Contagem e tom médio das notícias do GKG do GDELT a cada hora, desde "
        "fevereiro de 2015: todas, as de bitcoin e as de assuntos que podem mover o mercado "
        "(juros, bolsas, regulação, ataques hackers, guerras, sanções, incerteza).",
        provider="GDELT Project (GKG 2.1): Google BigQuery no histórico, arquivos brutos ao vivo",
        source_url="https://www.gdeltproject.org/data.html#rawdatafiles",
        license="GDELT: uso livre com citação.",
        cadence="1h",
        partition="month",
        key=("time",),
        fields=_gkg_fields(),
        schedule="realtime",
        # Só vale sem nenhum dado gravado; depois o `update` continua da última hora.
        update_hours=3,
        append_only=True,
        historical=True,
        backfill_chunk_days=2,
        # Em JSON seriam ~54 MB (contra ~7 MB em Parquet), sem uso até a API existir.
        json_partitions=False,
        critical=False,
        notes="Uma hora só é gravada depois que o GDELT publica todos os arquivos dela (cerca de "
        "20 min depois do fim da hora). Horas em que o GDELT não publicou nada não têm linha (a "
        "maior falha vai de 14/06 a 02/07/2025). Quando en_files ou translated_files é menor que "
        "4, faltou um arquivo de 15 min e as contagens da hora estão subestimadas. LACUNA "
        "CONHECIDA: até 15/11/2026 os dados vêm do BigQuery, que está sem arquivos inteiros "
        "(quase sempre de traduções) em cerca de um quarto das horas (estimativa de 26%, por "
        "research/gkg_gaps.py); nelas faltam em geral de 10% a 20% das notícias, e as colunas "
        "de arquivos estão vazias porque não foi possível marcá-las. De 16/11/2026 em diante os "
        "dados vêm dos arquivos brutos e são completos. Prefira proporções (assunto / articles) "
        "a contagens brutas. Os temas de EPU_UNCERTAINTY só se firmam em 2016; antes disso "
        "trate world_uncertainty_articles como ausente.",
    ),
    Dataset(
        id="btc-news",
        title="Notícias de bitcoin, uma a uma (GDELT GKG)",
        description="Cada notícia que o GDELT marcou com o tema de bitcoin (ECON_BITCOIN), em "
        "qualquer idioma: título, endereço, veículo, tom e assuntos. Alimenta a página de notícias "
        "do site. Começa em novembro de 2026, quando a coleta foi ligada.",
        provider="GDELT Project (GKG 2.1, arquivos brutos)",
        source_url="https://www.gdeltproject.org/data.html#rawdatafiles",
        license="GDELT: uso livre com citação. Os títulos pertencem aos veículos originais.",
        cadence="15min",
        # Por dia, para o arquivo regravado a cada hora ser pequeno.
        partition="day",
        key=("time", "url"),
        fields=(
            _time("Arquivo de 15 min do GDELT em que a notícia apareceu (UTC)"),
            Field("url", "str", None, "Endereço da notícia"),
            Field("title", "str", None, "Título, no idioma original (vazio se o GDELT não o registrou)"),
            Field("in_title", "bool", None, "True se o título cita bitcoin ou criptomoedas (ver wimcg.news)"),
            Field("domain", "str", None, "Veículo"),
            Field("language", "str", None, "Idioma original, no código do GDELT (eng = inglês)"),
            Field("tone", "float", None, "Tom da notícia (V2Tone; negativo = tom negativo)"),
            Field("topics", "str", None, "Assuntos de gkg-hourly presentes na notícia, separados por vírgula"),
        ),
        schedule="realtime",
        # Só vale sem nenhum dado gravado: a primeira coleta busca os dois últimos dias.
        update_hours=48,
        append_only=True,
        historical=True,
        backfill_chunk_days=2,
        critical=False,
        notes="O tema do GDELT é largo: entram notícias que só citam bitcoin no meio do texto. "
        "Para ficar só com as que tratam do assunto, filtre por in_title. Uma hora só é gravada "
        "depois que o GDELT publica todos os arquivos dela.",
    ),
)

BY_ID: dict[str, Dataset] = {d.id: d for d in DATASETS}


def get(dataset_id: str) -> Dataset:
    try:
        return BY_ID[dataset_id]
    except KeyError:
        raise KeyError(f"dataset desconhecido: {dataset_id}") from None


__all__ = ["BY_ID", "DATASETS", "Dataset", "Field", "get"]
