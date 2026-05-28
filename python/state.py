"""Storage SQLite multi-cliente.

Schema:
  clients         (id, name, url, locale, resend_*, from_email, reply_to, is_default, …)
  leads           PK (client_id, key)
  settings        PK (client_id, key)
  suppressions    PK (client_id, email)
  search_presets  (id, client_id, label, term, cities, max_results, …)

Migração one-shot na primeira execução: faz backup do .sqlite, cria a tabela
clients (seed upstat + martinsadviser), e recria leads/settings/suppressions
com client_id, atribuindo 'upstat' aos registros legados.
"""

import json
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from paths import DB_FILE, LEGACY_FILE

LEGACY_CLIENT_ID = "upstat"

_db = sqlite3.connect(str(DB_FILE), check_same_thread=False)
_db.row_factory = sqlite3.Row
# RLock pra permitir chamadas reentrantes (ex: create_preset chama get_preset
# dentro do mesmo `with connection()`). Lock simples deadlocaria.
_lock = threading.RLock()

_db.execute("PRAGMA journal_mode=WAL")
_db.execute("PRAGMA busy_timeout=5000")
_db.execute("PRAGMA foreign_keys=ON")


@contextmanager
def connection():
    """Context manager para outros módulos compartilharem a conexão e o lock."""
    with _lock:
        yield _db


# ---------------------------------------------------------------- migração
def _columns(table):
    return {r["name"] for r in _db.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_exists(name):
    row = _db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _backup_db_once():
    """Copia o .sqlite pra um backup antes da migração multi-cliente, uma única vez."""
    if not DB_FILE.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = DB_FILE.with_name(f"{DB_FILE.name}.backup-pre-multiclient-{stamp}")
    # se já tiver algum backup pre-multiclient, não cria de novo
    existing = list(DB_FILE.parent.glob(f"{DB_FILE.name}.backup-pre-multiclient-*"))
    if existing:
        return
    try:
        shutil.copy2(DB_FILE, backup_path)
    except Exception:
        pass  # backup é best-effort


def _ensure_clients_table():
    _db.execute(
        """
        CREATE TABLE IF NOT EXISTS clients (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          url TEXT NOT NULL,
          locale TEXT NOT NULL DEFAULT 'pt-BR',
          resend_api_key TEXT,
          from_email TEXT,
          reply_to TEXT,
          is_default INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _ensure_presets_table():
    _db.execute(
        """
        CREATE TABLE IF NOT EXISTS search_presets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          client_id TEXT NOT NULL,
          label TEXT NOT NULL,
          term TEXT NOT NULL,
          cities TEXT NOT NULL,
          max_results INTEGER NOT NULL DEFAULT 30,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_presets_client ON search_presets(client_id)"
    )


def _migrate_leads():
    """Garante que leads tenha PK (client_id, key)."""
    if not _table_exists("leads"):
        _db.execute(
            """
            CREATE TABLE leads (
              client_id TEXT NOT NULL,
              key TEXT NOT NULL,
              name TEXT,
              website TEXT,
              email TEXT,
              status TEXT,
              data TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (client_id, key)
            )
            """
        )
        _db.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_client_status ON leads(client_id, status)"
        )
        _db.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_client_email ON leads(client_id, email)"
        )
        return

    cols = _columns("leads")
    if "client_id" in cols:
        return  # já migrado

    _db.execute(
        """
        CREATE TABLE leads_new (
          client_id TEXT NOT NULL,
          key TEXT NOT NULL,
          name TEXT,
          website TEXT,
          email TEXT,
          status TEXT,
          data TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (client_id, key)
        )
        """
    )
    _db.execute(
        """
        INSERT INTO leads_new (client_id, key, name, website, email, status, data, updated_at)
        SELECT ?, key, name, website, email, status, data, updated_at FROM leads
        """,
        (LEGACY_CLIENT_ID,),
    )
    _db.execute("DROP TABLE leads")
    _db.execute("ALTER TABLE leads_new RENAME TO leads")
    _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_client_status ON leads(client_id, status)"
    )
    _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_client_email ON leads(client_id, email)"
    )


def _migrate_settings():
    if not _table_exists("settings"):
        _db.execute(
            """
            CREATE TABLE settings (
              client_id TEXT NOT NULL,
              key TEXT NOT NULL,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (client_id, key)
            )
            """
        )
        return

    cols = _columns("settings")
    if "client_id" in cols:
        return

    _db.execute(
        """
        CREATE TABLE settings_new (
          client_id TEXT NOT NULL,
          key TEXT NOT NULL,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (client_id, key)
        )
        """
    )
    _db.execute(
        """
        INSERT INTO settings_new (client_id, key, value, updated_at)
        SELECT ?, key, value, updated_at FROM settings
        """,
        (LEGACY_CLIENT_ID,),
    )
    _db.execute("DROP TABLE settings")
    _db.execute("ALTER TABLE settings_new RENAME TO settings")


def _migrate_suppressions():
    if not _table_exists("suppressions"):
        _db.execute(
            """
            CREATE TABLE suppressions (
              client_id TEXT NOT NULL,
              email TEXT NOT NULL,
              reason TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (client_id, email)
            )
            """
        )
        return

    cols = _columns("suppressions")
    if "client_id" in cols:
        return

    _db.execute(
        """
        CREATE TABLE suppressions_new (
          client_id TEXT NOT NULL,
          email TEXT NOT NULL,
          reason TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (client_id, email)
        )
        """
    )
    _db.execute(
        """
        INSERT INTO suppressions_new (client_id, email, reason, created_at)
        SELECT ?, email, reason, created_at FROM suppressions
        """,
        (LEGACY_CLIENT_ID,),
    )
    _db.execute("DROP TABLE suppressions")
    _db.execute("ALTER TABLE suppressions_new RENAME TO suppressions")


def _init_schema():
    needs_migration = (
        _table_exists("leads")
        and "client_id" not in _columns("leads")
    ) or (
        _table_exists("settings")
        and "client_id" not in _columns("settings")
    ) or (
        _table_exists("suppressions")
        and "client_id" not in _columns("suppressions")
    )
    if needs_migration:
        _backup_db_once()

    with _lock:
        try:
            _db.execute("BEGIN")
            _ensure_clients_table()
            _migrate_leads()
            _migrate_settings()
            _migrate_suppressions()
            _ensure_presets_table()
            _db.commit()
        except Exception:
            _db.rollback()
            raise


_init_schema()


# ---------------------------------------------------------------- leads
def _key_of(lead):
    return lead.get("website") or lead.get("name")


def _load_legacy_json():
    if not LEGACY_FILE.exists():
        return []
    try:
        parsed = json.loads(LEGACY_FILE.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


_legacy_imported = False


def _migrate_legacy_json_if_needed(client_id):
    """Importa leads.json antigo se o banco do cliente upstat ainda estiver vazio."""
    global _legacy_imported
    if _legacy_imported or client_id != LEGACY_CLIENT_ID:
        return
    _legacy_imported = True
    with _lock:
        row = _db.execute(
            "SELECT COUNT(*) AS count FROM leads WHERE client_id = ?", (client_id,)
        ).fetchone()
    if row["count"] > 0:
        return
    legacy = [l for l in _load_legacy_json() if _key_of(l)]
    if not legacy:
        return
    save(client_id, legacy)


def load(client_id):
    """Carrega todos os leads de um cliente."""
    if not client_id:
        raise ValueError("client_id é obrigatório em state.load()")
    _migrate_legacy_json_if_needed(client_id)
    with _lock:
        rows = _db.execute(
            "SELECT data FROM leads WHERE client_id = ? ORDER BY rowid", (client_id,)
        ).fetchall()
    return [json.loads(r["data"]) for r in rows]


def save(client_id, leads):
    """Upsert dos leads passados; remove do banco quem não estiver na lista (escopado ao cliente)."""
    if not client_id:
        raise ValueError("client_id é obrigatório em state.save()")
    with _lock:
        try:
            _db.execute("BEGIN")
            keys = []
            for lead in leads:
                key = _key_of(lead)
                if not key:
                    continue
                keys.append(key)
                _db.execute(
                    """
                    INSERT INTO leads (client_id, key, name, website, email, status, data, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(client_id, key) DO UPDATE SET
                      name = excluded.name,
                      website = excluded.website,
                      email = excluded.email,
                      status = excluded.status,
                      data = excluded.data,
                      updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        client_id,
                        key,
                        lead.get("name") or "",
                        lead.get("website") or "",
                        lead.get("email") or "",
                        lead.get("status") or "",
                        json.dumps(lead, ensure_ascii=False),
                    ),
                )
            if keys:
                placeholders = ",".join("?" * len(keys))
                _db.execute(
                    f"DELETE FROM leads WHERE client_id = ? AND key NOT IN ({placeholders})",
                    (client_id, *keys),
                )
            else:
                _db.execute("DELETE FROM leads WHERE client_id = ?", (client_id,))
            _db.commit()
        except Exception:
            _db.rollback()
            raise


# ---------------------------------------------------------------- settings
def get_setting(client_id, key, fallback=None):
    if not client_id:
        raise ValueError("client_id é obrigatório em state.get_setting()")
    with _lock:
        row = _db.execute(
            "SELECT value FROM settings WHERE client_id = ? AND key = ?",
            (client_id, key),
        ).fetchone()
    return row["value"] if row else fallback


def set_setting(client_id, key, value):
    if not client_id:
        raise ValueError("client_id é obrigatório em state.set_setting()")
    with _lock:
        _db.execute(
            """
            INSERT INTO settings (client_id, key, value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(client_id, key) DO UPDATE SET
              value = excluded.value,
              updated_at = CURRENT_TIMESTAMP
            """,
            (client_id, key, str(value)),
        )
        _db.commit()


# ---------------------------------------------------------------- suppressions
def add_suppression(client_id, email, reason="unsubscribe"):
    if not client_id:
        raise ValueError("client_id é obrigatório em state.add_suppression()")
    email = (email or "").strip().lower()
    if not email:
        return
    with _lock:
        _db.execute(
            """
            INSERT INTO suppressions (client_id, email, reason, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(client_id, email) DO UPDATE SET
              reason = excluded.reason,
              created_at = CURRENT_TIMESTAMP
            """,
            (client_id, email, reason),
        )
        _db.commit()


def is_suppressed(client_id, email):
    if not client_id:
        raise ValueError("client_id é obrigatório em state.is_suppressed()")
    email = (email or "").strip().lower()
    if not email:
        return False
    with _lock:
        row = _db.execute(
            "SELECT 1 FROM suppressions WHERE client_id = ? AND email = ?",
            (client_id, email),
        ).fetchone()
    return row is not None


def list_suppressions(client_id):
    if not client_id:
        raise ValueError("client_id é obrigatório em state.list_suppressions()")
    with _lock:
        rows = _db.execute(
            """
            SELECT email, reason, created_at FROM suppressions
            WHERE client_id = ?
            ORDER BY created_at DESC
            """,
            (client_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- cross-client lookup
def find_lead_by_resend_id(email_id):
    """Busca um lead por resend_id em todos os clientes. Retorna (client_id, lead) ou (None, None)."""
    if not email_id:
        return None, None
    with _lock:
        rows = _db.execute(
            """
            SELECT client_id, data FROM leads
            WHERE data LIKE ? OR data LIKE ?
            """,
            (f'%"resendId": "{email_id}"%', f'%"followupResendId": "{email_id}"%'),
        ).fetchall()
    for r in rows:
        lead = json.loads(r["data"])
        if (
            lead.get("resendId") == email_id
            or lead.get("followupResendId") == email_id
        ):
            return r["client_id"], lead
    return None, None
