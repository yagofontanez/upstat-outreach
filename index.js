import "dotenv/config";
import { scrape } from "./lib/scraper.js";
import { enrichEmails } from "./lib/emails.js";
import { review } from "./lib/review.js";
import { send } from "./lib/sender.js";
import { load, save } from "./lib/state.js";

const [cmd, ...args] = process.argv.slice(2);

function usage() {
  console.log(`
Uso:
  node index.js scrape "<termo>" "<cidade>" [maxResults=30]
      Ex: node index.js scrape "agência de marketing" "Curitiba" 40

  node index.js reenrich [--force]
      Re-tenta extrair email dos sites dos leads pendentes (sem refazer o Maps).
      Com --force, re-tenta também leads que já têm email.

  node index.js review
      Abre revisão interativa dos leads coletados.

  node index.js send [--limit N] [--email <addr>]
      Envia via Resend para os leads aprovados ainda não enviados.
      --limit N         envia só os N primeiros da fila (ex: --limit 10)
      --email <addr>    envia 1 email de TESTE pra esse endereço, sem mexer no leads.json
`);
}

try {
  if (cmd === "scrape") {
    const [term, city, max = "30"] = args;
    if (!term || !city) {
      usage();
      process.exit(1);
    }

    const existing = load();
    const fresh = await scrape({ term, city, max: parseInt(max, 10) });
    console.log(
      `\n${fresh.length} resultados do Maps. Buscando emails nos sites…`,
    );
    const enriched = await enrichEmails(fresh);

    const map = new Map(existing.map((l) => [l.website || l.name, l]));
    for (const l of enriched) {
      const key = l.website || l.name;
      if (!map.has(key))
        map.set(key, {
          ...l,
          status: "pending",
          searchedAs: `${term} / ${city}`,
        });
    }
    save([...map.values()]);
    console.log(`\nSalvo em leads.json. Total acumulado: ${map.size}`);
  } else if (cmd === "reenrich") {
    const force = args.includes("--force");
    const leads = load();
    const targets = leads.filter(
      (l) => l.status === "pending" && l.website && (force || !l.email),
    );
    if (targets.length === 0) {
      console.log("Nada pra re-enriquecer.");
    } else {
      console.log(`Re-tentando email em ${targets.length} leads…`);
      const updated = await enrichEmails(targets);
      const byKey = new Map(updated.map((u) => [u.website || u.name, u.email]));
      for (const l of leads) {
        const k = l.website || l.name;
        if (byKey.has(k)) l.email = byKey.get(k) || l.email;
      }
      save(leads);
      console.log("Atualizado leads.json.");
    }
  } else if (cmd === "review") {
    await review();
  } else if (cmd === "send") {
    const opts = {};
    const limitIdx = args.indexOf("--limit");
    if (limitIdx >= 0) {
      const n = parseInt(args[limitIdx + 1], 10);
      if (!Number.isFinite(n) || n <= 0)
        throw new Error("--limit precisa de um número > 0");
      opts.limit = n;
    }
    const emailIdx = args.indexOf("--email");
    if (emailIdx >= 0) {
      const addr = args[emailIdx + 1];
      if (!addr || !addr.includes("@"))
        throw new Error("--email precisa de um endereço válido");
      opts.testEmail = addr;
    }
    await send(opts);
  } else {
    usage();
  }
} catch (err) {
  console.error("\n[erro]", err.message);
  process.exit(1);
}
