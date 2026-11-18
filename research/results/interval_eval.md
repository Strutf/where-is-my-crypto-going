# Faixa provável e calibração

Gerado por `python research/interval_eval.py`. Período: 2020-01 a 2026-10. Método e critérios registrados antes em `docs/contexto.md` (18/11/2026). **Não é recomendação de investimento.**

## Faixa provável (retorno em %, log)

| Horizonte | Faixa | Cobertura 90% | Cobertura 50% | Largura 90% | Largura 50% | Pinball |
|---|---|---|---|---|---|---|
| 1 h | volatilidade prevista × z | 89.9% | 49.6% | 1.64 | 0.57 | 0.1127 |
| 1 h | fixa no mês (referência) | 90.1% | 51.2% | 1.81 | 0.51 | 0.1224 |
| 1 h | Normal com HAR (referência) | 83.0% | 48.4% | 1.29 | 0.53 | 0.1170 |
| 4 h | volatilidade prevista × z | 90.0% | 49.5% | 3.50 | 1.12 | 0.2301 |
| 4 h | fixa no mês (referência) | 90.3% | 51.0% | 3.81 | 1.01 | 0.2467 |
| 4 h | Normal com HAR (referência) | 87.3% | 57.1% | 3.17 | 1.30 | 0.2362 |
| 24 h | volatilidade prevista × z | 90.1% | 49.7% | 9.55 | 3.03 | 0.6023 |
| 24 h | fixa no mês (referência) | 90.6% | 50.9% | 9.78 | 2.95 | 0.6235 |
| 24 h | Normal com HAR (referência) | 88.0% | 57.2% | 8.88 | 3.64 | 0.6124 |

Cobertura da faixa de 90% do modelo, por ano e por agitação do mercado:

- 1 h: 2020: 89.7%, 2021: 89.2%, 2022: 91.1%, 2023: 89.4%, 2024: 89.4%, 2025: 90.0%, 2026: 90.3% | calm: 89.6%, normal: 89.9%, volatile: 90.0%, extreme: 91.1%
- 4 h: 2020: 90.1%, 2021: 89.3%, 2022: 91.0%, 2023: 89.3%, 2024: 89.5%, 2025: 90.1%, 2026: 90.5% | calm: 89.4%, normal: 89.8%, volatile: 90.6%, extreme: 92.1%
- 24 h: 2020: 91.1%, 2021: 89.5%, 2022: 90.5%, 2023: 89.4%, 2024: 89.1%, 2025: 91.5%, 2026: 89.9% | calm: 88.9%, normal: 90.1%, volatile: 91.7%, extreme: 92.2%

## Calibração da probabilidade de alta

| Horizonte | Brier skill sem | com | Diferença (IC 95%) | Acerto sem | com | λ (mín · mediana · máx · último) |
|---|---|---|---|---|---|---|
| 1 h | +0.87% | +0.91% | +0.03 [+0.00, +0.07] | 54.0% | 53.9% | 0.58 · 0.90 · 1.00 · 0.61 |
| 4 h | +0.13% | +0.81% | +0.68 [+0.48, +0.87] | 53.6% | 53.5% | 0.28 · 0.55 · 0.85 · 0.28 |
| 24 h | -4.49% | -0.01% | +4.48 [+3.33, +5.60] | 51.6% | 51.6% | 0.00 · 0.12 · 0.54 · 0.09 |

Confiabilidade depois da calibração (probabilidade prevista → frequência observada, por decil):

- 1 h: 0.42→0.43, 0.46→0.46, 0.47→0.47, 0.49→0.48, 0.50→0.50, 0.51→0.51, 0.53→0.52, 0.54→0.54, 0.56→0.56, 0.60→0.59
- 4 h: 0.42→0.44, 0.46→0.46, 0.48→0.48, 0.50→0.49, 0.51→0.50, 0.52→0.50, 0.53→0.52, 0.54→0.54, 0.56→0.57, 0.61→0.60
- 24 h: 0.48→0.53, 0.50→0.52, 0.51→0.53, 0.51→0.51, 0.52→0.51, 0.52→0.52, 0.53→0.53, 0.53→0.53, 0.54→0.50, 0.58→0.55
