"""Um conector por dataset do catálogo.

Todo conector tem a assinatura ``(client, start, end) -> DataFrame`` e devolve colunas com os
nomes de ``Dataset.fields``. A deduplicação é feita pelo writer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import httpx
import pandas as pd

from wimcg.pipeline.connectors import coinbase, feargreed

Connector = Callable[[httpx.Client, datetime, datetime], pd.DataFrame]

CONNECTORS: dict[str, Connector] = {
    "btc-usd-1m": coinbase.btc_usd_1m,
    "fear-greed": feargreed.fear_greed,
}
