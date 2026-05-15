import "dotenv/config";
import express from "express";
import session from "express-session";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { scrape } from "./lib/scraper.js";
import { enrichEmails } from "./lib/emails.js";
import { send } from "./lib/sender.js";
import { personalizeLeads } from "./lib/personalize.js";
import { load, save } from "./lib/state.js";
import { runJob, getJob, subscribe } from "./lib/jobs.js";
import { requireAuth, checkPassword } from "./lib/auth.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = parseInt(process.env.PORT || "3000", 10);

if (!process.env.UI_PASSWORD) {
  console.error("Defina UI_PASSWORD no .env antes de subir o servidor.");
  process.exit(1);
}

app.set("view engine", "ejs");
app.set("views", join(__dirname, "views"));
app.use(express.static(join(__dirname, "public")));
app.use(express.urlencoded({ extended: false }));
app.use(express.json());
app.use(
  session({
    secret: process.env.SESSION_SECRET || "dev-secret-change-me",
    resave: false,
    saveUninitialized: false,
    cookie: {
      httpOnly: true,
      sameSite: "lax",
      maxAge: 1000 * 60 * 60 * 24 * 7,
    },
  }),
);

app.get("/login", (req, res) => {
  if (req.session?.authed) return res.redirect("/");
  res.render("login", { error: null });
});

app.post("/login", (req, res) => {
  if (checkPassword(req.body.password)) {
    req.session.authed = true;
    return res.redirect("/");
  }
  res.status(401).render("login", { error: "Senha incorreta." });
});

app.post("/logout", (req, res) => {
  req.session.destroy(() => res.redirect("/login"));
});

app.use(requireAuth);

app.get("/", (req, res) => {
  const leads = load();
  const stats = {
    total: leads.length,
    pending: leads.filter((l) => l.status === "pending").length,
    approved: leads.filter((l) => l.status === "approved" && !l.sentAt).length,
    rejected: leads.filter((l) => l.status === "rejected").length,
    sent: leads.filter((l) => l.status === "sent").length,
    personalizable: leads.filter(
      (l) =>
        l.status === "pending" &&
        l.website &&
        !(l.personalizedHook && l.personalizedSubject),
    ).length,
  };
  res.render("dashboard", { stats });
});

app.get("/scrape", (req, res) => {
  res.render("scrape");
});

app.post("/api/scrape", (req, res) => {
  const { term, city, max } = req.body;
  if (!term || !city)
    return res.status(400).json({ error: "term e city são obrigatórios" });
  const maxN = Math.max(1, Math.min(100, parseInt(max, 10) || 30));

  const job = runJob(async (onProgress) => {
    const existing = load();
    const fresh = await scrape({ term, city, max: maxN, onProgress });
    onProgress({
      type: "log",
      message: `${fresh.length} resultados do Maps. Buscando emails…`,
    });
    const enriched = await enrichEmails(fresh, onProgress);

    const map = new Map(existing.map((l) => [l.website || l.name, l]));
    let added = 0;
    for (const l of enriched) {
      const key = l.website || l.name;
      if (!map.has(key)) {
        map.set(key, {
          ...l,
          status: "pending",
          searchedAs: `${term} / ${city}`,
        });
        added++;
      }
    }
    save([...map.values()]);
    onProgress({
      type: "done",
      message: `Adicionados ${added} novos leads. Total: ${map.size}.`,
      added,
      total: map.size,
    });
  });

  res.json({ jobId: job.id });
});

app.get("/review", (req, res) => {
  const leads = load();
  const pending = leads.filter((l) => l.status === "pending");
  res.render("review", { leads: pending });
});

app.post("/api/leads/:key", (req, res) => {
  const { key } = req.params;
  const { status, email, personalizedSubject, personalizedHook } = req.body;
  const leads = load();
  const lead = leads.find((l) => (l.website || l.name) === key);
  if (!lead) return res.status(404).json({ error: "not found" });
  if (status) lead.status = status;
  if (typeof email === "string") lead.email = email.trim().toLowerCase();
  if (typeof personalizedSubject === "string")
    lead.personalizedSubject = personalizedSubject.trim();
  if (typeof personalizedHook === "string")
    lead.personalizedHook = personalizedHook.trim();
  save(leads);
  res.json({ ok: true, lead });
});

app.post("/api/leads/bulk", (req, res) => {
  const { keys, status } = req.body;
  if (!Array.isArray(keys) || !status)
    return res.status(400).json({ error: "keys e status obrigatórios" });
  const leads = load();
  const set = new Set(keys);
  let count = 0;
  for (const l of leads) {
    if (set.has(l.website || l.name)) {
      l.status = status;
      count++;
    }
  }
  save(leads);
  res.json({ ok: true, count });
});

app.get("/personalize", (req, res) => {
  const leads = load();
  const pendingCount = leads.filter(
    (l) =>
      l.status === "pending" &&
      l.website &&
      !(l.personalizedHook && l.personalizedSubject),
  ).length;
  const doneCount = leads.filter(
    (l) => l.personalizedHook && l.personalizedSubject,
  ).length;
  res.render("personalize", { pendingCount, doneCount });
});

app.post("/api/personalize", (req, res) => {
  const force = !!req.body.force;
  const job = runJob(async (onProgress) => {
    const leads = load();
    const targets = leads.filter(
      (l) =>
        l.status === "pending" &&
        l.website &&
        (force || !(l.personalizedHook && l.personalizedSubject)),
    );
    await personalizeLeads(targets, { force, onProgress });
    save(leads);
  });
  res.json({ jobId: job.id });
});

app.get("/send", (req, res) => {
  const leads = load();
  const approvedCount = leads.filter(
    (l) => l.status === "approved" && !l.sentAt,
  ).length;
  res.render("send", { approvedCount });
});

app.post("/api/send", (req, res) => {
  const { limit, testEmail } = req.body;
  const opts = {};
  if (testEmail) opts.testEmail = String(testEmail).trim();
  if (limit) {
    const n = parseInt(limit, 10);
    if (Number.isFinite(n) && n > 0) opts.limit = n;
  }

  const job = runJob(async (onProgress) => {
    await send({ ...opts, onProgress });
  });

  res.json({ jobId: job.id });
});

app.get("/api/jobs/:id/stream", (req, res) => {
  const job = getJob(req.params.id);
  if (!job) return res.status(404).end();

  res.set({
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  res.flushHeaders?.();

  for (const ev of job.events) {
    res.write(`data: ${JSON.stringify(ev)}\n\n`);
  }
  if (job.done) return res.end();

  const unsub = subscribe(job, (ev) => {
    res.write(`data: ${JSON.stringify(ev)}\n\n`);
    if (ev.type === "done" || ev.type === "fatal") res.end();
  });

  req.on("close", () => unsub());
});

app.listen(PORT, () => {
  console.log(`\n  UpStat outreach UI rodando em http://localhost:${PORT}`);
  console.log(`  Senha definida via UI_PASSWORD no .env\n`);
});
