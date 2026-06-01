#!/usr/bin/env bash
# Pipeline diário SEM humano no loop, pra cada cliente (só o PRIMEIRO email —
# follow-up fica de fora de propósito):
#   reabastece (scrape) → personaliza → auto-aprova → envia (teto/dia)
#
# Agende via systemd timer (deploy/upstat-outreach-pipeline.timer) — recomendado —
# ou via cron:
#   30 13 * * 1-5 /opt/upstat-outreach/deploy/pipeline.sh   # 13:30 UTC (~10:30 BRT), seg-sex
#
# Variáveis de ambiente (opcionais):
#   PIPELINE_LIMIT     teto de envios/dia por cliente (default 20)
#   PIPELINE_CLIENTS   lista de slugs separados por espaço (default "upstat martinsadviser")
set -euo pipefail

APP="${APP_DIR:-/opt/upstat-outreach}"
PY="$APP/python/.venv/bin/python"
CLI="$APP/python/cli.py"
LOG_DIR="$APP/logs"
LIMIT="${PIPELINE_LIMIT:-20}"
CLIENTS="${PIPELINE_CLIENTS:-upstat martinsadviser}"

# O Chromium do Playwright fica num caminho fixo (mesmo do .service). No cron,
# sem isso o scrape não acha o browser. Pode sobrescrever via ambiente.
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$APP/.playwright}"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/pipeline-$(date +%Y%m%d-%H%M%S).log"

cd "$APP/python"
for c in $CLIENTS; do
  echo "===== $(date -Is) · cliente=$c · limite=$LIMIT =====" | tee -a "$LOG"
  if ! "$PY" "$CLI" --client "$c" pipeline --limit "$LIMIT" 2>&1 | tee -a "$LOG"; then
    echo "[erro] pipeline falhou pro cliente '$c' — sigo pros próximos" | tee -a "$LOG"
  fi
done

# retém só os últimos 30 logs
ls -1t "$LOG_DIR"/pipeline-*.log 2>/dev/null | tail -n +31 | xargs -r rm -f
