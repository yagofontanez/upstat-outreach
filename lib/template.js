const UPSTAT_URL =
  "https://upstat.online/?utm_source=outreach&utm_medium=email";

export function buildEmail({ name, replyTo }) {
  const cleanName = (name || "time")
    .replace(/[®™©]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const subject = `monitoramento de uptime pra ${cleanName}`;

  const text = `Oi, time da ${cleanName}!

Sou o Yago, fundador do UpStat. Um SaaS de monitoramento de uptime feito pensando em agências e empresas pequenas que não querem pagar caro nem configurar Datadog pra monitorar 3 sites.

Vi que vocês têm site no ar e imaginei que talvez já tenham passado pela cena clássica: cliente avisando no WhatsApp que o site caiu antes da gente perceber. O UpStat resolve isso, alerta no e-mail/Discord/WhatsApp em segundos quando algo cai ou fica lento, com página de status pública que você pode mostrar pro cliente.

Plano grátis cobre uns primeiros sites, sem cartão. Dá uma olhada aqui: ${UPSTAT_URL}

Abraço,
Yago

---
Você recebeu este email porque sua empresa apareceu numa busca pública por agências/empresas. Se preferir não receber mais nada, escreva pra ${replyTo} com "remover" no assunto.`;

  const paragraphs = text.split("\n\n").map((p) => {
    const linked = p.replace(
      /(https?:\/\/[^\s)]+)/g,
      '<a href="$1" style="color:#2563eb;text-decoration:underline;">$1</a>',
    );
    return `<p style="margin:0 0 12px 0;line-height:1.5;">${linked.replace(/\n/g, "<br/>")}</p>`;
  });

  return {
    subject,
    text,
    html: `<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:15px;line-height:1.5;max-width:560px;">${paragraphs.join("")}</div>`,
  };
}
