# Direção do Bitcoin: comparação dos modelos

Gerado por `wimcg ml-report` em 2026-11-18T22:30:46Z, com o dataset na revisão `e27e190d8f`. Teste fora da amostra, mês a mês, de 2019-01 a 2026-10: treino nos 3 anos anteriores, sem as últimas 24 h antes de cada mês. Intervalos de 95% por bootstrap em blocos de 168 h.

**Não é recomendação de investimento.**

Colunas:

- **acerto:** % das horas em que a direção prevista (probabilidade > 50%) aconteceu;
- **vs sempre alta:** acerto menos o de quem sempre diz que sobe, em pontos percentuais;
- **vs inverter:** acerto menos o de quem diz o contrário do que aconteceu nas últimas *h* horas;
- **retorno (pb):** retorno médio por previsão na direção prevista, em pontos-base (0,01%), antes de qualquer custo de negociação. Mede o tamanho do acerto; não é estratégia;
- **Brier skill:** ganho da probabilidade sobre a taxa de alta da janela de treino (0 = igual, > 0 = melhor);
- **AUC:** 0,5 = não separa altas de baixas;
- **erro do retorno:** ganho sobre prever retorno zero (passeio aleatório), pelo erro absoluto médio.

## Horizonte de 1 h

| Modelo | Variáveis | Sementes | Acerto (%) | vs sempre alta (p.p.) | vs inverter (p.p.) | Brier skill (%) | AUC | Erro do retorno (%) | Retorno (pb) |
|---|---|---|---|---|---|---|---|---|---|
| gb | price | 1 | 54.1 [53.8, 54.5] | +3.4 [+2.9, +3.8] | +1.0 [+0.6, +1.4] | +1.0 [+0.8, +1.1] | 0.556 | +0.7 [+0.5, +0.8] | +0.77 [+0.36, +1.22] |
| gb_noes | price | 1 | 54.0 [53.7, 54.4] | +3.3 [+2.8, +3.8] | +0.9 [+0.5, +1.3] | +0.7 [+0.5, +0.9] | 0.555 | +0.6 [+0.5, +0.8] | +0.86 [+0.43, +1.31] |
| gb_tuned | price | 1 | 54.2 [53.9, 54.5] | +3.5 [+3.0, +3.9] | +1.1 [+0.7, +1.5] | +0.9 [+0.7, +1.0] | 0.555 | +0.6 [+0.5, +0.8] | +0.84 [+0.43, +1.25] |
| logit | price | 1 | 53.0 [52.6, 53.3] | +2.2 [+1.8, +2.7] | -0.1 [-0.4, +0.2] | +0.5 [+0.4, +0.6] | 0.543 | -0.2 [-0.2, -0.1] | +0.32 [-0.08, +0.79] |

Recortes (acerto do modelo / da regra de inverter, % das horas):

| Modelo | Variáveis | p > 0,52 | p > 0,55 | p > 0,60 | Depois de movimento grande | Movimento futuro grande (descritivo) |
|---|---|---|---|---|---|---|
| gb | price | 55.1 / 53.7 (75.7) | 56.7 / 55.2 (42.5) | 60.7 / 59.3 (7.6) | 53.3 / 53.0 (1.9) | 41.6 / 42.9 (7.0) |
| gb_noes | price | 54.9 / 53.6 (79.9) | 56.2 / 54.5 (51.6) | 58.6 / 56.7 (18.2) | 52.8 / 53.0 (1.9) | 42.0 / 42.9 (7.0) |
| gb_tuned | price | 55.0 / 53.7 (76.3) | 56.4 / 55.2 (43.2) | 60.0 / 58.7 (7.4) | 54.3 / 53.0 (1.9) | 41.6 / 42.9 (7.0) |
| logit | price | 54.8 / 54.7 (53.5) | 56.3 / 56.4 (17.4) | 54.7 / 54.8 (3.2) | 53.4 / 53.0 (1.9) | 42.1 / 42.9 (7.0) |

Brier skill sem e com a calibração de 18/11 (de 2020-01), e comparação pareada com a base:

| Modelo | Variáveis | Brier skill sem | com | Diferença (IC 95%) | Acerto − base (IC 95%) | Brier skill − base (IC 95%) | Escolhas (gb_tuned) |
|---|---|---|---|---|---|---|---|
| gb | price | +0.87% | +0.91% | +0.03 [+0.00, +0.07] | +0.07 [-0.21, +0.35] | +0.27 [+0.17, +0.37] | — |
| gb_noes | price | +0.57% | +0.86% | +0.28 [+0.21, +0.37] | — | — | — |
| gb_tuned | price | +0.79% | +0.83% | +0.04 [+0.01, +0.07] | +0.16 [-0.07, +0.41] | +0.21 [+0.11, +0.31] | 50/3000: 25, 50/200: 22, 50/1000: 13, 100/200: 13, 100/3000: 11, 100/1000: 6, 200/3000: 4 |
| logit | price | +0.43% | +0.46% | +0.03 [-0.00, +0.05] | -1.08 [-1.46, -0.72] | -0.23 [-0.40, -0.04] | — |

Neste horizonte, sempre alta acerta 50.7% e inverter acerta 53.1%. A variação média, em módulo, é de 39 pb.

## Horizonte de 4 h

| Modelo | Variáveis | Sementes | Acerto (%) | vs sempre alta (p.p.) | vs inverter (p.p.) | Brier skill (%) | AUC | Erro do retorno (%) | Retorno (pb) |
|---|---|---|---|---|---|---|---|---|---|
| gb | price | 1 | 53.7 [53.2, 54.2] | +2.7 [+2.0, +3.3] | +0.5 [+0.0, +1.0] | +0.3 [-0.1, +0.7] | 0.554 | +0.5 [+0.2, +0.7] | +2.91 [+1.58, +4.10] |
| gb_noes | price | 1 | 53.8 [53.3, 54.2] | +2.7 [+2.0, +3.3] | +0.5 [-0.0, +1.1] | +0.4 [+0.0, +0.7] | 0.554 | +0.4 [+0.2, +0.7] | +3.12 [+1.70, +4.35] |
| gb_tuned | price | 1 | 54.1 [53.6, 54.6] | +3.0 [+2.3, +3.7] | +0.9 [+0.4, +1.4] | +1.0 [+0.8, +1.3] | 0.558 | +0.4 [+0.2, +0.7] | +2.44 [+1.02, +3.87] |
| logit | price | 1 | 52.6 [52.1, 53.0] | +1.5 [+1.0, +2.1] | -0.6 [-1.2, -0.1] | +0.3 [+0.2, +0.4] | 0.535 | -0.2 [-0.3, -0.1] | +0.94 [-0.37, +2.43] |

Recortes (acerto do modelo / da regra de inverter, % das horas):

| Modelo | Variáveis | p > 0,52 | p > 0,55 | p > 0,60 | Depois de movimento grande | Movimento futuro grande (descritivo) |
|---|---|---|---|---|---|---|
| gb | price | 54.6 / 53.6 (82.1) | 55.9 / 54.7 (57.3) | 57.5 / 55.9 (25.4) | 51.2 / 51.4 (1.9) | 44.8 / 42.5 (8.4) |
| gb_noes | price | 54.5 / 53.7 (81.8) | 55.8 / 54.7 (56.4) | 58.1 / 56.7 (24.5) | 52.6 / 51.4 (1.9) | 44.9 / 42.5 (8.4) |
| gb_tuned | price | 55.2 / 54.0 (75.7) | 57.0 / 55.6 (42.8) | 61.3 / 58.8 (10.2) | 50.9 / 51.4 (1.9) | 44.1 / 42.5 (8.4) |
| logit | price | 54.0 / 54.2 (51.3) | 55.0 / 55.1 (13.0) | 56.2 / 55.9 (1.4) | 50.9 / 51.4 (1.9) | 45.0 / 42.5 (8.4) |

Brier skill sem e com a calibração de 18/11 (de 2020-01), e comparação pareada com a base:

| Modelo | Variáveis | Brier skill sem | com | Diferença (IC 95%) | Acerto − base (IC 95%) | Brier skill − base (IC 95%) | Escolhas (gb_tuned) |
|---|---|---|---|---|---|---|---|
| gb | price | +0.13% | +0.81% | +0.68 [+0.48, +0.87] | -0.01 [-0.29, +0.28] | -0.09 [-0.22, +0.04] | — |
| gb_noes | price | +0.25% | +0.84% | +0.60 [+0.42, +0.78] | — | — | — |
| gb_tuned | price | +0.94% | +0.99% | +0.05 [-0.01, +0.10] | +0.38 [+0.08, +0.71] | +0.63 [+0.43, +0.82] | 50/200: 26, 50/3000: 21, 50/1000: 18, 100/3000: 9, 200/3000: 7, 100/1000: 6, 100/200: 5, 200/200: 2 |
| logit | price | +0.32% | +0.32% | +0.01 [-0.02, +0.03] | -1.16 [-1.63, -0.64] | -0.10 [-0.40, +0.22] | — |

Neste horizonte, sempre alta acerta 51.1% e inverter acerta 53.2%. A variação média, em módulo, é de 80 pb.

## Horizonte de 24 h

| Modelo | Variáveis | Sementes | Acerto (%) | vs sempre alta (p.p.) | vs inverter (p.p.) | Brier skill (%) | AUC | Erro do retorno (%) | Retorno (pb) |
|---|---|---|---|---|---|---|---|---|---|
| gb | price | 1 | 52.0 [50.8, 53.2] | -0.1 [-1.6, +1.6] | -0.7 [-2.1, +0.8] | -3.8 [-5.2, -2.6] | 0.525 | -1.7 [-2.5, -0.9] | +6.09 [-3.01, +13.91] |
| gb_noes | price | 1 | 52.0 [50.8, 53.2] | -0.1 [-1.6, +1.5] | -0.7 [-2.1, +0.7] | -3.6 [-5.1, -2.3] | 0.527 | -1.7 [-2.6, -0.8] | +6.83 [-1.64, +15.12] |
| gb_tuned | price | 1 | 52.6 [51.3, 53.8] | +0.4 [-1.1, +1.8] | -0.1 [-1.4, +1.2] | -0.2 [-1.0, +0.5] | 0.529 | -1.7 [-2.6, -0.8] | +8.76 [-0.44, +17.87] |
| logit | price | 1 | 51.6 [50.4, 52.7] | -0.6 [-1.7, +0.7] | -1.2 [-2.4, +0.3] | +0.2 [-0.1, +0.4] | 0.515 | -0.1 [-0.3, +0.2] | +3.11 [-6.21, +12.72] |

Recortes (acerto do modelo / da regra de inverter, % das horas):

| Modelo | Variáveis | p > 0,52 | p > 0,55 | p > 0,60 | Depois de movimento grande | Movimento futuro grande (descritivo) |
|---|---|---|---|---|---|---|
| gb | price | 52.4 / 52.9 (87.4) | 52.7 / 53.1 (69.0) | 53.3 / 53.7 (42.5) | 51.2 / 48.6 (1.9) | 47.5 / 42.3 (11.9) |
| gb_noes | price | 52.4 / 52.8 (87.0) | 53.0 / 53.0 (68.5) | 54.0 / 53.5 (42.1) | 51.1 / 48.6 (1.9) | 48.2 / 42.3 (11.9) |
| gb_tuned | price | 53.0 / 52.9 (77.6) | 54.4 / 54.0 (47.2) | 56.7 / 54.9 (14.2) | 51.2 / 48.6 (1.9) | 48.7 / 42.3 (11.9) |
| logit | price | 52.8 / 54.1 (56.0) | 54.6 / 54.9 (19.7) | 60.8 / 60.8 (2.4) | 51.3 / 48.6 (1.9) | 47.9 / 42.3 (11.9) |

Brier skill sem e com a calibração de 18/11 (de 2020-01), e comparação pareada com a base:

| Modelo | Variáveis | Brier skill sem | com | Diferença (IC 95%) | Acerto − base (IC 95%) | Brier skill − base (IC 95%) | Escolhas (gb_tuned) |
|---|---|---|---|---|---|---|---|
| gb | price | -4.49% | -0.01% | +4.48 [+3.33, +5.60] | +0.04 [-0.44, +0.47] | -0.23 [-0.59, +0.14] | — |
| gb_noes | price | -4.26% | -0.02% | +4.24 [+3.14, +5.35] | — | — | — |
| gb_tuned | price | -0.45% | +0.10% | +0.55 [+0.09, +1.03] | +0.59 [-0.34, +1.45] | +3.37 [+2.38, +4.34] | 50/3000: 56, 50/200: 12, 100/3000: 11, 50/1000: 9, 200/3000: 6 |
| logit | price | +0.13% | +0.19% | +0.06 [-0.08, +0.18] | -0.44 [-1.67, +0.87] | +3.77 [+2.52, +5.05] | — |

Neste horizonte, sempre alta acerta 52.2% e inverter acerta 52.7%. A variação média, em módulo, é de 212 pb.
