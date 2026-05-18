import { existsSync, readFileSync } from "node:fs";

const emitWarning = process.emitWarning;
process.emitWarning = (warning, type, ...args) => {
  if (type === "ExperimentalWarning" && String(warning).includes("SQLite")) {
    return;
  }
  return emitWarning.call(process, warning, type, ...args);
};
const { DatabaseSync } = await import("node:sqlite");
process.emitWarning = emitWarning;

const LEGACY_FILE = "leads.json";
const DB_FILE = "outreach.sqlite";

const db = new DatabaseSync(DB_FILE);

db.exec(`
  CREATE TABLE IF NOT EXISTS leads (
    key TEXT PRIMARY KEY,
    name TEXT,
    website TEXT,
    email TEXT,
    status TEXT,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

  CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
  CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);

  CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
`);

function keyOf(lead) {
  return lead.website || lead.name;
}

function loadLegacyJson() {
  if (!existsSync(LEGACY_FILE)) return [];
  try {
    const parsed = JSON.parse(readFileSync(LEGACY_FILE, "utf8"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function migrateLegacyIfNeeded() {
  const row = db.prepare("SELECT COUNT(*) AS count FROM leads").get();
  if (row.count > 0) return;

  const legacy = loadLegacyJson().filter((lead) => keyOf(lead));
  if (legacy.length === 0) return;
  save(legacy);
}

export function load() {
  migrateLegacyIfNeeded();
  return db
    .prepare("SELECT data FROM leads ORDER BY rowid")
    .all()
    .map((row) => JSON.parse(row.data));
}

export function save(leads) {
  const replace = db.prepare(`
    INSERT INTO leads (key, name, website, email, status, data, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(key) DO UPDATE SET
      name = excluded.name,
      website = excluded.website,
      email = excluded.email,
      status = excluded.status,
      data = excluded.data,
      updated_at = CURRENT_TIMESTAMP
  `);
  const removeMissing = db.prepare(
    "DELETE FROM leads WHERE key NOT IN (SELECT value FROM json_each(?))",
  );

  db.exec("BEGIN");
  try {
    const keys = [];
    for (const lead of leads) {
      const key = keyOf(lead);
      if (!key) continue;
      keys.push(key);
      replace.run(
        key,
        lead.name || "",
        lead.website || "",
        lead.email || "",
        lead.status || "",
        JSON.stringify(lead),
      );
    }
    removeMissing.run(JSON.stringify(keys));
    db.exec("COMMIT");
  } catch (err) {
    db.exec("ROLLBACK");
    throw err;
  }
}

export function getSetting(key, fallback = null) {
  const row = db.prepare("SELECT value FROM settings WHERE key = ?").get(key);
  return row ? row.value : fallback;
}

export function setSetting(key, value) {
  db.prepare(`
    INSERT INTO settings (key, value, updated_at)
    VALUES (?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(key) DO UPDATE SET
      value = excluded.value,
      updated_at = CURRENT_TIMESTAMP
  `).run(key, String(value));
}
