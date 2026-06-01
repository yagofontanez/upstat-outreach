"""Pipeline diário sem humano no loop.

Encadeia, pra um cliente:
  1. reabastece (scrape do próximo preset) SE o estoque vendável estiver baixo
  2. personaliza os pendentes sem hook (com teto, pra não estourar a quota do Groq)
  3. auto-aprova com filtros de qualidade (substitui o `review` manual)
  4. envia o PRIMEIRO email com teto diário (proteção de reputação do remetente)

O follow-up NÃO faz parte do ciclo automático por padrão (o cron cuida só do
primeiro contato). Pra incluí-lo, passe do_followup=True / `--followup` no CLI,
ou rode `cli.py followup` à parte.

Pensado pra rodar via systemd timer / cron (deploy/pipeline.sh). Cada passo loga
no console (console_progress) e reaproveita exatamente as mesmas funções da UI/CLI.
"""

import presets as presets_mod
from auto_approve import auto_approve, eligible
from emails import enrich_emails
from personalize import personalize_leads
from progress import console_progress
from scraper import scrape_cities
from sender import send, send_followups
from state import get_setting, is_suppressed, load, save, set_setting

# Flag por cliente que liga/desliga o ciclo automático (cron). Default: desligado.
ENABLED_KEY = "pipeline_enabled"


def is_enabled(client_id):
    """True se o envio automático está ligado pra esse cliente (default: desligado)."""
    return get_setting(client_id, ENABLED_KEY, "0") == "1"


def set_enabled(client_id, enabled):
    """Liga/desliga o ciclo automático pra esse cliente."""
    set_setting(client_id, ENABLED_KEY, "1" if enabled else "0")
    return bool(enabled)


def _sendable_stock(client, leads):
    """Quantos leads dá pra enviar 'já' ou 'logo': approved não-enviados +
    pending que passariam na auto-aprovação."""
    n = 0
    for l in leads:
        if l.get("status") == "approved" and l.get("email") and not l.get("sentAt"):
            if not is_suppressed(client["id"], l["email"]):
                n += 1
        elif eligible(client, l):
            n += 1
    return n


def _next_preset(client):
    """Rotaciona pelos presets do cliente, guardando o índice em settings."""
    items = presets_mod.list_presets(client["id"])
    if not items:
        return None
    try:
        idx = int(get_setting(client["id"], "pipeline_preset_idx", "0"))
    except (TypeError, ValueError):
        idx = 0
    preset = items[idx % len(items)]
    set_setting(client["id"], "pipeline_preset_idx", str((idx + 1) % len(items)))
    return preset


def _replenish(client, log):
    """Scrapa o próximo preset e mescla os novos leads (status pending)."""
    preset = _next_preset(client)
    if not preset:
        log(f"[{client['name']}] sem presets cadastrados — pulei o scrape.")
        return 0
    log(f"[{client['name']}] reabastecendo via preset #{preset['id']} «{preset['label']}»…")
    fresh = scrape_cities(
        preset["term"],
        preset["cities"],
        max=preset["max_results"],
        locale=client.get("locale") or "pt-BR",
    )
    enriched = enrich_emails(fresh)
    leads = load(client["id"])
    by_key = {(l.get("website") or l.get("name")): l for l in leads}
    added = 0
    for l in enriched:
        key = l.get("website") or l.get("name")
        if key and key not in by_key:
            by_key[key] = {
                **l,
                "status": "pending",
                "searchedAs": l.get("searchedAs") or preset["label"],
            }
            added += 1
    save(client["id"], list(by_key.values()))
    log(f"[{client['name']}] +{added} novos leads (total {len(by_key)}).")
    return added


def run(
    client,
    limit=20,
    stock_target=None,
    personalize_cap=40,
    require_hook=True,
    min_score=None,
    do_scrape=True,
    do_followup=False,
    force=False,
    on_progress=console_progress,
):
    """Roda um ciclo completo do pipeline pra um cliente.

    Respeita a flag `pipeline_enabled` do cliente (controlada pelo toggle na web):
    se estiver desligada, não faz nada. Passe force=True pra rodar mesmo desligado
    (ex.: teste manual via `cli.py pipeline --force`).
    """

    def log(msg):
        on_progress({"type": "log", "message": msg})

    if not force and not is_enabled(client["id"]):
        log(f"[{client['name']}] envio automático DESATIVADO — nada a fazer (ligue no painel ou use --force).")
        return

    stock_target = stock_target if stock_target is not None else limit * 2
    log(f"=== pipeline [{client['name']}] · teto/dia {limit} · alvo estoque {stock_target} ===")

    # 1. reabastecer só se o estoque vendável estiver abaixo do alvo
    if do_scrape:
        stock = _sendable_stock(client, load(client["id"]))
        if stock < stock_target:
            log(f"[{client['name']}] estoque vendável {stock} < alvo {stock_target}.")
            _replenish(client, log)
        else:
            log(f"[{client['name']}] estoque vendável {stock} ≥ alvo {stock_target} — sem scrape.")

    # 2. personalizar pendentes sem hook (com teto)
    leads = load(client["id"])
    targets = [
        l
        for l in leads
        if l.get("status") == "pending" and l.get("website") and not l.get("personalizedHook")
    ][:personalize_cap]
    if targets:
        log(f"[{client['name']}] personalizando {len(targets)} leads…")
        personalize_leads(client, targets, force=False)
        save(client["id"], leads)
    else:
        log(f"[{client['name']}] nada a personalizar.")

    # 3. auto-aprovar com filtros (constrói um buffer até o alvo de estoque)
    leads = load(client["id"])
    approved = auto_approve(
        client,
        leads,
        limit=stock_target,
        require_hook=require_hook,
        min_score=min_score,
        on_progress=on_progress,
    )
    if approved:
        save(client["id"], leads)

    # 4. enviar o primeiro email respeitando o teto diário
    send(client, limit=limit, on_progress=on_progress)

    # 5. follow-up — fora do ciclo automático por padrão (só com do_followup=True)
    if do_followup:
        send_followups(client, limit=limit, on_progress=on_progress)

    log(f"=== pipeline [{client['name']}] fim ===")
