import { getSetting, setSetting } from "./state.js";

const UPSTAT_URL =
  "https://upstat.online/?utm_source=outreach&utm_medium=email";

export const DEFAULT_TEMPLATE = {
  subject: "monitoramento de uptime pra {{company}}",
  body: `{{opening}}

Sou o Yago, fundador do UpStat. Um SaaS de monitoramento de uptime feito pensando em agências e empresas pequenas que não querem pagar caro nem configurar Datadog pra monitorar 3 sites.

Imaginei que talvez vocês já tenham passado pela cena clássica: cliente avisando no WhatsApp que o site caiu antes da gente perceber. O UpStat resolve isso, alerta no e-mail/Discord/WhatsApp em segundos quando algo cai ou fica lento, com página de status pública que você pode mostrar pro cliente.

Plano grátis cobre uns primeiros sites, sem cartão. Dá uma olhada aqui: {{url}}

Abraço,
Yago

---
Você recebeu este email porque sua empresa apareceu numa busca pública por agências/empresas. Se preferir não receber mais nada, escreva pra {{replyTo}} com "remover" no assunto.`,
};

function cleanCompanyName(name) {
  return (name || "time")
    .replace(/[®™©]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function renderTemplate(template, vars) {
  let out = String(template || "");
  for (const [key, value] of Object.entries(vars)) {
    out = out.replace(
      new RegExp(`{{\\s*${escapeRegExp(key)}\\s*}}`, "g"),
      value ?? "",
    );
  }
  return out;
}

function escapeHtml(s) {
  return String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[c],
  );
}

export function getEmailTemplate() {
  return {
    subject: getSetting("email_template_subject", DEFAULT_TEMPLATE.subject),
    body: getSetting("email_template_body", DEFAULT_TEMPLATE.body),
  };
}

export function saveEmailTemplate({ subject, body }) {
  const cleanSubject = String(subject || "").trim();
  const cleanBody = String(body || "").trim();
  if (!cleanSubject) throw new Error("subject não pode ficar vazio");
  if (!cleanBody) throw new Error("body não pode ficar vazio");
  setSetting("email_template_subject", cleanSubject);
  setSetting("email_template_body", cleanBody);
  return getEmailTemplate();
}

export function buildSubject(name) {
  const cleanName = cleanCompanyName(name);
  return renderTemplate(getEmailTemplate().subject, {
    company: cleanName,
    name: cleanName,
  });
}

function formatStack(siteInsights) {
  return siteInsights?.techStack?.join(", ") || "";
}

function formatPainSignals(siteInsights) {
  return (
    siteInsights?.painSignals
      ?.slice(0, 3)
      .map((signal) => signal.label)
      .join(", ") || ""
  );
}

export function buildEmail({ name, replyTo, personalizedHook, siteInsights }) {
  return buildEmailWithTemplate(getEmailTemplate(), {
    name,
    replyTo,
    personalizedHook,
    siteInsights,
  });
}

export function buildEmailWithTemplate(
  template,
  { name, replyTo, personalizedHook, siteInsights },
) {
  const cleanName = cleanCompanyName(name);
  const subject = renderTemplate(template.subject, {
    company: cleanName,
    name: cleanName,
  });

  const opening = personalizedHook?.trim()
    ? `Oi, time da ${cleanName}! ${personalizedHook.trim()}`
    : `Oi, time da ${cleanName}!`;

  const text = renderTemplate(template.body, {
    company: cleanName,
    name: cleanName,
    hook: personalizedHook?.trim() || "",
    opening,
    replyTo,
    stack: formatStack(siteInsights),
    painSignals: formatPainSignals(siteInsights),
    url: UPSTAT_URL,
  });

  const paragraphs = text.split("\n\n").map((p) => {
    const linked = escapeHtml(p).replace(
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
