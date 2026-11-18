# Volatilidade: persistência, HAR, gradient boosting e GARCH

Gerado por `wimcg ml-vol-eval`. Teste mês a mês de 2019-01 a 2026-10, treino nos 3 anos anteriores. Erro absoluto médio da volatilidade das próximas h horas, em pontos percentuais; diferenças relativas com IC de 95% (bootstrap em blocos de 168 h). Negativo = erra menos.

| Horizonte | Persistência | HAR | gb | GARCH | gb vs HAR | GARCH vs HAR | GARCH vs gb |
|---|---|---|---|---|---|---|---|
| 1 h | 0.359 | 0.283 | 0.273 | 0.376 | -3.7% [-4.3, -3.0] | +32.9% [+31.6, +34.2] | +38.0% [+36.1, +39.8] |
| 4 h | 0.575 | 0.471 | 0.433 | 0.568 | -8.1% [-9.1, -7.1] | +20.5% [+19.2, +21.9] | +31.1% [+29.0, +33.3] |
| 24 h | 1.088 | 0.931 | 0.864 | 1.059 | -7.2% [-9.4, -5.2] | +13.8% [+12.0, +15.7] | +22.6% [+19.8, +25.7] |
