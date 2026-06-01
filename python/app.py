"""Servidor web — FastAPI + Jinja2, agora multi-cliente.

Cada request carrega `request.state.client` (lido da sessão; cai no default
se a sessão ainda não tiver escolhido). Todas as telas e jobs operam sobre o
cliente ativo. As rotas /clients e /presets gerenciam a configuração.
"""

import json
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

import clients as clients_mod
import jobs as jobs_mod
import presets as presets_mod
from emails import enrich_emails
from mailtemplate import (
    build_email,
    build_email_with_template,
    build_subject,
    get_email_template,
    get_followup_template,
    save_email_template,
    save_followup_template,
)
from paths import PUBLIC_DIR, TEMPLATES_DIR
from personalize import personalize_leads
from pipeline import is_enabled as pipeline_is_enabled, set_enabled as pipeline_set_enabled
from scoring import apply_score, score_label
from scraper import scrape, scrape_cities
from sender import followup_candidates, send as send_emails, send_followups
from site_insights import analyze_site
from state import add_suppression, is_suppressed, list_suppressions, load, save
from unsubscribe import unsubscribe_url, verify_token
from webhooks import process_event, verify_signature

from jinja2 import Environment, FileSystemLoader, select_autoescape

PORT = int(os.environ.get("PORT", "3000"))

if not os.environ.get("UI_PASSWORD"):
    raise SystemExit("Defina UI_PASSWORD no .env antes de subir o servidor.")

# Garante que os defaults de clientes/presets existem no boot.
clients_mod.seed_defaults()
presets_mod.seed_defaults()

# ---------------------------------------------------------------- templates
env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _strip_scheme(url):
    import re

    return re.sub(r"^https?://", "", url or "")


def _datetimebr(iso):
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return iso


env.filters["strip_scheme"] = _strip_scheme
env.filters["datetimebr"] = _datetimebr
env.globals["score_label"] = score_label


def render(request, name, **ctx):
    client = getattr(request.state, "client", None) or clients_mod.default_client()
    ctx.setdefault("active_client", client)
    ctx.setdefault("all_clients", clients_mod.list_clients())
    # build_subject precisa do cliente; expomos uma versão já ligada pro Jinja.
    ctx.setdefault(
        "build_subject", (lambda lead_name, c=client: build_subject(c, lead_name)) if client else (lambda _n: "")
    )
    return HTMLResponse(env.get_template(name).render(**ctx))


# ---------------------------------------------------------------- helpers
def lead_key(lead):
    return lead.get("website") or lead.get("name")


def find_lead(leads, key):
    return next((l for l in leads if lead_key(l) == key), None)


def reply_to_addr(client):
    return (
        (client.get("reply_to") or "").strip()
        or (client.get("from_email") or "").strip()
        or os.environ.get("REPLY_TO")
        or os.environ.get("FROM_EMAIL")
        or "reply@example.com"
    )


# ---------------------------------------------------------------- app
app = FastAPI()

PUBLIC_PATHS = {
    "/login",
    "/logout",
    "/styles.css",
    "/app.js",
    "/favicon.svg",
    "/unsubscribe",
    "/webhooks/resend",
}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or request.session.get("authed"):
            # Resolve cliente ativo a partir da sessão, com fallback no default.
            client = None
            active_id = request.session.get("active_client")
            if active_id:
                client = clients_mod.get_client(active_id)
            if not client:
                client = clients_mod.default_client()
                if client:
                    request.session["active_client"] = client["id"]
            request.state.client = client
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login", status_code=302)


app.add_middleware(AuthMiddleware)
_cookie_secure = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret-change-me"),
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
    https_only=_cookie_secure,
)


def _require_client(request):
    client = getattr(request.state, "client", None)
    if not client:
        raise RuntimeError("Nenhum cliente cadastrado. Cadastre um em /clients.")
    return client


# ---------------------------------------------------------------- static
@app.get("/styles.css")
def styles():
    return FileResponse(PUBLIC_DIR / "styles.css", media_type="text/css")


@app.get("/app.js")
def appjs():
    return FileResponse(PUBLIC_DIR / "app.js", media_type="text/javascript")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(PUBLIC_DIR / "favicon.svg", media_type="image/svg+xml")


# ---------------------------------------------------------------- auth
@app.get("/login")
def login_page(request: Request):
    if request.session.get("authed"):
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(env.get_template("login.html").render(error=None, show_nav=False))


@app.post("/login")
def login_submit(request: Request, password: str = Form("")):
    if os.environ.get("UI_PASSWORD") and password == os.environ["UI_PASSWORD"]:
        request.session["authed"] = True
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(
        env.get_template("login.html").render(error="Senha incorreta.", show_nav=False),
        status_code=401,
    )


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ---------------------------------------------------------------- pages
@app.get("/")
def dashboard(request: Request):
    client = _require_client(request)
    leads = load(client["id"])
    stats = {
        "total": len(leads),
        "pending": sum(1 for l in leads if l.get("status") == "pending"),
        "approved": sum(
            1 for l in leads if l.get("status") == "approved" and not l.get("sentAt")
        ),
        "rejected": sum(1 for l in leads if l.get("status") == "rejected"),
        "sent": sum(1 for l in leads if l.get("status") == "sent"),
        "personalizable": sum(
            1
            for l in leads
            if l.get("status") == "pending"
            and l.get("website")
            and not l.get("personalizedHook")
        ),
    }
    return render(
        request,
        "dashboard.html",
        stats=stats,
        pipeline_enabled=pipeline_is_enabled(client["id"]),
        active="",
    )


@app.post("/api/pipeline/enabled")
async def api_pipeline_enabled(request: Request):
    """Liga/desliga o ciclo automático (cron) pro cliente ativo."""
    client = _require_client(request)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    pipeline_set_enabled(client["id"], enabled)
    return JSONResponse({"ok": True, "enabled": enabled})


@app.get("/scrape")
def scrape_page(request: Request):
    client = _require_client(request)
    return render(
        request,
        "scrape.html",
        active="scrape",
        presets=presets_mod.list_presets(client["id"]),
    )


@app.post("/api/scrape")
async def api_scrape(request: Request):
    client = _require_client(request)
    body = await request.json()
    term = body.get("term")
    city = body.get("city")
    if not term or not city:
        return JSONResponse({"error": "term e city são obrigatórios"}, status_code=400)
    try:
        max_n = int(body.get("max") or 30)
    except (TypeError, ValueError):
        max_n = 30
    max_n = max(1, min(100, max_n))
    locale = client.get("locale") or "pt-BR"
    cid = client["id"]

    def work(on_progress):
        existing = load(cid)
        fresh = scrape(term, city, max=max_n, locale=locale, on_progress=on_progress)
        on_progress(
            {
                "type": "log",
                "message": f"{len(fresh)} resultados do Maps. Buscando emails…",
            }
        )
        enriched = enrich_emails(fresh, on_progress)

        by_key = {(l.get("website") or l.get("name")): l for l in existing}
        added = 0
        for l in enriched:
            key = l.get("website") or l.get("name")
            if key not in by_key:
                by_key[key] = {
                    **l,
                    "status": "pending",
                    "searchedAs": f"{term} / {city}",
                }
                added += 1
        save(cid, list(by_key.values()))
        on_progress(
            {
                "type": "done",
                "message": f"Adicionados {added} novos leads. Total: {len(by_key)}.",
                "added": added,
                "total": len(by_key),
            }
        )

    job = jobs_mod.run_job(work)
    return JSONResponse({"jobId": job.id})


@app.post("/api/scrape/preset/{preset_id}")
async def api_scrape_preset(request: Request, preset_id: int):
    client = _require_client(request)
    preset = presets_mod.get_preset(preset_id)
    if not preset or preset["client_id"] != client["id"]:
        return JSONResponse({"error": "preset não encontrado"}, status_code=404)
    locale = client.get("locale") or "pt-BR"
    cid = client["id"]

    def work(on_progress):
        existing = load(cid)
        fresh = scrape_cities(
            preset["term"],
            preset["cities"],
            max=preset["max_results"],
            locale=locale,
            on_progress=on_progress,
        )
        on_progress(
            {
                "type": "log",
                "message": f"{len(fresh)} resultados totais. Buscando emails…",
            }
        )
        enriched = enrich_emails(fresh, on_progress)
        by_key = {(l.get("website") or l.get("name")): l for l in existing}
        added = 0
        for l in enriched:
            key = l.get("website") or l.get("name")
            if key not in by_key:
                by_key[key] = {
                    **l,
                    "status": "pending",
                    "searchedAs": l.get("searchedAs") or preset["label"],
                }
                added += 1
        save(cid, list(by_key.values()))
        on_progress(
            {
                "type": "done",
                "message": f"Preset \"{preset['label']}\" concluído. {added} novos, total {len(by_key)}.",
                "added": added,
                "total": len(by_key),
            }
        )

    job = jobs_mod.run_job(work)
    return JSONResponse({"jobId": job.id})


@app.get("/review")
def review_page(request: Request):
    client = _require_client(request)
    leads = load(client["id"])
    pending = [l for l in leads if l.get("status") == "pending"]
    pending.sort(key=lambda l: l.get("score") or 0, reverse=True)
    return render(request, "review.html", leads=pending, active="review")


@app.get("/leads/{key:path}")
def lead_detail(request: Request, key: str):
    client = _require_client(request)
    leads = load(client["id"])
    lead = find_lead(leads, key)
    if not lead:
        return HTMLResponse(
            env.get_template("lead.html").render(
                lead=None, email=None, active="",
                active_client=client, all_clients=clients_mod.list_clients(),
            ),
            status_code=404,
        )
    email = build_email(
        client,
        name=lead.get("name"),
        reply_to=reply_to_addr(client),
        personalized_hook=lead.get("personalizedHook"),
        site_insights=lead.get("siteInsights"),
    )
    return render(request, "lead.html", lead=lead, email=email, active="")


@app.get("/template")
def template_page(request: Request):
    client = _require_client(request)
    return render(
        request,
        "template.html",
        template=get_email_template(client),
        followup=get_followup_template(client),
        active="template",
    )


@app.post("/api/followup-template")
async def api_followup_template(request: Request):
    client = _require_client(request)
    body = await request.json()
    try:
        followup = save_followup_template(
            client, body.get("subject"), body.get("body"), body.get("delay_days")
        )
        return JSONResponse({"ok": True, "followup": followup})
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/template")
async def api_template(request: Request):
    client = _require_client(request)
    body = await request.json()
    try:
        template = save_email_template(client, body.get("subject"), body.get("body"))
        return JSONResponse({"ok": True, "template": template})
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/template/preview")
async def api_template_preview(request: Request):
    client = _require_client(request)
    body = await request.json()
    current = get_email_template(client)
    try:
        template = {
            "subject": body["subject"] if isinstance(body.get("subject"), str) else current["subject"],
            "body": body["body"] if isinstance(body.get("body"), str) else current["body"],
        }
        sample_hook = (
            "saw that you handle interstate freight across the south."
            if (client.get("locale") or "").lower().startswith("en")
            else "vi que vocês trabalham com presença digital para empresas locais."
        )
        sample_name = (
            "Acme Trucking" if (client.get("locale") or "").lower().startswith("en") else "Agência Exemplo"
        )
        email = build_email_with_template(
            client,
            template,
            name=body.get("name") or sample_name,
            reply_to=reply_to_addr(client),
            personalized_hook=body.get("personalizedHook") or sample_hook,
            site_insights=None,
        )
        return JSONResponse({"ok": True, "email": email})
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/leads/bulk")
async def api_leads_bulk(request: Request):
    client = _require_client(request)
    body = await request.json()
    keys = body.get("keys")
    status = body.get("status")
    if not isinstance(keys, list) or not status:
        return JSONResponse({"error": "keys e status obrigatórios"}, status_code=400)
    leads = load(client["id"])
    keyset = set(keys)
    count = 0
    for l in leads:
        if (l.get("website") or l.get("name")) in keyset:
            l["status"] = status
            count += 1
    save(client["id"], leads)
    return JSONResponse({"ok": True, "count": count})


@app.get("/api/leads/{key:path}/preview")
def api_lead_preview(request: Request, key: str):
    client = _require_client(request)
    leads = load(client["id"])
    lead = find_lead(leads, key)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)
    email = build_email(
        client,
        name=lead.get("name"),
        reply_to=reply_to_addr(client),
        personalized_hook=lead.get("personalizedHook"),
        site_insights=lead.get("siteInsights"),
    )
    return JSONResponse({"ok": True, "email": email, "lead": lead})


@app.post("/api/leads/{key:path}/analyze")
async def api_lead_analyze(request: Request, key: str):
    client = _require_client(request)
    leads = load(client["id"])
    lead = find_lead(leads, key)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not lead.get("website"):
        return JSONResponse({"error": "lead sem site"}, status_code=400)
    try:
        lead["siteInsights"] = analyze_site(lead["website"])
        apply_score(lead)
        save(client["id"], leads)
        return JSONResponse(
            {
                "ok": True,
                "lead": lead,
                "siteInsights": lead["siteInsights"],
                "score": lead.get("score"),
            }
        )
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/leads/{key:path}")
async def api_lead_update(request: Request, key: str):
    client = _require_client(request)
    body = await request.json()
    leads = load(client["id"])
    lead = find_lead(leads, key)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)
    if body.get("status"):
        lead["status"] = body["status"]
    if isinstance(body.get("email"), str):
        lead["email"] = body["email"].strip().lower()
    if isinstance(body.get("personalizedHook"), str):
        lead["personalizedHook"] = body["personalizedHook"].strip()
    if isinstance(body.get("notes"), str):
        lead["notes"] = body["notes"].strip()
    if "replied" in body:
        lead["repliedAt"] = datetime.now().isoformat() if body["replied"] else None
    save(client["id"], leads)
    return JSONResponse({"ok": True, "lead": lead})


@app.get("/personalize")
def personalize_page(request: Request):
    client = _require_client(request)
    leads = load(client["id"])
    pending_count = sum(
        1
        for l in leads
        if l.get("status") == "pending"
        and l.get("website")
        and not l.get("personalizedHook")
    )
    done_count = sum(1 for l in leads if l.get("personalizedHook"))
    return render(
        request,
        "personalize.html",
        pendingCount=pending_count,
        doneCount=done_count,
        active="personalize",
    )


@app.post("/api/personalize")
async def api_personalize(request: Request):
    client = _require_client(request)
    body = await request.json()
    force = bool(body.get("force"))
    cid = client["id"]

    def work(on_progress):
        leads = load(cid)
        targets = [
            l
            for l in leads
            if l.get("status") == "pending"
            and l.get("website")
            and (force or not l.get("personalizedHook"))
        ]
        personalize_leads(client, targets, force=force, on_progress=on_progress)
        save(cid, leads)

    job = jobs_mod.run_job(work)
    return JSONResponse({"jobId": job.id})


@app.get("/send")
def send_page(request: Request):
    client = _require_client(request)
    leads = load(client["id"])
    approved_count = sum(
        1 for l in leads if l.get("status") == "approved" and not l.get("sentAt")
    )
    followup_count = len(followup_candidates(client, leads))
    return render(
        request,
        "send.html",
        approvedCount=approved_count,
        followupCount=followup_count,
        active="send",
    )


@app.post("/api/followup")
async def api_followup(request: Request):
    client = _require_client(request)
    body = await request.json()
    opts = {}
    if body.get("limit"):
        try:
            n = int(body["limit"])
            if n > 0:
                opts["limit"] = n
        except (TypeError, ValueError):
            pass

    def work(on_progress):
        send_followups(client, on_progress=on_progress, **opts)

    job = jobs_mod.run_job(work)
    return JSONResponse({"jobId": job.id})


@app.post("/api/rescore")
async def api_rescore(request: Request):
    client = _require_client(request)
    cid = client["id"]

    def work(on_progress):
        leads = load(cid)
        targets = [l for l in leads if l.get("siteInsights")]
        on_progress(
            {"type": "log", "message": f"Recalculando score de {len(targets)} leads com scan…"}
        )
        for l in targets:
            apply_score(l)
        save(cid, leads)
        on_progress(
            {
                "type": "done",
                "message": f"{len(targets)} leads pontuados.",
                "scored": len(targets),
            }
        )

    job = jobs_mod.run_job(work)
    return JSONResponse({"jobId": job.id})


@app.post("/api/send")
async def api_send(request: Request):
    client = _require_client(request)
    body = await request.json()
    opts = {}
    if body.get("testEmail"):
        opts["test_email"] = str(body["testEmail"]).strip()
    if body.get("limit"):
        try:
            n = int(body["limit"])
            if n > 0:
                opts["limit"] = n
        except (TypeError, ValueError):
            pass

    def work(on_progress):
        send_emails(client, on_progress=on_progress, **opts)

    job = jobs_mod.run_job(work)
    return JSONResponse({"jobId": job.id})


@app.get("/api/jobs/{job_id}/stream")
def job_stream(job_id: str):
    job = jobs_mod.get_job(job_id)
    if not job:
        return JSONResponse(None, status_code=404)

    def gen():
        buffered, q, done = jobs_mod.subscribe(job)
        for ev in buffered:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        if done:
            return
        try:
            while True:
                try:
                    ev = q.get(timeout=15)
                except Exception:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") in ("done", "fatal"):
                    return
        finally:
            jobs_mod.unsubscribe(job, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------- clients
@app.get("/clients")
def clients_page(request: Request):
    return render(request, "clients.html", clients=clients_mod.list_clients(), active="clients")


@app.post("/api/clients")
async def api_create_client(request: Request):
    body = await request.json()
    try:
        client = clients_mod.create_client(
            id=body.get("id") or body.get("name"),
            name=body.get("name") or "",
            url=body.get("url") or "",
            locale=body.get("locale") or "pt-BR",
            resend_api_key=body.get("resend_api_key") or "",
            from_email=body.get("from_email") or "",
            reply_to=body.get("reply_to") or "",
            is_default=bool(body.get("is_default")),
        )
        return JSONResponse({"ok": True, "client": client})
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/clients/{client_id}")
async def api_update_client(request: Request, client_id: str):
    body = await request.json()
    try:
        client = clients_mod.update_client(client_id, **body)
        if not client:
            return JSONResponse({"error": "cliente não encontrado"}, status_code=404)
        return JSONResponse({"ok": True, "client": client})
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/clients/{client_id}/activate")
def api_activate_client(request: Request, client_id: str):
    client = clients_mod.get_client(client_id)
    if not client:
        return JSONResponse({"error": "cliente não encontrado"}, status_code=404)
    request.session["active_client"] = client["id"]
    return JSONResponse({"ok": True, "active_client": client["id"]})


@app.post("/switch-client")
def switch_client(request: Request, client_id: str = Form(...), next: str = Form("/")):
    client = clients_mod.get_client(client_id)
    if client:
        request.session["active_client"] = client["id"]
    return RedirectResponse(next or "/", status_code=302)


# ---------------------------------------------------------------- presets
@app.get("/presets")
def presets_page(request: Request):
    client = _require_client(request)
    return render(
        request,
        "presets.html",
        presets=presets_mod.list_presets(client["id"]),
        active="presets",
    )


@app.post("/api/presets")
async def api_create_preset(request: Request):
    client = _require_client(request)
    body = await request.json()
    try:
        preset = presets_mod.create_preset(
            client_id=client["id"],
            label=body.get("label") or "",
            term=body.get("term") or "",
            cities=body.get("cities") or "",
            max_results=body.get("max_results") or 30,
        )
        return JSONResponse({"ok": True, "preset": preset})
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/presets/{preset_id}/delete")
def api_delete_preset(request: Request, preset_id: int):
    client = _require_client(request)
    preset = presets_mod.get_preset(preset_id)
    if not preset or preset["client_id"] != client["id"]:
        return JSONResponse({"error": "preset não encontrado"}, status_code=404)
    presets_mod.delete_preset(preset_id)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- webhooks
@app.post("/webhooks/resend")
async def resend_webhook(request: Request):
    raw = await request.body()
    if not verify_signature(request.headers, raw):
        return JSONResponse({"error": "assinatura inválida"}, status_code=401)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "payload inválido"}, status_code=400)
    summary = process_event(payload)
    return JSONResponse({"ok": True, "handled": summary})


# ---------------------------------------------------------------- unsubscribe
@app.get("/unsubscribe")
def unsubscribe_get(e: str = "", t: str = ""):
    if not e or not verify_token(e, t):
        return HTMLResponse(
            env.get_template("unsubscribe.html").render(
                email=e, token=t, done=False, valid=False, show_nav=False
            ),
            status_code=400,
        )
    return HTMLResponse(
        env.get_template("unsubscribe.html").render(
            email=e, token=t, done=False, valid=True, show_nav=False
        )
    )


def _do_unsubscribe_all(email):
    """Suprime em todos os clientes — o lead pode pertencer a qualquer um."""
    now = datetime.now().isoformat()
    for client in clients_mod.list_clients():
        cid = client["id"]
        add_suppression(cid, email, reason="unsubscribe")
        leads = load(cid)
        changed = False
        for l in leads:
            if (l.get("email") or "").strip().lower() == email.strip().lower():
                l["unsubscribedAt"] = now
                l["status"] = "rejected"
                changed = True
        if changed:
            save(cid, leads)


@app.post("/unsubscribe")
async def unsubscribe_post(request: Request):
    e = request.query_params.get("e", "")
    t = request.query_params.get("t", "")
    if not (e and t):
        try:
            form = await request.form()
            e = e or form.get("e", "")
            t = t or form.get("t", "")
        except Exception:
            pass
    if not e or not verify_token(e, t):
        return JSONResponse({"error": "link inválido"}, status_code=400)
    _do_unsubscribe_all(e)
    return HTMLResponse(
        env.get_template("unsubscribe.html").render(
            email=e, token=t, done=True, valid=True, show_nav=False
        )
    )


# ---------------------------------------------------------------- suppressions
@app.get("/suppressions")
def suppressions_page(request: Request):
    client = _require_client(request)
    return render(
        request,
        "suppressions.html",
        suppressions=list_suppressions(client["id"]),
        active="",
    )


@app.post("/api/suppressions")
async def api_add_suppression(request: Request):
    client = _require_client(request)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "email inválido"}, status_code=400)
    add_suppression(client["id"], email, reason=body.get("reason") or "manual")
    # marca local nos leads desse cliente
    leads = load(client["id"])
    now = datetime.now().isoformat()
    changed = False
    for l in leads:
        if (l.get("email") or "").strip().lower() == email:
            l["unsubscribedAt"] = now
            l["status"] = "rejected"
            changed = True
    if changed:
        save(client["id"], leads)
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn

    print(f"\n  outreach multi-cliente rodando em http://localhost:{PORT}")
    print("  Senha definida via UI_PASSWORD no .env\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
