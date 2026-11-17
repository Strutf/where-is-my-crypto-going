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

Com 14 meses de velas (out/2025 a nov/2026), coletados localmente:

- **Escala de 1 h:** 182 grandes movimentos (81 de alta, 101 de baixa).
- **Escala de 24 h:** 17 grandes movimentos (4 de alta, 13 de baixa).

Maiores movimentos de 24 h:

| Início (UTC) | Variação | Desvios da volatilidade típica |
|---|---|---|
| 19/08/2026 11h | +11,7% | +8,3 |
| 05/02/2026 00h | −14,0% | −6,2 |
| 02/06/2026 04h | −7,2% | −4,5 |
| 20/11/2025 10h | −10,6% | −4,5 |
| 14/11/2026 02h | −8,3% | −4,4 |
| 06/02/2026 00h | +12,3% | +4,4 |

## Sobre a IA

Expectativas registradas desde o início:

- No mercado, padrões previsíveis tendem a desaparecer, porque quem os usa os elimina. A
  referência honesta é o **passeio aleatório**, e a maioria dos modelos de preço não o supera de
  forma consistente.
- Um acerto de 52–55% numa avaliação honesta já seria um resultado real. Se ficar em 50%, isso
  também é resultado e deve ser publicado com transparência.
- A **volatilidade** é bem mais previsível que a direção, e a referência para ela é o GARCH.

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

## Site (plano para a fase 4, definido em 17/11/2026)

- **Idioma:** inglês como principal, com troca para português.
- **Aviso em todas as páginas:** não é recomendação de investimento.
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
    Animações dos personagens ficam para depois.
- **Onde ficam os dados:** tudo no dataset do Hugging Face. A Vercel só hospeda o site e a API.
- **O que o site lê:** `json/btc-usd-1h/<mês>.json` (gráfico), `json/market-state/latest.json`
  (volatilidade e regime agora), `json/btc-news/<dia>.json` (notícias; filtrar por `in_title`,
  porque o tema `ECON_BITCOIN` do GDELT também marca notícias que só citam bitcoin de passagem:
  em 63 notícias de teste, só 19 tratavam do assunto) e `json/big-moves/<ano>.json`.
- **O que ainda falta para o site:** a tabela de previsões, que vem com o modelo.

## Próximos passos

1. Conferir a carga das velas desde 2015 (cobertura, buracos da Coinbase) e os grandes
   movimentos do histórico completo; registrar os números aqui.
2. Conferir as primeiras coletas disparadas pelo cron-job.org (uma a cada 15 min, 1 a 2 min).
3. Conferir a tabela `btc-news` (notícias de bitcoin com título e endereço, ligada em
   17/11/2026; o histórico dela começa nessa data).
4. Acompanhar as notícias dos grandes movimentos (`move-news`), que entram aos poucos, e
   conferir a página do dataset no Hugging Face.
5. Reimportar o histórico de notícias (`backfill: gkg-history`), para todas as partições de
   `gkg-hourly` terem as mesmas colunas e o visualizador do Hugging Face abrir a tabela.
6. **Fase 3, linha de base:** modelo de direção só com preço e Fear & Greed, com avaliação
   completa. Depois, o ganho das notícias sobre a linha de base.
7. **Fase 4:** API e site (ver "Site").

Adiado de propósito: consertar a lacuna do histórico de notícias (ver "GDELT, arquivos brutos").
