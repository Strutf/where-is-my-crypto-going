"""Modelos comparados. Todos têm a mesma interface e são treinados nas mesmas janelas.

`fit(train, features, history)` recebe as linhas de treino (com os alvos de todos os horizontes) e
a tabela inteira (de onde a `gru` tira as sequências, sempre olhando só para trás), e
`predict(test)` devolve, para cada horizonte, a probabilidade de alta e o retorno previsto.

- **gb:** gradient boosting do scikit-learn, um classificador e um regressor por horizonte.
- **mlp:** rede neural densa sobre as mesmas variáveis, com uma saída por horizonte.
- **gb_noes:** o mesmo gradient boosting sem a parada antecipada padrão do scikit-learn, que separa a
  validação ao acaso (otimista com alvos sobrepostos).
- **gb_tuned:** gradient boosting sem parada antecipada, com `max_iter` e `min_samples_leaf`
  escolhidos por horizonte numa validação temporal (os últimos 6 meses da janela de treino).
- **logit:** regressão logística (e Ridge para o retorno) sobre poucos retornos e a volatilidade.
- **gru:** rede recorrente que lê as últimas `SEQ_HOURS` horas (retorno, amplitude e volume, hora a
  hora) e junta o resultado às mesmas variáveis da `mlp`. Comparar `gru` com `mlp` mede o que ler
  a sequência acrescenta.

Os hiperparâmetros foram fixados antes de olhar o resultado do teste e não são ajustados nele.
As redes são treinadas do zero na primeira janela e, nos meses seguintes, continuam dos pesos do
mês anterior por algumas épocas na janela nova (só com dados passados).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from wimcg.ml.features import HORIZONS

GB_PARAMS = {"learning_rate": 0.05, "max_iter": 200, "max_leaf_nodes": 15, "min_samples_leaf": 200, "l2_regularization": 1.0}
SEQ_HOURS = 168
SEQ_CHANNELS = ("ret_1h", "range_1h", "volume_z")
NN_PARAMS = {"hidden": 64, "dropout": 0.2, "lr": 1e-3, "weight_decay": 1e-4, "batch": 512, "first_epochs": 12, "monthly_epochs": 2, "return_weight": 0.5}


def _targets(df: pd.DataFrame, h: int) -> tuple[np.ndarray, np.ndarray]:
    return df[f"up_next_{h}h"].to_numpy(float), df[f"ret_next_{h}h"].to_numpy(float)


class GradientBoosting:
    name = "gb"

    def __init__(self, seed: int = 0, early_stopping: bool | str = "auto"):
        # "auto" é o padrão do scikit-learn (o modelo publicado em 17/11/2026 usa esse).
        self.early_stopping = early_stopping

    def fit(self, train: pd.DataFrame, features: list[str], history: pd.DataFrame) -> None:
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

        self.features = features
        self.models = {}
        x = train[features].to_numpy(float)
        params = {**GB_PARAMS, "early_stopping": self.early_stopping, "random_state": 0}
        for h in HORIZONS:
            up, ret = _targets(train, h)
            ok = ~np.isnan(ret)
            clf = HistGradientBoostingClassifier(**params).fit(x[ok], up[ok])
            # Erro absoluto: o retorno previsto é a mediana, e a medida de erro é o erro absoluto.
            reg = HistGradientBoostingRegressor(**params, loss="absolute_error").fit(x[ok], ret[ok])
            self.models[h] = (clf, reg)

    def predict(self, test: pd.DataFrame) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        x = test[self.features].to_numpy(float)
        return {h: (clf.predict_proba(x)[:, 1], reg.predict(x)) for h, (clf, reg) in self.models.items()}


TUNE_GRID = [(it, leaf) for it in (50, 100, 200) for leaf in (200, 1000, 3000)]
TUNE_VALID = pd.DateOffset(months=6)


class TunedGB:
    """Gradient boosting com a regularização escolhida por horizonte numa validação temporal."""

    name = "gb_tuned"

    def __init__(self, seed: int = 0):
        self.choice: dict[int, str] = {}

    def fit(self, train: pd.DataFrame, features: list[str], history: pd.DataFrame) -> None:
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

        self.features = features
        self.models = {}
        base = {k: v for k, v in GB_PARAMS.items() if k not in ("max_iter", "min_samples_leaf")}
        split = train.index.max() - TUNE_VALID
        # Purga: as linhas de treino da escolha não podem ter o alvo dentro da validação.
        fit_rows = train[train.index < split - pd.Timedelta(hours=max(HORIZONS))]
        valid = train[train.index >= split]
        x_all, x_fit, x_val = (d[features].to_numpy(float) for d in (train, fit_rows, valid))
        for h in HORIZONS:
            up_fit, ret_fit = _targets(fit_rows, h)
            up_val, ret_val = _targets(valid, h)
            ok_fit, ok_val = ~np.isnan(ret_fit), ~np.isnan(ret_val)
            scores = []
            for it, leaf in TUNE_GRID:
                clf = HistGradientBoostingClassifier(**base, max_iter=it, min_samples_leaf=leaf, early_stopping=False, random_state=0)
                clf.fit(x_fit[ok_fit], up_fit[ok_fit])
                prob = clf.predict_proba(x_val[ok_val])[:, 1]
                scores.append(np.mean((prob - up_val[ok_val]) ** 2))
            it, leaf = TUNE_GRID[int(np.argmin(scores))]
            self.choice[h] = f"{it}/{leaf}"
            up, ret = _targets(train, h)
            ok = ~np.isnan(ret)
            clf = HistGradientBoostingClassifier(**base, max_iter=it, min_samples_leaf=leaf, early_stopping=False, random_state=0).fit(x_all[ok], up[ok])
            reg = HistGradientBoostingRegressor(**GB_PARAMS, early_stopping=False, loss="absolute_error", random_state=0).fit(x_all[ok], ret[ok])
            self.models[h] = (clf, reg)

    def predict(self, test: pd.DataFrame) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        x = test[self.features].to_numpy(float)
        return {h: (clf.predict_proba(x)[:, 1], reg.predict(x)) for h, (clf, reg) in self.models.items()}


LOGIT_COLUMNS = ("ret_1h", "ret_4h", "ret_24h", "vol_24h")


class Logit:
    """Referência linear: logística para a direção e Ridge para o retorno, poucas variáveis."""

    name = "logit"

    def __init__(self, seed: int = 0):
        pass

    @staticmethod
    def _x(df: pd.DataFrame) -> np.ndarray:
        x = df[list(LOGIT_COLUMNS)].to_numpy(float)
        ratio = x[:, 0] / np.where(x[:, 3] > 0, x[:, 3], np.nan)  # retorno em unidades de volatilidade
        return np.column_stack([x, ratio])

    def fit(self, train: pd.DataFrame, features: list[str], history: pd.DataFrame) -> None:
        from sklearn.linear_model import LogisticRegression, Ridge

        x = self._x(train)
        self.mean, self.std = np.nanmean(x, axis=0), np.nanstd(x, axis=0)
        self.std[self.std == 0] = 1
        z = np.nan_to_num((x - self.mean) / self.std)
        self.models = {}
        for h in HORIZONS:
            up, ret = _targets(train, h)
            ok = ~np.isnan(ret)
            self.models[h] = (LogisticRegression(C=1.0, max_iter=1000).fit(z[ok], up[ok]), Ridge(alpha=1.0).fit(z[ok], ret[ok]))

    def predict(self, test: pd.DataFrame) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        z = np.nan_to_num((self._x(test) - self.mean) / self.std)
        return {h: (clf.predict_proba(z)[:, 1], reg.predict(z)) for h, (clf, reg) in self.models.items()}


class _Scaler:
    """Padroniza pela mediana e pelo intervalo interquartil do treino; vazios viram 0 + indicador."""

    def fit(self, x: np.ndarray) -> _Scaler:
        self.center = np.nanmedian(x, axis=0)
        q75, q25 = np.nanpercentile(x, [75, 25], axis=0)
        self.scale = np.where((q75 - q25) > 0, q75 - q25, 1.0)
        self.center = np.nan_to_num(self.center)
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        z = np.clip((x - self.center) / self.scale, -10, 10)
        missing = np.isnan(z)
        return np.concatenate([np.nan_to_num(z), missing.astype(float)], axis=1).astype(np.float32)


class _NeuralNet:
    """Base das redes: saídas = uma probabilidade e um retorno por horizonte."""

    sequence = False

    def __init__(self, seed: int = 0):
        self.seed = seed
        self.net = None

    def _build(self, n_inputs: int):
        import torch
        from torch import nn

        p = NN_PARAMS

        class Net(nn.Module):
            def __init__(self, sequence: bool):
                super().__init__()
                self.sequence = sequence
                width = n_inputs
                if sequence:
                    self.gru = nn.GRU(len(SEQ_CHANNELS), 32, batch_first=True)
                    width += 32
                self.body = nn.Sequential(
                    nn.Linear(width, p["hidden"]), nn.GELU(), nn.Dropout(p["dropout"]),
                    nn.Linear(p["hidden"], p["hidden"] // 2), nn.GELU(),
                    nn.Linear(p["hidden"] // 2, 2 * len(HORIZONS)),
                )

            def forward(self, tab, seq=None):
                if self.sequence:
                    _, last = self.gru(seq)
                    tab = torch.cat([tab, last[-1]], dim=1)
                return self.body(tab)

        torch.manual_seed(self.seed)
        return Net(self.sequence)

    def _sequences(self, df: pd.DataFrame) -> np.ndarray | None:
        if not self.sequence:
            return None
        # As últimas SEQ_HOURS linhas até a própria hora, inclusive, de cada canal padronizado.
        rows = self.history.index.get_indexer(df.index)
        window = rows[:, None] - np.arange(SEQ_HOURS - 1, -1, -1)[None, :]
        return self.channels[np.clip(window, 0, None)]

    def fit(self, train: pd.DataFrame, features: list[str], history: pd.DataFrame) -> None:
        import torch

        self.features = features
        self.scaler = _Scaler().fit(train[features].to_numpy(float))
        if self.sequence:
            # Canais de toda a tabela (passado e teste): cada janela só olha para trás.
            self.history = history
            raw = history[list(SEQ_CHANNELS)].to_numpy(float)
            ref = train[list(SEQ_CHANNELS)].to_numpy(float)
            med, iqr = np.nanmedian(ref, axis=0), np.subtract(*np.nanpercentile(ref, [75, 25], axis=0))
            self.channels = np.nan_to_num(np.clip((raw - med) / np.where(iqr > 0, iqr, 1), -10, 10)).astype(np.float32)
        x = self.scaler.transform(train[features].to_numpy(float))
        seq = self._sequences(train)
        up = np.stack([_targets(train, h)[0] for h in HORIZONS], axis=1)
        ret = np.stack([_targets(train, h)[1] for h in HORIZONS], axis=1)
        self.ret_scale = np.nanstd(ret, axis=0)
        ret = ret / self.ret_scale

        first = self.net is None
        if first:
            self.net = self._build(x.shape[1])
            self.opt = torch.optim.AdamW(self.net.parameters(), lr=NN_PARAMS["lr"], weight_decay=NN_PARAMS["weight_decay"])
        epochs = NN_PARAMS["first_epochs"] if first else NN_PARAMS["monthly_epochs"]
        tensors = [torch.from_numpy(a.astype(np.float32)) for a in (x, up, ret)]
        seq_t = torch.from_numpy(seq) if seq is not None else None
        bce = torch.nn.BCEWithLogitsLoss(reduction="none")
        huber = torch.nn.HuberLoss(reduction="none")
        gen = torch.Generator().manual_seed(self.seed)
        self.net.train()
        for _ in range(epochs):
            for batch in torch.randperm(len(x), generator=gen).split(NN_PARAMS["batch"]):
                xb, upb, retb = (t[batch] for t in tensors)
                out = self.net(xb, seq_t[batch] if seq_t is not None else None)
                logits, pred_ret = out[:, : len(HORIZONS)], out[:, len(HORIZONS) :]
                mask = ~torch.isnan(upb)
                loss = bce(logits[mask], upb[mask]).mean()
                loss = loss + NN_PARAMS["return_weight"] * huber(pred_ret[mask], retb[mask]).mean()
                self.opt.zero_grad()
                loss.backward()
                self.opt.step()

    def predict(self, test: pd.DataFrame) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        import torch

        self.net.eval()
        x = torch.from_numpy(self.scaler.transform(test[self.features].to_numpy(float)))
        seq = self._sequences(test)
        with torch.no_grad():
            out = self.net(x, torch.from_numpy(seq) if seq is not None else None).numpy()
        k = len(HORIZONS)
        prob = 1 / (1 + np.exp(-out[:, :k]))
        ret = out[:, k:] * self.ret_scale
        return {h: (prob[:, i], ret[:, i]) for i, h in enumerate(HORIZONS)}


class MLP(_NeuralNet):
    name = "mlp"


class GRU(_NeuralNet):
    name = "gru"
    sequence = True


# Cada entrada recebe a semente (as que não usam, ignoram).
MODELS = {
    "gb": GradientBoosting,
    "gb_noes": lambda seed=0: GradientBoosting(seed, early_stopping=False),
    "gb_tuned": TunedGB,
    "logit": Logit,
    "mlp": MLP,
    "gru": GRU,
}
