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
  - ~7 MB por arquivo, sem limite de requisições;
  - as notícias de cripto vêm marcadas com o tema `ECON_BITCOIN`;
  - também trazem os temas de todas as notícias do mundo (conflitos, desastres, acordos);
  - **decisão:** são a fonte das notícias **ao vivo** no Actions (fase 2). Para o histórico dos
    movimentos, a API de busca roda localmente, com paciência.
- **Histórico longo de notícias:** a API de busca é limitada demais, e os arquivos brutos somam
  terabytes. **Caminho escolhido:** o GDELT no Google BigQuery (base pública; o sandbox gratuito
  dá 1 TB de consulta por mês, sem cartão), extraindo contagens e tom por hora desde 2015.
  Ainda não foi feito: depende de o autor ativar o sandbox.
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
- **Coleta:** `ingest` a cada 15 min pelo agendamento nativo. O cron-job.org é opcional por
  enquanto e vale configurar na fase 3, para as previsões serem pontuais.
- **Cargas históricas no Actions:** Fear & Greed desde 2018 e, em seguida, velas desde 2015.

## Próximos passos

1. Conferir se o agendamento nativo do `ingest` dispara a cada ~15 min.
2. Terminar a carga histórica das velas desde 2015 e rodar `wimcg moves` no histórico
   completo.
3. **Fase 3, linha de base:** modelo de direção só com preço e Fear & Greed, com avaliação
   completa.
4. Notícias dos 20 maiores movimentos (GDELT), para a linha do tempo "o que moveu o Bitcoin".
5. Histórico de notícias pelo BigQuery e teste do ganho das notícias sobre a linha de base.
6. Coleta ao vivo das notícias pelos arquivos brutos do GDELT.
7. **Fase 4:** API e site.
