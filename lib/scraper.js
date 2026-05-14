import { chromium } from "playwright";
import { consoleProgress } from "./progress.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rand = (a, b) => a + Math.random() * (b - a);

export async function scrape({
  term,
  city,
  max = 30,
  onProgress = consoleProgress,
}) {
  const query = `${term} em ${city}`;
  const browser = await chromium.launch({ headless: false });
  const ctx = await browser.newContext({
    locale: "pt-BR",
    viewport: { width: 1280, height: 900 },
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  });
  const page = await ctx.newPage();

  onProgress({ type: "log", message: `Abrindo Google Maps: "${query}"` });
  await page.goto(
    `https://www.google.com/maps/search/${encodeURIComponent(query)}/?hl=pt-BR`,
    {
      waitUntil: "domcontentloaded",
    },
  );

  await page
    .locator(
      'button:has-text("Aceitar tudo"), button:has-text("Aceitar todos")',
    )
    .first()
    .click({ timeout: 3000 })
    .catch(() => {});

  const feed = page.locator('[role="feed"]');
  await feed.waitFor({ timeout: 15000 }).catch(() => {});

  let lastCount = 0,
    stable = 0;
  for (let i = 0; i < 30; i++) {
    const count = await page.locator("a.hfpxzc").count();
    if (count >= max) break;
    if (count === lastCount) stable++;
    else stable = 0;
    if (stable >= 3) break;
    lastCount = count;
    await feed
      .evaluate((el) => el.scrollBy(0, el.scrollHeight))
      .catch(() => {});
    await sleep(rand(900, 1600));
  }

  const links = await page.locator("a.hfpxzc").evaluateAll(
    (els, lim) =>
      els.slice(0, lim).map((a) => ({
        href: a.getAttribute("href"),
        name: a.getAttribute("aria-label") || "",
      })),
    max,
  );
  onProgress({
    type: "log",
    message: `Coletados ${links.length} cards. Abrindo cada painel…`,
  });

  const results = [];
  for (let i = 0; i < links.length; i++) {
    const { name, href } = links[i];
    if (!href) continue;
    try {
      await page.goto(href, { waitUntil: "domcontentloaded" });
      await page
        .locator("h1.DUwDvf, h1")
        .first()
        .waitFor({ timeout: 8000 })
        .catch(() => {});
      await sleep(rand(400, 900));

      const data = await page.evaluate(() => {
        const get = (sel) => document.querySelector(sel);
        const heading = get("h1.DUwDvf, h1")?.textContent?.trim() || "";
        const websiteEl =
          get('a[data-item-id="authority"]') ||
          get('a[aria-label^="Site"]') ||
          get('a[aria-label^="Website"]');
        const website = websiteEl?.getAttribute("href") || "";
        const phoneEl =
          document.querySelector('button[data-item-id^="phone"]') ||
          document.querySelector('[aria-label^="Telefone"]');
        const phone =
          phoneEl
            ?.getAttribute("aria-label")
            ?.replace(/Telefone:\s*/i, "")
            .trim() || "";
        const addrEl = get('button[data-item-id="address"]');
        const address =
          addrEl
            ?.getAttribute("aria-label")
            ?.replace(/Endereço:\s*/i, "")
            .trim() || "";
        return { heading, website, phone, address };
      });

      const result = {
        name: data.heading || name,
        website: cleanWebsite(data.website),
        phone: data.phone,
        address: data.address,
      };
      results.push(result);
      onProgress({
        type: "item",
        index: i + 1,
        total: links.length,
        name: result.name,
        status: result.website ? "site ✓" : "sem site",
      });
    } catch {
      onProgress({
        type: "item",
        index: i + 1,
        total: links.length,
        name,
        status: "falha",
      });
    }
  }

  await browser.close();
  return results;
}

function cleanWebsite(url) {
  if (!url) return "";
  try {
    const u = new URL(url);
    if (u.hostname.includes("google.com")) return "";
    return `${u.protocol}//${u.hostname}${u.pathname.replace(/\/$/, "")}`;
  } catch {
    return url;
  }
}
