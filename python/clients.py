"""CRUD da tabela clients + seed dos dois SaaS iniciais.

Um 'client' é um SaaS que você opera (UpStat, MartinsAdviser, …). Cada um tem
sua URL pública, locale, credenciais de envio (Resend) e suas próprias filas
de leads/templates/supressões/presets — tudo namespaced por client_id.
"""

import re

from state import LEGACY_CLIENT_ID, connection

SEEDS = [
    {
        "id": "upstat",
        "name": "UpStat",
        "url": "https://upstat.online/?utm_source=outreach&utm_medium=email",
        "locale": "pt-BR",
        "is_default": 1,
        # As credenciais do UpStat continuam vindo do .env na primeira execução.
        # O usuário pode preencher resend_api_key/from_email/reply_to aqui via UI
        # se quiser tirar do .env eventualmente.
    },
    {
        "id": "martinsadviser",
        "name": "MartinsAdviser",
        "url": "https://martinsadviser.com/?utm_source=outreach&utm_medium=email",
        "locale": "en-US",
        "is_default": 0,
    },
]


def _row_to_dict(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "locale": row["locale"],
        "resend_api_key": row["resend_api_key"] or "",
        "from_email": row["from_email"] or "",
        "reply_to": row["reply_to"] or "",
        "is_default": bool(row["is_default"]),
        "created_at": row["created_at"],
    }


def list_clients():
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM clients ORDER BY is_default DESC, created_at ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_client(client_id):
    if not client_id:
        return None
    with connection() as db:
        row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return _row_to_dict(row)


def default_client():
    """Retorna o cliente default (is_default=1) ou o primeiro cadastrado."""
    with connection() as db:
        row = db.execute(
            "SELECT * FROM clients ORDER BY is_default DESC, created_at ASC LIMIT 1"
        ).fetchone()
    return _row_to_dict(row)


def _slugify(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "client"


def create_client(
    id, name, url, locale="pt-BR", resend_api_key="", from_email="", reply_to="",
    is_default=False,
):
    client_id = _slugify(id or name)
    if not name or not url:
        raise ValueError("name e url são obrigatórios")
    with connection() as db:
        if is_default:
            db.execute("UPDATE clients SET is_default = 0")
        db.execute(
            """
            INSERT INTO clients (id, name, url, locale, resend_api_key, from_email, reply_to, is_default)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                name.strip(),
                url.strip(),
                locale or "pt-BR",
                (resend_api_key or "").strip(),
                (from_email or "").strip(),
                (reply_to or "").strip(),
                1 if is_default else 0,
            ),
        )
        db.commit()
    return get_client(client_id)


def update_client(client_id, **fields):
    allowed = {"name", "url", "locale", "resend_api_key", "from_email", "reply_to", "is_default"}
    sets = []
    args = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "is_default":
            v = 1 if v else 0
        else:
            v = (v or "").strip()
        sets.append(f"{k} = ?")
        args.append(v)
    if not sets:
        return get_client(client_id)
    with connection() as db:
        if fields.get("is_default"):
            db.execute("UPDATE clients SET is_default = 0")
        args.append(client_id)
        db.execute(f"UPDATE clients SET {', '.join(sets)} WHERE id = ?", args)
        db.commit()
    return get_client(client_id)


def delete_client(client_id):
    with connection() as db:
        db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        # Mantém leads/settings/suppressions órfãos (não derruba o banco; o usuário
        # pode recriar o cliente com o mesmo id e recupera tudo).
        db.commit()


def seed_defaults():
    """Cadastra UpStat + MartinsAdviser se não existirem. Idempotente."""
    existing = {c["id"] for c in list_clients()}
    for seed in SEEDS:
        if seed["id"] in existing:
            continue
        with connection() as db:
            db.execute(
                """
                INSERT INTO clients (id, name, url, locale, is_default)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    seed["id"],
                    seed["name"],
                    seed["url"],
                    seed["locale"],
                    seed.get("is_default", 0),
                ),
            )
            db.commit()


def ensure_legacy_client():
    """Garante que o cliente legado 'upstat' existe (necessário pós-migração)."""
    if not get_client(LEGACY_CLIENT_ID):
        with connection() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO clients (id, name, url, locale, is_default)
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    LEGACY_CLIENT_ID,
                    "UpStat",
                    "https://upstat.online/?utm_source=outreach&utm_medium=email",
                    "pt-BR",
                ),
            )
            db.commit()
