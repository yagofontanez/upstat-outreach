import { consoleProgress } from "./progress.js";

const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;

const PATHS = [
  "",
  "/contato",
  "/contatos",
  "/contact",
  "/contact-us",
  "/sobre",
  "/about",
  "/fale-conosco",
  "/equipe",
  "/quem-somos",
];

const PLAUSIBLE_TLDS = new Set([
  "com",
  "br",
  "net",
  "org",
  "io",
  "co",
  "dev",
  "app",
  "tech",
  "ag",
  "agency",
  "studio",
  "design",
  "digital",
  "me",
  "pt",
  "eu",
  "us",
  "info",
  "biz",
  "tv",
  "cc",
  "xyz",
  "ai",
  "gg",
  "page",
  "site",
]);

const BLOCKLIST = [
  "sentry.io",
  "sentry-next.wixpress.com",
  "wixpress.com",
  "example.com",
  "domain.com",
  "email.com",
  "seudominio.com",
  "yourdomain.com",
  "test.com",
  "mysite.com",
  "website.com",
];

const FETCH_TIMEOUT_MS = 8000;

async function fetchText(url) {
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

function preprocess(html) {
  let s = html
    .replace(/&#64;/gi, "@")
    .replace(/&#0?46;/gi, ".")
    .replace(/&commat;/gi, "@")
    .replace(/&period;/gi, ".")
    .replace(/&amp;/gi, "&")
    .replace(/&nbsp;/gi, " ")
    .replace(/[​-‍﻿­]/g, "")
    .replace(/<wbr\s*\/?>/gi, "")
    .replace(/<!--.*?-->/gs, "");

  s = s
    .replace(/\s*\[\s*at\s*\]\s*/gi, "@")
    .replace(/\s*\(\s*at\s*\)\s*/gi, "@")
    .replace(/\s+at\s+(?=[a-z0-9-]+\s*(?:\[|\()\s*dot)/gi, "@")
    .replace(/\s*\(arroba\)\s*/gi, "@")
    .replace(/\s+arroba\s+/gi, "@")
    .replace(/\s*\[\s*dot\s*\]\s*/gi, ".")
    .replace(/\s*\(\s*dot\s*\)\s*/gi, ".")
    .replace(/\s*\(ponto\)\s*/gi, ".")
    .replace(/\s+ponto\s+/gi, ".");

  return s;
}

function tldOf(email) {
  const parts = email.split("@")[1]?.split(".") || [];
  return parts[parts.length - 1]?.toLowerCase() || "";
}

function rootDomain(host) {
  return host.replace(/^www\./, "").toLowerCase();
}

function isValidEmail(email, siteHost) {
  if (BLOCKLIST.some((b) => email.includes(b))) return false;
  if (/\.(png|jpe?g|gif|svg|webp|woff2?|ttf|ico|css|js)(\?|$)/.test(email))
    return false;
  if (/^[a-f0-9]{16,}@/i.test(email)) return false;
  if (email.length > 80) return false;

  const tld = tldOf(email);
  if (PLAUSIBLE_TLDS.has(tld)) return true;

  if (siteHost) {
    const siteTld = rootDomain(siteHost).split(".").pop();
    if (tld === siteTld) return true;
  }
  return false;
}

function extract(html, siteHost) {
  const processed = preprocess(html);
  const mailtos = new Set();
  const plain = new Set();

  for (const m of processed.matchAll(/mailto:([^"'?\s>&]+)/gi))
    mailtos.add(m[1]);
  for (const m of processed.matchAll(EMAIL_RE)) plain.add(m[0]);

  const clean = (e) => e.toLowerCase().replace(/[.,;:]+$/, "");
  const mailtoList = [...mailtos]
    .map(clean)
    .filter((e) => isValidEmail(e, siteHost));
  const plainList = [...plain]
    .map(clean)
    .filter((e) => isValidEmail(e, siteHost));

  const root = siteHost ? rootDomain(siteHost) : "";
  const sameDomain = (e) =>
    root && (e.endsWith("@" + root) || e.endsWith("." + root));

  return (
    mailtoList.find(sameDomain) ||
    plainList.find(sameDomain) ||
    mailtoList[0] ||
    plainList[0] ||
    ""
  );
}

async function findEmail(website) {
  if (!website) return "";
  let base;
  try {
    base = new URL(website);
  } catch {
    return "";
  }

  for (const path of PATHS) {
    const url = `${base.protocol}//${base.hostname}${path}`;
    const html = await fetchText(url);
    if (!html) continue;
    const email = extract(html, base.hostname);
    if (email) return email;
  }
  return "";
}

export async function enrichEmails(leads, onProgress = consoleProgress) {
  const out = [];
  for (let i = 0; i < leads.length; i++) {
    const l = leads[i];
    const email = await findEmail(l.website);
    onProgress({
      type: "item",
      index: i + 1,
      total: leads.length,
      name: l.name,
      status: email || "(sem email)",
    });
    out.push({ ...l, email });
  }
  return out;
}
