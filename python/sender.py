"""Envio via Resend — equivalente a lib/sender.js, com supressão, unsubscribe e follow-ups."""

import os
import time
from datetime import datetime, timezone

import resend

from mailtemplate import build_email, build_email_with_template, get_followup_template
from progress import console_progress
from state import is_suppressed, load, save
from unsubscribe import unsubscribe_url

DELAY_S = 6.0


def _now():
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso):
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except Exception:
        return 1e9


def _env():
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("FROM_EMAIL")
    reply_to = os.environ.get("REPLY_TO")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY ausente no .env")
    if not from_email:
        raise RuntimeError("FROM_EMAIL ausente no .env")
    resend.api_key = api_key
    return from_email, reply_to


def _send_email(to, from_email, reply_to, email):
    unsub = unsubscribe_url(to)
    params = {
        "from": from_email,
        "to": to,
        "subject": email["subject"],
        "text": email["text"],
        "html": email["html"],
    }
    if reply_to:
        params["reply_to"] = reply_to
    if unsub:
        params["headers"] = {
            "List-Unsubscribe": f"<{unsub}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
    return resend.Emails.send(params)


def send(limit=None, test_email=None, on_progress=console_progress):
    from_email, reply_to = _env()
    leads = load()

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
            email = build_email(
                name=sample_name,
                reply_to=reply_to or from_email,
                personalized_hook=(sample or {}).get("personalizedHook"),
                site_insights=(sample or {}).get("siteInsights"),
                unsubscribe_url=unsubscribe_url(test_email),
            )
            data = _send_email(test_email, from_email, reply_to, email)
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
                {"type": "done", "message": "Nenhum lead foi alterado.", "ok": 1, "fail": 0}
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

    queue = []
    skipped = 0
    for l in leads:
        if l.get("status") == "approved" and l.get("email") and not l.get("sentAt"):
            if is_suppressed(l["email"]):
                skipped += 1
                continue
            queue.append(l)

    if not queue:
        msg = "Nada na fila."
        if skipped:
            msg += f" ({skipped} suprimidos ignorados)"
        on_progress({"type": "done", "message": msg, "ok": 0, "fail": 0})
        return

    total_approved = len(queue)
    if limit and limit > 0:
        queue = queue[:limit]

    suffix = f" (limit {limit}/{total_approved})" if limit else ""
    skip_note = f" · {skipped} suprimidos ignorados" if skipped else ""
    on_progress(
        {
            "type": "log",
            "message": (
                f"Enviando para {len(queue)} leads{suffix} "
                f"(delay {int(DELAY_S)}s entre envios){skip_note}…"
            ),
        }
    )

    ok = 0
    fail = 0
    for i, lead in enumerate(queue):
        try:
            email = build_email(
                name=lead.get("name"),
                reply_to=reply_to or from_email,
                personalized_hook=lead.get("personalizedHook"),
                site_insights=lead.get("siteInsights"),
                unsubscribe_url=unsubscribe_url(lead.get("email")),
            )
            data = _send_email(lead.get("email"), from_email, reply_to, email)
            lead["status"] = "sent"
            lead["sentAt"] = _now()
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


def followup_candidates(leads, template=None):
    """Leads elegíveis para follow-up: enviados, sem resposta/descadastro, fora do prazo."""
    template = template or get_followup_template()
    delay = template["delay_days"]
    out = []
    for l in leads:
        if l.get("status") != "sent":
            continue
        if not l.get("email") or not l.get("sentAt"):
            continue
        if l.get("repliedAt") or l.get("unsubscribedAt"):
            continue
        if l.get("bouncedAt") or l.get("complainedAt"):
            continue
        if l.get("followupStep"):  # já recebeu follow-up
            continue
        if is_suppressed(l["email"]):
            continue
        if _days_since(l["sentAt"]) < delay:
            continue
        out.append(l)
    return out


def send_followups(limit=None, on_progress=console_progress):
    from_email, reply_to = _env()
    template = get_followup_template()
    leads = load()
    queue = followup_candidates(leads, template)

    if not queue:
        on_progress(
            {
                "type": "done",
                "message": f"Nenhum follow-up pendente (prazo {template['delay_days']}d).",
                "ok": 0,
                "fail": 0,
            }
        )
        return

    total = len(queue)
    if limit and limit > 0:
        queue = queue[:limit]

    suffix = f" (limit {limit}/{total})" if limit else ""
    on_progress(
        {
            "type": "log",
            "message": f"Enviando follow-up pra {len(queue)} leads{suffix}…",
        }
    )

    ok = 0
    fail = 0
    for i, lead in enumerate(queue):
        try:
            email = build_email_with_template(
                template,
                name=lead.get("name"),
                reply_to=reply_to or from_email,
                personalized_hook=lead.get("personalizedHook"),
                site_insights=lead.get("siteInsights"),
                unsubscribe_url=unsubscribe_url(lead.get("email")),
            )
            data = _send_email(lead.get("email"), from_email, reply_to, email)
            lead["followupStep"] = 1
            lead["followupAt"] = _now()
            lead["followupResendId"] = data.get("id")
            ok += 1
            rid = (data.get("id") or "?")[:8]
            on_progress(
                {
                    "type": "item",
                    "index": i + 1,
                    "total": len(queue),
                    "name": lead.get("email"),
                    "status": f"follow-up ok ({rid})",
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
            "message": f"Follow-ups: {ok} enviados, {fail} falhas.",
            "ok": ok,
            "fail": fail,
        }
    )
