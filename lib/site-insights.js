const FETCH_TIMEOUT_MS = 9000;
const MAX_HTML_CHARS = 900000;

const CRAWL_PATHS = [
  "",
  "/servicos",
  "/serviços",
  "/portfolio",
  "/portifolio",
  "/clientes",
  "/cases",
  "/manutencao",
  "/manutenção",
  "/sobre",
];

const STACK_RULES = [
  { name: "WordPress", patterns: [/wp-content/i, /wp-includes/i, /<meta[^>]+generator[^>]+wordpress/i] },
  { name: "Elementor", patterns: [/elementor/i] },
  { name: "WooCommerce", patterns: [/woocommerce/i, /wc-ajax/i] },
  { name: "Wix", patterns: [/wixstatic/i, /wix-code/i, /wix\.com/i] },
  { name: "Shopify", patterns: [/cdn\.shopify/i, /Shopify\.theme/i, /myshopify/i] },
  { name: "Webflow", patterns: [/webflow\.js/i, /data-wf-page/i, /webflow\.com/i] },
  { name: "Loja Integrada", patterns: [/lojaintegrada/i, /cdn\.awsli\.com\.br/i] },
  { name: "Nuvemshop", patterns: [/nuvemshop/i, /tiendanube/i, /cdn\.nuvemshop/i] },
  { name: "React", patterns: [/react/i, /__REACT_DEVTOOLS_GLOBAL_HOOK__/i] },
  { name: "Next.js", patterns: [/_next\/static/i, /__NEXT_DATA__/i] },
  { name: "Vercel", patterns: [/x-vercel-id/i, /vercel/i] },
  { name: "Cloudflare", patterns: [/cloudflare/i, /cf-ray/i] },
];

function normalizeWebsite(website) {
  if (!website) return null;
  try {
    return new URL(website);
  } catch {
    try {
      return new URL(`https://${website}`);
    } catch {
      return null;
    }
  }
}

function byteLength(s) {
  return Buffer.byteLength(String(s || ""), "utf8");
}

function stripTags(s) {
  return String(s || "")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function titleOf(html) {
  return stripTags(html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] || "");
}

function headerBlob(headers) {
  return [...headers.entries()].map(([k, v]) => `${k}: ${v}`).join("\n");
}

async function fetchPage(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  const started = Date.now();
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
    const responseMs = Date.now() - started;
    const contentType = res.headers.get("content-type") || "";
    const isHtml = contentType.includes("text/html") || contentType.includes("text/");
    const html = isHtml ? (await res.text()).slice(0, MAX_HTML_CHARS) : "";
    return {
      ok: res.ok,
      url,
      finalUrl: res.url,
      redirected: res.redirected,
      status: res.status,
      responseMs,
      bytes: byteLength(html),
      title: titleOf(html),
      contentType,
      headers: headerBlob(res.headers),
      html,
    };
  } catch (err) {
    return {
      ok: false,
      url,
      finalUrl: url,
      redirected: false,
      status: 0,
      responseMs: Date.now() - started,
      bytes: 0,
      title: "",
      contentType: "",
      headers: "",
      html: "",
      error: err.message,
    };
  } finally {
    clearTimeout(timer);
  }
}

function detectStack(pages) {
  const haystack = pages
    .map((p) => `${p.headers}\n${p.html}`)
    .join("\n")
    .slice(0, MAX_HTML_CHARS * 2);
  return STACK_RULES.filter((rule) =>
    rule.patterns.some((pattern) => pattern.test(haystack)),
  ).map((rule) => rule.name);
}

function hasStatusPage(pages) {
  return pages.some((p) => {
    const combined = `${p.finalUrl}\n${p.html}`.toLowerCase();
    return (
      combined.includes("/status") ||
      combined.includes("status page") ||
      combined.includes("página de status") ||
      combined.includes("statuspage")
    );
  });
}

function buildPainSignals(baseUrl, pages) {
  const signals = [];
  const home = pages[0];
  const successful = pages.filter((p) => p.ok);

  if (baseUrl.protocol !== "https:") {
    signals.push({
      key: "no_https",
      severity: "high",
      label: "Site inicial não usa HTTPS",
      detail: "A URL coletada começa com HTTP.",
    });
  }

  if (!home?.ok) {
    signals.push({
      key: "home_unavailable",
      severity: "high",
      label: "Home indisponível",
      detail: home?.error || `status ${home?.status || "sem resposta"}`,
    });
  }

  for (const page of pages) {
    if (page.status >= 500) {
      signals.push({
        key: "server_error",
        severity: "high",
        label: "Erro 5xx encontrado",
        detail: `${page.status} em ${page.url}`,
      });
    } else if (page.status >= 400) {
      signals.push({
        key: "client_error",
        severity: "medium",
        label: "Página relevante quebrada",
        detail: `${page.status} em ${page.url}`,
      });
    }
  }

  if (home?.responseMs > 5000) {
    signals.push({
      key: "very_slow_home",
      severity: "high",
      label: "Home muito lenta",
      detail: `${home.responseMs}ms para responder.`,
    });
  } else if (home?.responseMs > 2500) {
    signals.push({
      key: "slow_home",
      severity: "medium",
      label: "Home lenta",
      detail: `${home.responseMs}ms para responder.`,
    });
  }

  if (home?.bytes > 1200000) {
    signals.push({
      key: "heavy_home",
      severity: "medium",
      label: "Home pesada",
      detail: `${Math.round(home.bytes / 1024)}KB de HTML inicial.`,
    });
  }

  if (home?.redirected) {
    signals.push({
      key: "redirects",
      severity: "low",
      label: "Redirecionamento na home",
      detail: `${home.url} -> ${home.finalUrl}`,
    });
  }

  if (successful.length > 0 && !hasStatusPage(pages)) {
    signals.push({
      key: "no_status_page",
      severity: "low",
      label: "Sem status page aparente",
      detail: "Não encontrei link ou rota de status nas páginas analisadas.",
    });
  }

  return signals;
}

function summarizePages(pages) {
  return pages.map((p) => ({
    path: new URL(p.url).pathname || "/",
    url: p.url,
    finalUrl: p.finalUrl,
    status: p.status,
    ok: p.ok,
    responseMs: p.responseMs,
    bytes: p.bytes,
    title: p.title,
    redirected: p.redirected,
    error: p.error,
  }));
}

export async function analyzeSite(website, { onProgress } = {}) {
  const base = normalizeWebsite(website);
  if (!base) throw new Error("website inválido");

  const pages = [];
  const seen = new Set();
  const paths = CRAWL_PATHS.slice(0, 8);

  for (const path of paths) {
    const url = new URL(path, `${base.protocol}//${base.hostname}`);
    const href = url.toString();
    if (seen.has(href)) continue;
    seen.add(href);
    onProgress?.({ type: "log", message: `checking ${url.pathname || "/"}` });
    const page = await fetchPage(href);
    pages.push(page);
  }

  const techStack = detectStack(pages);
  const painSignals = buildPainSignals(base, pages);
  const successful = pages.filter((p) => p.ok);

  return {
    checkedAt: new Date().toISOString(),
    website: base.toString(),
    techStack,
    painSignals,
    pages: summarizePages(pages),
    summary: {
      pagesChecked: pages.length,
      pagesOk: successful.length,
      homeResponseMs: pages[0]?.responseMs || null,
      homeStatus: pages[0]?.status || 0,
      homeBytes: pages[0]?.bytes || 0,
      hasStatusPage: hasStatusPage(pages),
    },
  };
}
