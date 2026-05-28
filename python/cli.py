"""CLI multi-cliente. Aceita --client <slug> em qualquer comando (default = cliente
default cadastrado em clients).

Exemplos:
  python cli.py --client upstat scrape "agência de marketing" "Curitiba" 30
  python cli.py --client martinsadviser scrape "trucking company" "Houston, TX" 30
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

import clients as clients_mod
import presets as presets_mod
from emails import enrich_emails
from personalize import personalize_leads
from scoring import apply_score
from scraper import scrape, scrape_cities
from sender import send, send_followups
from state import load, save

clients_mod.seed_defaults()
presets_mod.seed_defaults()


def usage():
    print(
        """
Uso:
  python cli.py [--client <slug>] <comando> [args...]

Clientes cadastrados:
  {clients}

Comandos:
  scrape "<termo>" "<cidade>" [maxResults=30]
      Ex: python cli.py --client upstat scrape "agência de marketing" "Curitiba" 40
      Ex: python cli.py --client martinsadviser scrape "trucking company" "Houston, TX" 30

  preset <id|label>
      Roda um preset de busca (lista de cidades) do cliente atual.

  presets
      Lista os presets cadastrados pro cliente atual.

  reenrich [--force]
      Re-tenta extrair email dos sites dos leads pendentes.

  review
      Revisão interativa (terminal).

  personalize [--force]
      Gera abertura personalizada via Groq pra cada lead pendente.

  send [--limit N] [--email <addr>]
      Envia via Resend pros leads aprovados. --email <addr> envia 1 email de teste.

  followup [--limit N]
      Dispara o follow-up pros leads enviados sem resposta após o prazo.

  rescore
      Recalcula o pain score dos leads que já têm scan.

  clients
      Lista os clientes cadastrados.
""".format(clients=", ".join(c["id"] for c in clients_mod.list_clients()) or "(nenhum)")
    )


def _fmt(l):
    BOLD = "\x1b[1m"
    RESET = "\x1b[0m"
    YELLOW = "\x1b[33m"
    lines = [
        f"{BOLD}{l.get('name')}{RESET}",
        f"  site:  {l.get('website') or '-'}",
        f"  email: {l.get('email') or YELLOW + '(vazio)' + RESET}",
    ]
    if l.get("phone"):
        lines.append(f"  tel:   {l['phone']}")
    if l.get("address"):
        lines.append(f"  end:   {l['address']}")
    lines.append(f"  busca: {l.get('searchedAs') or '-'}")
    return "\n".join(lines)


def _pop_flag_value(rest, flag):
    """Remove e retorna o valor de um --flag, se presente. Modifica rest in-place."""
    if flag in rest:
        idx = rest.index(flag)
        try:
            value = rest[idx + 1]
        except IndexError:
            raise SystemExit(f"{flag} precisa de um valor")
        rest.pop(idx + 1)
        rest.pop(idx)
        return value
    return None


def review(client):
    leads = load(client["id"])
    pending = [l for l in leads if l.get("status") == "pending"]
    if not pending:
        print("Nada pendente. Rode `scrape` primeiro.")
        return

    print(
        f"\n[{client['name']}] {len(pending)} leads pendentes. Comandos: "
        "[y] aprovar, [n] descartar, [e] editar email, [s] sair\n"
    )
    approved = 0
    rejected = 0
    for i, lead in enumerate(pending):
        print(f"\n— {i + 1}/{len(pending)} —")
        print(_fmt(lead))
        while True:
            ans = input("\n[y/n/e/s] > ").strip().lower()
            if ans == "y":
                if not lead.get("email"):
                    print('\x1b[33m! sem email — use "e" para adicionar\x1b[0m')
                    continue
                lead["status"] = "approved"
                approved += 1
                break
            elif ans == "n":
                lead["status"] = "rejected"
                rejected += 1
                break
            elif ans == "e":
                novo = input(f"  novo email (enter mantém \"{lead.get('email')}\"): ").strip()
                if novo:
                    lead["email"] = novo.lower()
                continue
            elif ans == "s":
                save(client["id"], leads)
                print(
                    f"\nSaindo. Aprovados nesta sessão: {approved}, descartados: {rejected}."
                )
                return
            else:
                print("use y/n/e/s")
        save(client["id"], leads)
    print(f"\nRevisão completa. Aprovados: {approved}, descartados: {rejected}.")


def main():
    args = sys.argv[1:]
    if not args:
        usage()
        return

    # extrai --client antes de fazer o split de comando
    client_slug = _pop_flag_value(args, "--client")
    if client_slug:
        client = clients_mod.get_client(client_slug)
        if not client:
            raise SystemExit(
                f"cliente '{client_slug}' não cadastrado. Rode `python cli.py clients` pra listar."
            )
    else:
        client = clients_mod.default_client()
        if not client:
            raise SystemExit(
                "Nenhum cliente cadastrado. Rode a UI primeiro (`python app.py`) "
                "ou rode `python cli.py clients` pra acionar o seed."
            )

    if not args:
        usage()
        return
    cmd, rest = args[0], args[1:]

    if cmd == "clients":
        for c in clients_mod.list_clients():
            tag = " (default)" if c.get("is_default") else ""
            print(f"  {c['id']}{tag}  · {c['name']}  · {c['url']}  · {c['locale']}")
        return

    if cmd == "presets":
        items = presets_mod.list_presets(client["id"])
        if not items:
            print(f"Nenhum preset cadastrado pro {client['name']}.")
            return
        print(f"\n[{client['name']}] {len(items)} presets:\n")
        for p in items:
            print(f"  #{p['id']}  {p['label']}  · {p['term']}  · {len(p['cities'])} cidades")
        return

    if cmd == "preset":
        if not rest:
            raise SystemExit("uso: preset <id>")
        try:
            pid = int(rest[0])
        except ValueError:
            raise SystemExit("preset_id precisa ser um número (use `presets` pra listar)")
        preset = presets_mod.get_preset(pid)
        if not preset or preset["client_id"] != client["id"]:
            raise SystemExit("preset não pertence ao cliente ativo")
        existing = load(client["id"])
        fresh = scrape_cities(
            preset["term"],
            preset["cities"],
            max=preset["max_results"],
            locale=client.get("locale") or "pt-BR",
        )
        print(f"\n{len(fresh)} resultados totais. Buscando emails…")
        enriched = enrich_emails(fresh)
        by_key = {(l.get("website") or l.get("name")): l for l in existing}
        for l in enriched:
            key = l.get("website") or l.get("name")
            if key not in by_key:
                by_key[key] = {
                    **l,
                    "status": "pending",
                    "searchedAs": l.get("searchedAs") or preset["label"],
                }
        save(client["id"], list(by_key.values()))
        print(f"Salvo. Total acumulado no {client['name']}: {len(by_key)}")
        return

    if cmd == "scrape":
        term = rest[0] if len(rest) > 0 else None
        city = rest[1] if len(rest) > 1 else None
        max_n = int(rest[2]) if len(rest) > 2 else 30
        if not term or not city:
            usage()
            sys.exit(1)
        existing = load(client["id"])
        fresh = scrape(term, city, max=max_n, locale=client.get("locale") or "pt-BR")
        print(f"\n{len(fresh)} resultados do Maps. Buscando emails nos sites…")
        enriched = enrich_emails(fresh)
        by_key = {(l.get("website") or l.get("name")): l for l in existing}
        for l in enriched:
            key = l.get("website") or l.get("name")
            if key not in by_key:
                by_key[key] = {**l, "status": "pending", "searchedAs": f"{term} / {city}"}
        save(client["id"], list(by_key.values()))
        print(f"\nSalvo. Total acumulado no {client['name']}: {len(by_key)}")
        return

    if cmd == "reenrich":
        force = "--force" in rest
        leads = load(client["id"])
        targets = [
            l
            for l in leads
            if l.get("status") == "pending" and l.get("website") and (force or not l.get("email"))
        ]
        if not targets:
            print("Nada pra re-enriquecer.")
        else:
            print(f"Re-tentando email em {len(targets)} leads…")
            updated = enrich_emails(targets)
            by_key = {(u.get("website") or u.get("name")): u.get("email") for u in updated}
            for l in leads:
                k = l.get("website") or l.get("name")
                if k in by_key:
                    l["email"] = by_key[k] or l.get("email")
            save(client["id"], leads)
            print("Atualizado.")
        return

    if cmd == "review":
        review(client)
        return

    if cmd == "personalize":
        force = "--force" in rest
        leads = load(client["id"])
        targets = [
            l
            for l in leads
            if l.get("status") == "pending"
            and l.get("website")
            and (force or not l.get("personalizedHook"))
        ]
        if not targets:
            print("Nada pra personalizar.")
        else:
            personalize_leads(client, targets, force=force)
            save(client["id"], leads)
            print("Atualizado.")
        return

    if cmd == "send":
        opts = {}
        if "--limit" in rest:
            try:
                n = int(rest[rest.index("--limit") + 1])
            except (ValueError, IndexError):
                raise SystemExit("--limit precisa de um número > 0")
            if n <= 0:
                raise SystemExit("--limit precisa de um número > 0")
            opts["limit"] = n
        if "--email" in rest:
            try:
                addr = rest[rest.index("--email") + 1]
            except IndexError:
                addr = None
            if not addr or "@" not in addr:
                raise SystemExit("--email precisa de um endereço válido")
            opts["test_email"] = addr
        send(client, **opts)
        return

    if cmd == "followup":
        opts = {}
        if "--limit" in rest:
            try:
                n = int(rest[rest.index("--limit") + 1])
            except (ValueError, IndexError):
                raise SystemExit("--limit precisa de um número > 0")
            if n <= 0:
                raise SystemExit("--limit precisa de um número > 0")
            opts["limit"] = n
        send_followups(client, **opts)
        return

    if cmd == "rescore":
        leads = load(client["id"])
        targets = [l for l in leads if l.get("siteInsights")]
        for l in targets:
            apply_score(l)
        save(client["id"], leads)
        print(f"{len(targets)} leads pontuados.")
        return

    usage()


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"\n[erro] {err}")
        sys.exit(1)
