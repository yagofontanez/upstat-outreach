"""Storage SQLite — equivalente a lib/state.js.

Usa o mesmo arquivo outreach.sqlite e o mesmo schema do código Node,
então os dois podem coexistir sobre o mesmo banco.
"""

import json
import sqlite3
import threading

from paths import DB_FILE, LEGACY_FILE

# check_same_thread=False: os jobs rodam em threads separadas. Serializamos com um lock.
_db = sqlite3.connect(str(DB_FILE), check_same_thread=False)
_db.row_factory = sqlite3.Row
_lock = threading.Lock()

_db.executescript(
    """
    CREATE TABLE IF NOT EXISTS leads (
      key TEXT PRIMARY KEY,
      name TEXT,
      website TEXT,
      email TEXT,
      status TEXT,
      data TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
    CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);

    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """
)
_db.commit()


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


def _migrate_legacy_if_needed():
    with _lock:
        row = _db.execute("SELECT COUNT(*) AS count FROM leads").fetchone()
    if row["count"] > 0:
        return
    legacy = [l for l in _load_legacy_json() if _key_of(l)]
    if not legacy:
        return
    save(legacy)


def load():
    _migrate_legacy_if_needed()
    with _lock:
        rows = _db.execute("SELECT data FROM leads ORDER BY rowid").fetchall()
    return [json.loads(r["data"]) for r in rows]


def save(leads):
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
                    INSERT INTO leads (key, name, website, email, status, data, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                      name = excluded.name,
                      website = excluded.website,
                      email = excluded.email,
                      status = excluded.status,
                      data = excluded.data,
                      updated_at = CURRENT_TIMESTAMP
                    """,
                    (
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
                    f"DELETE FROM leads WHERE key NOT IN ({placeholders})", keys
                )
            else:
                _db.execute("DELETE FROM leads")
            _db.commit()
        except Exception:
            _db.rollback()
            raise


def get_setting(key, fallback=None):
    with _lock:
        row = _db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else fallback


def set_setting(key, value):
    with _lock:
        _db.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              updated_at = CURRENT_TIMESTAMP
            """,
            (key, str(value)),
        )
        _db.commit()
