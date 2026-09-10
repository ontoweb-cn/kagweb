import fs from "node:fs";
import path from "node:path";

function listJsonFiles(dir) {
  const out = [];
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) out.push(...listJsonFiles(full));
    else if (ent.isFile() && ent.name.endsWith(".json")) out.push(full);
  }
  return out;
}

function loadJson(p) {
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function flattenKeys(obj, prefix = "") {
  const keys = [];
  if (!obj || typeof obj !== "object") return keys;
  for (const [k, v] of Object.entries(obj)) {
    const next = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) keys.push(...flattenKeys(v, next));
    else keys.push(next);
  }
  return keys;
}

function toRel(p, root) {
  return path.relative(root, p).replaceAll("\\", "/");
}

const webRoot = path.resolve(process.cwd());
const localesRoot = path.join(webRoot, "locales");
const enRoot = path.join(localesRoot, "en");
const zhRoot = path.join(localesRoot, "zh");

if (!fs.existsSync(enRoot) || !fs.existsSync(zhRoot)) {
  console.error(`[i18n:parity] Missing locales roots: ${enRoot} or ${zhRoot}`);
  process.exit(2);
}

const enFiles = listJsonFiles(enRoot).map((p) => toRel(p, enRoot)).sort();
const zhFiles = listJsonFiles(zhRoot).map((p) => toRel(p, zhRoot)).sort();

const missingInZh = enFiles.filter((f) => !zhFiles.includes(f));
const extraInZh = zhFiles.filter((f) => !enFiles.includes(f));

let ok = true;
if (missingInZh.length) {
  ok = false;
  console.error("[i18n:parity] Missing zh files:");
  for (const f of missingInZh) console.error(`- ${f}`);
}
if (extraInZh.length) {
  ok = false;
  console.error("[i18n:parity] Extra zh files:");
  for (const f of extraInZh) console.error(`- ${f}`);
}

for (const rel of enFiles) {
  if (!zhFiles.includes(rel)) continue;
  const enPath = path.join(enRoot, rel);
  const zhPath = path.join(zhRoot, rel);
  const enJson = loadJson(enPath);
  const zhJson = loadJson(zhPath);
  const enKeys = new Set(flattenKeys(enJson));
  const zhKeys = new Set(flattenKeys(zhJson));

  // Locale keys are the English copy itself (keySeparator is disabled), so an
  // entry whose value equals its key is redundant: i18next already returns the
  // key for a missed lookup. Those identity entries are therefore elided from
  // en — shipping them cost 324 KB on every route for text the key already
  // spells out. Only the entries where the English differs from the key (the
  // dotted namespace keys like `codex.oauth.signIn`) are real overrides.
  //
  // That makes the two directions asymmetric, and only one of them is a bug:
  //   - en → zh: an override with no translation is a real defect. Strict.
  //   - zh → en: normal and expected. en expresses a sentence by *naming a key*
  //     with it, so a zh key has no en counterpart by design. Reported as
  //     informational, never fatal.
  const missingKeys = [...enKeys].filter((k) => !zhKeys.has(k)).sort();
  const untrackedInEn = [...zhKeys].filter((k) => !enKeys.has(k)).sort();

  if (missingKeys.length) {
    ok = false;
    console.error(`[i18n:parity] Key mismatch in ${rel}`);
    console.error("  Missing zh keys:");
    for (const k of missingKeys) console.error(`  - ${k}`);
  }
  if (untrackedInEn.length) {
    // Not a failure — just visibility. A spike here means a large zh-only
    // batch landed; that is normal, but a *tiny* count usually means a real
    // key typo in zh, so it is worth seeing in the log.
    console.log(
      `[i18n:parity] ${rel}: ${untrackedInEn.length} zh keys have no en override ` +
        `(expected — the English copy is the key).`,
    );
  }
}

if (!ok) process.exit(1);
console.log("[i18n:parity] OK");
