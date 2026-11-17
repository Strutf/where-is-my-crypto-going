"""Página do dataset no Hugging Face (README.md), gerada a partir do catálogo.

É regravada junto com o catálogo. Só traz informação estável (o início de cada tabela, não o
fim), para o arquivo não mudar a cada coleta.
"""

from __future__ import annotations

from wimcg.catalog import Dataset

REPO = "https://github.com/Strutf/where-is-my-crypto-going"
KEY = "README.md"
_PARTITION = {"day": "dia", "month": "mês", "year": "ano"}
_SCHEDULE = {
    "realtime": "coletada a cada 15 min pelo GitHub Actions",
    "daily": "coletada uma vez por dia",
    "derived": "calculada pelo projeto a cada coleta",
    "manual": "carregada por um comando próprio",
}


def _front_matter(datasets: list[Dataset], starts: dict[str, str | None]) -> str:
    lines = ["---", "pretty_name: Where Is My Crypto Going?", "language:", "- pt", "tags:"]
    lines += [f"- {tag}" for tag in ("bitcoin", "cryptocurrency", "time-series", "news", "gdelt")]
    # Uma configuração por tabela, para o visualizador do Hugging Face. Só as que já têm dados:
    # uma pasta vazia faz o visualizador acusar erro.
    with_data = [ds for ds in datasets if starts.get(ds.id)]
    if with_data:
        lines.append("configs:")
    for ds in with_data:
        lines += [f"- config_name: {ds.id}", f'  data_files: "parquet/{ds.id}/*.parquet"']
    return "\n".join([*lines, "---"])


def _table(ds: Dataset, start: str | None) -> str:
    out = [f"### `{ds.id}`: {ds.title}", "", ds.description, ""]
    out.append(f"- **Fonte:** {ds.provider}")
    out.append(f"- **Cadência:** {ds.cadence}, {_SCHEDULE[ds.schedule]}")
    out.append(f"- **Desde:** {start[:10] if start else 'ainda sem dados'}")
    out.append(f"- **Arquivos:** `parquet/{ds.id}/`, um por {_PARTITION[ds.partition]}")
    out.append(f"- **Licença:** {ds.license}")
    if ds.notes:
        out.append(f"- **Atenção:** {ds.notes}")
    out += ["", "| Coluna | Tipo | Unidade | Descrição |", "|---|---|---|---|"]
    out += [f"| `{f.name}` | {f.type} | {f.unit or ''} | {f.description} |" for f in ds.fields]
    return "\n".join(out)


def render(datasets: list[Dataset], starts: dict[str, str | None]) -> str:
    """Texto do README.md. `starts` traz o início da cobertura de cada tabela (ou None)."""
    tables = "\n\n".join(_table(ds, starts.get(ds.id)) for ds in datasets)
    return f"""{_front_matter(datasets, starts)}

# Where Is My Crypto Going?

*Open research data on Bitcoin price direction and the news around its large moves. The
documentation below is in Portuguese.*

Dados do projeto de pesquisa [Where Is My Crypto Going?]({REPO}), de Victor Miranda Florido
(UFF). O projeto estuda a **direção do Bitcoin** em 1 h, 4 h e 24 h e as **notícias em torno
dos grandes movimentos** do preço.

**Não é recomendação de investimento.** Nada aqui sugere compra ou venda.

## Como usar

Cada tabela é uma pasta de arquivos Parquet. Toda tabela tem uma coluna `time`, em UTC.

```python
import pandas as pd

hourly = pd.read_parquet("hf://datasets/Strutf/where-is-my-crypto-going/parquet/btc-usd-1h")
```

Também há uma cópia em JSON das tabelas pequenas, lida pelo site: `json/<tabela>/latest.json`
(últimos valores), `json/<tabela>/manifest.json` (cobertura) e `json/catalog.json` (este
catálogo, em formato de máquina).

A coleta roda no GitHub Actions a cada 15 minutos. O código, os testes e o histórico das
decisões estão no [repositório]({REPO}).

## Tabelas

{tables}

## Como citar

Florido, V. M. *Where Is My Crypto Going?* {REPO}

Cada fonte tem os seus termos: cite também a fonte original da tabela usada.
"""
