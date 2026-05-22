"""Análise de sites (tech stack + sinais de dor) — equivalente a lib/site-insights.js.

Usa httpx para o fetch com timeout. A lógica de detecção por regex é idêntica
ao original em JS.
"""

import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx

FETCH_TIMEOUT_S = 9.0
MAX_HTML_CHARS = 900000

CRAWL_PATHS = [
    "",
    "/servicos",
    "/serviços",
    "/portfolio",
    "/portifolio",
    "/clientes",
    "/cases",
    "/manutencao",
    "/manutenção",
    "/sobre",
]

STACK_RULES = [
    {"name": "WordPress", "patterns": [r"wp-content", r"wp-includes", r"<meta[^>]+generator[^>]+wordpress"]},
    {"name": "Elementor", "patterns": [r"elementor"]},
    {"name": "WooCommerce", "patterns": [r"woocommerce", r"wc-ajax"]},
    {"name": "Wix", "patterns": [r"wixstatic", r"wix-code", r"wix\.com"]},
    {"name": "Shopify", "patterns": [r"cdn\.shopify", r"Shopify\.theme", r"myshopify"]},
    {"name": "Webflow", "patterns": [r"webflow\.js", r"data-wf-page", r"webflow\.com"]},
    {"name": "Loja Integrada", "patterns": [r"lojaintegrada", r"cdn\.awsli\.com\.br"]},
    {"name": "Nuvemshop", "patterns": [r"nuvemshop", r"tiendanube", r"cdn\.nuvemshop"]},
    {"name": "React", "patterns": [r"react", r"__REACT_DEVTOOLS_GLOBAL_HOOK__"]},
    {"name": "Next.js", "patterns": [r"_next/static", r"__NEXT_DATA__"]},
    {"name": "Vercel", "patterns": [r"x-vercel-id", r"vercel"]},
    {"name": "Cloudflare", "patterns": [r"cloudflare", r"cf-ray"]},
]

# Compila os padrões uma vez (case-insensitive, como os /i do JS).
for rule in STACK_RULES:
    rule["compiled"] = [re.compile(p, re.IGNORECASE) for p in rule["patterns"]]


def normalize_website(website):
    if not website:
        return None
    parsed = urlparse(website)
    if parsed.scheme and parsed.hostname:
        return parsed
    parsed = urlparse(f"https://{website}")
    if parsed.scheme and parsed.hostname:
        return parsed
    return None


def byte_length(s):
    return len(str(s or "").encode("utf-8"))


def strip_tags(s):
    s = str(s or "")
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"&amp;", "&", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def title_of(html):
    m = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.IGNORECASE)
    return strip_tags(m.group(1) if m else "")


def header_blob(headers):
    return "\n".join(f"{k}: {v}" for k, v in headers.items())


def fetch_page(url):
    started = time.time()
    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT_S,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
            },
        ) as client:
            res = client.get(url)
        response_ms = int((time.time() - started) * 1000)
        content_type = res.headers.get("content-type", "")
        is_html = "text/html" in content_type or "text/" in content_type
        html = res.text[:MAX_HTML_CHARS] if is_html else ""
        return {
            "ok": res.is_success,
            "url": url,
            "finalUrl": str(res.url),
            "redirected": len(res.history) > 0,
            "status": res.status_code,
            "responseMs": response_ms,
            "bytes": byte_length(html),
            "title": title_of(html),
            "contentType": content_type,
            "headers": header_blob(res.headers),
            "html": html,
            "error": None,
        }
    except Exception as err:
        return {
            "ok": False,
            "url": url,
            "finalUrl": url,
            "redirected": False,
            "status": 0,
            "responseMs": int((time.time() - started) * 1000),
            "bytes": 0,
            "title": "",
            "contentType": "",
            "headers": "",
            "html": "",
            "error": str(err),
        }


def detect_stack(pages):
    haystack = "\n".join(f"{p['headers']}\n{p['html']}" for p in pages)
    haystack = haystack[: MAX_HTML_CHARS * 2]
    return [
        rule["name"]
        for rule in STACK_RULES
        if any(pat.search(haystack) for pat in rule["compiled"])
    ]


def has_status_page(pages):
    for p in pages:
        combined = f"{p['finalUrl']}\n{p['html']}".lower()
        if (
            "/status" in combined
            or "status page" in combined
            or "página de status" in combined
            or "statuspage" in combined
        ):
            return True
    return False


def build_pain_signals(base_url, pages):
    signals = []
    home = pages[0] if pages else None
    successful = [p for p in pages if p["ok"]]

    if base_url.scheme != "https":
        signals.append(
            {
                "key": "no_https",
                "severity": "high",
                "label": "Site inicial não usa HTTPS",
                "detail": "A URL coletada começa com HTTP.",
            }
        )

    if not (home and home["ok"]):
        detail = (home and home.get("error")) or (
            f"status {(home and home.get('status')) or 'sem resposta'}"
        )
        signals.append(
            {
                "key": "home_unavailable",
                "severity": "high",
                "label": "Home indisponível",
                "detail": detail,
            }
        )

    for page in pages:
        if page["status"] >= 500:
            signals.append(
                {
                    "key": "server_error",
                    "severity": "high",
                    "label": "Erro 5xx encontrado",
                    "detail": f"{page['status']} em {page['url']}",
                }
            )
        elif page["status"] >= 400:
            signals.append(
                {
                    "key": "client_error",
                    "severity": "medium",
                    "label": "Página relevante quebrada",
                    "detail": f"{page['status']} em {page['url']}",
                }
            )

    if home and home["responseMs"] > 5000:
        signals.append(
            {
                "key": "very_slow_home",
                "severity": "high",
                "label": "Home muito lenta",
                "detail": f"{home['responseMs']}ms para responder.",
            }
        )
    elif home and home["responseMs"] > 2500:
        signals.append(
            {
                "key": "slow_home",
                "severity": "medium",
                "label": "Home lenta",
                "detail": f"{home['responseMs']}ms para responder.",
            }
        )

    if home and home["bytes"] > 1200000:
        signals.append(
            {
                "key": "heavy_home",
                "severity": "medium",
                "label": "Home pesada",
                "detail": f"{round(home['bytes'] / 1024)}KB de HTML inicial.",
            }
        )

    if home and home["redirected"]:
        signals.append(
            {
                "key": "redirects",
                "severity": "low",
                "label": "Redirecionamento na home",
                "detail": f"{home['url']} -> {home['finalUrl']}",
            }
        )

    if len(successful) > 0 and not has_status_page(pages):
        signals.append(
            {
                "key": "no_status_page",
                "severity": "low",
                "label": "Sem status page aparente",
                "detail": "Não encontrei link ou rota de status nas páginas analisadas.",
            }
        )

    return signals


def summarize_pages(pages):
    out = []
    for p in pages:
        out.append(
            {
                "path": urlparse(p["url"]).path or "/",
                "url": p["url"],
                "finalUrl": p["finalUrl"],
                "status": p["status"],
                "ok": p["ok"],
                "responseMs": p["responseMs"],
                "bytes": p["bytes"],
                "title": p["title"],
                "redirected": p["redirected"],
                "error": p["error"],
            }
        )
    return out


def analyze_site(website, on_progress=None):
    base = normalize_website(website)
    if not base:
        raise ValueError("website inválido")

    pages = []
    seen = set()
    paths = CRAWL_PATHS[:8]
    origin = f"{base.scheme}://{base.hostname}"

    for path in paths:
        href = urljoin(origin, path)
        if href in seen:
            continue
        seen.add(href)
        if on_progress:
            on_progress({"type": "log", "message": f"checking {urlparse(href).path or '/'}"})
        pages.append(fetch_page(href))

    tech_stack = detect_stack(pages)
    pain_signals = build_pain_signals(base, pages)
    successful = [p for p in pages if p["ok"]]

    return {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "website": origin,
        "techStack": tech_stack,
        "painSignals": pain_signals,
        "pages": summarize_pages(pages),
        "summary": {
            "pagesChecked": len(pages),
            "pagesOk": len(successful),
            "homeResponseMs": pages[0]["responseMs"] if pages else None,
            "homeStatus": pages[0]["status"] if pages else 0,
            "homeBytes": pages[0]["bytes"] if pages else 0,
            "hasStatusPage": has_status_page(pages),
        },
    }


if __name__ == "__main__":
    import json
    import sys

    site = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    result = analyze_site(site, on_progress=lambda e: print(e.get("message", "")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
