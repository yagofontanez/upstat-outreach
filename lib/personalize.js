import Groq from "groq-sdk";
import { consoleProgress } from "./progress.js";

const MODEL = "llama-3.3-70b-versatile";
const FETCH_TIMEOUT_MS = 8000;
const MAX_SIGNAL_CHARS = 1800;

async function fetchHtml(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: ctrl.signal,
      redirect: "follow",
      headers: {
        "User-Agent":
          "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
      },
    });
    if (!res.ok) return "";
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("text") && !ct.includes("html")) return "";
    return await res.text();
  } catch {
    return "";
  } finally {
    clearTimeout(t);
  }
}

function stripTags(s) {
  return s
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function extractSignals(html) {
  const title = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] || "";
  const metaDesc =
    html.match(
      /<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)["']/i,
    )?.[1] ||
    html.match(
      /<meta[^>]+content=["']([^"']+)["'][^>]+name=["']description["']/i,
    )?.[1] ||
    "";
  const ogDesc =
    html.match(
      /<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']+)["']/i,
    )?.[1] || "";
  const h1 = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)?.[1] || "";
  const h2s = [...html.matchAll(/<h2[^>]*>([\s\S]*?)<\/h2>/gi)]
    .slice(0, 3)
    .map((m) => m[1]);
  const ps = [...html.matchAll(/<p[^>]*>([\s\S]*?)<\/p>/gi)]
    .slice(0, 5)
    .map((m) => m[1]);

  const parts = [
    title && `TITLE: ${stripTags(title)}`,
    metaDesc && `DESC: ${stripTags(metaDesc)}`,
    ogDesc && metaDesc !== ogDesc && `OG: ${stripTags(ogDesc)}`,
    h1 && `H1: ${stripTags(h1)}`,
    h2s.length && `H2: ${h2s.map(stripTags).filter(Boolean).join(" | ")}`,
    ps.length && `P: ${ps.map(stripTags).filter(Boolean).join(" · ")}`,
  ].filter(Boolean);

  const signals = parts.join("\n").slice(0, MAX_SIGNAL_CHARS);
  return signals;
}

const SYSTEM = `Você ajuda a personalizar emails de cold outreach em português brasileiro.

Receberá: o nome de uma empresa e trechos do site oficial dela (título, meta, h1, parágrafos).

Sua tarefa: gerar um JSON com dois campos:
- "subject": linha de assunto curta (máx 55 caracteres), específica pro nicho da empresa, sem clickbait, sem caps lock, sem emoji, sem palavras de spam ("grátis", "oportunidade", "urgente"). Pode ser em letras minúsculas, estilo conversacional.
- "hook": 1 frase (máx 220 caracteres) de abertura natural que referencia ESPECIFICAMENTE o que a empresa faz com base nos trechos fornecidos. Tom casual, direto, em primeira pessoa ("vi que vocês..."). Sem elogio genérico ("site bonito"), sem inventar fatos não presentes nos trechos, sem prêmios/clientes não citados.

Regras importantes:
- Se os trechos forem vagos ou insuficientes, escreva um hook neutro mas honesto baseado só no nicho aparente (não invente nada).
- Nunca mencione monitoramento, uptime, SaaS, UpStat ou qualquer produto — isso já está no corpo do email. O hook é só a abertura.
- Responda APENAS com JSON válido, sem markdown, sem comentários. Exemplo: {"subject":"...","hook":"..."}`;

function buildUserPrompt(name, signals) {
  return `Empresa: ${name}

Trechos do site:
${signals || "(site não retornou conteúdo útil)"}

Gere o JSON.`;
}

function sanitize(str, maxLen) {
  return String(str || "")
    .replace(/\s+/g, " ")
    .replace(/^["'`\s]+|["'`\s]+$/g, "")
    .slice(0, maxLen)
    .trim();
}

function parseResponse(content) {
  if (!content) return null;
  const jsonMatch = content.match(/\{[\s\S]*\}/);
  if (!jsonMatch) return null;
  try {
    const obj = JSON.parse(jsonMatch[0]);
    const subject = sanitize(obj.subject, 70);
    const hook = sanitize(obj.hook, 260);
    if (!subject || !hook) return null;
    return { subject, hook };
  } catch {
    return null;
  }
}

let client;
function getClient() {
  if (!process.env.GROQ_API_KEY)
    throw new Error("GROQ_API_KEY ausente no .env");
  if (!client) client = new Groq({ apiKey: process.env.GROQ_API_KEY });
  return client;
}

export async function personalizeLead(lead) {
  const groq = getClient();
  let signals = "";
  if (lead.website) {
    const html = await fetchHtml(lead.website);
    if (html) signals = extractSignals(html);
  }

  const completion = await groq.chat.completions.create({
    model: MODEL,
    temperature: 0.7,
    max_tokens: 300,
    response_format: { type: "json_object" },
    messages: [
      { role: "system", content: SYSTEM },
      { role: "user", content: buildUserPrompt(lead.name, signals) },
    ],
  });

  const content = completion.choices?.[0]?.message?.content || "";
  const parsed = parseResponse(content);
  if (!parsed) throw new Error("resposta inválida do Groq");
  return parsed;
}

export async function personalizeLeads(
  leads,
  { force = false, onProgress = consoleProgress } = {},
) {
  const targets = leads.filter(
    (l) => force || !(l.personalizedHook && l.personalizedSubject),
  );
  if (targets.length === 0) {
    onProgress({ type: "done", message: "Nada pra personalizar." });
    return { ok: 0, fail: 0 };
  }

  onProgress({
    type: "log",
    message: `Personalizando ${targets.length} leads via Groq (${MODEL})…`,
  });

  let ok = 0,
    fail = 0;
  for (let i = 0; i < targets.length; i++) {
    const lead = targets[i];
    try {
      const { subject, hook } = await personalizeLead(lead);
      lead.personalizedSubject = subject;
      lead.personalizedHook = hook;
      lead.personalizedAt = new Date().toISOString();
      ok++;
      onProgress({
        type: "item",
        index: i + 1,
        total: targets.length,
        name: lead.name,
        status: `ok · "${subject.slice(0, 40)}…"`,
      });
    } catch (e) {
      fail++;
      onProgress({
        type: "item",
        index: i + 1,
        total: targets.length,
        name: lead.name,
        status: `falhou: ${e.message}`,
      });
    }
  }
  onProgress({
    type: "done",
    message: `Personalização: ${ok} ok, ${fail} falhas.`,
    ok,
    fail,
  });
  return { ok, fail };
}
