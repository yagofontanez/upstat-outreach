#!/usr/bin/env bash
# Backup consistente do SQLite (usa .backup, seguro mesmo com WAL ativo).
# Agende no cron: 0 * * * * /opt/upstat-outreach/deploy/backup.sh
set -euo pipefail

DB="/opt/upstat-outreach/outreach.sqlite"
DEST="/opt/upstat-outreach/backups"
KEEP=48  # mantém os últimos N backups

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
sqlite3 "$DB" ".backup '$DEST/outreach-$STAMP.sqlite'"

# remove os mais antigos além do limite
ls -1t "$DEST"/outreach-*.sqlite 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
