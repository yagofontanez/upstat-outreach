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

## 9. Atualizar (deploy de nova versão)

```bash
cd /opt/upstat-outreach
sudo -u upstat git pull
sudo -u upstat python/.venv/bin/pip install -r python/requirements.txt   # se mudou deps
sudo systemctl restart upstat-outreach
```

## Logs e troubleshooting

```bash
journalctl -u upstat-outreach -f      # logs do app
journalctl -u caddy -f                # logs do proxy/TLS
```

- **502 no navegador**: o serviço caiu — veja `journalctl -u upstat-outreach`.
- **Login não persiste**: confirme `COOKIE_SECURE=true` só com HTTPS funcionando (via Caddy).
- **Scrape falha sem erro claro**: provavelmente faltou `playwright install --with-deps chromium`.
- **Log ao vivo trava**: confira o `flush_interval -1` no Caddyfile.
