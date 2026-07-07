# -*- coding: utf-8 -*-
"""
Consolidador de Notícias Financeiras
Coleta feeds RSS, deduplica, classifica por tema e gera index.html.
Roda 3x/dia via GitHub Actions.
"""
import json
import re
import html
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests
import feedparser

# modo: "geral" = sem tema vai p/ Macro; "estrito" = sem tema descarta;
# "internacional" = tudo vai p/ Internacional (exceto carteira/exclusões)
FEEDS = [
    ("InfoMoney",       "https://www.infomoney.com.br/feed/",                                "geral"),
    ("Money Times",     "https://www.moneytimes.com.br/internacional/feed/",                 "internacional"),
    ("Money Times",     "https://www.moneytimes.com.br/feed/",                               "geral"),
    ("InvestNews",      "https://investnews.com.br/feed/",                                   "geral"),
    ("Bloomberg Línea", "https://www.bloomberglinea.com.br/arc/outboundfeeds/rss/?outputType=xml", "geral"),
    ("Brazil Journal",  "https://braziljournal.com/feed/",                                   "geral"),
    ("Exame",           "https://exame.com/feed/",                                           "estrito"),
]

JANELA_HORAS = 24
MAX_POR_SECAO = 25
LIMIAR_DEDUP = 0.72

SECOES = ["Macro Brasil", "Ações BR", "Internacional", "Empresas EUA", "Política", "IA", "Cripto"]
SECOES_TODAS = ["Carteira"] + SECOES

# carteira do usuário: prioridade máxima, seção própria
CARTEIRA_RE = re.compile(
    r"\b(irbr\d|bbse\d|cxse\d|bbas\d|itub\d|gmat\d|rani\d|also\d"
    r"|irb|bb seguridade|caixa seguridade|banco do brasil|itau"
    r"|grupo mateus|irani|allos)\b"
)

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
        "zona do euro", "recessao global", "america latina",
    ],
    "Empresas EUA": [
        "apple", "microsoft", "google", "alphabet", "amazon", "meta platforms",
        "zuckerberg", "instagram", "whatsapp", "tesla", "netflix", "walmart",
        "jpmorgan", "goldman sachs", "berkshire", "boeing", "disney", "intel",
        "amd", "qualcomm", "oracle", "salesforce", "uber", "spacex", "palantir",
        "broadcom", "morgan stanley", "bank of america", "citigroup", "visa",
        "mastercard", "coca-cola", "nike", "general motors", "exxon", "chevron",
        "big tech*", "wall street journal",
    ],
    "Macro Brasil": [
        "selic", "copom", "banco central", "ipca*", "igp-m", "inflacao", "pib",
        "fiscal", "arcabouco", "divida publica", "tesouro nacional", "cambio",
        "dolar", "juros", "boletim focus", "cdi", "arrecadacao", "orcamento",
        "fazenda", "haddad", "galipolo", "reforma tributaria",
        "imposto*", "caged", "desemprego", "atividade economica", "varejo",
        "industria", "credito",
    ],
    "Política": [
        "eleicao*", "eleitoral", "candidat*", "lula", "bolsonaro", "congresso",
        "senado", "camara dos deputados", "stf", "tse", "ministro*", "partido*",
        "campanha eleitoral", "cpi", "impeachment", "planalto", "datafolha",
        "pesquisa eleitoral", "oposicao", "presidenciavel*", "governo federal",
    ],
    "IA": [
        "inteligencia artificial", "openai", "chatgpt", "anthropic", "claude",
        "gemini", "copilot", "deepseek", "nvidia", "llm*", "machine learning",
        "data center*", "semicondutor*",
    ],
    "Ações BR": [
        "ibovespa", "b3", "acoes", "acao", "dividendo*", "jcp",
        "juros sobre capital", "balanco*", "lucro", "receita liquida",
        "fato relevante", "ipo", "follow-on", "recompra", "petrobras", "vale",
        "itau", "itausa", "bradesco", "ambev", "weg", "embraer", "magalu", "fii*",
        "fundo* imobiliario*", "small cap*", "bolsa brasileira", "pregao",
        "aquisicao*", "fusao*", "controlador*", "minoritario*",
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
PRIORIDADE = {"Cripto": 7, "IA": 6, "Empresas EUA": 5, "Internacional": 4,
              "Política": 3, "Ações BR": 2, "Macro Brasil": 1}

EXCLUIR = [
    "copa do mundo", "futebol", "selecao brasileira", "neymar", "onde assistir",
    "horoscopo", "bbb", "enem", "mega-sena", "loteria", "celebridade",
    "jogos de hoje", "novela", "receita de", "day trade", "daytrade",
    "mini indice", "mini dolar", "loteria federal", "videogame", "playstation",
    "xbox", "nintendo", "esports", "e-sports", "jogos olimpicos", "cassino",
    "apostas esportivas", "efeito vozinha", "viralizou", "viral no",
]

CATEGORIAS_EXCLUIR = {"esporte", "pop", "casual", "mundo", "carreira", "marketing"}

URLS_EXCLUIR = ("/eu-e/", "/patrocinado/")

TITULOS_LIXO = {"correcao", "cartas de leitores", "errata"}

TICKER_RE = re.compile(r"\b[A-Z]{4}\d{1,2}\b")

UA = {"User-Agent": "Mozilla/5.0 (compatible; ConsolidadorNoticias/1.0)"}


def normalizar(texto: str) -> str:
    """minúsculas, sem acentos"""
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def classificar(titulo: str, resumo: str, modo: str):
    txt = normalizar(f"{titulo} {resumo}")

    for kw in EXCLUIR:
        if kw in txt:
            return None

    if CARTEIRA_RE.search(normalizar(titulo)):
        return "Carteira"

    scores = {s: len(KW_RE[s].findall(txt)) for s in SECOES}
    if TICKER_RE.search(titulo or ""):
        scores["Ações BR"] += 2
    if re.search(r"\bIA\b", f"{titulo} {resumo}"):
        scores["IA"] += 1

    if modo == "internacional":
        # feed 100% internacional: só IA/Cripto/Empresas EUA escapam
        sub = {s: scores[s] for s in ("IA", "Cripto", "Empresas EUA")}
        melhor = max(sub, key=lambda s: (sub[s], PRIORIDADE[s]))
        return melhor if sub[melhor] > 0 else "Internacional"

    melhor = max(scores, key=lambda s: (scores[s], PRIORIDADE[s]))
    if scores[melhor] == 0:
        # sem nenhum sinal temático: descarta (mantém as seções focadas)
        return None
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


IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.I)


def extrair_imagem(e) -> str:
    """Pega a imagem da matéria: media:content > media:thumbnail > enclosure > <img> no conteúdo."""
    for attr in ("media_content", "media_thumbnail"):
        for m in getattr(e, attr, None) or []:
            u = (m or {}).get("url", "")
            if u.startswith("http"):
                return u
    for enc in getattr(e, "enclosures", None) or []:
        u = enc.get("href") or enc.get("url") or ""
        if u.startswith("http") and (enc.get("type", "").startswith("image")
                                     or re.search(r"\.(jpe?g|png|webp|gif)", u, re.I)):
            return u
    blob = ""
    for c in getattr(e, "content", None) or []:
        blob += (c or {}).get("value", "")
    blob += getattr(e, "summary", "") or ""
    m = IMG_RE.search(blob)
    return m.group(1) if m and m.group(1).startswith("http") else ""


OG_RE1 = re.compile(r'property=["\']og:image["\'][^>]*?content=["\']([^"\']+)', re.I)
OG_RE2 = re.compile(r'content=["\']([^"\']+)["\'][^>]*?property=["\']og:image["\']', re.I)


def preencher_imagens(por_secao, max_busca=30):
    """Busca og:image na página da matéria para itens sem imagem no feed."""
    n = 0
    for itens in por_secao.values():
        for it in itens:
            if it.get("img") or n >= max_busca:
                continue
            n += 1
            try:
                r = requests.get(it["link"], headers=UA, timeout=8)
                m = OG_RE1.search(r.text) or OG_RE2.search(r.text)
                if m and m.group(1).startswith("http"):
                    it["img"] = html.unescape(m.group(1))
            except Exception:
                pass


def coletar():
    agora = datetime.now(timezone.utc)
    limite = agora - timedelta(hours=JANELA_HORAS)
    itens = []

    for nome, url, modo in FEEDS:
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
            if any(p in link for p in URLS_EXCLUIR):
                continue
            if normalizar(titulo) in TITULOS_LIXO:
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
            secao = classificar(titulo, resumo, modo)
            if secao is None:
                continue

            itens.append({
                "titulo": titulo,
                "link": link,
                "fonte": nome,
                "dt": dt,
                "secao": secao,
                "img": extrair_imagem(e),
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

    por_secao = {s: [i for i in finais if i["secao"] == s][:MAX_POR_SECAO] for s in SECOES_TODAS}
    return por_secao


CSS = """
:root{--bg:#f4f5f8;--card:#ffffff;--txt:#1c2230;--mut:#68738a;--acc:#2f6fd6;
--macro:#2f6fd6;--acoes:#178a5c;--intl:#d97c06;--cripto:#8a4fd3;--cart:#c99400;--pol:#d64545;--ia:#0e9d93;--eua:#d6538a}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--txt);font-family:-apple-system,'Segoe UI',Roboto,sans-serif;line-height:1.45}
header{padding:calc(18px + env(safe-area-inset-top)) 16px 10px;position:sticky;top:0;background:rgba(255,255,255,.94);backdrop-filter:blur(8px);z-index:9;border-bottom:1px solid #e3e6ee}
h1{font-size:1.15rem;font-weight:700}
.atualizado{color:var(--mut);font-size:.75rem;margin-top:2px}
nav{display:flex;flex-wrap:wrap;gap:8px;padding:10px 0 6px}
nav a{flex:0 0 auto;font-size:.78rem;font-weight:600;padding:5px 12px;border-radius:20px;text-decoration:none;color:var(--txt);background:var(--card);border:1px solid #d9dee9}
main{padding:10px 14px 44px;max-width:560px;margin:0 auto}
section{margin-top:28px}
h2{font-size:.95rem;font-weight:700;display:flex;align-items:center;gap:8px;padding-bottom:8px}
h2 .dot{width:9px;height:9px;border-radius:50%}
h2 .n{color:var(--mut);font-weight:500;font-size:.8rem}
.item{background:var(--card);border-radius:16px;margin-bottom:18px;overflow:hidden;box-shadow:0 1px 8px rgba(25,35,60,.09);border:1px solid #e7eaf1;touch-action:pan-y;position:relative}
.item.saindo{transition:transform .22s ease-out,opacity .22s ease-out}
.item.sumindo{transition:height .2s ease-out,margin .2s ease-out,padding .2s ease-out}
.recarregar{position:fixed;right:16px;bottom:calc(18px + env(safe-area-inset-bottom));width:54px;height:54px;border-radius:50%;background:var(--acc);color:#fff;border:none;font-size:1.5rem;box-shadow:0 4px 14px rgba(25,35,60,.3);z-index:99;cursor:pointer}
.dica{background:#e9edf5;color:var(--mut);font-size:.75rem;text-align:center;padding:8px 12px;border-radius:10px;margin:2px 0 12px}
.capa{position:relative;width:100%;height:190px;background:#e9edf4;overflow:hidden}
.capa img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.ph{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#eef1f7,#e0e5ee)}
.ph span{font-size:1.25rem;font-weight:800;letter-spacing:2px;color:#9aa4b8;text-transform:uppercase}
.corpo{padding:14px 16px 15px}
.item a{color:var(--txt);text-decoration:none;font-size:1.02rem;font-weight:600;line-height:1.35;display:block}
.meta{margin-top:8px;font-size:.75rem;color:var(--mut);display:flex;align-items:center;gap:6px}
.meta b{color:var(--acc);font-weight:600}
.meta .tag{margin-left:auto;font-size:.68rem;font-weight:700;padding:2px 9px;border-radius:12px}
.vazio{color:var(--mut);font-size:.85rem;padding:8px 2px}
footer{text-align:center;color:var(--mut);font-size:.7rem;padding:20px 20px calc(20px + env(safe-area-inset-bottom))}
"""

CORES = {"Carteira": "--cart", "Macro Brasil": "--macro", "Ações BR": "--acoes",
         "Internacional": "--intl", "Empresas EUA": "--eua", "Política": "--pol",
         "IA": "--ia", "Cripto": "--cripto"}


def render(por_secao) -> str:
    try:
        from zoneinfo import ZoneInfo
        agora_br = datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        agora_br = datetime.now(timezone(timedelta(hours=-3)))

    visiveis = [s for s in SECOES_TODAS if s == "Carteira" or por_secao.get(s)]
    nav = "".join(f'<a href="#{s.replace(" ", "-")}">{s}</a>' for s in visiveis)
    corpo = []
    for s in visiveis:
        itens = por_secao.get(s, [])
        cards = []
        for it in itens:
            hora = it["dt"].astimezone(agora_br.tzinfo).strftime("%d/%m %H:%M")
            img_tag = (f'<img src="{html.escape(it["img"])}" alt="" '
                       f'loading="lazy" onerror="this.remove()">') if it.get("img") else ""
            capa = (f'<div class="capa"><div class="ph"><span>{it["fonte"]}</span></div>'
                    f'{img_tag}</div>')
            cards.append(
                f'<div class="item" data-id="{html.escape(it["link"])}">{capa}<div class="corpo">'
                f'<a href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
                f'{html.escape(it["titulo"])}</a>'
                f'<div class="meta"><b>{it["fonte"]}</b> · {hora}'
                f'<span class="tag" style="background:var({CORES[s]});color:#fff">{s}</span>'
                f'</div></div></div>'
            )
        vazio_msg = ("Nenhuma notícia das ações da sua carteira nos últimos 7 dias."
                     if s == "Carteira" else "Sem notícias nas últimas 24h.")
        conteudo = "".join(cards) or f'<div class="vazio">{vazio_msg}</div>'
        corpo.append(
            f'<section id="{s.replace(" ", "-")}">'
            f'<h2><span class="dot" style="background:var({CORES[s]})"></span>{s} '
            f'<span class="n">{len(itens)}{" · 7 dias" if s == "Carteira" else ""}</span></h2>{conteudo}</section>'
        )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#f4f5f8">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
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
<footer>InfoMoney · Money Times · InvestNews · Bloomberg Línea · Brazil Journal · Exame</footer>
<button class="recarregar" onclick="location.href=location.pathname+'?r='+Date.now()" aria-label="Buscar novas notícias">&#8635;</button>
{SCRIPT}
</body>
</html>"""


SCRIPT = """
<script>
(function(){
  var KEY='noticiasDescartadas', HINT='dicaSwipeVista';
  var desc={};
  try{desc=JSON.parse(localStorage.getItem(KEY))||{};}catch(e){}
  var agora=Date.now(), mudou=false;
  for(var k in desc){ if(agora-desc[k]>3*864e5){ delete desc[k]; mudou=true; } }
  function salvar(){ try{localStorage.setItem(KEY,JSON.stringify(desc));}catch(e){} }
  if(mudou) salvar();

  function atualizar(){
    document.querySelectorAll('section').forEach(function(sec){
      var n=sec.querySelectorAll('.item').length;
      var b=sec.querySelector('h2 .n'); if(b) b.textContent=n;
      if(n===0 && sec.id!=='Carteira') sec.style.display='none';
    });
  }

  document.querySelectorAll('.item[data-id]').forEach(function(el){
    if(desc[el.getAttribute('data-id')]) el.remove();
  });
  atualizar();

  if(!localStorage.getItem(HINT) && document.querySelector('.item')){
    var d=document.createElement('div');
    d.className='dica'; d.textContent='Deslize a notícia para a esquerda para descartá-la';
    var main=document.querySelector('main'); main.insertBefore(d,main.firstChild);
    try{localStorage.setItem(HINT,'1');}catch(e){}
  }

  function descartar(el){
    desc[el.getAttribute('data-id')]=Date.now(); salvar();
    el.classList.add('saindo');
    el.style.transform='translateX(-110%)'; el.style.opacity='0';
    setTimeout(function(){
      el.style.height=el.offsetHeight+'px';
      el.offsetHeight;
      el.classList.add('sumindo');
      el.style.height='0'; el.style.marginBottom='0';
      setTimeout(function(){ el.remove(); atualizar(); },210);
    },230);
  }

  document.querySelectorAll('.item[data-id]').forEach(function(el){
    var sx=0, sy=0, dx=0, ativo=false, horizontal=false;
    el.addEventListener('touchstart',function(e){
      sx=e.touches[0].clientX; sy=e.touches[0].clientY;
      dx=0; ativo=true; horizontal=false; el.style.transition='none';
    },{passive:true});
    el.addEventListener('touchmove',function(e){
      if(!ativo) return;
      dx=e.touches[0].clientX-sx;
      var dy=e.touches[0].clientY-sy;
      if(!horizontal){
        if(Math.abs(dx)>12 && Math.abs(dx)>Math.abs(dy)*1.4) horizontal=true;
        else if(Math.abs(dy)>14){ ativo=false; el.style.transform=''; el.style.opacity=''; return; }
      }
      if(horizontal && dx<0){
        el.style.transform='translateX('+dx+'px)';
        el.style.opacity=String(Math.max(0.25,1+dx/320));
      }
    },{passive:true});
    el.addEventListener('touchend',function(){
      if(!ativo) return; ativo=false;
      el.style.transition='';
      if(horizontal && dx<-90){ descartar(el); }
      else{
        el.classList.add('saindo');
        el.style.transform=''; el.style.opacity='';
        setTimeout(function(){ el.classList.remove('saindo'); },250);
      }
    });
  });
})();
</script>"""


HIST_ARQ = "carteira_historico.json"
HIST_DIAS = 7
CARTEIRA_MAX = 10


def atualizar_carteira_com_historico(dados):
    """Acumula notícias da carteira em arquivo e exibe as dos últimos 7 dias."""
    try:
        with open(HIST_ARQ, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        hist = {}

    for it in dados.get("Carteira", []):
        hist[it["link"]] = {"titulo": it["titulo"], "fonte": it["fonte"],
                            "dt": it["dt"].isoformat(), "img": it.get("img", "")}

    limite = datetime.now(timezone.utc) - timedelta(days=HIST_DIAS)
    hist = {k: v for k, v in hist.items()
            if datetime.fromisoformat(v["dt"]) >= limite}

    with open(HIST_ARQ, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)

    itens = [{"titulo": v["titulo"], "link": k, "fonte": v["fonte"],
              "dt": datetime.fromisoformat(v["dt"]), "secao": "Carteira",
              "img": v.get("img", ""), "tnorm": normalizar(v["titulo"])}
             for k, v in hist.items()]
    itens.sort(key=lambda i: i["dt"], reverse=True)
    dados["Carteira"] = itens[:CARTEIRA_MAX]


if __name__ == "__main__":
    dados = coletar()
    atualizar_carteira_com_historico(dados)
    preencher_imagens(dados)
    total = sum(len(v) for v in dados.values())
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(render(dados))
    print(f"[FEITO] index.html gerado com {total} notícias")
