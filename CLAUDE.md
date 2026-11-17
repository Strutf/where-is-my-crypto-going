# CLAUDE.md

Orientações para o Claude Code neste repositório. O histórico das decisões, com os motivos,
está em [`docs/contexto.md`](docs/contexto.md).

## Projeto

**Where Is My Crypto Going?** é um projeto de estudo e pesquisa de Victor Miranda Florido
(UFF; ORCID 0009-0002-4580-2122). Ele prevê a **direção do Bitcoin** (sobe ou desce, com
probabilidade) em 1 h, 4 h e 24 h, e analisa as **notícias por trás dos grandes movimentos**.
É projeto irmão do [Future Space](https://github.com/Strutf/future-space), de quem reaproveita
a arquitetura.

**Não é recomendação de investimento.** Nada no código, no site ou nos textos deve sugerir
compra ou venda.

## Regras de trabalho

- **Commits:**
  - sempre com `-c user.email=145017694+Strutf@users.noreply.github.com -c user.name="Victor Miranda Florido"`;
  - **nunca** incluir `Co-Authored-By` nem qualquer atribuição ao Claude em commits ou PRs;
  - mensagens em português: um título curto e um parágrafo explicando o porquê;
  - commitar só depois de `ruff check src tests` e `pytest -q` passarem.
- **Segredos:**
  - o token do Hugging Face fica **só** nos secrets do GitHub (`HF_TOKEN`);
  - nunca pedir, mostrar ou gravar tokens no código, nos logs ou no chat;
  - para dados locais, use `FS_STORAGE=local` (o padrão).
- **Idioma:** textos, comentários e documentação em português. O código (nomes de variáveis e
  funções) fica em inglês, como já está.
- **Fontes externas:** antes de depender de uma fonte, teste-a de verdade, dos servidores do
  GitHub Actions quando ela for rodar lá. Ver os achados em `docs/contexto.md`.
- **Infraestrutura:** tudo em planos gratuitos (GitHub Actions público, Hugging Face, Vercel).
  Não introduzir serviços pagos sem perguntar.
- **Honestidade nos resultados:** nunca inventar números. Toda métrica sai de um comando do
  repositório e é comparada com referências simples (50%, passeio aleatório).

## Comandos

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,hf]"
wimcg update                                  # dados recentes
wimcg backfill btc-usd-1m --start 2015-07-20  # histórico (blocos de 30 dias)
wimcg derive                                  # velas de 1 h, grandes movimentos e estado do mercado (--full: histórico inteiro)
wimcg news --limit 20                         # notícias dos maiores movimentos (GDELT); --quick para na 1ª recusa
wimcg gkg-sql --start 2015-02-19 --end 2021-01-01   # consulta do BigQuery (notícias por hora)
wimcg gkg-sql --files --start 2015-02-19 --end 2026-11-17   # consulta leve: arquivos por hora
wimcg gkg-pack a.csv b.csv --files f.csv      # CSVs do BigQuery -> history/gkg-hourly.parquet
wimcg gkg-import                              # grava o histórico no armazenamento
uvicorn wimcg.api.app:app --reload            # API local, lendo ./data
wimcg train --publish-if-better               # treina, avalia mês a mês e publica se não piorar (workflow train)
wimcg predict && wimcg verify                 # previsões da última hora e verificação (na coleta)
pip install -e ".[ml,nn]"                     # pesquisa: scikit-learn e PyTorch (o nn não vai ao Actions)
wimcg ml-data                                 # baixa as tabelas do Hugging Face e monta a tabela por hora
wimcg ml-eval                                 # avaliação mês a mês de gb, mlp e gru (previsões em data/ml/)
wimcg ml-report                               # resumo em research/results/direction_eval.{json,md}
ruff check src tests && pytest -q
```

## Estrutura

- `src/wimcg/catalog.py`: datasets, com campos, chave, partição e se a fonte é crítica.
- `src/wimcg/pipeline/`:
  - `connectors/`: um conector por fonte, com a assinatura `(client, start, end) -> DataFrame`;
  - `writer.py`: gravação idempotente em Parquet/JSON;
  - `http.py`: cliente HTTP com novas tentativas.
- `src/wimcg/moves.py`: detecção dos grandes movimentos.
- `src/wimcg/market.py`: velas de 1 hora e estado do mercado (volatilidade, regime, queda desde o pico).
- `src/wimcg/card.py`: página do dataset no Hugging Face, gerada do catálogo.
- `src/wimcg/api/app.py`: API de leitura (FastAPI, sem pandas). Na Vercel entra por `api/index.py`
  e `vercel.json`, com as dependências de `requirements.txt`. O site vai morar no mesmo aplicativo.
- `src/wimcg/news.py`: notícias de antes, durante e depois de cada movimento (GDELT DOC API).
- `src/wimcg/ml/`: pesquisa do modelo de direção (roda localmente, não no Actions):
  - `features.py`: tabela por hora, com as variáveis em grupos e sem informação do futuro
    (testes em `tests/test_ml.py`);
  - `models.py`: gradient boosting, rede densa e rede recorrente, com a mesma interface;
  - `evaluate.py`: avaliação andando para a frente, medidas e intervalos de confiança;
  - `run.py`: rodadas em paralelo e relatório;
  - `volatility.py`: volatilidade realizada, referências (persistência, HAR) e gradient boosting;
  - `train.py`: treino mensal publicado, avaliação mês a mês e trava de publicação;
  - `serve.py`: previsão a cada coleta (tabela `predictions`, nunca reescrita), verificação
    (`verification`) e resumo para o site.
- `src/wimcg/outlets.py`: lista dos veículos grandes aceitos nas tabelas que guardam endereços de notícias.
- `src/wimcg/gkg.py`: notícias por hora do GKG (consulta do BigQuery e agregação ao vivo).
  Ao vivo, o conector `gdelt_gkg` lê os arquivos brutos de 15 min e alimenta `gkg-hourly`
  (contagens) e `btc-news` (notícias de bitcoin com título e endereço).
- `history/gkg-hourly.parquet`: histórico exportado do BigQuery (~3 MB). O Actions o grava no
  Hugging Face, já que o token só existe lá.
- `research/gkg_gaps.py`: estimativa das horas incompletas no histórico do BigQuery (~26%).
  A lacuna foi aceita por enquanto e está descrita no README e em `docs/contexto.md`.
- `.github/workflows/train.yml`: treino mensal (dia 1), com a trava de publicação.
- `.github/workflows/ingest.yml`: coleta a cada 15 min (disparada pelo cron-job.org), previsão e
  verificação, mais
  carga histórica por `workflow_dispatch` (`backfill`, `desde`, `ate`; `backfill: gkg-history`
  importa o Parquet).

## Cuidados de avaliação (fase 3 em diante)

- Validação **andando para a frente** (treina até o mês *m*, testa em *m+1*), nunca aleatória.
- Amostras sobrepostas: avaliar em blocos não sobrepostos ou com testes que considerem a
  dependência entre amostras.
- Sem vazamento do futuro: a vela só entra depois de fechada, a notícia só depois de vista
  pelo GDELT.
- Medir o ganho de cada grupo de variáveis (com e sem), como no Future Space com as CMEs.
