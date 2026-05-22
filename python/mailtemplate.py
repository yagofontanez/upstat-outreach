"""Montagem de email + template editável — equivalente a lib/template.js."""

import re

from state import get_setting, set_setting

UPSTAT_URL = "https://upstat.online/?utm_source=outreach&utm_medium=email"

DEFAULT_TEMPLATE = {
    "subject": "monitoramento de uptime pra {{company}}",
    "body": """{{opening}}

Sou o Yago, fundador do UpStat. Um SaaS de monitoramento de uptime feito pensando em agências e empresas pequenas que não querem pagar caro nem configurar Datadog pra monitorar 3 sites.

Imaginei que talvez vocês já tenham passado pela cena clássica: cliente avisando no WhatsApp que o site caiu antes da gente perceber. O UpStat resolve isso, alerta no e-mail/Discord/WhatsApp em segundos quando algo cai ou fica lento, com página de status pública que você pode mostrar pro cliente.

Plano grátis cobre uns primeiros sites, sem cartão. Dá uma olhada aqui: {{url}}

Abraço,
Yago

---
Você recebeu este email porque sua empresa apareceu numa busca pública por agências/empresas. Se preferir não receber mais nada, escreva pra {{replyTo}} com "remover" no assunto.""",
}


def _clean_company_name(name):
    name = name or "time"
    name = re.sub(r"[®™©]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def _render_template(template, variables):
    out = str(template or "")
    for key, value in variables.items():
        out = re.sub(
            r"\{\{\s*" + re.escape(key) + r"\s*\}\}",
            lambda _m, v=value: v if v is not None else "",
            out,
        )
    return out


_HTML_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
}


def _escape_html(s):
    return re.sub(r"[&<>\"']", lambda c: _HTML_ESCAPES[c.group(0)], str(s))


def get_email_template():
    return {
        "subject": get_setting("email_template_subject", DEFAULT_TEMPLATE["subject"]),
        "body": get_setting("email_template_body", DEFAULT_TEMPLATE["body"]),
    }


def save_email_template(subject, body):
    clean_subject = str(subject or "").strip()
    clean_body = str(body or "").strip()
    if not clean_subject:
        raise ValueError("subject não pode ficar vazio")
    if not clean_body:
        raise ValueError("body não pode ficar vazio")
    set_setting("email_template_subject", clean_subject)
    set_setting("email_template_body", clean_body)
    return get_email_template()


def build_subject(name):
    clean_name = _clean_company_name(name)
    return _render_template(
        get_email_template()["subject"], {"company": clean_name, "name": clean_name}
    )


def _format_stack(site_insights):
    if not site_insights:
        return ""
    return ", ".join(site_insights.get("techStack") or [])


def _format_pain_signals(site_insights):
    if not site_insights:
        return ""
    signals = site_insights.get("painSignals") or []
    return ", ".join(s["label"] for s in signals[:3])


def build_email(name, reply_to, personalized_hook=None, site_insights=None):
    return build_email_with_template(
        get_email_template(),
        name=name,
        reply_to=reply_to,
        personalized_hook=personalized_hook,
        site_insights=site_insights,
    )


def build_email_with_template(
    template, name, reply_to, personalized_hook=None, site_insights=None
):
    clean_name = _clean_company_name(name)
    subject = _render_template(
        template["subject"], {"company": clean_name, "name": clean_name}
    )

    hook = (personalized_hook or "").strip()
    opening = (
        f"Oi, time da {clean_name}! {hook}" if hook else f"Oi, time da {clean_name}!"
    )

    text = _render_template(
        template["body"],
        {
            "company": clean_name,
            "name": clean_name,
            "hook": hook,
            "opening": opening,
            "replyTo": reply_to,
            "stack": _format_stack(site_insights),
            "painSignals": _format_pain_signals(site_insights),
            "url": UPSTAT_URL,
        },
    )

    paragraphs = []
    for p in text.split("\n\n"):
        linked = re.sub(
            r"(https?://[^\s)]+)",
            r'<a href="\1" style="color:#2563eb;text-decoration:underline;">\1</a>',
            _escape_html(p),
        )
        linked = linked.replace("\n", "<br/>")
        paragraphs.append(
            f'<p style="margin:0 0 12px 0;line-height:1.5;">{linked}</p>'
        )

    html = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'font-size:15px;line-height:1.5;max-width:560px;">'
        + "".join(paragraphs)
        + "</div>"
    )

    return {"subject": subject, "text": text, "html": html}
