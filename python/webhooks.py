"""Processamento de webhooks do Resend: engajamento + supressão automática.

O Resend assina os webhooks no padrão Svix (headers svix-id/svix-timestamp/
svix-signature). Se RESEND_WEBHOOK_SECRET estiver no .env, a assinatura é validada;
caso contrário, aceita sem validar (útil em dev) — em produção, configure o segredo.
"""

import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone

from state import add_suppression, load, save


def _now():
    return datetime.now(timezone.utc).isoformat()


def verify_signature(headers, raw_body):
    secret = os.environ.get("RESEND_WEBHOOK_SECRET")
    if not secret:
        return True  # sem segredo configurado: não valida (dev)

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

    # svix-signature é "v1,<sig> v1,<sig2> ..."
    for part in svix_signature.split():
        _, _, sig = part.partition(",")
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False


def _find_by_resend_id(leads, email_id):
    if not email_id:
        return None
    for l in leads:
        if l.get("resendId") == email_id or l.get("followupResendId") == email_id:
            return l
    return None


def process_event(payload):
    """Aplica um evento de webhook do Resend. Retorna um resumo curto pra log."""
    event_type = payload.get("type", "")
    data = payload.get("data") or {}
    email_id = data.get("email_id") or data.get("id")
    to = data.get("to")
    if isinstance(to, list):
        to = to[0] if to else None

    leads = load()
    lead = _find_by_resend_id(leads, email_id)

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

    if changed:
        save(leads)

    # Supressão automática em bounce/complaint, mesmo sem lead casado.
    target_email = (lead and lead.get("email")) or to
    if event_type == "email.bounced":
        add_suppression(target_email, reason="bounce")
    elif event_type == "email.complained":
        add_suppression(target_email, reason="complaint")

    matched = "lead atualizado" if lead else "sem lead casado"
    return f"{event_type} · {target_email or '?'} · {matched}"
