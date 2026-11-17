"""Veículos de imprensa aceitos nas tabelas que guardam endereços de notícias.

O GDELT acompanha dezenas de milhares de sites, quase todos pequenos. Como o site do projeto
publica o título e o endereço de cada notícia, só entram veículos grandes e conhecidos: agências,
jornais e emissoras de alcance nacional ou internacional, imprensa financeira e os principais
veículos de cripto. Isso reduz muito o risco de mandar o leitor para um site malicioso, mas não é
uma garantia de segurança.

A lista é uma escolha editorial do projeto, feita à mão. Para incluir ou tirar um veículo, edite
`DOMAINS`. Um endereço é aceito quando o seu servidor é um desses domínios ou um subdomínio deles
(`finance.yahoo.com` vale por `yahoo.com`).

As contagens de `gkg-hourly` não passam por esta lista: ali entram todas as fontes do GDELT, e
nenhum endereço é guardado.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Em bloco de texto, e não em lista, para ser fácil de ler e de editar.
DOMAINS: frozenset[str] = frozenset(
    """
    reuters.com apnews.com afp.com bloomberg.com upi.com
    bbc.com bbc.co.uk cnn.com cnbc.com nbcnews.com cbsnews.com go.com foxnews.com foxbusiness.com
    npr.org pbs.org aljazeera.com dw.com france24.com euronews.com sky.com voanews.com
    nytimes.com wsj.com washingtonpost.com ft.com theguardian.com economist.com usatoday.com
    latimes.com telegraph.co.uk independent.co.uk thetimes.com thetimes.co.uk time.com
    newsweek.com theatlantic.com politico.com axios.com thehill.com theweek.com
    forbes.com fortune.com businessinsider.com marketwatch.com barrons.com yahoo.com aol.com msn.com
    investopedia.com morningstar.com nasdaq.com seekingalpha.com fool.com thestreet.com
    investing.com investors.com benzinga.com kiplinger.com
    techcrunch.com wired.com theverge.com arstechnica.com zdnet.com cnet.com engadget.com
    coindesk.com cointelegraph.com theblock.co decrypt.co bitcoinmagazine.com blockworks.co
    dlnews.com cryptoslate.com
    cbc.ca theglobeandmail.com financialpost.com ctvnews.ca globalnews.ca
    scmp.com nikkei.com japantimes.co.jp straitstimes.com channelnewsasia.com
    koreaherald.com koreatimes.co.kr yna.co.kr chosun.com joins.com hankyung.com mk.co.kr
    indiatimes.com hindustantimes.com thehindu.com ndtv.com livemint.com business-standard.com
    moneycontrol.com indianexpress.com
    abc.net.au smh.com.au afr.com news.com.au nzherald.co.nz
    xinhuanet.com news.cn chinadaily.com.cn people.com.cn caixin.com cna.com.tw
    lemonde.fr lefigaro.fr lesechos.fr spiegel.de faz.net handelsblatt.com zeit.de sueddeutsche.de
    welt.de elpais.com elmundo.es expansion.com corriere.it repubblica.it ilsole24ore.com nzz.ch
    globo.com uol.com.br estadao.com.br infomoney.com.br exame.com cnnbrasil.com.br terra.com.br
    r7.com publico.pt expresso.pt
    clarin.com lanacion.com.ar infobae.com eltiempo.com elfinanciero.com.mx eluniversal.com.mx
    jpost.com timesofisrael.com arabnews.com thenationalnews.com gulfnews.com news24.com
    tass.com rbc.ru kommersant.ru interfax.ru
    aa.com.tr hurriyet.com.tr businesstimes.com.sg srf.ch kurier.at inquirer.com
    theregister.com techradar.com
    """.split()  # noqa: SIM905
)


def host(url: str | None) -> str:
    """Servidor de um endereço http(s), em minúsculas e sem `www.` (vazio se o endereço não serve)."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return parts.hostname.lower().removeprefix("www.")


def is_major(url: str | None) -> bool:
    """True se o endereço é de um dos veículos de `DOMAINS` (ou de um subdomínio deles)."""
    name = host(url)
    return any(name == d or name.endswith("." + d) for d in DOMAINS) if name else False
