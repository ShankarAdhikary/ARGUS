// Fails the build if hi.json drifts from en.json: a missing key, an extra key, a lost {placeholder},
// or a _review entry that points at a key that does not exist.
import { readFileSync } from "node:fs";

const load = (n) => JSON.parse(readFileSync(new URL(`../src/i18n/${n}.json`, import.meta.url), "utf8"));
const en = load("en");
const hi = load("hi");

function flatten(tree, prefix = "", out = {}) {
  for (const [k, v] of Object.entries(tree)) {
    if (k.startsWith("_")) continue; // _comment, _review: metadata, not strings
    if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, `${prefix}${k}.`, out);
    else out[`${prefix}${k}`] = v;
  }
  return out;
}

const a = flatten(en);
const b = flatten(hi);
const problems = [];
for (const k of Object.keys(a)) if (!(k in b)) problems.push(`hi.json is missing "${k}"`);
for (const k of Object.keys(b)) if (!(k in a)) problems.push(`hi.json has "${k}" which is not in en.json`);
for (const k of Object.keys(a)) {
  if (!(k in b)) continue;
  if (typeof b[k] !== "string" || b[k].trim() === "") problems.push(`hi.json "${k}" is empty`);
  if (/\[REVIEW\]/.test(b[k])) problems.push(`hi.json "${k}" contains a literal [REVIEW] marker: list the key in _review instead`);
  const ph = (s) => (String(s).match(/\{\w+\}/g) ?? []).sort().join(",");
  if (ph(a[k]) !== ph(b[k])) problems.push(`"${k}": placeholders differ between en and hi`);
}
for (const k of hi._review ?? []) if (!(k in b)) problems.push(`_review lists unknown key "${k}"`);

if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(`i18n ok: ${Object.keys(a).length} keys, ${(hi._review ?? []).length} flagged for review`);
