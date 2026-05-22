# UpStat Outreach — versão Python

Port completo do app Node (`server.js` + `lib/` + `index.js`) para Python, usando
**FastAPI + Jinja2**. Compartilha o mesmo banco `outreach.sqlite` e os mesmos assets
(`public/`) da versão Node — as duas podem coexistir sobre os mesmos dados.

## Mapa de equivalência

| Node (raiz) | Python (`python/`) |
|---|---|
| `server.js` (Express) | `app.py` (FastAPI) |
| `views/*.ejs` | `templates/*.html` (Jinja2) |
| `lib/scraper.js` (Playwright) | `scraper.py` |
| `lib/site-insights.js` | `site_insights.py` |
| `lib/emails.js` | `emails.py` |
| `lib/personalize.js` (Groq) | `personalize.py` |
| `lib/sender.js` (Resend) | `sender.py` |
| `lib/template.js` | `mailtemplate.py` |
| `lib/state.js` (SQLite) | `state.py` |
| `lib/jobs.js` + SSE | `jobs.py` |
| `lib/progress.js` | `progress.py` |
| `index.js` (CLI) | `cli.py` |

## Setup

```bash
cd python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # só necessário para o comando scrape
```

As variáveis de ambiente são lidas do `.env` na raiz do projeto (o mesmo do Node):
`UI_PASSWORD`, `SESSION_SECRET`, `PORT`, `GROQ_API_KEY`, `RESEND_API_KEY`,
`FROM_EMAIL`, `REPLY_TO`.

## Rodar a web

```bash
python app.py            # sobe em http://localhost:$PORT (default 3000)
```

## Rodar o CLI

```bash
python cli.py scrape "agência de marketing" "Curitiba" 40
python cli.py reenrich [--force]
python cli.py review
python cli.py personalize [--force]
python cli.py send [--limit N] [--email teste@dominio.com]
```

## Notas de port

- O scraping usa o **Playwright síncrono**; os jobs (scrape/personalize/send) rodam em
  threads daemon, então o Playwright sync funciona fora do event loop do FastAPI.
- O template de email continua usando `{{var}}` por regex (não pelo Jinja) — idêntico
  ao `lib/template.js`. Na página `/template` essas chaves aparecem literais via `{% raw %}`.
- O SSE (`/api/jobs/{id}/stream`) replica o comportamento do Express: reenvia eventos já
  emitidos e depois faz streaming dos novos, com keepalive a cada 15s.
- Os seletores do Google Maps em `scraper.py` são os mesmos do JS; se o Maps mudar o
  layout, ambos quebram igual.
