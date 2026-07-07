# -*- coding: utf-8 -*-
"""
Consolidador de Notícias Financeiras
Coleta feeds RSS, deduplica, classifica por tema e gera index.html.
Roda 3x/dia via GitHub Actions.
"""
import re
import html
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests
import feedparser

FEEDS = [
    ("InfoMoney",       "https://www.infomoney.com.br/feed/",                                False),
    ("Money Times",     "https://www.moneytimes.com.br/feed/",                               False),
    ("InvestNews",      "https://investnews.com.br/feed/",                                   False),
    ("Bloomberg Línea", "https://www.bloomberglinea.com.br/arc/outboundfeeds/rss/?outputType=xml", False),
    ("Valor",           "https://pox.globo.com/rss/valor",                                   False),
    ("Exame",           "https://exame.com/feed/",                                           True),
    ("Google News",     "https://news.google.com/rss/search?q=(Fed%20OR%20%22Wall%20Street%22%20OR%20Nasdaq%20OR%20%22S%26P%20500%22)%20when:1d&hl=pt-BR&gl=BR&ceid=BR:pt-419", True),
]

JANELA_HORAS = 24
MAX_POR_SECAO = 25
LIMIAR_DEDUP = 0.72

SECOES = ["Macro Brasil", "Ações BR", "Internacional", "Cripto"]

# '*' no fim = casa prefixo (treasur* pega treasury/treasuries)
KW = {
    "Cripto": [
        "bitcoin", "btc", "ethereum", "cripto*", "blockchain", "stablecoin*",
        "binance", "coinbase", "altcoin*", "solana", "xrp", "defi", "web3", "drex",
    ],
    "Internacional": [
        "fed", "fomc", "powell", "wall street", "nasdaq", "s&p", "dow jones",
        "treasur*", "bce", "boj", "fmi", "opep", "brent", "petroleo", "ouro",
        "eua", "estados unidos", "europa", "china", "japao", "argentina",
        "mexico", "tarifa*", "trump", "bolsas globais", "mercados globais",
        "zona do euro", "recessao global", "nvidia", "apple", "microsoft",
        "tesla", "amazon", "big tech*",
    ],
    "Macro Brasil": [
        "selic", "copom", "banco central", "ipca*", "igp-m", "inflacao", "pib",
        "fiscal", "arcabouco", "divida publica", "tesouro nacional", "cambio",
        "dolar", "juros", "boletim focus", "cdi", "arrecadacao", "orcamento",
        "fazenda", "haddad", "galipolo", "lula", "congresso", "reforma tributaria",
        "imposto*", "caged", "desemprego", "atividade economica", "varejo",
        "industria", "credito",
    ],
    "Ações BR": [
        "ibovespa", "b3", "acoes", "acao", "dividendo*", "jcp",
        "juros sobre capital", "balanco*", "lucro", "receita liquida",
        "fato relevante", "ipo", "follow-on", "recompra", "petrobras", "vale",
        "itau", "bradesco", "ambev", "weg", "embraer", "magalu", "fii*",
        "fundo* imobiliario*", "small cap*", "bolsa brasileira", "pregao",
    ],
}

def _compilar(lista):
    padroes = []
    for kw in lista:
        partes = []
        for p in kw.split(" "):
            if p.endswith("*"):
                partes.append(re.escape(p[:-1]) + r"\w*")
            else:
                partes.append(re.escape(p) + r"s?")
        padroes.append(r"\b" + r"\s+".join(partes) + r"\b")
    return re.compile("|".join(padroes))

KW_RE = {s: _compilar(kws) for s, kws in KW.items()}

# desempate: temas mais específicos vencem os genéricos
PRIORIDADE = {"Cripto": 3, "Internacional": 2, "Ações BR": 1, "Macro Brasil": 0}

EXCLUIR = [
    "copa do mundo", "futebol", "selecao brasileira", "neymar", "onde assistir",
    "horoscopo", "bbb", "enem", "mega-sena", "loteria", "celebridade",
    "jogos de hoje", "novela", "receita de",
]

CATEGORIAS_EXCLUIR = {"esporte", "pop", "casual", "mundo", "carreira", "marketing"}

TICKER_RE = re.compile(r"\b[A-Z]{4}\d{1,2}\b")

UA = {"User-Agent": "Mozilla/5.0 (compatible; ConsolidadorNoticias/1.0)"}


def normalizar(texto: str) -> str:
    """minúsculas, sem acentos"""
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def classificar(titulo: str, resumo: str, fonte_restrita: bool):
    txt = normalizar(f"{titulo} {resumo}")

    for kw in EXCLUIR:
        if kw in txt:
            return None

    scores = {s: len(KW_RE[s].findall(txt)) for s in SECOES}
    if TICKER_RE.search(titulo or ""):
        scores["Ações BR"] += 2

    melhor = max(scores, key=lambda s: (scores[s], PRIORIDADE[s]))
    if scores[melhor] == 0:
        # sem sinal temático: mantém em Macro Brasil se a fonte é 100% finanças
        return None if fonte_restrita else "Macro Brasil"
    return melhor


_STOP = {"a", "o", "e", "de", "do", "da", "dos", "das", "em", "no", "na", "nos",
         "nas", "com", "por", "para", "ao", "aos", "um", "uma", "que", "apos",
         "diz", "veja", "sobre", "mais", "ate", "seu", "sua", "os", "as"}


def _tokens(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9%$]+", t) if w not in _STOP and len(w) > 1}


def duplicada(titulo_norm: str, vistos: list) -> bool:
    """Duplicata se muito parecido por caracteres OU por sobreposição de palavras."""
    toks = _tokens(titulo_norm)
    for v in vistos:
        if SequenceMatcher(None, titulo_norm, v).ratio() > LIMIAR_DEDUP:
            return True
        vt = _tokens(v)
        if toks and vt:
            contencao = len(toks & vt) / min(len(toks), len(vt))
            if contencao >= 0.7:
                return True
    return False


def coletar():
    agora = datetime.now(timezone.utc)
    limite = agora - timedelta(hours=JANELA_HORAS)
    itens = []

    for nome, url, restrita in FEEDS:
        try:
            resp = requests.get(url, headers=UA, timeout=25)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"[AVISO] falha em {nome}: {e}")
            continue

        for e in feed.entries:
            titulo = html.unescape(getattr(e, "title", "")).strip()
            link = getattr(e, "link", "")
            if not titulo or not link:
                continue

            cats = {normalizar(t.get("term", "")) for t in getattr(e, "tags", [])}
            if cats & CATEGORIAS_EXCLUIR:
                continue

            tm = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            if not tm:
                continue
            dt = datetime(*tm[:6], tzinfo=timezone.utc)
            if dt < limite or dt > agora + timedelta(hours=1):
                continue

            resumo = re.sub(r"<[^>]+>", " ", getattr(e, "summary", ""))[:400]
            secao = classificar(titulo, resumo, restrita)
            if secao is None:
                continue

            itens.append({
                "titulo": titulo,
                "link": link,
                "fonte": nome,
                "dt": dt,
                "secao": secao,
                "tnorm": normalizar(titulo),
            })
        print(f"[OK] {nome}: {len(feed.entries)} entradas no feed")

    itens.sort(key=lambda i: i["dt"], reverse=True)
    finais, vistos = [], []
    for it in itens:
        if duplicada(it["tnorm"], vistos):
            continue
        vistos.append(it["tnorm"])
        finais.append(it)

    por_secao = {s: [i for i in finais if i["secao"] == s][:MAX_POR_SECAO] for s in SECOES}
    return por_secao


CSS = """
:root{--bg:#0f1115;--card:#181b22;--txt:#e8eaf0;--mut:#8b93a7;--acc:#4f9cf9;
--macro:#4f9cf9;--acoes:#34c98e;--intl:#f2a13c;--cripto:#b57bf0}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--txt);font-family:-apple-system,'Segoe UI',Roboto,sans-serif;line-height:1.45}
header{padding:calc(18px + env(safe-area-inset-top)) 16px 10px;position:sticky;top:0;background:rgba(15,17,21,.95);backdrop-filter:blur(8px);z-index:9;border-bottom:1px solid #232733}
h1{font-size:1.15rem;font-weight:700}
.atualizado{color:var(--mut);font-size:.75rem;margin-top:2px}
nav{display:flex;gap:8px;overflow-x:auto;padding:10px 0 4px;-webkit-overflow-scrolling:touch}
nav a{flex:0 0 auto;font-size:.78rem;font-weight:600;padding:5px 12px;border-radius:20px;text-decoration:none;color:var(--txt);background:var(--card);border:1px solid #2a2f3d}
main{padding:8px 16px 40px;max-width:640px;margin:0 auto}
section{margin-top:22px}
h2{font-size:.95rem;font-weight:700;display:flex;align-items:center;gap:8px;padding-bottom:8px}
h2 .dot{width:9px;height:9px;border-radius:50%}
h2 .n{color:var(--mut);font-weight:500;font-size:.8rem}
.item{background:var(--card);border-radius:10px;padding:11px 13px;margin-bottom:8px}
.item a{color:var(--txt);text-decoration:none;font-size:.9rem;font-weight:500;display:block}
.meta{margin-top:5px;font-size:.72rem;color:var(--mut)}
.meta b{color:var(--acc);font-weight:600}
.vazio{color:var(--mut);font-size:.85rem;padding:8px 2px}
footer{text-align:center;color:var(--mut);font-size:.7rem;padding:20px 20px calc(20px + env(safe-area-inset-bottom))}
"""

CORES = {"Macro Brasil": "--macro", "Ações BR": "--acoes",
         "Internacional": "--intl", "Cripto": "--cripto"}


def render(por_secao) -> str:
    try:
        from zoneinfo import ZoneInfo
        agora_br = datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        agora_br = datetime.now(timezone(timedelta(hours=-3)))

    nav = "".join(f'<a href="#{s.replace(" ", "-")}">{s}</a>' for s in SECOES)
    corpo = []
    for s in SECOES:
        itens = por_secao.get(s, [])
        cards = []
        for it in itens:
            hora = it["dt"].astimezone(agora_br.tzinfo).strftime("%d/%m %H:%M")
            cards.append(
                f'<div class="item"><a href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
                f'{html.escape(it["titulo"])}</a>'
                f'<div class="meta"><b>{it["fonte"]}</b> · {hora}</div></div>'
            )
        conteudo = "".join(cards) or '<div class="vazio">Sem notícias nas últimas 24h.</div>'
        corpo.append(
            f'<section id="{s.replace(" ", "-")}">'
            f'<h2><span class="dot" style="background:var({CORES[s]})"></span>{s} '
            f'<span class="n">{len(itens)}</span></h2>{conteudo}</section>'
        )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0f1115">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Mercado">
<title>Notícias do Mercado</title>
<style>{CSS}</style>
</head>
<body>
<header>
<h1>&#128202; Notícias do Mercado</h1>
<div class="atualizado">Atualizado em {agora_br.strftime("%d/%m/%Y às %H:%M")} (Brasília) · últimas {JANELA_HORAS}h</div>
<nav>{nav}</nav>
</header>
<main>{"".join(corpo)}</main>
<footer>InfoMoney · Money Times · InvestNews · Bloomberg Línea · Valor · Exame · Google News</footer>
</body>
</html>"""


if __name__ == "__main__":
    dados = coletar()
    total = sum(len(v) for v in dados.values())
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(render(dados))
    print(f"[FEITO] index.html gerado com {total} notícias")
