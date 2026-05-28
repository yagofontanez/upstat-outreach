"""Webhooks do Resend, com roteamento por cliente.

Ao enviar (sender.py), carimbamos cada email com tag {"name":"client_id", ...}.
O webhook lê essa tag pra saber em qual cliente o evento se aplica. Pra emails
enviados antes da migração multi-cliente (sem tag), faz fallback olhando o
resendId em todos os clientes.
"""

import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone

from state import add_suppression, find_lead_by_resend_id, load, save


def _now():
    return datetime.now(timezone.utc).isoformat()


def verify_signature(headers, raw_body):
    secret = os.environ.get("RESEND_WEBHOOK_SECRET")
    if not secret:
        return True

    svix_id = headers.get("svix-id")
    svix_timestamp = headers.get("svix-timestamp")
    svix_signature = headers.get("svix-signature")
    if not (svix_id and svix_timestamp and svix_signature):
        return False

    try:
        key = base64.b64decode(secret.split("_", 1)[1] if "_" in secret else secret)
    except Exception:
        return False

    body = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body
    signed = f"{svix_id}.{svix_timestamp}.{body}".encode("utf-8")
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()

    for part in svix_signature.split():
        _, _, sig = part.partition(",")
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False


def _client_id_from_tags(data):
    tags = data.get("tags")
    if not tags:
        return None
    # Resend devolve tags como lista de {name, value} ou dict {name: value}.
    if isinstance(tags, dict):
        return tags.get("client_id")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, dict) and t.get("name") == "client_id":
                return t.get("value")
    return None


def process_event(payload):
    """Aplica um evento de webhook do Resend. Retorna um resumo curto pra log."""
    event_type = payload.get("type", "")
    data = payload.get("data") or {}
    email_id = data.get("email_id") or data.get("id")
    to = data.get("to")
    if isinstance(to, list):
        to = to[0] if to else None

    # 1) Tenta achar o cliente via tag do Resend.
    client_id = _client_id_from_tags(data)
    lead = None

    if client_id:
        # Cliente conhecido: carrega leads dele e procura por resend_id.
        try:
            leads = load(client_id)
        except Exception:
            leads = []
        lead = next(
            (
                l
                for l in leads
                if l.get("resendId") == email_id or l.get("followupResendId") == email_id
            ),
            None,
        )
    else:
        # 2) Fallback: busca o resend_id em todos os clientes.
        client_id, lead = find_lead_by_resend_id(email_id)
        if client_id and lead:
            # carrega o conjunto pra poder salvar a alteração depois
            leads = load(client_id)
            lead = next(
                (
                    l
                    for l in leads
                    if l.get("resendId") == email_id
                    or l.get("followupResendId") == email_id
                ),
                None,
            )
        else:
            leads = []

    changed = False
    if lead:
        if event_type == "email.delivered":
            lead["deliveredAt"] = lead.get("deliveredAt") or _now()
            changed = True
        elif event_type == "email.opened":
            lead["openedAt"] = lead.get("openedAt") or _now()
            lead["openCount"] = (lead.get("openCount") or 0) + 1
            changed = True
        elif event_type == "email.clicked":
            lead["clickedAt"] = lead.get("clickedAt") or _now()
            lead["clickCount"] = (lead.get("clickCount") or 0) + 1
            changed = True
        elif event_type == "email.bounced":
            lead["bouncedAt"] = _now()
            lead["bounceType"] = (data.get("bounce") or {}).get("type") or "bounce"
            changed = True
        elif event_type == "email.complained":
            lead["complainedAt"] = _now()
            changed = True

    if changed and client_id:
        save(client_id, leads)

    target_email = (lead and lead.get("email")) or to
    if client_id and target_email:
        if event_type == "email.bounced":
            add_suppression(client_id, target_email, reason="bounce")
        elif event_type == "email.complained":
            add_suppression(client_id, target_email, reason="complaint")

    matched = "lead atualizado" if lead else "sem lead casado"
    return f"{event_type} · client={client_id or '?'} · {target_email or '?'} · {matched}"
