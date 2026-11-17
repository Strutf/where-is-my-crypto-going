"""Site: as páginas renderizam nos dois idiomas, com o aviso, e os arquivos estáticos são servidos com segurança."""

import json

import pytest
from fastapi.testclient import TestClient

from wimcg.api import app as site
from wimcg.api.app import create_app
from wimcg.storage import LocalStorage


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(LocalStorage(tmp_path), rate_limit_per_minute=0))


def _keys(d, prefix=""):
    out = set()
    for k, v in d.items():
        out |= _keys(v, f"{prefix}{k}.") if isinstance(v, dict) else {prefix + k}
    return out


def test_locales_have_the_same_keys():
    assert _keys(site.LOCALES["en"]) == _keys(site.LOCALES["pt"])


@pytest.mark.parametrize("name", list(site.PAGE_URLS))
@pytest.mark.parametrize("lang", site.LANGS)
def test_every_page_renders_with_the_disclaimer(client, name, lang):
    url = site.PAGE_URLS[name][lang]
    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "{{" not in html
    assert site.LOCALES[lang]["notice"] in html  # aviso no topo de todas as páginas
    assert f'data-nav="{name}"' in html
    other = "pt" if lang == "en" else "en"
    assert f'href="{site.PAGE_URLS[name][other]}"' in html  # troca de idioma leva à mesma página


def test_static_files_are_versioned_and_contained(client):
    for name, version in site.STATIC_VERSIONS.items():
        r = client.get(f"/static/{name}", params={"v": version})
        assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
        assert "immutable" not in client.get(f"/static/{name}").headers["cache-control"]
    assert client.get("/static/..%2Fapp.py").status_code == 404
    assert client.get("/static/nao-existe.css").status_code == 404


def test_api_info_moved_to_v1(client):
    body = client.get("/v1").json()
    assert body["datasets"] == "/v1/datasets" and "recomendação" in body["disclaimer"]


def test_character_images_appear_only_when_the_files_exist(monkeypatch):
    monkeypatch.setitem(site.STATIC_VERSIONS, "bear.png", "abc")
    assets = site._assets()
    assert assets == {"bear": "/static/bear.png?v=abc"}
    html = site.render_page("home", "en")
    assert json.dumps(assets) in html
