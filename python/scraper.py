"""Scraper do Google Maps via Playwright — equivalente a lib/scraper.js.

Usa a API síncrona do Playwright para manter a lógica próxima do original.
"""

import random
import time
from urllib.parse import quote, urlparse

from playwright.sync_api import sync_playwright

from progress import console_progress


def _sleep(ms):
    time.sleep(ms / 1000)


def _rand(a, b):
    return a + random.random() * (b - a)


def clean_website(url):
    if not url:
        return ""
    try:
        u = urlparse(url)
        if not u.scheme or not u.hostname:
            return url
        if "google.com" in u.hostname:
            return ""
        path = u.path.rstrip("/")
        return f"{u.scheme}://{u.hostname}{path}"
    except Exception:
        return url


def scrape(term, city, max=30, on_progress=console_progress):
    query = f"{term} em {city}"
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            locale="pt-BR",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        on_progress({"type": "log", "message": f'Abrindo Google Maps: "{query}"'})
        page.goto(
            f"https://www.google.com/maps/search/{quote(query)}/?hl=pt-BR",
            wait_until="domcontentloaded",
        )

        try:
            page.locator(
                'button:has-text("Aceitar tudo"), button:has-text("Aceitar todos")'
            ).first.click(timeout=3000)
        except Exception:
            pass

        feed = page.locator('[role="feed"]')
        try:
            feed.wait_for(timeout=15000)
        except Exception:
            pass

        last_count = 0
        stable = 0
        for _ in range(30):
            count = page.locator("a.hfpxzc").count()
            if count >= max:
                break
            if count == last_count:
                stable += 1
            else:
                stable = 0
            if stable >= 3:
                break
            last_count = count
            try:
                feed.evaluate("(el) => el.scrollBy(0, el.scrollHeight)")
            except Exception:
                pass
            _sleep(_rand(900, 1600))

        links = page.locator("a.hfpxzc").evaluate_all(
            """(els, lim) => els.slice(0, lim).map((a) => ({
                href: a.getAttribute("href"),
                name: a.getAttribute("aria-label") || "",
            }))""",
            max,
        )
        on_progress(
            {
                "type": "log",
                "message": f"Coletados {len(links)} cards. Abrindo cada painel…",
            }
        )

        for i, link in enumerate(links):
            name = link.get("name", "")
            href = link.get("href")
            if not href:
                continue
            try:
                page.goto(href, wait_until="domcontentloaded")
                try:
                    page.locator("h1.DUwDvf, h1").first.wait_for(timeout=8000)
                except Exception:
                    pass
                _sleep(_rand(400, 900))

                data = page.evaluate(
                    """() => {
                        const get = (sel) => document.querySelector(sel);
                        const heading = get("h1.DUwDvf, h1")?.textContent?.trim() || "";
                        const websiteEl =
                            get('a[data-item-id="authority"]') ||
                            get('a[aria-label^="Site"]') ||
                            get('a[aria-label^="Website"]');
                        const website = websiteEl?.getAttribute("href") || "";
                        const phoneEl =
                            document.querySelector('button[data-item-id^="phone"]') ||
                            document.querySelector('[aria-label^="Telefone"]');
                        const phone =
                            phoneEl?.getAttribute("aria-label")?.replace(/Telefone:\\s*/i, "").trim() || "";
                        const addrEl = get('button[data-item-id="address"]');
                        const address =
                            addrEl?.getAttribute("aria-label")?.replace(/Endereço:\\s*/i, "").trim() || "";
                        return { heading, website, phone, address };
                    }"""
                )

                result = {
                    "name": data.get("heading") or name,
                    "website": clean_website(data.get("website")),
                    "phone": data.get("phone", ""),
                    "address": data.get("address", ""),
                }
                results.append(result)
                on_progress(
                    {
                        "type": "item",
                        "index": i + 1,
                        "total": len(links),
                        "name": result["name"],
                        "status": "site ✓" if result["website"] else "sem site",
                    }
                )
            except Exception:
                on_progress(
                    {
                        "type": "item",
                        "index": i + 1,
                        "total": len(links),
                        "name": name,
                        "status": "falha",
                    }
                )

        browser.close()

    return results


if __name__ == "__main__":
    import json
    import sys

    term = sys.argv[1] if len(sys.argv) > 1 else "restaurante"
    city = sys.argv[2] if len(sys.argv) > 2 else "São Paulo"
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    out = scrape(term, city, max=limit)
    print(json.dumps(out, ensure_ascii=False, indent=2))
