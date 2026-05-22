"""Servidor web — equivalente a server.js, em FastAPI + Jinja2."""

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
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

import jobs as jobs_mod
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
from scoring import apply_score, score_label
from scraper import scrape
from sender import followup_candidates, send as send_emails, send_followups
from site_insights import analyze_site
from state import add_suppression, is_suppressed, list_suppressions, load, save
from unsubscribe import unsubscribe_url, verify_token
from webhooks import process_event, verify_signature

from jinja2 import Environment, FileSystemLoader, select_autoescape

PORT = int(os.environ.get("PORT", "3000"))

if not os.environ.get("UI_PASSWORD"):
    raise SystemExit("Defina UI_PASSWORD no .env antes de subir o servidor.")

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
env.globals["build_subject"] = build_subject
env.globals["score_label"] = score_label


def render(name, **ctx):
    return HTMLResponse(env.get_template(name).render(**ctx))


# ---------------------------------------------------------------- helpers
def lead_key(lead):
    return lead.get("website") or lead.get("name")


def find_lead(leads, key):
    return next((l for l in leads if lead_key(l) == key), None)


def reply_to_addr():
    return os.environ.get("REPLY_TO") or os.environ.get("FROM_EMAIL") or "reply@example.com"


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
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login", status_code=302)


app.add_middleware(AuthMiddleware)
# COOKIE_SECURE=true em produção (HTTPS) pra o cookie de login só trafegar via TLS.
_cookie_secure = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret-change-me"),
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
    https_only=_cookie_secure,
)

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
    return render("login.html", error=None, show_nav=False)


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
    leads = load()
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
    return render("dashboard.html", stats=stats, active="")


@app.get("/scrape")
def scrape_page():
    return render("scrape.html", active="scrape")


@app.post("/api/scrape")
async def api_scrape(request: Request):
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

    def work(on_progress):
        existing = load()
        fresh = scrape(term, city, max=max_n, on_progress=on_progress)
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
        save(list(by_key.values()))
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


@app.get("/review")
def review_page():
    leads = load()
    pending = [l for l in leads if l.get("status") == "pending"]
    # leads mais "quentes" (score de dor) primeiro
    pending.sort(key=lambda l: l.get("score") or 0, reverse=True)
    return render("review.html", leads=pending, active="review")


@app.get("/leads/{key:path}")
def lead_detail(key: str):
    leads = load()
    lead = find_lead(leads, key)
    if not lead:
        return HTMLResponse(
            env.get_template("lead.html").render(lead=None, email=None, active=""),
            status_code=404,
        )
    email = build_email(
        name=lead.get("name"),
        reply_to=reply_to_addr(),
        personalized_hook=lead.get("personalizedHook"),
        site_insights=lead.get("siteInsights"),
    )
    return render("lead.html", lead=lead, email=email, active="")


@app.get("/template")
def template_page():
    return render(
        "template.html",
        template=get_email_template(),
        followup=get_followup_template(),
        active="template",
    )


@app.post("/api/followup-template")
async def api_followup_template(request: Request):
    body = await request.json()
    try:
        followup = save_followup_template(
            body.get("subject"), body.get("body"), body.get("delay_days")
        )
        return JSONResponse({"ok": True, "followup": followup})
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/template")
async def api_template(request: Request):
    body = await request.json()
    try:
        template = save_email_template(body.get("subject"), body.get("body"))
        return JSONResponse({"ok": True, "template": template})
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/template/preview")
async def api_template_preview(request: Request):
    body = await request.json()
    current = get_email_template()
    try:
        template = {
            "subject": body["subject"] if isinstance(body.get("subject"), str) else current["subject"],
            "body": body["body"] if isinstance(body.get("body"), str) else current["body"],
        }
        email = build_email_with_template(
            template,
            name=body.get("name") or "Agência Exemplo",
            reply_to=reply_to_addr(),
            personalized_hook=(
                body.get("personalizedHook")
                or "vi que vocês trabalham com presença digital para empresas locais."
            ),
            site_insights=None,
        )
        return JSONResponse({"ok": True, "email": email})
    except Exception as err:
        return JSONResponse({"error": str(err)}, status_code=400)


@app.post("/api/leads/bulk")
async def api_leads_bulk(request: Request):
    body = await request.json()
    keys = body.get("keys")
    status = body.get("status")
    if not isinstance(keys, list) or not status:
        return JSONResponse({"error": "keys e status obrigatórios"}, status_code=400)
    leads = load()
    keyset = set(keys)
    count = 0
    for l in leads:
        if (l.get("website") or l.get("name")) in keyset:
            l["status"] = status
            count += 1
    save(leads)
    return JSONResponse({"ok": True, "count": count})


@app.get("/api/leads/{key:path}/preview")
def api_lead_preview(key: str):
    leads = load()
    lead = find_lead(leads, key)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)
    email = build_email(
        name=lead.get("name"),
        reply_to=reply_to_addr(),
        personalized_hook=lead.get("personalizedHook"),
        site_insights=lead.get("siteInsights"),
    )
    return JSONResponse({"ok": True, "email": email, "lead": lead})


@app.post("/api/leads/{key:path}/analyze")
async def api_lead_analyze(key: str):
    leads = load()
    lead = find_lead(leads, key)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not lead.get("website"):
        return JSONResponse({"error": "lead sem site"}, status_code=400)
    try:
        lead["siteInsights"] = analyze_site(lead["website"])
        apply_score(lead)
        save(leads)
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
async def api_lead_update(key: str, request: Request):
    body = await request.json()
    leads = load()
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
    save(leads)
    return JSONResponse({"ok": True, "lead": lead})


@app.get("/personalize")
def personalize_page():
    leads = load()
    pending_count = sum(
        1
        for l in leads
        if l.get("status") == "pending"
        and l.get("website")
        and not l.get("personalizedHook")
    )
    done_count = sum(1 for l in leads if l.get("personalizedHook"))
    return render(
        "personalize.html",
        pendingCount=pending_count,
        doneCount=done_count,
        active="personalize",
    )


@app.post("/api/personalize")
async def api_personalize(request: Request):
    body = await request.json()
    force = bool(body.get("force"))

    def work(on_progress):
        leads = load()
        targets = [
            l
            for l in leads
            if l.get("status") == "pending"
            and l.get("website")
            and (force or not l.get("personalizedHook"))
        ]
        personalize_leads(targets, force=force, on_progress=on_progress)
        save(leads)

    job = jobs_mod.run_job(work)
    return JSONResponse({"jobId": job.id})


@app.get("/send")
def send_page():
    leads = load()
    approved_count = sum(
        1 for l in leads if l.get("status") == "approved" and not l.get("sentAt")
    )
    followup_count = len(followup_candidates(leads))
    return render(
        "send.html",
        approvedCount=approved_count,
        followupCount=followup_count,
        active="send",
    )


@app.post("/api/followup")
async def api_followup(request: Request):
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
        send_followups(on_progress=on_progress, **opts)

    job = jobs_mod.run_job(work)
    return JSONResponse({"jobId": job.id})


@app.post("/api/rescore")
async def api_rescore():
    def work(on_progress):
        leads = load()
        targets = [l for l in leads if l.get("siteInsights")]
        on_progress(
            {"type": "log", "message": f"Recalculando score de {len(targets)} leads com scan…"}
        )
        for l in targets:
            apply_score(l)
        save(leads)
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
        send_emails(on_progress=on_progress, **opts)

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
    return render(
        "unsubscribe.html", email=e, token=t, done=False, valid=True, show_nav=False
    )


def _do_unsubscribe(email):
    add_suppression(email, reason="unsubscribe")
    leads = load()
    changed = False
    for l in leads:
        if (l.get("email") or "").strip().lower() == email.strip().lower():
            l["unsubscribedAt"] = datetime.now().isoformat()
            l["status"] = "rejected"
            changed = True
    if changed:
        save(leads)


@app.post("/unsubscribe")
async def unsubscribe_post(request: Request):
    # Suporta one-click (List-Unsubscribe-Post) e o form da página.
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
    _do_unsubscribe(e)
    return render(
        "unsubscribe.html", email=e, token=t, done=True, valid=True, show_nav=False
    )


# ---------------------------------------------------------------- suppressions
@app.get("/suppressions")
def suppressions_page():
    return render("suppressions.html", suppressions=list_suppressions(), active="")


@app.post("/api/suppressions")
async def api_add_suppression(request: Request):
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "email inválido"}, status_code=400)
    add_suppression(email, reason=body.get("reason") or "manual")
    _do_unsubscribe(email)
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn

    print(f"\n  UpStat outreach UI rodando em http://localhost:{PORT}")
    print("  Senha definida via UI_PASSWORD no .env\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
