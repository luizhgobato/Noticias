# -*- coding: utf-8 -*-
"""Testa collector.py com feeds simulados (sem rede/PyPI)."""
import os
import sys
import time
import types
from types import SimpleNamespace

agora = time.time()


def entry(titulo, horas_atras=1.0, cats=(), resumo=""):
    return SimpleNamespace(
        title=titulo,
        link="https://exemplo.com/%d" % abs(hash(titulo)),
        tags=[{"term": c} for c in cats],
        published_parsed=time.gmtime(agora - horas_atras * 3600),
        summary=resumo,
    )


T_COPOM1 = "Copom mantém Selic em 15% e sinaliza cautela com inflação"
T_COPOM2 = "Copom mantém Selic em 15% e sinaliza cautela"

FAKE_FEEDS = {
    "infomoney": [
        entry(T_COPOM1, 2),
        entry("PETR4 dispara 4% após anúncio de dividendos extraordinários", 3),
        entry("Bitcoin rompe US$ 120 mil com fluxo recorde em ETFs", 4),
        entry("Ibovespa fecha em alta de 1,2% puxado por bancos", 5),
        entry("Noticia velha que deve ser filtrada", 30),
    ],
    "moneytimes": [
        entry(T_COPOM2, 2.5),
        entry(T_COPOM1, 2.6),
        entry("Fed indica corte de juros em setembro, diz ata do Fomc", 6),
        entry("Dólar cai a R$ 5,10 com fluxo estrangeiro", 1),
    ],
    "investnews": [
        entry("Vale (VALE3) aprova recompra de ações de US$ 2 bi", 7),
        entry("Ethereum sobe 8% com aprovação de novo ETF nos EUA", 8),
    ],
    "bloomberglinea": [
        entry("Wall Street renova máximas com balanços de tecnologia", 9),
        entry("Argentina anuncia novo pacote fiscal e mercados reagem", 10),
    ],
    "valorinveste": [
        entry("Arrecadação federal bate recorde em junho, diz Receita", 11),
    ],
    "exame": [
        entry("IPCA-15 desacelera a 0,2% em junho, abaixo do esperado", 3, cats=["Economia"]),
        entry("Brasil x Escócia: onde assistir ao jogo da Copa do Mundo", 2, cats=["Esporte"]),
        entry("Avatar estreia segunda temporada na Netflix", 1, cats=["Pop"]),
        entry("Materia generica sem tema financeiro nenhum aqui", 1, cats=["Casual"]),
    ],
}


def fake_get(url, headers=None, timeout=None):
    for chave in FAKE_FEEDS:
        if chave in url:
            return SimpleNamespace(content=chave, raise_for_status=lambda: None)
    raise ConnectionError("feed desconhecido (sem stub): %s" % url)


def fake_parse(content):
    return SimpleNamespace(entries=FAKE_FEEDS.get(content, []))


sys.modules["requests"] = types.ModuleType("requests")
sys.modules["requests"].get = fake_get
sys.modules["feedparser"] = types.ModuleType("feedparser")
sys.modules["feedparser"].parse = fake_parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collector

dados = collector.coletar()
print("\n--- RESULTADO ---")
for secao, itens in dados.items():
    print("\n%s (%d):" % (secao, len(itens)))
    for it in itens:
        print("  [%s] %s" % (it["fonte"], it["titulo"]))

todos = [it["titulo"] for v in dados.values() for it in v]
titulos = {s: [i["titulo"] for i in dados[s]] for s in dados}

assert not any(("Esc" in t and "cia" in t) or "Avatar" in t or "generica" in t for t in todos), "exclusao falhou"
assert not any("velha" in t for t in todos), "filtro de conteudo sem sinal financeiro falhou"
dups = [t for t in todos if "Selic em 15%" in t]
assert len(dups) == 1, "dedup falhou: %r" % dups
assert any("Ethereum" in t for t in titulos["Cripto"]), "Ethereum deveria ser Cripto"
assert any("Wall Street" in t for t in titulos["Internacional"]), "Wall Street deveria ser Internacional"

esperado = {
    "Macro Brasil": ["Copom", "Dólar cai", "IPCA-15", "Arrecada"],
    "Ações BR": ["PETR4", "Ibovespa", "VALE3"],
    "Internacional": ["Fed indica", "Wall Street", "Argentina"],
    "Cripto": ["Bitcoin", "Ethereum"],
}
erros = []
for secao, fragmentos in esperado.items():
    for frag in fragmentos:
        if not any(frag.lower() in t.lower() for t in titulos[secao]):
            erros.append("%r nao esta em %s" % (frag, secao))
if erros:
    print("\nERROS DE CLASSIFICACAO:")
    for e in erros:
        print(" -", e)
    sys.exit(1)

print("\nOK: classificacao, dedup, janela de datas e exclusoes")
html_out = collector.render(dados)
assert "<html" in html_out and "Notícias do Mercado" in html_out
print("OK: render() gerou HTML valido (%d bytes)" % len(html_out))
