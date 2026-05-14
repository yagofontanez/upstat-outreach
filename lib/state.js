import { readFileSync, writeFileSync, existsSync } from "node:fs";

const FILE = "leads.json";

export function load() {
  if (!existsSync(FILE)) return [];
  try {
    return JSON.parse(readFileSync(FILE, "utf8"));
  } catch {
    return [];
  }
}

export function save(leads) {
  writeFileSync(FILE, JSON.stringify(leads, null, 2));
}
