# Contexto do projeto

Resumo da conversa em que o projeto nasceu (novembro de 2026), com as decisões, os motivos e
o que foi testado. Serve para retomar o trabalho, para quem chega ao projeto e para o próprio
autor.

## Origem

O projeto nasceu depois do [Future Space](https://github.com/Strutf/future-space), que prevê
tempestades geomagnéticas com dados abertos. O autor quis aplicar o mesmo esquema ao Bitcoin:
uma API conectada aos dados, uma IA que prevê e um site que mostra. A ideia de partida era que
o BTC segue tendências de mercado e reage ao contexto global, como desastres, guerras e
acordos de paz.

## Decisões e motivos

| Decisão | Motivo |
|---|---|
| Repositório e projeto separados do Future Space | o Future Space tem DOI e vai para uma seleção de mestrado; não misturar |
| Nome **Where Is My Crypto Going?** | "going" indica direção e tendência; "Where does my crypto go?" sugeriria rastrear fundos ou golpes; "crypto", e não "bitcoin", deixa espaço para outras moedas |
| Pergunta principal: **direção** (sobe ou desce), com probabilidade | é a pergunta mínima útil; o tamanho do movimento entra como segunda medida |
| Horizontes de 1 h, 4 h e 24 h | sugeridos e aceitos |
| Dados de 1 minuto, coletas a cada 15 min | a granularidade fina vem do histórico das corretoras; coletar a cada minuto exigiria servidor ligado, fora do esquema gratuito; 15 min é o ritmo do GDELT |
| Grande movimento = variação acima de **3 vezes a volatilidade típica** dos 30 dias anteriores, em 1 h e 24 h | um critério relativo torna os períodos comparáveis ("5% num dia" era comum em 2018 e é raro hoje) |
| Notícias separadas em **antes, durante e depois** do movimento | sugestão do autor de "coletar as notícias do que levou às grandes variações"; separar as fases evita confundir explicação a posteriori com causa |
| Não é recomendação de investimento | óbvio para o autor; registrado no README |

## O que foi testado (e o que mudou por causa disso)

- **Binance:** a API ao vivo bloqueia endereços dos EUA, onde rodam os servidores do GitHub
  Actions. O arquivo histórico público (data.binance.vision) funciona. **Decisão:** o preço vem
  da Coinbase, que funciona dos servidores do GitHub (testado) e tem velas de 1 min desde julho
  de 2015, com até 300 por requisição.
- **Coinbase:** daqui (Brasil) o download faz ~1 requisição/s, e 14 meses de velas levaram
  ~20 min. Dos servidores do GitHub (EUA) deve ser mais rápido.
- **Fear & Greed (alternative.me):** histórico diário desde fev/2018, sem chave.
- **GDELT, API de busca (DOC 2.0):**
  - funciona, por exemplo, para 05/02/2026 (queda de −14%), com notícias da CNN, NBC, Yahoo
    Finance e outros;
  - **mas limita a uma consulta a cada 5 s por IP**, e num teste o IP ficou bloqueado por
    vários minutos depois de poucas consultas;
  - nas manchetes do dia da queda, a maioria **descrevia** a queda ("crash accelerates", "what's
    going on?"). É o que motivou a separação antes, durante e depois.
  - **modos de série temporal** (`timelinevolraw`, `timelinetone`): com janelas de uma semana,
    devolvem valores **por hora** (artigos, total monitorado e tom médio). Só aceitam datas a
    partir de 2017 ("Invalid query start date" para 2015);
  - **dos servidores do GitHub Actions** (workflow `gdelt-probe`, 16/11/2026): só 5 de 20
    consultas passaram, mesmo com ~20 s entre elas; as outras levaram 429. Os IPs compartilhados
    do Actions já gastam o limite. **Não serve para cargas grandes no Actions.**
- **GDELT, arquivos brutos (GKG, a cada 15 min):**
  - são dois arquivos a cada 15 min, de 2 a 5 MB cada: as notícias em inglês e as traduzidas
    de outros idiomas. Sem limite de requisições;
  - as notícias de cripto vêm marcadas com o tema `ECON_BITCOIN`;
  - também trazem os temas de todas as notícias do mundo (conflitos, desastres, acordos);
  - **decisão:** são a fonte das notícias **ao vivo** no Actions (conector `gdelt_gkg`, dataset
    `gkg-hourly`). Para o histórico dos movimentos, a API de busca roda localmente, com paciência;
  - **conferência com o BigQuery (17/11/2026):** em cinco horas de 2015 a 2026, a agregação dos
    8 arquivos da hora é idêntica à do BigQuery em todas as colunas;
  - **o BigQuery tem buracos:** em outras cinco horas testadas (quatro de nov/2026 e uma de
    2021), ele está sem um arquivo inteiro de traduções, embora o arquivo exista e tenha sido
    publicado no horário. A hora fica com 15% a 20% menos notícias. Por isso o dataset tem as
    colunas `en_files` e `translated_files` (arquivos de 15 min que entraram na hora; 4 =
    completa). Ao vivo elas já saem preenchidas; no histórico ficam vazias;
  - **tamanho da lacuna (estimativa, `python research/gkg_gaps.py`):** comparando `articles`
    com o tamanho dos arquivos publicados pelo GDELT, **26.748 das 101.789 horas do histórico
    (26,3%) parecem incompletas no BigQuery**, de 11,8% (2017) a 45,1% (2020) conforme o ano.
    Em 15.804 horas faltam de 10% a 20% das notícias, em 3.391 de 20% a 30% e em 1.543 mais de
    30%. Na fonte, 95,3% das horas têm os 8 arquivos. A estimativa separa bem as 10 horas
    conferidas arquivo por arquivo (completas: razão de 0,99 a 1,02; sem um arquivo de
    traduções: de 0,80 a 0,86), mas não é uma contagem;
  - **decisão (17/11/2026): o histórico fica como está, com a lacuna documentada**, para o
    projeto sair do zero. Consequências a lembrar no treino: as contagens do histórico estão
    subestimadas em cerca de um quarto das horas, e as da coleta ao vivo (desde 16/11/2026) não.
    Proporções (assunto ÷ `articles`) sofrem menos que contagens brutas. O ganho das notícias
    medido na fase 3 pode sair menor do que seria com o histórico correto;
  - **como consertar depois:** (a) marcar as horas com a consulta leve `wimcg gkg-sql --files`,
    que não rodou porque a cota gratuita do BigQuery de novembro acabou nas duas consultas
    grandes; ou (b) refazer o histórico pelos arquivos brutos no Actions, com o mesmo conector
    da coleta ao vivo: são 7,2 TB em 804 mil arquivos (estimativa de ~100 horas de máquina, não
    medida). A opção (b) dispensa o BigQuery;
  - **armadilha do endereço público:** https://data.gdeltproject.org guarda por 1 hora a
    resposta 404 de quem pediu um arquivo antes de ele ser publicado. Em 17/11/2026, dois
    arquivos já publicados e listados ainda davam 404. O conector lê direto do armazenamento
    (`storage.googleapis.com/data.gdeltproject.org`), onde o 404 não é guardado;
  - **dos servidores do GitHub Actions** (workflow `gkg-probe`, 17/11/2026): 2 horas (16
    arquivos) em 3,6 s, com os mesmos números obtidos localmente.
- **Histórico longo de notícias:** a API de busca é limitada demais, e os arquivos brutos somam
  terabytes. **Caminho escolhido:** o GDELT no Google BigQuery (base pública; o sandbox gratuito
  dá 1 TB de consulta por mês, sem cartão), extraindo contagens e tom por hora desde 2015.
  **Feito em 16/11/2026:** a consulta de `wimcg gkg-sql` rodou no console do BigQuery em dois
  pedaços (2015-02-19 a 2020-12-31 e 2021-01-01 a 2026-11-15) e foi exportada em CSV (~10 MB).
  O `wimcg gkg-pack` a guardou em `history/gkg-hourly.parquet` (3,2 MB), e o Actions a grava no
  dataset `gkg-hourly` do Hugging Face. Conferência:
  - são 101.789 horas, sem repetição; 1.123 horas faltam no próprio GDELT, em 131 blocos. O
    maior vai de 14/06 a 02/07/2025 (416 h), e em out-nov/2020 faltam várias madrugadas;
  - 29 horas têm menos de 500 notícias (falha parcial do GDELT): não tratar como zero;
  - o total de notícias por hora cai de ~25 mil (2015) para ~12 mil (2026): usar proporções;
  - `world_uncertainty` é quase nula em 2015 (13/h, contra ~300 em 2016).
  - **Lição:** não mandar arquivos grandes pelo chat do Claude Code, que estoura o limite da
    conversa. Basta dizer onde estão.
- **Lição do Future Space:** o Open-Meteo passou a recusar conexões vindas do GitHub Actions
  (IPs compartilhados). Por isso as fontes auxiliares são marcadas como não críticas: se
  falharem, a coleta segue e só registra um aviso.

## Resultados até agora

### Conferência dos dados no Hugging Face (17/11/2026)

Tabelas baixadas do dataset e conferidas depois da carga histórica:

- **Velas (`btc-usd-1m`, `btc-usd-1h`):** de 20/07/2015 a hoje, 99.271 horas. Faltam 56 horas
  (0,06%), a maior falha com 15 h (10/08/2018) e as outras com até 5 h. Nenhuma hora repetida,
  nenhum preço zero ou máxima abaixo da mínima. A maior variação de 1 h é de março de 2020
  (−19,9% em log).
- **Grandes movimentos:** 1.961 de 1 h e 218 de 24 h no histórico inteiro, de 147 a 216 por ano
  (de 1 h) e de 14 a 27 por ano (de 24 h). Os maiores de 24 h: 13/03/2020 (−45,4%, −20,2 desvios)
  e 15/11/2018 (−15,6%, −18,6 desvios).
- **Estado do mercado:** 17 viradas de regime desde 2018 pelo cruzamento das médias. A
  convenção atrasa: a virada para "bear" de 2022 só veio em 15/01/2022, e a de volta para "bull"
  em 09/09/2026 aconteceu com o preço já 30% abaixo do pico.
- **Fear & Greed:** 3.208 dias desde 01/02/2018; faltam 4.
- **Notícias por hora:** 101.829 horas, sem repetição, sem nenhuma hora faltando desde o fim do
  histórico do BigQuery (15/11 23h); as 40 horas coletadas ao vivo têm os 8 arquivos.
- **Notícias de bitcoin (`btc-news`):** 213 desde 15/11, de 56 veículos; 86 com cripto no título.
- **Notícias dos grandes movimentos (`move-news`):** em ~13 h de coleta, só 15 das 5.511 fases
  (3 por movimento desde 2017) passaram pelo limite da busca do GDELT. Nesse ritmo levaria meses.
  Não afeta o treino; para a linha do tempo do site, rodar também localmente, com paciência.
- **Coleta:** 51 execuções seguidas sem falha desde o fim da carga, cerca de 1 min cada.

### Primeira comparação de modelos (17/11/2026)

`wimcg ml-data && wimcg ml-eval && wimcg ml-report`, com o dataset na revisão `ff2a4e24d7`.
Números completos em `research/results/direction_eval.md`. Teste mês a mês de jan/2019 a
out/2026, treino nos 3 anos anteriores; três famílias de modelos (gradient boosting, rede densa,
rede recorrente) com quatro conjuntos cumulativos de variáveis; redes com 3 sementes.

- **1 h:** todos os modelos de preço acertam ~54%, contra 50,7% de "sempre alta". Mas uma regra
  trivial, dizer o contrário da última hora, já acerta 53,1%: os modelos ganham só ~1 ponto dela.
  O ganho cai com o tempo (55% em 2019-2020, 52,4% em 2025-2026), e o retorno médio na direção
  prevista é de 0,8 pb por hora antes de custos, negativo em 2025-2026, muito abaixo de qualquer
  taxa de negociação. A reversão de curto prazo é real, mas pequena e sumindo.
- **4 h:** ~53,7% contra 51,1% de "sempre alta", mas igual à regra de inverter (53,2%); a
  probabilidade não é melhor que a taxa de alta da janela de treino.
- **24 h:** nenhum modelo supera "sempre alta" (52,2%). As probabilidades são piores que a taxa de
  alta da janela de treino (Brier skill de −2% a −17%): os modelos se ajustam a padrões que não
  se repetem no mês seguinte.
- **Grupos de variáveis:** estado do mercado, Fear & Greed e notícias não acrescentam nada ao
  gradient boosting. Nas redes neurais, pioram: mais variáveis, mais ajuste ao ruído (a rede
  simples cai de 54,1% para 52,5% em 1 h com as notícias).
- **Redes neurais:** não superam o gradient boosting. A rede de sequência, que lê as últimas 168
  horas, empata com a rede simples sobre as mesmas variáveis: ler a sequência não acrescentou nada.

**Volatilidade (17/11/2026):** mesma avaliação, com `rv_next` = raiz da soma dos quadrados dos
retornos horários das próximas *h* horas. Erro absoluto médio, em pontos percentuais:

| Horizonte | Persistência | HAR | Gradient boosting | Ganho do gb sobre a persistência / o HAR |
|---|---|---|---|---|
| 1 h | 0,359 | 0,283 | 0,273 | 24,0% / 3,7% |
| 4 h | 0,575 | 0,471 | 0,433 | 24,6% / 8,1% |
| 24 h | 1,088 | 0,931 | 0,864 | 20,6% / 7,2% |

Ainda sem intervalo de confiança; o relatório de cada treino mensal guarda os números por mês.

**Leitura honesta:** não há sinal de direção aproveitável nestes dados e horizontes. O resultado
confirma a expectativa registrada em "Sobre a IA" e é publicável assim. Antes de qualquer
previsão ir ao site, ela precisa superar a regra de inverter, não só 50%.

Lições do processo:
- a regra de inverter só entrou como referência depois do primeiro relatório; sem ela, os 54%
  pareceriam ganho do modelo;
- rodadas em paralelo precisam de 1 núcleo cada: com mais, elas disputaram o processador e não
  terminaram (gb: 3,4 s por mês sozinho, mais de 35 min sem terminar em paralelo).

### Primeiros números locais (14 meses, out/2025 a nov/2026)

- **Escala de 1 h:** 182 grandes movimentos (81 de alta, 101 de baixa).
- **Escala de 24 h:** 17 grandes movimentos (4 de alta, 13 de baixa).

## Sobre a IA

Expectativas registradas desde o início:

- No mercado, padrões previsíveis tendem a desaparecer, porque quem os usa os elimina. A
  referência honesta é o **passeio aleatório**, e a maioria dos modelos de preço não o supera de
  forma consistente.
- Um acerto de 52–55% numa avaliação honesta já seria um resultado real. Se ficar em 50%, isso
  também é resultado e deve ser publicado com transparência.
- A **volatilidade** é bem mais previsível que a direção, e a referência para ela é o GARCH.

- **Redes neurais (17/11/2026):** entram como comparação, depois da linha de base, nas mesmas
  divisões de tempo e com as mesmas medidas. Em variáveis tabulares o gradient boosting costuma
  empatar ou ganhar; o que uma rede acrescenta é ler a sequência de preços diretamente. A
  expectativa é de pouco ou nenhum ganho, e um "não ganhou" medido também é resultado.

Plano:

1. **Previsão:** gradient boosting (como no Future Space), com um classificador para a
   direção e um regressor para o retorno.
   - **Variáveis:** retornos em várias janelas, volatilidade, amplitude, volume, hora e dia da
     semana, Fear & Greed.
   - **Treino:** janela móvel (o mercado muda de regime) e validação andando para a frente.
2. **Notícias:** sem IA própria no começo.
   - Os temas e o tom do GDELT, depois o FinBERT (modelo aberto de sentimento financeiro),
     viram variáveis do mesmo modelo.
   - O ganho é medido com e sem as notícias.
   - Uma IA de linguagem grande, que tem custo, fica para analisar só os maiores movimentos.

## Infraestrutura (estado em 16/11/2026)

- **Repositório:** https://github.com/Strutf/where-is-my-crypto-going (público).
- **API e site:** https://where-is-my-crypto-going.vercel.app (documentação em `/docs`).
- **Dataset:** https://huggingface.co/datasets/Strutf/where-is-my-crypto-going (público, licença
  em branco de propósito: os dados têm termos de várias fontes).
- **Configuração no GitHub:** secret `HF_TOKEN` (token fine-grained só deste dataset, chamado
  `wimcg-github`) e variável `FS_HF_REPO`.
- **Coleta:** o agendamento nativo do `ingest` não disparou nenhuma vez nas primeiras 3h30 do
  repositório (como já acontecia no Future Space). Desde 16/11/2026 o disparo vem do
  cron-job.org, a cada 15 min, com um token do GitHub só deste repositório (permissão Actions:
  leitura e escrita). **Quando o token expirar, a coleta para:** renovar antes.
- **Cargas históricas no Actions:** Fear & Greed desde 2018 (feita), notícias por hora desde
  2015 (feita, `backfill: gkg-history`) e velas desde 2015 (iniciada em 16/11/2026, ~52 s por
  bloco de 30 dias).
- **Derivados (`wimcg derive`, a cada coleta):**
  - **velas de 1 hora** (`btc-usd-1h`): as últimas 48 h são reagrupadas a cada coleta;
  - **grandes movimentos:** lê 46 dias de velas e recalcula os últimos 14;
  - **estado do mercado** (`market-state`): volatilidade de 24 h e sua posição no último ano
    (calm, normal, volatile, extreme), regime (bull se a média de 50 dias está acima da de 200
    dias) e queda desde o pico de 365 dias. Lê um ano de velas de 1 hora;
  - o histórico inteiro só é refeito com `--full`, depois de uma carga de velas, ou sozinho
    quando a tabela ainda não existe. Nas velas locais (out/2025 a nov/2026), o cálculo
    recente dá o mesmo resultado que o completo.
- **Rótulo de regime (decidido em 17/11/2026):** fica a convenção usual, o cruzamento das
  médias de 50 e 200 dias. Ela demora a reagir: em 16/11/2026 dizia "bull" com o preço 33,65%
  abaixo do pico do ano e abaixo das duas médias. Por isso o site deve mostrar, ao lado do
  rótulo, a queda desde o pico e o preço contra as médias, que já estão em `market-state`.
- **Notícias dos grandes movimentos:** `wimcg news --limit 1 --quick` em cada coleta. Cada fase
  (antes, durante, depois) é gravada assim que chega e marcada em `json/move-news/done.json`,
  então a busca continua de onde parou. A busca do GDELT recusa a maioria das consultas.
- **Página do dataset:** o `README.md` do Hugging Face é gerado do catálogo a cada coleta.
- **API (publicada em 17/11/2026 em https://where-is-my-crypto-going.vercel.app):** FastAPI no mesmo formato do Future Space, só de
  leitura, sem pandas. Rotas: `/v1/datasets`, `/v1/datasets/{id}/latest` e
  `/v1/datasets/{id}/data?start=&end=&fields=`. As tabelas só em Parquet (`btc-usd-1m`,
  `gkg-hourly`) não passam pela API. Com as velas locais: estado atual em 1 ms, 7 dias de velas
  em 20 KB, 1 ano de fechamentos por hora em 428 KB. Na Vercel (projeto ligado ao repositório,
  variável `FS_HF_REPO`, deploy a cada envio para o `main`): primeira resposta em 2 a 4 s,
  depois ~0,2 s, com cache da CDN e compressão funcionando. Previsões e site entram depois.

## Site (plano para a fase 4, definido em 17/11/2026)

- **Idioma:** inglês como principal, com troca para português.
- **Aviso em todas as páginas:** não é recomendação de investimento.
- **Celular:** o site precisa funcionar bem em telas pequenas, como o do Future Space. No
  gráfico, tocar num ponto faz o papel de passar o mouse.
- **Design:** o autor desenha e envia depois; aqui fica só o conteúdo.
- **Páginas:**
  1. **Início:** gráfico do Bitcoin agora, se está mais volátil que o normal ou calmo, se o
     regime é de alta ou de baixa (bull/bear), grandes movimentos marcados. Não depende do modelo.
  2. **Previsões:** direção em 1 h, 4 h e 24 h com probabilidade, e o acerto público: acerto da
     direção contra 50% e erro do retorno previsto contra quem prevê "fica igual" (prever +0,5%
     e dar −0,1% é um erro pequeno; prever +70% é um erro enorme). Cada previsão é gravada na
     hora em que é feita, numa tabela que nunca é reescrita. Um indicador de mudança de regime
     entra como experimental: há poucas viradas no histórico para validá-lo.
  3. **Notícias:** as notícias recentes de bitcoin (`btc-news`) e a linha do tempo "o que moveu
     o Bitcoin", com as notícias de antes, durante e depois de cada grande movimento.
  4. **Sobre:** dados, fórmulas, modelo, avaliação e limitações.
- **Só veículos grandes (decidido em 17/11/2026):** as tabelas que guardam endereços de
  notícias (`btc-news` e `move-news`) só aceitam veículos de uma lista feita à mão
  (`src/wimcg/outlets.py`, cerca de 160 domínios: agências, grandes jornais e emissoras, imprensa
  financeira e os principais veículos de cripto), para o site não mandar o leitor a um site
  desconhecido ou malicioso. Numa amostra de 14 horas, a lista manteve 61 de 323 notícias (19%)
  e 25 das 135 com cripto no título. As contagens de `gkg-hourly` continuam com todas as
  fontes, porque ali não há endereço. No site, os links devem abrir com
  `rel="noopener noreferrer nofollow"`.
- **Esboço do autor (17/11/2026):**
  - cabeçalho com o nome do site e um shiba inu segurando uma moeda de bitcoin; as abas à
    direita: Início, Previsões, Sobre o modelo, Notícias;
  - gráfico no centro; ao passar o mouse ou clicar num período, mostrar os valores exatos;
  - ao lado do gráfico, um personagem conforme o regime: em baixa, um urso polar com neve
    caindo; em alta, um touro correndo de um lado para o outro, soltando fumaça pelo nariz.
    Animações dos personagens ficam para depois;
  - **imagens:** o site começa sem os personagens. O código deve deixar os três lugares prontos
    (shiba no cabeçalho, urso e touro ao lado do gráfico), e o autor envia as imagens depois,
    em PNG com fundo transparente. Desenho feito por código não serve: o autor não gosta do
    resultado. Neve e fumaça podem ser feitas por código.
- **Onde ficam os dados:** tudo no dataset do Hugging Face. A Vercel só hospeda o site e a API.
- **O que o site lê:** `json/btc-usd-1h/<mês>.json` (gráfico), `json/market-state/latest.json`
  (volatilidade e regime agora), `json/btc-news/<dia>.json` (notícias; filtrar por `in_title`,
  porque o tema `ECON_BITCOIN` do GDELT também marca notícias que só citam bitcoin de passagem:
  em 63 notícias de teste, só 19 tratavam do assunto) e `json/big-moves/<ano>.json`.
- **O que ainda falta para o site:** a tabela de previsões, que vem com o modelo.

## Previsões publicadas (decidido em 17/11/2026)

- **O que vai ao ar:** direção (gradient boosting só com preço) e volatilidade (gradient boosting
  com preço e estado do mercado), em 1, 4 e 24 h. A direção é publicada mesmo sem superar as
  regras simples, como verificação pública; a volatilidade supera a persistência e o HAR.
- **Como no Future Space:** cada previsão é conferida com o que aconteceu e vai para uma tabela
  (`verification`), com as referências ao lado (sempre alta, inverter, persistência).
- **Treino mensal no Actions** (`train.yml`, dia 1 às 03:17 UTC): avalia mês a mês desde 2019 e
  publica só se, nos meses em comum com o modelo atual, o acerto da direção não cair mais de
  0,5 ponto e o erro da volatilidade não subir mais de 2%. Disparo pelo agendamento do GitHub e,
  se o autor configurar, também pelo cron-job.org (mesmo token da coleta).
- **Dependências:** o grupo `ml` (scikit-learn, com versões fixas iguais às do Future Space) vai
  ao Actions; o PyTorch fica no grupo `nn`, só para a pesquisa local.

## Próximos passos

1. Primeiro treino publicado (`train` com `force`) e conferir as primeiras previsões e verificações.
2. Notícias dos grandes movimentos (`move-news`) também localmente, para a linha do tempo do site.
3. Confirmar que o visualizador do Hugging Face voltou depois da reimportação do histórico de
   notícias (17/11/2026, todas as partições de `gkg-hourly` com as mesmas colunas).
4. **Fase 4:** API (publicada) e site (ver "Site").
5. Depois do site no ar: testar outras variáveis (financiamento dos contratos futuros, dados da
   blockchain, juros e dólar), com a mesma avaliação e a regra de inverter como referência.

Adiado de propósito: consertar a lacuna do histórico de notícias (ver "GDELT, arquivos brutos").
