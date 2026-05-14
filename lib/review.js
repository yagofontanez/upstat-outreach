import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { load, save } from "./state.js";

function fmt(l) {
  return [
    `\x1b[1m${l.name}\x1b[0m`,
    `  site:  ${l.website || "-"}`,
    `  email: ${l.email || "\x1b[33m(vazio)\x1b[0m"}`,
    l.phone ? `  tel:   ${l.phone}` : null,
    l.address ? `  end:   ${l.address}` : null,
    `  busca: ${l.searchedAs || "-"}`,
  ]
    .filter(Boolean)
    .join("\n");
}

export async function review() {
  const leads = load();
  const pending = leads.filter((l) => l.status === "pending");

  if (pending.length === 0) {
    console.log("Nada pendente. Rode `node index.js scrape` primeiro.");
    return;
  }

  console.log(
    `\n${pending.length} leads pendentes. Comandos: [y] aprovar, [n] descartar, [e] editar email, [s] sair\n`,
  );
  const rl = readline.createInterface({ input, output });

  let approved = 0,
    rejected = 0;
  try {
    for (let i = 0; i < pending.length; i++) {
      const lead = pending[i];
      console.log(`\n— ${i + 1}/${pending.length} —`);
      console.log(fmt(lead));

      while (true) {
        const ans = (await rl.question("\n[y/n/e/s] > ")).trim().toLowerCase();
        if (ans === "y") {
          if (!lead.email) {
            console.log('\x1b[33m! sem email — use "e" para adicionar\x1b[0m');
            continue;
          }
          lead.status = "approved";
          approved++;
          break;
        } else if (ans === "n") {
          lead.status = "rejected";
          rejected++;
          break;
        } else if (ans === "e") {
          const novo = (
            await rl.question(`  novo email (enter mantém "${lead.email}"): `)
          ).trim();
          if (novo) lead.email = novo.toLowerCase();
          continue;
        } else if (ans === "s") {
          save(leads);
          console.log(
            `\nSaindo. Aprovados nesta sessão: ${approved}, descartados: ${rejected}.`,
          );
          return;
        } else {
          console.log("use y/n/e/s");
        }
      }
      save(leads);
    }
  } finally {
    rl.close();
  }
  console.log(
    `\nRevisão completa. Aprovados: ${approved}, descartados: ${rejected}.`,
  );
}
