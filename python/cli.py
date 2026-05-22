"""CLI — equivalente a index.js. Comandos: scrape, reenrich, review, personalize, send."""

import os
import sys

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from emails import enrich_emails
from personalize import personalize_leads
from scoring import apply_score
from scraper import scrape
from sender import send, send_followups
from state import load, save


def usage():
    print(
        """
Uso:
  python cli.py scrape "<termo>" "<cidade>" [maxResults=30]
      Ex: python cli.py scrape "agência de marketing" "Curitiba" 40

  python cli.py reenrich [--force]
      Re-tenta extrair email dos sites dos leads pendentes (sem refazer o Maps).
      Com --force, re-tenta também leads que já têm email.

  python cli.py review
      Abre revisão interativa dos leads coletados.

  python cli.py personalize [--force]
      Gera abertura personalizada via Groq (llama-3.3-70b)
      pra cada lead pendente com site. Com --force, regenera tudo.

  python cli.py send [--limit N] [--email <addr>]
      Envia via Resend para os leads aprovados ainda não enviados.
      --limit N         envia só os N primeiros da fila (ex: --limit 10)
      --email <addr>    envia 1 email de TESTE pra esse endereço, sem alterar leads

  python cli.py followup [--limit N]
      Dispara o follow-up pros leads enviados que não responderam, passado o prazo.

  python cli.py rescore
      Recalcula o score de dor de todos os leads que já têm scan de site.
"""
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


def review():
    leads = load()
    pending = [l for l in leads if l.get("status") == "pending"]
    if not pending:
        print("Nada pendente. Rode `python cli.py scrape` primeiro.")
        return

    print(
        f"\n{len(pending)} leads pendentes. Comandos: "
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
                save(leads)
                print(
                    f"\nSaindo. Aprovados nesta sessão: {approved}, descartados: {rejected}."
                )
                return
            else:
                print("use y/n/e/s")
        save(leads)
    print(f"\nRevisão completa. Aprovados: {approved}, descartados: {rejected}.")


def main():
    args = sys.argv[1:]
    if not args:
        usage()
        return
    cmd, rest = args[0], args[1:]

    if cmd == "scrape":
        term = rest[0] if len(rest) > 0 else None
        city = rest[1] if len(rest) > 1 else None
        max_n = int(rest[2]) if len(rest) > 2 else 30
        if not term or not city:
            usage()
            sys.exit(1)
        existing = load()
        fresh = scrape(term, city, max=max_n)
        print(f"\n{len(fresh)} resultados do Maps. Buscando emails nos sites…")
        enriched = enrich_emails(fresh)
        by_key = {(l.get("website") or l.get("name")): l for l in existing}
        for l in enriched:
            key = l.get("website") or l.get("name")
            if key not in by_key:
                by_key[key] = {**l, "status": "pending", "searchedAs": f"{term} / {city}"}
        save(list(by_key.values()))
        print(f"\nSalvo em outreach.sqlite. Total acumulado: {len(by_key)}")

    elif cmd == "reenrich":
        force = "--force" in rest
        leads = load()
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
            save(leads)
            print("Atualizado outreach.sqlite.")

    elif cmd == "review":
        review()

    elif cmd == "personalize":
        force = "--force" in rest
        leads = load()
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
            personalize_leads(targets, force=force)
            save(leads)
            print("Atualizado outreach.sqlite.")

    elif cmd == "send":
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
        send(**opts)

    elif cmd == "followup":
        opts = {}
        if "--limit" in rest:
            try:
                n = int(rest[rest.index("--limit") + 1])
            except (ValueError, IndexError):
                raise SystemExit("--limit precisa de um número > 0")
            if n <= 0:
                raise SystemExit("--limit precisa de um número > 0")
            opts["limit"] = n
        send_followups(**opts)

    elif cmd == "rescore":
        leads = load()
        targets = [l for l in leads if l.get("siteInsights")]
        for l in targets:
            apply_score(l)
        save(leads)
        print(f"{len(targets)} leads pontuados.")

    else:
        usage()


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"\n[erro] {err}")
        sys.exit(1)
