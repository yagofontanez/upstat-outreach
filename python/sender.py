"""Envio via Resend — equivalente a lib/sender.js."""

import os
import time
from datetime import datetime, timezone

import resend

from mailtemplate import build_email
from progress import console_progress
from state import load, save

DELAY_S = 6.0


def send(limit=None, test_email=None, on_progress=console_progress):
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("FROM_EMAIL")
    reply_to = os.environ.get("REPLY_TO")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY ausente no .env")
    if not from_email:
        raise RuntimeError("FROM_EMAIL ausente no .env")

    resend.api_key = api_key
    leads = load()

    def send_one(to, name, personalized_hook, site_insights):
        email = build_email(
            name=name,
            reply_to=reply_to or from_email,
            personalized_hook=personalized_hook,
            site_insights=site_insights,
        )
        params = {
            "from": from_email,
            "to": to,
            "subject": email["subject"],
            "text": email["text"],
            "html": email["html"],
        }
        if reply_to:
            params["reply_to"] = reply_to
        return resend.Emails.send(params)

    if test_email:
        sample = next((l for l in leads if l.get("status") == "approved"), None) or (
            leads[0] if leads else None
        )
        sample_name = (sample or {}).get("name") or "Empresa Teste"
        on_progress(
            {
                "type": "log",
                "message": f'[TESTE] Enviando 1 email pra {test_email} (nome: "{sample_name}")',
            }
        )
        try:
            data = send_one(
                test_email,
                sample_name,
                (sample or {}).get("personalizedHook"),
                (sample or {}).get("siteInsights"),
            )
            on_progress(
                {
                    "type": "item",
                    "index": 1,
                    "total": 1,
                    "name": test_email,
                    "status": f"ok ({data.get('id', '?')})",
                }
            )
            on_progress(
                {
                    "type": "done",
                    "message": "Nenhum lead foi alterado.",
                    "ok": 1,
                    "fail": 0,
                }
            )
        except Exception as e:
            on_progress(
                {
                    "type": "item",
                    "index": 1,
                    "total": 1,
                    "name": test_email,
                    "status": f"falhou: {e}",
                }
            )
            on_progress({"type": "done", "message": "Teste falhou.", "ok": 0, "fail": 1})
        return

    queue = [
        l
        for l in leads
        if l.get("status") == "approved" and l.get("email") and not l.get("sentAt")
    ]

    if not queue:
        on_progress({"type": "done", "message": "Nada na fila.", "ok": 0, "fail": 0})
        return

    total_approved = len(queue)
    if limit and limit > 0:
        queue = queue[:limit]

    suffix = f" (limit {limit}/{total_approved})" if limit else ""
    on_progress(
        {
            "type": "log",
            "message": (
                f"Enviando para {len(queue)} leads{suffix} "
                f"(delay {int(DELAY_S)}s entre envios)…"
            ),
        }
    )

    ok = 0
    fail = 0
    for i, lead in enumerate(queue):
        try:
            data = send_one(
                lead.get("email"),
                lead.get("name"),
                lead.get("personalizedHook"),
                lead.get("siteInsights"),
            )
            lead["status"] = "sent"
            lead["sentAt"] = datetime.now(timezone.utc).isoformat()
            lead["resendId"] = data.get("id")
            ok += 1
            rid = (data.get("id") or "?")[:8]
            on_progress(
                {
                    "type": "item",
                    "index": i + 1,
                    "total": len(queue),
                    "name": lead.get("email"),
                    "status": f"ok ({rid})",
                }
            )
        except Exception as e:
            lead["lastError"] = str(e)
            fail += 1
            on_progress(
                {
                    "type": "item",
                    "index": i + 1,
                    "total": len(queue),
                    "name": lead.get("email"),
                    "status": f"falhou: {e}",
                }
            )
        save(leads)
        if i < len(queue) - 1:
            time.sleep(DELAY_S)

    on_progress(
        {
            "type": "done",
            "message": f"Fim. Enviados: {ok}, falhas: {fail}.",
            "ok": ok,
            "fail": fail,
        }
    )
