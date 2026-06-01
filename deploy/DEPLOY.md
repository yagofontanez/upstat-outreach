# Deploy em VPS (systemd + Caddy)

Guia para subir o UpStat Outreach num servidor Linux (Ubuntu 22.04+ assumido).
Caminho de instalação usado nos exemplos: `/opt/upstat-outreach`.

## 0. Pré-requisitos

- Uma VPS com IP público (Hetzner, DigitalOcean, etc.).
- Um domínio (ex.: `outreach.seu-dominio.com`) com um registro **A** apontando pro IP da VPS.
- Domínio do remetente verificado no Resend (SPF/DKIM/DMARC).

## 1. Pacotes do sistema

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip sqlite3 git
```

## 2. Usuário de serviço + código

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin upstat
sudo mkdir -p /opt/upstat-outreach
sudo chown upstat:upstat /opt/upstat-outreach

# clone o repositório nesse caminho (como root ou via deploy key)
sudo -u upstat git clone <URL_DO_REPO> /opt/upstat-outreach
```

A estrutura esperada é `/opt/upstat-outreach/python`, `/opt/upstat-outreach/public`,
`/opt/upstat-outreach/.env` e o banco em `/opt/upstat-outreach/outreach.sqlite`.

## 3. Ambiente Python + Playwright

```bash
cd /opt/upstat-outreach/python
sudo -u upstat python3 -m venv .venv
sudo -u upstat .venv/bin/pip install -r requirements.txt

# instala o Chromium + libs de sistema necessárias (headless no servidor)
sudo PLAYWRIGHT_BROWSERS_PATH=/opt/upstat-outreach/.playwright \
  .venv/bin/playwright install --with-deps chromium
sudo chown -R upstat:upstat /opt/upstat-outreach/.playwright
```

## 4. Arquivo `.env` de produção

Crie `/opt/upstat-outreach/.env` (dono `upstat`, permissão `600`):

```bash
RESEND_API_KEY=re_...
FROM_EMAIL="Sua Marca <noreply@seu-dominio.com>"
REPLY_TO="voce@seu-dominio.com"
GROQ_API_KEY=gsk_...

UI_PASSWORD="uma-senha-bem-forte"
SESSION_SECRET="string-longa-e-aleatoria"
PORT=8000

# produção:
COOKIE_SECURE=true
BASE_URL="https://outreach.seu-dominio.com"
RESEND_WEBHOOK_SECRET="whsec_..."   # do dashboard do Resend (passo 8)
```

```bash
sudo chown upstat:upstat /opt/upstat-outreach/.env
sudo chmod 600 /opt/upstat-outreach/.env
```

> Observação: o scraper roda **headless** por padrão no servidor. Para depurar com janela
> visível numa máquina com display, use `SCRAPER_HEADFUL=1`.

## 5. Serviço systemd

```bash
sudo cp /opt/upstat-outreach/deploy/upstat-outreach.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now upstat-outreach
sudo systemctl status upstat-outreach        # deve estar "active (running)"
```

O serviço escuta só em `127.0.0.1:8000` — quem expõe pra internet é o Caddy.

## 6. Caddy (HTTPS automático + proxy)

```bash
# instala o Caddy (repo oficial)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# configure (troque o domínio dentro do arquivo)
sudo cp /opt/upstat-outreach/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile         # ajuste outreach.seu-dominio.com
sudo systemctl reload caddy
```

Acesse `https://outreach.seu-dominio.com` — login com `UI_PASSWORD`.

## 7. Backup do banco

```bash
sudo chmod +x /opt/upstat-outreach/deploy/backup.sh
sudo crontab -u upstat -e
# adicione (backup de hora em hora):
0 * * * * /opt/upstat-outreach/deploy/backup.sh
```

## 8. Webhook do Resend

No dashboard do Resend → **Webhooks** → adicione o endpoint:

```
https://outreach.seu-dominio.com/webhooks/resend
```

Eventos: `email.delivered`, `email.opened`, `email.clicked`, `email.bounced`, `email.complained`.
Copie o **signing secret** (`whsec_...`) pra `RESEND_WEBHOOK_SECRET` no `.env` e reinicie:

```bash
sudo systemctl restart upstat-outreach
```

## 9. Pipeline automático (sem humano no loop)

O comando `pipeline` roda o ciclo completo pra cada cliente, **dispensando a
aprovação manual** em `/review`:

1. **reabastece** — se o estoque vendável estiver abaixo do alvo (default `2× limite`),
   scrapa o próximo preset do cliente (rotaciona entre os presets a cada execução);
2. **personaliza** — gera o hook via Groq pros pendentes que ainda não têm (com teto);
3. **auto-aprova** — marca `pending → approved` só quem passa nas travas de qualidade:
   email válido, **não** suprimido (bounce/unsub/complaint) e **com** hook personalizado;
4. **envia o primeiro email** — respeita o teto diário (`--limit`, default **20/cliente**
   — conservador pra preservar a reputação do remetente).

O **follow-up fica de fora** do ciclo automático (o cron cuida só do primeiro contato).
Pra incluí-lo, passe `--followup` no comando, ou agende o `followup` à parte.

### Liga/desliga pela web (default: DESLIGADO)

O ciclo só roda pra um cliente se o **envio automático** estiver **ligado** no painel
(toggle no dashboard `/`, seção *02 ▸ automation*). A flag fica em `settings`
(`pipeline_enabled`, por cliente) e **nasce desligada** — então mesmo com o timer
ativo, nada é enviado até você ligar na web. Pra desligar tudo na hora, é só virar
o toggle (o timer continua disparando, mas o pipeline sai sem fazer nada).

Teste manual primeiro (1 cliente, sem scrape, teto baixo). Como a flag nasce
desligada, use `--force` pra rodar ignorando o toggle:

```bash
cd /opt/upstat-outreach/python
sudo -u upstat PLAYWRIGHT_BROWSERS_PATH=/opt/upstat-outreach/.playwright \
  .venv/bin/python cli.py --client upstat pipeline --limit 3 --no-scrape --force
```

Agende com **systemd timer** (recomendado — registra logs no journal):

```bash
sudo chmod +x /opt/upstat-outreach/deploy/pipeline.sh
sudo cp /opt/upstat-outreach/deploy/upstat-outreach-pipeline.service /etc/systemd/system/
sudo cp /opt/upstat-outreach/deploy/upstat-outreach-pipeline.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now upstat-outreach-pipeline.timer

# confira o horário do próximo disparo (a VPS costuma estar em UTC):
systemctl list-timers upstat-outreach-pipeline.timer
# rode na mão uma vez, sem esperar o horário:
sudo systemctl start upstat-outreach-pipeline.service
journalctl -u upstat-outreach-pipeline.service -f
```

Ajustes ficam no `.service` (`PIPELINE_LIMIT`, `PIPELINE_CLIENTS`) e no `.timer`
(`OnCalendar` — hoje `Mon..Fri 13:30` UTC ≈ 10:30 BRT). Os logs detalhados de cada
run ficam também em `/opt/upstat-outreach/logs/pipeline-*.log` (mantém os últimos 30).

> Alternativa via cron (sem journal):
> ```bash
> sudo crontab -u upstat -e
> 30 13 * * 1-5 /opt/upstat-outreach/deploy/pipeline.sh
> ```

## 10. Atualizar (deploy de nova versão)

```bash
cd /opt/upstat-outreach
sudo -u upstat git pull
sudo -u upstat python/.venv/bin/pip install -r python/requirements.txt   # se mudou deps
sudo systemctl restart upstat-outreach
```

## Logs e troubleshooting

```bash
journalctl -u upstat-outreach -f              # logs do app
journalctl -u upstat-outreach-pipeline -f     # logs do pipeline automático
journalctl -u caddy -f                        # logs do proxy/TLS
```

- **502 no navegador**: o serviço caiu — veja `journalctl -u upstat-outreach`.
- **Login não persiste**: confirme `COOKIE_SECURE=true` só com HTTPS funcionando (via Caddy).
- **Scrape falha sem erro claro**: provavelmente faltou `playwright install --with-deps chromium`.
- **Log ao vivo trava**: confira o `flush_interval -1` no Caddyfile.
