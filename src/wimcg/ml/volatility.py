"""Previsão da volatilidade: quão agitado o preço vai ficar nas próximas *h* horas.

Alvo: a volatilidade realizada das próximas *h* horas, raiz da soma dos quadrados dos retornos
horários (em log), em %. Para *h* = 1 é o tamanho da próxima variação, sem sinal.

Modelos comparados, na mesma avaliação andando para a frente da direção:
- **persistence:** a volatilidade realizada das últimas *h* horas (referência mais simples);
- **har:** regressão linear do log da volatilidade futura sobre o log da volatilidade das últimas
  *h* horas, do último dia, da última semana e do último mês, a referência clássica (Corsi, 2009);
- **gb:** gradient boosting sobre o log da volatilidade, com as variáveis de preço e de estado do
  mercado e as mesmas volatilidades passadas do HAR.

Medidas: erro absoluto médio da volatilidade (em pontos percentuais) e do seu log (erro relativo),
cada uma comparada com a persistência e com o HAR.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from wimcg.ml.features import HORIZONS

PAST_WINDOWS = (24, 168, 720)
GB_PARAMS = {"learning_rate": 0.05, "max_iter": 300, "max_leaf_nodes": 15, "min_samples_leaf": 200, "l2_regularization": 1.0}


def add_columns(table: pd.DataFrame) -> pd.DataFrame:
    """Volatilidades realizadas passadas (variáveis) e futuras (alvos), em %, na grade da tabela."""
    r2 = table["ret_1h"] ** 2
    out = table.copy()
    for h in sorted({*HORIZONS, *PAST_WINDOWS}):
        # Passada: as últimas h horas, até a própria hora (já fechada). Escalada para 1 h.
        out[f"rv_past_{h}h"] = np.sqrt(r2.rolling(h, min_periods=max(1, int(h * 0.9))).sum() / h) * 100
    for h in HORIZONS:
        # Futura: as h horas seguintes. Fica no tamanho do horizonte (não escalada).
        out[f"rv_next_{h}h"] = np.sqrt(r2[::-1].rolling(h, min_periods=h).sum()[::-1].shift(-1)) * 100
    return out


def har_columns(h: int) -> list[str]:
    return [f"rv_past_{w}h" for w in sorted({h, *PAST_WINDOWS})]


def _log(x: pd.DataFrame | pd.Series | np.ndarray) -> np.ndarray:
    return np.log(np.maximum(np.asarray(x, dtype=float), 1e-4))


class Persistence:
    name = "persistence"

    def fit(self, train: pd.DataFrame, features: list[str], history=None) -> None:
        pass

    def predict(self, test: pd.DataFrame) -> dict[int, np.ndarray]:
        # A volatilidade horária recente, levada ao tamanho do horizonte (raiz do tempo).
        return {h: test[f"rv_past_{h}h"].to_numpy() * np.sqrt(h) for h in HORIZONS}


class HAR:
    name = "har"

    def fit(self, train: pd.DataFrame, features: list[str], history=None) -> None:
        self.coef = {}
        for h in HORIZONS:
            cols = har_columns(h)
            d = train[[*cols, f"rv_next_{h}h"]].dropna()
            x = np.column_stack([np.ones(len(d)), _log(d[cols])])
            y = _log(d[f"rv_next_{h}h"])
            coef, *_ = np.linalg.lstsq(x, y, rcond=None)
            # Correção do viés de voltar do log: a média dos resíduos exponenciados.
            self.coef[h] = (coef, float(np.mean(np.exp(y - x @ coef))))

    def predict(self, test: pd.DataFrame) -> dict[int, np.ndarray]:
        out = {}
        for h, (coef, bias) in self.coef.items():
            x = np.column_stack([np.ones(len(test)), _log(test[har_columns(h)].fillna(test[har_columns(h)].median()))])
            out[h] = np.exp(x @ coef) * bias
        return out


class GradientBoosting:
    name = "gb"

    def fit(self, train: pd.DataFrame, features: list[str], history=None) -> None:
        from sklearn.ensemble import HistGradientBoostingRegressor

        self.features = features
        self.models = {}
        for h in HORIZONS:
            cols = [*features, *har_columns(h)]
            d = train[train[f"rv_next_{h}h"].notna()]
            y = _log(d[f"rv_next_{h}h"])
            model = HistGradientBoostingRegressor(**GB_PARAMS, random_state=0).fit(d[cols].to_numpy(float), y)
            bias = float(np.mean(np.exp(y - model.predict(d[cols].to_numpy(float)))))
            self.models[h] = (cols, model, bias)

    def predict(self, test: pd.DataFrame) -> dict[int, np.ndarray]:
        return {h: np.exp(m.predict(test[cols].to_numpy(float))) * bias for h, (cols, m, bias) in self.models.items()}


MODELS = {"persistence": Persistence, "har": HAR, "gb": GradientBoosting}


def walk_forward(table: pd.DataFrame, features: list[str], model_name: str, first: str, last: str, train_years: int, purge: pd.Timedelta) -> pd.DataFrame:
    model = MODELS[model_name]()
    out = []
    for start in pd.date_range(first, last, freq="MS", tz="UTC"):
        end = start + pd.offsets.MonthBegin(1)
        train = table[(table.index >= start - pd.DateOffset(years=train_years)) & (table.index < start - purge)]
        test = table[(table.index >= start) & (table.index < end)]
        if test.empty:
            continue
        model.fit(train, features)
        for h, pred in model.predict(test).items():
            out.append(pd.DataFrame({"time": test.index, "horizon": h, "pred_vol": pred, "vol": test[f"rv_next_{h}h"].to_numpy()}))
    preds = pd.concat(out, ignore_index=True)
    return preds[preds["vol"].notna() & preds["pred_vol"].notna()]


def scores(g: pd.DataFrame) -> dict[str, float]:
    return {
        "mae": float(np.mean(np.abs(g["pred_vol"] - g["vol"]))),
        "mae_log": float(np.mean(np.abs(_log(g["pred_vol"]) - _log(g["vol"])))),
    }
