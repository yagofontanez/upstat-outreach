"""Geração de hook personalizado via Groq — equivalente a lib/personalize.js."""

import json
import os
import re
from datetime import datetime, timezone

import httpx
from groq import Groq

from progress import console_progress

MODEL = "llama-3.3-70b-versatile"
FETCH_TIMEOUT_S = 8.0
MAX_SIGNAL_CHARS = 1800

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
}

SYSTEM = """Você ajuda a personalizar emails de cold outreach em português brasileiro.

Receberá: o nome de uma empresa, trechos do site oficial dela e, quando existir, uma análise técnica simples do site.

Sua tarefa: gerar um JSON com um campo:
- "hook": 1 frase (máx 220 caracteres) de abertura natural que referencia ESPECIFICAMENTE o que a empresa faz com base nos trechos fornecidos. Tom casual, direto, em primeira pessoa ("vi que vocês..."). Sem elogio genérico ("site bonito"), sem inventar fatos não presentes nos trechos, sem prêmios/clientes não citados.

Regras importantes:
- Se os trechos forem vagos ou insuficientes, escreva um hook neutro mas honesto baseado só no nicho aparente (não invente nada).
- Se houver análise técnica, use apenas como contexto secundário. Prefira mencionar serviços reais da empresa; só mencione stack/dor se isso estiver claro e não soar acusatório.
- Nunca mencione monitoramento, uptime, SaaS, UpStat ou qualquer produto — isso já está no corpo do email. O hook é só a abertura.
- Responda APENAS com JSON válido, sem markdown, sem comentários. Exemplo: {"hook":"..."}"""


def fetch_html(url):
    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT_S, follow_redirects=True, headers=_HEADERS
        ) as client:
            res = client.get(url)
        if not res.is_success:
            return ""
        ct = res.headers.get("content-type", "")
        if "text" not in ct and "html" not in ct:
            return ""
        return res.text
    except Exception:
        return ""


def strip_tags(s):
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"&amp;", "&", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _first(pattern, html, flags=re.IGNORECASE):
    m = re.search(pattern, html, flags)
    return m.group(1) if m else ""


def extract_signals(html):
    title = _first(r"<title[^>]*>([\s\S]*?)</title>", html)
    meta_desc = _first(
        r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)[\"']", html
    ) or _first(
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']description[\"']", html
    )
    og_desc = _first(
        r"<meta[^>]+property=[\"']og:description[\"'][^>]+content=[\"']([^\"']+)[\"']",
        html,
    )
    h1 = _first(r"<h1[^>]*>([\s\S]*?)</h1>", html)
    h2s = [m.group(1) for m in re.finditer(r"<h2[^>]*>([\s\S]*?)</h2>", html, re.IGNORECASE)][:3]
    ps = [m.group(1) for m in re.finditer(r"<p[^>]*>([\s\S]*?)</p>", html, re.IGNORECASE)][:5]

    parts = []
    if title:
        parts.append(f"TITLE: {strip_tags(title)}")
    if meta_desc:
        parts.append(f"DESC: {strip_tags(meta_desc)}")
    if og_desc and meta_desc != og_desc:
        parts.append(f"OG: {strip_tags(og_desc)}")
    if h1:
        parts.append(f"H1: {strip_tags(h1)}")
    if h2s:
        parts.append("H2: " + " | ".join(filter(None, (strip_tags(x) for x in h2s))))
    if ps:
        parts.append("P: " + " · ".join(filter(None, (strip_tags(x) for x in ps))))

    return "\n".join(parts)[:MAX_SIGNAL_CHARS]


def format_insights(insights):
    if not insights:
        return ""
    stack = (
        f"STACK: {', '.join(insights['techStack'])}"
        if insights.get("techStack")
        else ""
    )
    pains = ""
    if insights.get("painSignals"):
        pains = "SINAIS: " + " | ".join(
            f"{s['label']} ({s['detail']})" for s in insights["painSignals"][:4]
        )
    pages = ""
    if insights.get("pages"):
        ok_pages = [p["path"] for p in insights["pages"] if p.get("ok")][:5]
        if ok_pages:
            pages = "PÁGINAS: " + ", ".join(ok_pages)
    return "\n".join(filter(None, [stack, pains, pages]))


def build_user_prompt(name, signals, insights):
    tech_signals = format_insights(insights)
    return f"""Empresa: {name}

Trechos do site:
{signals or "(site não retornou conteúdo útil)"}

Análise técnica:
{tech_signals or "(sem análise técnica salva)"}

Gere o JSON."""


def sanitize(s, max_len):
    s = str(s or "")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"^[\"'`\s]+|[\"'`\s]+$", "", s)
    return s[:max_len].strip()


def parse_response(content):
    if not content:
        return None
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        hook = sanitize(obj.get("hook"), 260)
        if not hook:
            return None
        return {"hook": hook}
    except Exception:
        return None


_client = None


def get_client():
    global _client
    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY ausente no .env")
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


def personalize_lead(lead):
    groq = get_client()
    signals = ""
    if lead.get("website"):
        html = fetch_html(lead["website"])
        if html:
            signals = extract_signals(html)

    completion = groq.chat.completions.create(
        model=MODEL,
        temperature=0.7,
        max_tokens=300,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": build_user_prompt(
                    lead.get("name"), signals, lead.get("siteInsights")
                ),
            },
        ],
    )

    content = ""
    if completion.choices:
        content = completion.choices[0].message.content or ""
    parsed = parse_response(content)
    if not parsed:
        raise RuntimeError("resposta inválida do Groq")
    return parsed


def personalize_leads(leads, force=False, on_progress=console_progress):
    targets = [l for l in leads if force or not l.get("personalizedHook")]
    if not targets:
        on_progress({"type": "done", "message": "Nada pra personalizar."})
        return {"ok": 0, "fail": 0}

    on_progress(
        {
            "type": "log",
            "message": f"Personalizando {len(targets)} leads via Groq ({MODEL})…",
        }
    )

    ok = 0
    fail = 0
    for i, lead in enumerate(targets):
        try:
            hook = personalize_lead(lead)["hook"]
            lead["personalizedHook"] = hook
            lead["personalizedAt"] = datetime.now(timezone.utc).isoformat()
            ok += 1
            on_progress(
                {
                    "type": "item",
                    "index": i + 1,
                    "total": len(targets),
                    "name": lead.get("name"),
                    "status": f'ok · "{hook[:40]}…"',
                }
            )
        except Exception as e:
            fail += 1
            on_progress(
                {
                    "type": "item",
                    "index": i + 1,
                    "total": len(targets),
                    "name": lead.get("name"),
                    "status": f"falhou: {e}",
                }
            )
    on_progress(
        {
            "type": "done",
            "message": f"Personalização: {ok} ok, {fail} falhas.",
            "ok": ok,
            "fail": fail,
        }
    )
    return {"ok": ok, "fail": fail}
