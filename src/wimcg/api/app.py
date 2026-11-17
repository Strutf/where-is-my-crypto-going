"""API pública de consulta e site. Só lê os arquivos JSON gerados pela coleta: não há banco.

Depende apenas de fastapi + httpx (sem pandas), para caber em funções serverless gratuitas.
O site mora no mesmo aplicativo, como no Future Space: cada página é o miolo em web/pages,
encaixado no molde web/base.html, com os textos de web/locales/<idioma>.json. Inglês é o idioma
principal (endereços sem prefixo); português fica em /pt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from wimcg import __version__, catalog
from wimcg.partitions import (
    CATALOG_KEY,
    format_time,
    json_key,
    labels_between,
    latest_key,
    parse_time,
)
from wimcg.storage import Storage, reader_from_env

# Chaves do armazenamento escritas por wimcg.ml.serve (repetidas aqui para a API não importar o
# pacote de machine learning, que puxa o pandas).
MODELS_POINTER = "models/current.json"
MODELS_INDEX = "models/index.json"
SUMMARY_KEY = "json/verification/summary.json"

DISCLAIMER = "Projeto de estudo e pesquisa. Não é recomendação de investimento."
DESCRIPTION = f"""
Dados abertos do projeto **Where Is My Crypto Going?**: preço do Bitcoin, estado do mercado,
grandes movimentos e notícias. Toda tabela tem uma coluna `time` em UTC.

**{DISCLAIMER}**

- `GET /v1/datasets`: as tabelas que existem e a cobertura de cada uma
- `GET /v1/datasets/{{id}}/latest`: os valores mais recentes
- `GET /v1/datasets/{{id}}/data`: as linhas de um intervalo de tempo
- `GET /v1/datasets/predictions/latest`: as previsões da última hora (direção e volatilidade)
- `GET /v1/datasets/verification/data`: cada previsão comparada com o que aconteceu
- `GET /v1/verification/summary`: acerto do modelo e das referências nos últimos 7 e 30 dias
- `GET /v1/models/current`: o modelo em uso e a avaliação dele
- `GET /v1/models`: todos os treinos, publicados ou recusados pela trava

Cite sempre a fonte original indicada em `provider` e `license`.
"""

# Maior intervalo por consulta e intervalo padrão (quando não se informa `start`), conforme o
# tamanho dos arquivos de cada tabela: um por dia, por mês ou por ano.
MAX_SPAN = {"day": timedelta(days=31), "month": timedelta(days=400), "year": timedelta(days=366 * 30)}
DEFAULT_SPAN = {"day": timedelta(days=2), "month": timedelta(days=30), "year": timedelta(days=365)}
# Únicos parâmetros de URL aceitos. Qualquer outro é recusado, o que impede furar o cache
# da CDN com URLs sempre diferentes (?x=1, ?x=2...) sem custo para quem ataca.
ALLOWED_QUERY_PARAMS = frozenset({"start", "end", "fields", "v"})

# ---------------------------------------------------------------- site
WEB = Path(__file__).parent / "web"
STATIC = WEB / "static"
# Versão de cada arquivo estático = início do hash do conteúdo. Vai no endereço (?v=...), então
# qualquer mudança no arquivo muda o endereço e nenhum navegador fica com uma cópia antiga.
STATIC_VERSIONS = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()[:10] for f in STATIC.iterdir() if f.is_file()}
LANGS = ("en", "pt")
PAGE_URLS = {
    "home": {"en": "/", "pt": "/pt"},
    "forecasts": {"en": "/forecasts", "pt": "/pt/previsoes"},
    "about": {"en": "/about", "pt": "/pt/sobre"},
    "news": {"en": "/news", "pt": "/pt/noticias"},
}
SITE_URL = "https://where-is-my-crypto-going.vercel.app"
LOCALES = {lang: json.loads((WEB / "locales" / f"{lang}.json").read_text(encoding="utf-8")) for lang in LANGS}
# Imagens dos personagens: entram sozinhas quando o arquivo existir em web/static
# (shiba.png no cabeçalho; bear.png e bull.png ao lado do gráfico).
CHARACTERS = ("shiba", "bear", "bull")


def _static_url(name: str) -> str:
    return f"/static/{name}?v={STATIC_VERSIONS[name]}"


def _assets() -> dict[str, str]:
    return {c: _static_url(f"{c}.png") for c in CHARACTERS if f"{c}.png" in STATIC_VERSIONS}


def _lookup(locale: dict, key: str) -> str:
    value = locale
    for part in key.split("."):
        value = value[part]  # KeyError aqui = chave faltando no arquivo de tradução
    return value


def render_page(name: str, lang: str) -> str:
    html = (WEB / "base.html").read_text(encoding="utf-8").replace("{{content}}", (WEB / "pages" / f"{name}.html").read_text(encoding="utf-8"))
    locale = LOCALES[lang]
    html = re.sub(r"\{\{t:([\w.]+)\}\}", lambda m: _lookup(locale, m.group(1)), html)
    html = re.sub(r"\{\{url:(\w+)\}\}", lambda m: PAGE_URLS[m.group(1)][lang], html)
    html = re.sub(r"\{\{static:([\w.-]+)\}\}", lambda m: _static_url(m.group(1)), html)
    other = "pt" if lang == "en" else "en"
    assets = _assets()
    mascot = (
        f'<img class="mascot" src="{assets["shiba"]}" alt="">'
        if "shiba" in assets
        else '<span class="coin" data-icon="coin" data-scale="5" aria-hidden="true"></span>'
    )
    hreflang = "\n".join(
        f'<link rel="alternate" hreflang="{LOCALES[x]["lang"]}" href="{SITE_URL}{PAGE_URLS[name][x]}">' for x in LANGS
    ) + f'\n<link rel="alternate" hreflang="x-default" href="{SITE_URL}{PAGE_URLS[name]["en"]}">'
    replacements = {
        "{{title}}": locale["titles"][name],
        "{{page}}": name,
        "{{lang}}": lang,
        "{{hreflang_links}}": hreflang,
        "{{lang_switch}}": f'<a class="btn" href="{PAGE_URLS[name][other]}" hreflang="{LOCALES[other]["lang"]}" lang="{LOCALES[other]["lang"]}" '
        f'aria-label="{locale["nav"]["other_lang_label"]}">{locale["nav"]["other_lang"]}</a>',
        "{{mascot}}": mascot,
        "{{js_i18n}}": json.dumps(locale["js"], ensure_ascii=False).replace("</", "<\\/"),
        "{{assets}}": json.dumps(assets),
    }
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)
    return html


RENDERED_PAGES = {urls[lang]: render_page(name, lang) for name, urls in PAGE_URLS.items() for lang in LANGS}


class CachedStorage(Storage):
    """Cache em memória com TTL: instâncias serverless "quentes" evitam leituras repetidas."""

    def __init__(self, inner: Storage, ttl: float = 60):
        self.inner = inner
        self.ttl = ttl
        self._cache: dict[str, tuple[float, bytes | None]] = {}
        self._lock = threading.Lock()

    def read(self, key: str) -> bytes | None:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        data = self.inner.read(key)
        with self._lock:
            self._cache[key] = (now + self.ttl, data)
            if len(self._cache) > 512:
                self._cache.pop(next(iter(self._cache)))
        return data


class RateLimiter:
    """Janela deslizante de 60 s por cliente, em memória.

    Na Vercel cada instância da função tem a própria memória, então o limite vale por
    instância: barra scripts e abusos simples, não um ataque distribuído.
    """

    WINDOW = 60.0

    def __init__(self, per_minute: int, max_clients: int = 10_000):
        self.per_minute = per_minute
        self.max_clients = max_clients
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, client: str) -> float | None:
        """Registra a requisição; devolve os segundos de espera se o limite estourou."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(client)
            if hits is None:
                if len(self._hits) >= self.max_clients:
                    self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.WINDOW}
                hits = self._hits[client] = deque()
            while hits and now - hits[0] >= self.WINDOW:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return self.WINDOW - (now - hits[0])
            hits.append(now)
            return None


def _client_ip(request: Request) -> str:
    # Na Vercel, x-real-ip e x-forwarded-for são definidos pela borda, não pelo cliente.
    forwarded = request.headers.get("x-forwarded-for", "")
    return request.headers.get("x-real-ip") or forwarded.split(",")[0].strip() or (request.client.host if request.client else "desconhecido")


def _cache_header(seconds: int) -> dict[str, str]:
    # stale-while-revalidate: a CDN continua servindo a última resposta boa enquanto
    # atualiza em segundo plano, o que segura picos de acesso sem chamar a função.
    return {"Cache-Control": f"public, max-age={seconds}, s-maxage={seconds}, stale-while-revalidate={max(seconds, 600)}, stale-if-error=86400"}


def create_app(storage: Storage | None = None, rate_limit_per_minute: int | None = None) -> FastAPI:
    store = CachedStorage(storage or reader_from_env())
    pool = ThreadPoolExecutor(max_workers=8)
    if rate_limit_per_minute is None:
        rate_limit_per_minute = int(os.environ.get("FS_RATE_LIMIT_PER_MINUTE", "60"))
    limiter = RateLimiter(rate_limit_per_minute) if rate_limit_per_minute > 0 else None

    app = FastAPI(
        title="Where Is My Crypto Going? API",
        version=__version__,
        description=DESCRIPTION,
        license_info={"name": "MIT (código). Dados: ver licença de cada tabela."},
    )

    # Registrado antes do CORS e dos cabeçalhos de segurança, que assim também envolvem
    # as respostas 400/429 daqui.
    @app.middleware("http")
    async def guard(request: Request, call_next):
        unknown = sorted(set(request.query_params) - ALLOWED_QUERY_PARAMS)
        if unknown:
            return JSONResponse({"detail": f"parâmetros desconhecidos: {', '.join(unknown)}"}, status_code=400, headers=_cache_header(3600))
        if limiter:
            wait = limiter.retry_after(_client_ip(request))
            if wait is not None:
                return JSONResponse(
                    {"detail": "muitas requisições; aguarde antes de tentar de novo"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, int(wait) + 1)), "Cache-Control": "no-store"},
                )
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    def read_json(key: str):
        try:
            raw = store.read(key)
        except httpx.HTTPError:
            # Não repassa detalhes internos (URLs, cabeçalhos) para quem chamou.
            raise HTTPException(503, "armazenamento de dados indisponível; tente novamente em instantes") from None
        return json.loads(raw) if raw else None

    def dataset_or_404(dataset_id: str) -> catalog.Dataset:
        try:
            return catalog.get(dataset_id)
        except KeyError:
            raise HTTPException(404, f"tabela '{dataset_id}' não existe; veja /v1/datasets") from None

    def parse_or_400(value: str, name: str) -> datetime:
        try:
            return parse_time(value)
        except ValueError:
            raise HTTPException(400, f"{name} inválido: use 2026-11-06 ou 2026-11-06T12:00:00Z") from None

    def json_response(payload, seconds: int) -> JSONResponse:
        return JSONResponse(payload, headers=_cache_header(seconds))

    def page_route(html: str):
        return lambda: HTMLResponse(html, headers=_cache_header(300))

    for path, html in RENDERED_PAGES.items():
        app.add_api_route(path, page_route(html), methods=["GET"], include_in_schema=False)

    @app.get("/static/{name}", include_in_schema=False)
    def static_file(name: str, v: str | None = None):
        # Só arquivos que existem direto em web/static: nada de subpastas nem "..".
        path = STATIC / name
        if "/" in name or name.startswith(".") or name not in STATIC_VERSIONS:
            raise HTTPException(404, "arquivo não encontrado")
        media = {".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json"}
        # Com a versão certa no endereço, o conteúdo nunca muda: pode ficar em cache para sempre.
        cache = {"Cache-Control": "public, max-age=31536000, immutable"} if v == STATIC_VERSIONS[name] else _cache_header(60)
        return Response(path.read_bytes(), media_type=media.get(path.suffix, "application/octet-stream"), headers=cache)

    @app.get("/v1", include_in_schema=False)
    def info():
        return json_response({"name": "Where Is My Crypto Going?", "version": __version__, "disclaimer": DISCLAIMER, "docs": "/docs", "datasets": "/v1/datasets"}, 3600)

    @app.get("/v1/datasets", summary="Tabelas e cobertura de cada uma")
    def list_datasets():
        payload = read_json(CATALOG_KEY)
        if payload is None:  # antes da primeira coleta: o catálogo do código, sem cobertura
            payload = {"updated_at": None, "datasets": [d.to_dict() | {"coverage": None} for d in catalog.DATASETS]}
        return json_response(payload | {"disclaimer": DISCLAIMER}, 300)

    @app.get("/v1/datasets/{dataset_id}/latest", summary="Valores mais recentes de uma tabela")
    def latest(dataset_id: str):
        ds = dataset_or_404(dataset_id)
        payload = read_json(latest_key(ds.id)) if ds.latest_json else None
        if payload is None:
            raise HTTPException(404, f"'{ds.id}' ainda não tem dados")
        return json_response(payload, 60)

    @app.get("/v1/datasets/{dataset_id}/data", summary="Linhas de um intervalo de tempo")
    def data(
        dataset_id: str,
        start: str | None = Query(None, description="Início, inclusive (UTC). Padrão: um período recente, conforme a tabela."),
        end: str | None = Query(None, description="Fim, exclusive (UTC). Padrão: agora."),
        fields: str | None = Query(None, description="Colunas desejadas, separadas por vírgula. Padrão: todas."),
    ):
        ds = dataset_or_404(dataset_id)
        if not ds.json_partitions:
            raise HTTPException(404, f"'{ds.id}' é grande demais para a API: use os arquivos Parquet do dataset")
        end_dt = parse_or_400(end, "end") if end else datetime.now(UTC)
        start_dt = parse_or_400(start, "start") if start else end_dt - DEFAULT_SPAN[ds.partition]
        if start_dt >= end_dt:
            raise HTTPException(400, "start precisa ser anterior a end")
        if end_dt - start_dt > MAX_SPAN[ds.partition]:
            raise HTTPException(400, f"intervalo grande demais para '{ds.id}': no máximo {MAX_SPAN[ds.partition].days} dias por consulta")
        columns = ds.columns
        if fields:
            columns = [c.strip() for c in fields.split(",") if c.strip()]
            unknown = [c for c in columns if c not in ds.columns]
            if unknown:
                raise HTTPException(400, f"colunas desconhecidas: {', '.join(unknown)}")
            if "time" not in columns:
                columns = ["time", *columns]

        keys = [json_key(ds.id, lbl) for lbl in labels_between(start_dt, end_dt, ds.partition)]
        lo, hi = format_time(start_dt), format_time(end_dt)
        rows = []
        for part in pool.map(read_json, keys):  # as partições são lidas em paralelo
            # As datas estão sempre no mesmo formato, então a comparação de texto vale.
            rows += [{c: r.get(c) for c in columns} for r in part or [] if lo <= r["time"] < hi]
        # Dados que mal mudam (anos fechados) poderiam ficar mais tempo em cache; 60 s serve a todos.
        return json_response({"dataset": ds.id, "start": lo, "end": hi, "count": len(rows), "data": rows}, 60)

    @app.get("/v1/verification/summary", summary="Acerto das previsões e das referências")
    def verification_summary():
        payload = read_json(SUMMARY_KEY)
        if payload is None:
            raise HTTPException(404, "nenhuma previsão verificada ainda")
        return json_response(payload, 300)

    @app.get("/v1/models/current", summary="Modelo em uso e a avaliação dele")
    def current_model():
        pointer = read_json(MODELS_POINTER)
        if pointer is None:
            raise HTTPException(404, "nenhum modelo publicado ainda")
        return json_response({"model_version": pointer["model_version"], **pointer.get("summary", {}), "disclaimer": DISCLAIMER}, 3600)

    @app.get("/v1/models", summary="Histórico dos treinos")
    def models_index():
        payload = read_json(MODELS_INDEX)
        if payload is None:
            raise HTTPException(404, "nenhum treino registrado ainda")
        return json_response(payload, 3600)

    return app


app = create_app()
