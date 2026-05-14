# UpStat Outreach

Script de cold outreach pro UpStat: scrapa agências no Google Maps, busca email no site
delas, deixa você revisar e dispara via Resend.

## Setup (uma vez)

```bash
npm install
npx playwright install chromium
cp .env.example .env
# edite .env com sua RESEND_API_KEY
```

`.env` precisa de:

- `RESEND_API_KEY` — chave da [resend.com](https://resend.com)
- `FROM_EMAIL` — remetente. O domínio precisa estar **verificado** no Resend (SPF/DKIM)
- `REPLY_TO` — opcional. Email pra onde as respostas devem ir
- `UI_PASSWORD` — senha pra entrar na interface web (`npm run web`)
- `SESSION_SECRET` — string aleatória pra assinar cookies da UI
- `PORT` — porta da UI (default `3000`)

## Web UI (recomendado)

```bash
npm run web
# abre http://localhost:3000 — login com a senha de UI_PASSWORD
```

A interface tem quatro telas:

- **Dashboard** — contadores (pendentes/aprovados/enviados/descartados) e atalhos.
- **Scrape** — formulário com termo + cidade + máximo; log ao vivo via SSE conforme
  o Chromium roda.
- **Review** — tabela com todos os pendentes, checkbox de seleção, edição inline do
  email, ações por linha (`✓`/`✕`) e ações em lote (aprovar todos com email, descartar
  todos sem email, etc).
- **Send** — mostra a contagem de aprovados na fila, formulário separado pra teste
  (1 email pro endereço informado) e pra disparo real (com `limit` opcional). Log
  ao vivo do envio.

O CLI continua funcionando em paralelo — ambos compartilham o mesmo `leads.json`.

## Comandos CLI

Todos os comandos guardam estado em `leads.json`, então você pode parar e voltar a qualquer
momento. Cada lead tem um `status`: `pending` → `approved`/`rejected` → `sent`.

### `scrape` — coleta leads do Maps

```bash
node index.js scrape "<termo>" "<cidade>" [maxResults=30]
```

Abre o Chromium (visível, intencional), busca `<termo> em <cidade>` no Google Maps, rola a
lista até atingir `maxResults`, abre cada card e extrai: nome, site, telefone, endereço.
Depois visita o site de cada um e tenta extrair email.

Exemplos:

```bash
node index.js scrape "agência de marketing" "Curitiba" 30
node index.js scrape "estúdio de design" "São Paulo" 40
node index.js scrape "agência de viagens" "Belo Horizonte"
```

Os leads novos são adicionados ao `leads.json` (dedup por website). Rodar `scrape` várias
vezes com termos/cidades diferentes só acumula.

### `reenrich` — re-tenta extração de email

```bash
node index.js reenrich          # só os leads sem email
node index.js reenrich --force  # re-tenta todos, sobrescreve emails existentes
```

Útil depois de mexer em `lib/emails.js` (regex, ofuscações, paths). Não refaz o scrape do
Maps — só visita os sites de novo.

### `review` — revisão interativa

```bash
node index.js review
```

Mostra um a um os leads pendentes. Comandos durante a revisão:

| tecla | ação                               |
| ----- | ---------------------------------- |
| `y`   | aprovar (precisa ter email)        |
| `n`   | descartar                          |
| `e`   | editar/preencher email manualmente |
| `s`   | sair (salva o progresso)           |

Cada decisão é salva imediatamente em `leads.json` — se você sair no meio, da próxima vez
ele continua de onde parou.

### `send` — dispara os aprovados

```bash
node index.js send                              # envia tudo aprovado
node index.js send --limit 10                   # envia só os 10 primeiros da fila
node index.js send --email teste@gmail.com      # envia 1 email de teste, não toca em leads.json
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
# colete em vários termos/cidades
node index.js scrape "agência de marketing" "São Paulo" 40
node index.js scrape "agência de marketing" "Rio de Janeiro" 40
node index.js scrape "estúdio de design" "Curitiba" 30

# revise tudo de uma vez
node index.js review

# dispare
node index.js send
```

## Customizando

- **Copy do email:** `lib/template.js`. Edita antes do primeiro envio — quanto mais
  específico ao ICP da busca, melhor a resposta.
- **Paths visitados pra achar email:** `PATHS` em `lib/emails.js`.
- **TLDs aceitos:** `PLAUSIBLE_TLDS` em `lib/emails.js`.
- **Delay entre envios:** `DELAY_MS` em `lib/sender.js` (padrão 6000ms).
- **Seletores do Maps:** `lib/scraper.js`. Se o Google mudar o DOM e quebrar, abre o Maps
  no DevTools e ajusta `a.hfpxzc`, `h1.DUwDvf`, `a[data-item-id="authority"]`.

## Estrutura do `leads.json`

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
    "sentAt": null,
    "resendId": null
  }
]
```

## Cuidados

- **Volume.** Comece com 20-30 envios/dia do mesmo domínio. Acima disso a reputação cai
  rápido em cold outreach e os emails começam a ir pro spam pra todo mundo.
- **Suprimidos.** Quando alguém pedir pra sair ("remover", "unsubscribe"), marque manualmente
  como `status: "rejected"` no `leads.json` e nunca mais inclua. LGPD exige isso.
- **Honestidade.** Se alguém perguntar como você conseguiu o email, diga a verdade: "achei
  no site público da sua empresa".
- **Maps.** O scraping do Maps viola o ToS do Google. O risco prático é CAPTCHA/IP
  bloqueado temporário, não consequência legal — mas se quiser zerar esse risco, troque
  por Places API oficial ou SerpAPI.
