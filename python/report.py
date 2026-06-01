"""Relatório do run do pipeline — manda um email consolidado (todos os clientes)
com quem recebeu o 1º email, falhas, e contadores da janela.

Decoplado do pipeline: lê o estado final do banco filtrando pelos timestamps
(sentAt / lastErrorAt / approvedAt / personalizedAt) >= `since`. Pensado pra rodar
ao fim do deploy/pipeline.sh, depois que todos os clientes processaram.
"""

import os
from datetime import datetime, timedelta, timezone
from html import escape

import resend

import clients as clients_mod
from sender import _creds
from state import load


def _parse(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return None


def _within(iso, since_dt):
    dt = _parse(iso)
    return dt is not None and dt >= since_dt


def collect(since_dt):
    """Monta a estrutura do relatório pra cada cliente cadastrado."""
    out = []
    for client in clients_mod.list_clients():
        leads = load(client["id"])
        sent, failed = [], []
        personalized = approved = 0
        for l in leads:
            if _within(l.get("personalizedAt"), since_dt):
                personalized += 1
            if _within(l.get("approvedAt"), since_dt):
                approved += 1
            if _within(l.get("sentAt"), since_dt):
                sent.append(l)
            elif _within(l.get("lastErrorAt"), since_dt):
                failed.append(l)
        sent.sort(key=lambda l: l.get("sentAt") or "")
        out.append(
            {
                "client": client,
                "sent": sent,
                "failed": failed,
                "personalized": personalized,
                "approved": approved,
            }
        )
    return out


def _fmt_dt(iso):
    dt = _parse(iso)
    return dt.strftime("%d/%m %H:%M") if dt else "—"


def build(since_dt, blocks):
    total_sent = sum(len(b["sent"]) for b in blocks)
    total_fail = sum(len(b["failed"]) for b in blocks)
    when = since_dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    subject = f"[Outreach] Relatório do run — {total_sent} enviado(s)" + (
        f", {total_fail} falha(s)" if total_fail else ""
    )

    # ---- texto plano
    tl = [f"Relatório do pipeline · janela desde {when}", ""]
    for b in blocks:
        c = b["client"]
        tl.append(f"== {c['name']} ({c['id']}) ==")
        tl.append(
            f"  personalizados: {b['personalized']} · auto-aprovados: {b['approved']} · "
            f"enviados: {len(b['sent'])} · falhas: {len(b['failed'])}"
        )
        if b["sent"]:
            tl.append("  enviados para:")
            for l in b["sent"]:
                tl.append(
                    f"    • {l.get('name') or '—'} <{l.get('email')}>"
                    f"  [{_fmt_dt(l.get('sentAt'))}]  {l.get('searchedAs') or ''}".rstrip()
                )
        if b["failed"]:
            tl.append("  falhas:")
            for l in b["failed"]:
                tl.append(
                    f"    ✗ {l.get('name') or '—'} <{l.get('email')}>: {l.get('lastError') or '?'}"
                )
        tl.append("")
    tl.append(f"TOTAL: {total_sent} enviado(s), {total_fail} falha(s).")
    text = "\n".join(tl)

    # ---- html
    hl = [
        '<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:680px;color:#111">',
        f'<h2 style="margin:0 0 4px">Relatório do pipeline</h2>',
        f'<p style="color:#666;margin:0 0 16px;font-size:13px">janela desde {escape(when)}</p>',
        f'<p style="font-size:15px"><strong>{total_sent}</strong> email(s) enviado(s)'
        + (f', <strong style="color:#b00">{total_fail}</strong> falha(s)' if total_fail else "")
        + "</p>",
    ]
    for b in blocks:
        c = b["client"]
        hl.append(
            f'<h3 style="margin:18px 0 6px;border-bottom:1px solid #eee;padding-bottom:4px">'
            f'{escape(c["name"])} <span style="color:#999;font-weight:400">({escape(c["id"])})</span></h3>'
        )
        hl.append(
            f'<p style="font-size:13px;color:#555;margin:4px 0">personalizados: {b["personalized"]} · '
            f'auto-aprovados: {b["approved"]} · enviados: {len(b["sent"])} · falhas: {len(b["failed"])}</p>'
        )
        if b["sent"]:
            hl.append('<table style="border-collapse:collapse;width:100%;font-size:13px">')
            hl.append(
                '<tr style="text-align:left;color:#888">'
                "<th style=\"padding:4px 8px 4px 0\">empresa</th>"
                "<th style=\"padding:4px 8px\">email</th>"
                "<th style=\"padding:4px 8px\">quando</th>"
                "<th style=\"padding:4px 0\">busca</th></tr>"
            )
            for l in b["sent"]:
                hl.append(
                    "<tr>"
                    f'<td style="padding:4px 8px 4px 0;border-top:1px solid #f0f0f0">{escape(l.get("name") or "—")}</td>'
                    f'<td style="padding:4px 8px;border-top:1px solid #f0f0f0">{escape(l.get("email") or "")}</td>'
                    f'<td style="padding:4px 8px;border-top:1px solid #f0f0f0;color:#888">{escape(_fmt_dt(l.get("sentAt")))}</td>'
                    f'<td style="padding:4px 0;border-top:1px solid #f0f0f0;color:#888">{escape(l.get("searchedAs") or "")}</td>'
                    "</tr>"
                )
            hl.append("</table>")
        if b["failed"]:
            hl.append('<p style="font-size:13px;color:#b00;margin:8px 0 0">falhas:</p><ul style="font-size:13px;color:#b00;margin:4px 0">')
            for l in b["failed"]:
                hl.append(
                    f'<li>{escape(l.get("name") or "—")} &lt;{escape(l.get("email") or "")}&gt;: '
                    f'{escape(str(l.get("lastError") or "?"))}</li>'
                )
            hl.append("</ul>")
        if not b["sent"] and not b["failed"]:
            hl.append('<p style="font-size:13px;color:#999">nenhum envio nesta janela.</p>')
    hl.append("</div>")
    html = "\n".join(hl)

    return subject, text, html


def send_report(to, subject, text, html, sender_client=None):
    """Envia o relatório via Resend usando as credenciais do cliente default
    (ou de `sender_client`). `to` pode ser string ou lista de emails."""
    client = sender_client or clients_mod.default_client()
    if not client:
        raise RuntimeError("nenhum cliente cadastrado pra enviar o relatório")
    api_key, from_email, reply_to = _creds(client)
    resend.api_key = api_key
    recipients = to if isinstance(to, list) else [a.strip() for a in str(to).split(",") if a.strip()]
    params = {"from": from_email, "to": recipients, "subject": subject, "text": text, "html": html}
    if reply_to:
        params["reply_to"] = reply_to
    return resend.Emails.send(params)


def run(to, since=None, hours=24, on_progress=print):
    """Coleta o relatório da janela e envia pro(s) destinatário(s)."""
    since_dt = _parse(since) if since else None
    if since_dt is None:
        since_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    blocks = collect(since_dt)
    subject, text, html = build(since_dt, blocks)
    data = send_report(to, subject, text, html)
    total = sum(len(b["sent"]) for b in blocks)
    on_progress(f"Relatório enviado pra {to} ({total} envios na janela). id={data.get('id', '?')}")
    return data
