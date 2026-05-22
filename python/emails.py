"""Extração de emails dos sites — equivalente a lib/emails.js."""

import re
from urllib.parse import urlparse

import httpx

from progress import console_progress

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

PATHS = [
    "",
    "/contato",
    "/contatos",
    "/contact",
    "/contact-us",
    "/sobre",
    "/about",
    "/fale-conosco",
    "/equipe",
    "/quem-somos",
]

PLAUSIBLE_TLDS = {
    "com", "br", "net", "org", "io", "co", "dev", "app", "tech", "ag",
    "agency", "studio", "design", "digital", "me", "pt", "eu", "us",
    "info", "biz", "tv", "cc", "xyz", "ai", "gg", "page", "site",
}

BLOCKLIST = [
    "sentry.io",
    "sentry-next.wixpress.com",
    "wixpress.com",
    "example.com",
    "domain.com",
    "email.com",
    "seudominio.com",
    "yourdomain.com",
    "test.com",
    "mysite.com",
    "website.com",
]

FETCH_TIMEOUT_S = 8.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
}


def fetch_text(url):
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


def preprocess(html):
    s = html
    s = re.sub(r"&#64;", "@", s, flags=re.IGNORECASE)
    s = re.sub(r"&#0?46;", ".", s, flags=re.IGNORECASE)
    s = re.sub(r"&commat;", "@", s, flags=re.IGNORECASE)
    s = re.sub(r"&period;", ".", s, flags=re.IGNORECASE)
    s = re.sub(r"&amp;", "&", s, flags=re.IGNORECASE)
    s = re.sub(r"&nbsp;", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"[​-‍﻿­]", "", s)
    s = re.sub(r"<wbr\s*/?>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.DOTALL)

    s = re.sub(r"\s*\[\s*at\s*\]\s*", "@", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\(\s*at\s*\)\s*", "@", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+at\s+(?=[a-z0-9-]+\s*(?:\[|\()\s*dot)", "@", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\(arroba\)\s*", "@", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+arroba\s+", "@", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\[\s*dot\s*\]\s*", ".", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\(\s*dot\s*\)\s*", ".", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\(ponto\)\s*", ".", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+ponto\s+", ".", s, flags=re.IGNORECASE)
    return s


def tld_of(email):
    parts = email.split("@")[1].split(".") if "@" in email else []
    return parts[-1].lower() if parts else ""


def root_domain(host):
    return re.sub(r"^www\.", "", host).lower()


def is_valid_email(email, site_host):
    if any(b in email for b in BLOCKLIST):
        return False
    if re.search(r"\.(png|jpe?g|gif|svg|webp|woff2?|ttf|ico|css|js)(\?|$)", email):
        return False
    if re.match(r"^[a-f0-9]{16,}@", email, re.IGNORECASE):
        return False
    if len(email) > 80:
        return False

    tld = tld_of(email)
    if tld in PLAUSIBLE_TLDS:
        return True

    if site_host:
        site_tld = root_domain(site_host).split(".")[-1]
        if tld == site_tld:
            return True
    return False


def extract(html, site_host):
    processed = preprocess(html)
    mailtos = set()
    plain = set()

    for m in re.finditer(r"mailto:([^\"'?\s>&]+)", processed, re.IGNORECASE):
        mailtos.add(m.group(1))
    for m in EMAIL_RE.finditer(processed):
        plain.add(m.group(0))

    def clean(e):
        return re.sub(r"[.,;:]+$", "", e.lower())

    mailto_list = [e for e in (clean(x) for x in mailtos) if is_valid_email(e, site_host)]
    plain_list = [e for e in (clean(x) for x in plain) if is_valid_email(e, site_host)]

    root = root_domain(site_host) if site_host else ""

    def same_domain(e):
        return bool(root) and (e.endswith("@" + root) or e.endswith("." + root))

    return (
        next((e for e in mailto_list if same_domain(e)), None)
        or next((e for e in plain_list if same_domain(e)), None)
        or (mailto_list[0] if mailto_list else None)
        or (plain_list[0] if plain_list else None)
        or ""
    )


def find_email(website):
    if not website:
        return ""
    base = urlparse(website)
    if not base.scheme or not base.hostname:
        return ""

    for path in PATHS:
        url = f"{base.scheme}://{base.hostname}{path}"
        html = fetch_text(url)
        if not html:
            continue
        email = extract(html, base.hostname)
        if email:
            return email
    return ""


def enrich_emails(leads, on_progress=console_progress):
    out = []
    for i, lead in enumerate(leads):
        email = find_email(lead.get("website"))
        on_progress(
            {
                "type": "item",
                "index": i + 1,
                "total": len(leads),
                "name": lead.get("name"),
                "status": email or "(sem email)",
            }
        )
        out.append({**lead, "email": email})
    return out
