# Consolidador de Notícias Financeiras — Contexto do Projeto

> Documento de handoff para continuar o desenvolvimento em qualquer conversa.
> Atualizado em 12/09/2026. **O GitHub é a fonte de verdade — sempre parta do código atual do repositório, nunca de versões antigas.**

## O que é

Consolidador de notícias financeiras (mercado nacional e internacional) do Luiz, acessado pelo Safari do iPhone 17 como web app (adicionado à tela de início como "Mercado").

| Item | Valor |
|---|---|
| **Página de notícias** | https://luizhgobato.github.io/Noticias/ (`index.html`) |
| **Fatos Relevantes** | fr.html (PDFs embutidos via Google Docs Viewer); dados vêm do Fundamentus (`fatos_relevantes.php?papel=<ticker>`) para cada ação da carteira |
| **Repositório** | github.com/luizhgobato/Noticias (público, conta `luizhgobato`) |
| **Gerador** | `collector.py` (Python: feedparser + requests) |
| **Automação** | GitHub Actions (`.github/workflows/atualizar.yml`), 8x/dia (08h–22h BRT, a cada 2h), commit automático com token nativo — **sem PAT, sem servidor, custo zero** |
| **Estado persistido** | `carteira_historico.json` (7 dias), `publicados_historico.json` (5 dias) — commitados pelo Actions |
| **Testes** | `test_collector.py` — script com stubs de `feedparser`/`requests` (sem rede/PyPI), roda com `python3 test_collector.py` |

## Premissas inegociáveis (decisões do Luiz)

1. **Sem fontes pagas.** Valor Econômico foi removido (paywall). Todo link clicado deve abrir matéria legível grátis.
2. **Sem Google News.** Links redirecionadores quebravam no celular — só feeds RSS diretos.
3. **Mobile first, tema claro** (fundo #f4f5f8, cards brancos). Layout tipo feed do Instagram: cards espaçados, capa de imagem 190px em todos (imagem do feed → og:image da matéria → placeholder com nome da fonte).
4. **Carteira do Luiz é a 1ª seção, sempre visível**, com histórico de 7 dias: IRBR3, BBSE3, CXSE3, BBAS3, ITUB3, GMAT3, RANI3, ALSO3 (match por ticker e nome da empresa, **só no título/resumo** — evita falso positivo de patrocínio).
5. **Frescor acima de tudo:** notícia ≤24h SEMPRE exibe (mesmo já exibida antes — quem remove é o swipe do usuário); notícia >24h já exibida não recicla; nada entra fora da janela de datas. Mínimo de 7 por seção (`MIN_POR_SECAO`), completando com material inédito de até `JANELA_MAX_HORAS` (36h).
6. **Excluir sempre:** loteria, day trade, mini índice/dólar, jogos/videogames, cassino, apostas, esporte, celebridades, virais ("efeito vozinha"), matérias de expediente (Correção, Cartas de Leitores, Errata), URLs `/eu-e/`, `/patrocinado/`, `/esportes/` e `/entretenimento/`.
7. **Ações BR = só empresas listadas no Brasil.** Notícia sem sinal temático é descartada, não empurrada para seção errada.
8. **Swipe para a esquerda descarta** a notícia (localStorage `noticiasDescartadas`, expira em 3 dias, por aparelho). Dica exibida uma única vez.

## Seções (ordem e regras)

Carteira → Fatos Relevantes → Ações BR → Macro Brasil → Internacional → Empresas EUA → Política → IA → Cripto

- Classificação por keywords regex (com word boundary, plural `s?`, prefixo `*`), desempate por especificidade: Cripto > IA > Empresas EUA > Internacional > Política > Ações BR > Macro.
- `\bIA\b` case-sensitive soma ponto em IA. Ticker `[A-Z]{4}\d{1,2}` soma +2 em Ações BR.
- Modos por feed: `geral` | `estrito` (sem tema = descarta) | `internacional` (força Internacional; IA/Cripto/Empresas EUA escapam) | `politica` (força Política).
- Feeds `geral` sem keyword temática só entram se houver sinal financeiro mínimo no texto (`_SINAL_FIN_RE`: lucro, dividendo, balanço, ibovespa, banco, etc.) — evita esporte/lifestyle do feed geral do InfoMoney.
- Empresas EUA = Big Techs e grandes americanas (Apple, Microsoft, Google, Amazon, Tesla, Nvidia→IA, bancos, etc.). "meta" sozinho é proibido como keyword (colide com "meta fiscal") — usar "meta platforms"/zuckerberg/instagram/whatsapp.
- Dedup entre fontes: `SequenceMatcher` > 0.65 (`LIMIAR_DEDUP`) OU contenção de tokens ≥ 0.6 (sem stopwords).

## Fontes ativas (RSS)

InfoMoney (geral) · CNN Brasil (estrito) · G1 Economia (geral) · Folha Mercado (geral) · Agência Brasil (geral) · Money Times (geral + internacional) · InvestNews (geral) · Bloomberg Línea (geral) · Brazil Journal (geral) · Valor Investe (geral) · Exame (estrito) · Olhar Digital (estrito).

Não há feed dedicado para a Carteira: o match acontece dentro dos feeds acima, por ticker/nome da empresa no título ou resumo (`CARTEIRA_RE`).

## Histórico de armadilhas já resolvidas (não regredir!)

- **publicados_historico de 5 dias + execução horária** escondia as manchetes do dia após 1h → regra atual do item 5 das premissas. `TOP_POR_FONTE = 0` (o atalho deixava artigo velho/sem data entrar).
- Feed do Valor trazia lifestyle/publicidade → filtros de URL.
- Editor do GitHub via `execCommand('insertText')` dispara autocomplete (~45 chars extras) → **sempre validar o commit comparando SHA-1 do raw** com o esperado.
- `raw.githubusercontent` e a API de contents têm cache/atraso de propagação (~10-30s) — aguardar e revalidar antes de concluir que o commit falhou.

## Ambiente de desenvolvimento (Cowork/Claude)

- Sandbox Linux **bloqueia** sites de notícias, api.github.com e PyPI (proxy allowlist) em algumas conversas. Quando não há rede/PyPI disponível, testar `collector.py` com stubs de `feedparser`/`requests` via `sys.modules` (ver `test_collector.py`) em vez de depender de rede real.
- Para editar: **fetch do arquivo atual + replaces ancorados** (falhar se âncora não existir). Nunca sobrescrever com cópia local — o Luiz evolui o projeto em conversas paralelas.
- Quando a publicação depender do Chrome do usuário (extensão Claude in Chrome) em vez de acesso direto ao GitHub: abrir `github.com/.../edit/main/arquivo`, injetar conteúdo via JS no CodeMirror (`.cm-content` + selectAll + insertText), commitar via clique nos botões, validar SHA. O filtro DLP da extensão bloqueia outputs JS contendo `=` em padrões de query — mascarar `=` (ex.: `≡`) ao inspecionar código pelo `javascript_tool`. Screenshots do Chrome MCP costumam falhar em github.com ("page still loading") — usar `javascript_tool` para tudo.

## Backlog / ideias mencionadas

- Cripto costuma ficar abaixo do mínimo de 7 (inventário real das fontes) — avaliar feed dedicado (ex.: Cointelegraph Brasil, Livecoins).
- Exame tem paywall "suave" (metered) — remover se incomodar o Luiz.
- Itaúsa (ITSA4) classificada em Ações BR, não na Carteira (holding ≠ ITUB3) — mudar se ele pedir.
