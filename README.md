# UpStat Outreach

Ferramenta de cold outreach pro UpStat: scrapa agências no Google Maps, busca email no site
delas, deixa você revisar, editar o template e dispara via Resend. Implementada em **Python
(FastAPI + Jinja2)**, com web UI e CLI compartilhando o mesmo `outreach.sqlite`.

## Setup (uma vez)

```bash
cd python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # só necessário pro scrape
cp ../.env.example ../.env         # edite .env com suas chaves
```

`.env` (na raiz do projeto) precisa de:

- `RESEND_API_KEY` — chave da [resend.com](https://resend.com)
- `FROM_EMAIL` — remetente. O domínio precisa estar **verificado** no Resend (SPF/DKIM)
- `REPLY_TO` — opcional. Email pra onde as respostas devem ir
- `GROQ_API_KEY` — chave da [groq.com](https://console.groq.com) (free tier ok). Usado pra gerar aberturas personalizadas via `llama-3.3-70b-versatile`
- `UI_PASSWORD` — senha pra entrar na interface web
- `SESSION_SECRET` — string aleatória pra assinar cookies da UI
- `PORT` — porta da UI (default `3000`)

## Web UI (recomendado)

```bash
cd python && python app.py
# abre http://localhost:3000 — login com a senha de UI_PASSWORD
```

A interface tem seis telas:

- **Dashboard** — contadores (pendentes/aprovados/enviados/descartados) e atalhos.
- **Scrape** — formulário com termo + cidade + máximo; log ao vivo via SSE conforme
  o Chromium roda.
- **Personalize** — usa Groq (`llama-3.3-70b-versatile`) pra ler o site de cada lead
  pendente e gerar uma frase de abertura específica pro nicho. Botão de
  `regenerate everything` quando ajustar o prompt.
- **Review** — tabela com todos os pendentes, checkbox de seleção, edição inline do
  email **e da abertura personalizada**; preview do email final por lead. Ações por
  linha (`P`/`Y`/`N`) e ações em lote. O nome da empresa abre a página individual
  do lead.
- **Template** — editor do subject e corpo do email com variáveis (`{{company}}`,
  `{{opening}}`, `{{hook}}`, `{{stack}}`, `{{painSignals}}`, `{{url}}`,
  `{{replyTo}}`) e preview HTML/texto.
- **Send** — mostra a contagem de aprovados na fila, formulário separado pra teste
  (1 email pro endereço informado) e pra disparo real (com `limit` opcional). Log
  ao vivo do envio.

Cada lead também tem uma página própria (`/leads/...`) com dados, preview do site,
notas internas, histórico, email renderizado e um scan técnico sob demanda. O scan
faz um crawler leve em páginas como `/servicos`, `/portfolio`, `/clientes`,
`/cases` e `/manutencao`, detecta stack provável (WordPress, Wix, Shopify, Webflow,
Loja Integrada, Nuvemshop etc.) e salva sinais simples de dor como lentidão, erros
HTTP, redirects, home pesada e ausência de status page aparente.

O CLI continua funcionando em paralelo — ambos compartilham o mesmo `outreach.sqlite`.
Se existir um `leads.json` legado, ele é importado automaticamente na primeira execução
quando o banco ainda estiver vazio.

## Comandos CLI

Todos os comandos guardam estado em `outreach.sqlite`, então você pode parar e voltar a qualquer
momento. Cada lead tem um `status`: `pending` → `approved`/`rejected` → `sent`.
Rode tudo de dentro de `python/` com a venv ativada.

### `scrape` — coleta leads do Maps

```bash
python cli.py scrape "<termo>" "<cidade>" [maxResults=30]
```

Abre o Chromium (visível, intencional), busca `<termo> em <cidade>` no Google Maps, rola a
lista até atingir `maxResults`, abre cada card e extrai: nome, site, telefone, endereço.
Depois visita o site de cada um e tenta extrair email.

Exemplos:

```bash
python cli.py scrape "agência de marketing" "Curitiba" 30
python cli.py scrape "estúdio de design" "São Paulo" 40
python cli.py scrape "agência de viagens" "Belo Horizonte"
```

Os leads novos são adicionados ao banco SQLite (dedup por website). Rodar `scrape` várias
vezes com termos/cidades diferentes só acumula.

### `reenrich` — re-tenta extração de email

```bash
python cli.py reenrich          # só os leads sem email
python cli.py reenrich --force  # re-tenta todos, sobrescreve emails existentes
```

Útil depois de mexer em `emails.py` (regex, ofuscações, paths). Não refaz o scrape do
Maps — só visita os sites de novo.

### `personalize` — gera abertura por lead via Groq

```bash
python cli.py personalize           # só os leads sem personalização ainda
python cli.py personalize --force   # regenera tudo (após ajustar o prompt)
```

Pra cada lead pendente com site, baixa a home, extrai sinais (title, meta description,
h1, primeiros parágrafos) e manda pro `llama-3.3-70b-versatile` no Groq. O modelo
devolve um JSON com `hook` (1 frase de abertura referenciando o que a empresa faz).
Salvo no banco como `personalizedHook`. O subject vem do template configurado na UI
ou, por padrão, `monitoramento de uptime pra {Empresa}`. Custo: 0 (free tier do Groq, ~14k
requests/dia).

Prompt em `personalize.py` (`SYSTEM`). Ajuste lá se quiser tom diferente.

### `review` — revisão interativa

```bash
python cli.py review
```

Mostra um a um os leads pendentes. Comandos durante a revisão:

| tecla | ação                               |
| ----- | ---------------------------------- |
| `y`   | aprovar (precisa ter email)        |
| `n`   | descartar                          |
| `e`   | editar/preencher email manualmente |
| `s`   | sair (salva o progresso)           |

Cada decisão é salva imediatamente no banco — se você sair no meio, da próxima vez
ele continua de onde parou.

### `send` — dispara os aprovados

```bash
python cli.py send                              # envia tudo aprovado
python cli.py send --limit 10                   # envia só os 10 primeiros da fila
python cli.py send --email teste@gmail.com      # envia 1 email de teste, não altera leads
```

Envia via Resend pros leads com `status: approved` que ainda não foram enviados. Delay de
6s entre envios (~10/min) pra não disparar filtros de spam. Cada envio atualiza o lead
pra `status: sent` com `sentAt` e `resendId`. Pode rodar várias vezes — ignora os já
enviados.

**Flags:**

- `--limit N` — envia só os N primeiros aprovados da fila. Útil pra warm-up do domínio
  (ex: 10/dia nos primeiros dias).
- `--email <addr>` — modo teste. Envia 1 email pra esse endereço usando o nome de um
  lead aprovado como exemplo. Não marca nada como enviado. Use pra ver como o email
  renderiza no Gmail/Outlook antes de disparar de verdade.

## Fluxo típico

```bash
cd python && source .venv/bin/activate

# colete em vários termos/cidades
python cli.py scrape "agência de marketing" "São Paulo" 40
python cli.py scrape "agência de marketing" "Rio de Janeiro" 40
python cli.py scrape "estúdio de design" "Curitiba" 30

# gere aberturas únicas pra cada lead
python cli.py personalize

# revise tudo de uma vez (edite o hook se a IA escreveu algo estranho)
python cli.py review

# dispare
python cli.py send
```

## Estrutura do código (`python/`)

| arquivo | papel |
|---|---|
| `app.py` | servidor web FastAPI + rotas |
| `cli.py` | comandos de linha de comando |
| `scraper.py` | scraping do Google Maps (Playwright) |
| `site_insights.py` | scan técnico de sites |
| `emails.py` | extração de email dos sites |
| `personalize.py` | geração de hook via Groq |
| `sender.py` | envio via Resend |
| `mailtemplate.py` | montagem do email + template editável |
| `state.py` | persistência SQLite |
| `jobs.py` | jobs em background + streaming SSE |
| `templates/*.html` | views Jinja2 |

Os assets servidos ao navegador ficam em `public/` (na raiz).

## Customizando

- **Copy do email:** `mailtemplate.py`. Edita antes do primeiro envio — quanto mais
  específico ao ICP da busca, melhor a resposta. Pela UI, use a tela **Template**.
- **Paths visitados pra achar email:** `PATHS` em `emails.py`.
- **TLDs aceitos:** `PLAUSIBLE_TLDS` em `emails.py`.
- **Delay entre envios:** `DELAY_S` em `sender.py` (padrão 6s).
- **Seletores do Maps:** `scraper.py`. Se o Google mudar o DOM e quebrar, abre o Maps
  no DevTools e ajusta `a.hfpxzc`, `h1.DUwDvf`, `a[data-item-id="authority"]`.

## Estrutura dos leads

Os leads ficam em `outreach.sqlite`. O formato lógico de cada lead é:

```json
[
  {
    "name": "Agência Exemplo",
    "website": "https://exemplo.com.br",
    "phone": "(11) 99999-0000",
    "address": "Rua X, 100 - São Paulo",
    "email": "contato@exemplo.com.br",
    "searchedAs": "agência de marketing / São Paulo",
    "status": "pending",
    "personalizedHook": "vi que vocês trabalham com criação de sites pra restaurantes e cafés…",
    "personalizedAt": "2026-05-14T13:42:11.000Z",
    "sentAt": null,
    "resendId": null
  }
]
```

## Cuidados

- **Volume.** Comece com 20-30 envios/dia do mesmo domínio. Acima disso a reputação cai
  rápido em cold outreach e os emails começam a ir pro spam pra todo mundo.
- **Suprimidos.** Quando alguém pedir pra sair ("remover", "unsubscribe"), marque manualmente
  como `status: "rejected"` no banco e nunca mais inclua. LGPD exige isso.
- **Honestidade.** Se alguém perguntar como você conseguiu o email, diga a verdade: "achei
  no site público da sua empresa".
- **Maps.** O scraping do Maps viola o ToS do Google. O risco prático é CAPTCHA/IP
  bloqueado temporário, não consequência legal — mas se quiser zerar esse risco, troque
  por Places API oficial ou SerpAPI.
