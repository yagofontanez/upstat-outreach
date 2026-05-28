"""Montagem de email + templates editáveis, por cliente.

Cada cliente tem seu próprio template (subject + body) e follow-up, guardados
em settings/key, namespaced por client_id. Os defaults vivem aqui em
DEFAULTS_BY_CLIENT e dependem do locale (pt-BR / en-US).
"""

import re

from state import get_setting, set_setting

DEFAULTS_BY_CLIENT = {
    "upstat": {
        "template": {
            "subject": "monitoramento de uptime pra {{company}}",
            "body": """{{opening}}

Sou o Yago, fundador do UpStat. Um SaaS de monitoramento de uptime feito pensando em agências e empresas pequenas que não querem pagar caro nem configurar Datadog pra monitorar 3 sites.

Imaginei que talvez vocês já tenham passado pela cena clássica: cliente avisando no WhatsApp que o site caiu antes da gente perceber. O UpStat resolve isso, alerta no e-mail/Discord/WhatsApp em segundos quando algo cai ou fica lento, com página de status pública que você pode mostrar pro cliente.

Plano grátis cobre uns primeiros sites, sem cartão. Dá uma olhada aqui: {{url}}

Abraço,
Yago

---
Você recebeu este email porque sua empresa apareceu numa busca pública por agências/empresas. Se preferir não receber mais nada, escreva pra {{replyTo}} com "remover" no assunto.""",
        },
        "followup": {
            "subject": "re: monitoramento de uptime pra {{company}}",
            "body": """{{opening}}

Só subindo esse email pra não passar despercebido — sei como a caixa de entrada lota.

Resumindo em uma linha: o UpStat avisa em segundos quando o site de vocês (ou de um cliente) cai ou fica lento, com página de status pública pra mostrar pro cliente. Plano grátis, sem cartão: {{url}}

Se não fizer sentido, sem problema — é só ignorar.

Abraço,
Yago

---
Não quer mais receber? {{unsubscribeUrl}}""",
            "delay_days": 4,
        },
    },
    "martinsadviser": {
        "template": {
            "subject": "{{company}} — CRM built for trucking & permits",
            "body": """{{opening}}

I'm Yago, founder of MartinsAdviser — a CRM built specifically for US trucking and permit companies. Clients, trucks, permits and compliance live in one place, with an integrated Kanban and an AI copilot that handles the busywork.

If juggling permits, expirations and dispatch from spreadsheets is getting in the way, this could save your team a few hours a week. Free to try, no card required: {{url}}

Best,
Yago

---
You're getting this because your company came up in a public search. Reply with "remove" in the subject line if you'd rather not hear from us.""",
        },
        "followup": {
            "subject": "re: CRM built for trucking & permits — {{company}}",
            "body": """{{opening}}

Just bumping this so it doesn't get buried — I know how the inbox piles up.

One-liner: MartinsAdviser keeps your trucks, permits, clients and compliance organized in one place, with an AI copilot that watches for expirations and next steps. Free to try: {{url}}

If it's not a fit, no worries — just ignore.

Best,
Yago

---
Don't want to hear from us? {{unsubscribeUrl}}""",
            "delay_days": 4,
        },
    },
}


def _defaults_for(client):
    """Pega os defaults pelo id do cliente; cai num genérico se não tiver seed."""
    if not client:
        return DEFAULTS_BY_CLIENT["upstat"]
    return DEFAULTS_BY_CLIENT.get(client.get("id"), DEFAULTS_BY_CLIENT["upstat"])


def _clean_company_name(name):
    name = name or "team"
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


def get_email_template(client):
    defaults = _defaults_for(client)["template"]
    return {
        "subject": get_setting(client["id"], "email_template_subject", defaults["subject"]),
        "body": get_setting(client["id"], "email_template_body", defaults["body"]),
    }


def get_followup_template(client):
    defaults = _defaults_for(client)["followup"]
    return {
        "subject": get_setting(client["id"], "followup_subject", defaults["subject"]),
        "body": get_setting(client["id"], "followup_body", defaults["body"]),
        "delay_days": int(
            get_setting(client["id"], "followup_delay_days", defaults["delay_days"])
        ),
    }


def save_followup_template(client, subject, body, delay_days):
    clean_subject = str(subject or "").strip()
    clean_body = str(body or "").strip()
    if not clean_subject:
        raise ValueError("subject do follow-up não pode ficar vazio")
    if not clean_body:
        raise ValueError("body do follow-up não pode ficar vazio")
    try:
        days = int(delay_days)
    except (TypeError, ValueError):
        raise ValueError("delay_days precisa ser um número")
    if days < 1:
        raise ValueError("delay_days precisa ser >= 1")
    set_setting(client["id"], "followup_subject", clean_subject)
    set_setting(client["id"], "followup_body", clean_body)
    set_setting(client["id"], "followup_delay_days", days)
    return get_followup_template(client)


def save_email_template(client, subject, body):
    clean_subject = str(subject or "").strip()
    clean_body = str(body or "").strip()
    if not clean_subject:
        raise ValueError("subject não pode ficar vazio")
    if not clean_body:
        raise ValueError("body não pode ficar vazio")
    set_setting(client["id"], "email_template_subject", clean_subject)
    set_setting(client["id"], "email_template_body", clean_body)
    return get_email_template(client)


def build_subject(client, name):
    clean_name = _clean_company_name(name)
    return _render_template(
        get_email_template(client)["subject"],
        {"company": clean_name, "name": clean_name},
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


def _opening_for_locale(locale, clean_name, hook):
    """Saudação de abertura no idioma certo."""
    if (locale or "").lower().startswith("en"):
        return (
            f"Hi {clean_name} team — {hook}" if hook else f"Hi {clean_name} team,"
        )
    return (
        f"Oi, time da {clean_name}! {hook}" if hook else f"Oi, time da {clean_name}!"
    )


def build_email(client, name, reply_to, personalized_hook=None, site_insights=None, unsubscribe_url=""):
    return build_email_with_template(
        client,
        get_email_template(client),
        name=name,
        reply_to=reply_to,
        personalized_hook=personalized_hook,
        site_insights=site_insights,
        unsubscribe_url=unsubscribe_url,
    )


def build_email_with_template(
    client, template, name, reply_to, personalized_hook=None, site_insights=None, unsubscribe_url=""
):
    clean_name = _clean_company_name(name)
    subject = _render_template(
        template["subject"], {"company": clean_name, "name": clean_name}
    )

    hook = (personalized_hook or "").strip()
    opening = _opening_for_locale(client.get("locale"), clean_name, hook)

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
            "url": client.get("url") or "",
            "unsubscribeUrl": unsubscribe_url or "",
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
