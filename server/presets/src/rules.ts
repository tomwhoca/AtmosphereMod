// Community Presets: what a shared preset may hold. Pure functions (no database), tested on their own
// (server/presets/test/rules.test.mjs). The settings and the name rules come from the EFB app's own sources, so the
// server and the app agree on them.

import { presetName } from "../../../efb/PackageSources/AtmoWeather/src/presets";
import { TABS } from "../../../efb/PackageSources/AtmoWeather/src/specs";

export const NAME_MAX = 40;
export const AUTHOR_MAX = 40;
export const DESCRIPTION_MAX = 300;
/** A preset needs at least this many values to be one. */
export const MIN_VALUES = 5;

/** The plausible raw range of each shareable setting: the look only (T and C); not C13-C15 (the aircraft's, set by
 * the add-on) nor R (the weather radar's: personal). 0 is always allowed: for many settings it means "the sim's". */
const RANGES = new Map<string, [number, number]>();
for (const tab of TABS) {
  for (const s of tab.settings) {
    if (s.kind === "switch") {
      RANGES.set(s.key, [0, 1]);
      continue;
    }
    const a = s.toRaw(s.min), b = s.toRaw(s.max);
    const lo = Math.min(a, b), hi = Math.max(a, b);
    // (wider than the app's slider: tunables.ini can be edited by hand beyond it)
    RANGES.set(s.key, [Math.max(0, lo - (hi - lo)), hi + (hi - lo)]);
  }
}
// the settings the app doesn't show (shaders/tunables.defaults.ini)
const HIDDEN: [string, number, number][] = [
  ["T2", 0, 2], ["T5", 0, 20], ["T7", 0, 1], ["T12", 0, 1], ["T13", 0, 1], ["T14", 0, 700], ["T15", 0, 1],
  ["C4", 0, 0.9], ["C7", 0, 1000], ["C11", 0, 0.5], ["C18", 0, 20000], ["C29", 0, 5000], ["C30", 0, 1],
  ["C31", 0, 2000], ["C37", 0, 2],
];
for (const [key, lo, hi] of HIDDEN) {
  if (!RANGES.has(key)) {
    RANGES.set(key, [lo, hi]);
  }
}

export const shareable = (key: string): boolean => RANGES.has(key);

/** "0.10.2" after "0.9.1": by the numbers in it. */
export function versionOrder(a: string, b: string): number {
  const x = a.split(/[^0-9]+/).filter((s) => s).map(Number), y = b.split(/[^0-9]+/).filter((s) => s).map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if ((x[i] || 0) !== (y[i] || 0)) {
      return (x[i] || 0) - (y[i] || 0);
    }
  }
  return a < b ? -1 : a > b ? 1 : 0;
}
export const rangeOf = (key: string): [number, number] | undefined => RANGES.get(key);

/** Words a name, an author or a description may not contain (whole words, any case). Short on purpose: the admin
 * removes the rest (tools/presets_admin.py). */
const BLOCKED = ["fuck", "fucking", "fucker", "shit", "bitch", "cunt", "asshole", "whore", "nazi", "hitler",
                 "wichser", "scheisse", "scheiße", "fotze", "hurensohn", "arschloch", "nutte"];
/** A link, or what looks like one (flightsim.to and the like stay allowed: people name their profile there). */
const LINK = /:\/\/|www\.|\b[a-z0-9-]+\.(com|net|org|io|ru|xyz|info|ly|gg|link|click|shop|top|biz|cn|tk)\b/i;

/** A control character or a line break (the C0 and C1 controls, DEL, the line and paragraph separators). */
function controlChar(ch: string): boolean {
  const c = ch.codePointAt(0) || 0;
  return c < 32 || (c >= 127 && c <= 159) || c === 8232 || c === 8233;
}

/** Why a text can't go public, or "". */
export function textProblem(text: string, what: string, max: number, required: boolean): string {
  if (required && text.trim() === "") {
    return `${what}: needed`;
  }
  if (text.length > max) {
    return `${what}: at most ${max} characters`;
  }
  // (printable only: no control characters, no line breaks)
  if (Array.from(text).some(controlChar)) {
    return `${what}: plain text only`;
  }
  if (LINK.test(text)) {
    return `${what}: no links`;
  }
  const words = text.toLowerCase().split(/[^\p{L}\p{N}]+/u);
  if (words.some((w) => BLOCKED.indexOf(w) >= 0)) {
    return `${what}: please choose other words`;
  }
  return "";
}

export interface Submission {
  name: string;
  author: string;
  description: string;
  version: string;
  values: Record<string, number>;
}

/** A submission checked and cleaned (names trimmed, values rounded to 4 decimals as tunables.ini keeps them), or
 * why not. */
export function checkSubmission(body: unknown): { ok: true; preset: Submission } | { ok: false; error: string } {
  if (!body || typeof body !== "object") {
    return { ok: false, error: "not a preset" };
  }
  const b = body as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" ? v : "");
  const name = presetName(str(b.name));
  if (name === "") {
    return { ok: false, error: `name: letters, digits, spaces and - _ ( ) . , + ', up to ${NAME_MAX} characters` };
  }
  const author = str(b.author).trim();
  const description = str(b.description).trim();
  const version = str(b.version).trim().slice(0, 20);
  for (const [text, what, max, required] of [[name, "name", NAME_MAX, true], [author, "author", AUTHOR_MAX, true],
                                             [description, "description", DESCRIPTION_MAX, false]] as const) {
    const problem = textProblem(text, what, max, required);
    if (problem) {
      return { ok: false, error: problem };
    }
  }
  if (!/^[0-9a-z.+ ()-]*$/i.test(version)) {
    return { ok: false, error: "version: not a version" };
  }
  if (!b.values || typeof b.values !== "object" || Array.isArray(b.values)) {
    return { ok: false, error: "values: none" };
  }
  const values: Record<string, number> = {};
  for (const [key, v] of Object.entries(b.values as Record<string, unknown>)) {
    const range = RANGES.get(key);
    if (!range) {
      continue;  // (not shareable, e.g. the radar's: left out, not an error)
    }
    if (typeof v !== "number" || !isFinite(v)) {
      return { ok: false, error: `${key}: not a number` };
    }
    if (v !== 0 && (v < range[0] - 1e-9 || v > range[1] + 1e-9)) {
      return { ok: false, error: `${key}: ${v} is outside ${range[0]}..${range[1]}` };
    }
    values[key] = Math.round(v * 1e4) / 1e4;
  }
  if (Object.keys(values).length < MIN_VALUES) {
    return { ok: false, error: "values: too few to be a preset" };
  }
  return { ok: true, preset: { name, author, description, version, values } };
}

/** The values in one order and form, for their fingerprint. */
export function canonical(values: Record<string, number>): string {
  return Object.keys(values).sort((a, b) => (a[0] === b[0] ? +a.slice(1) - +b.slice(1) : a < b ? -1 : 1))
    .map((k) => `${k}=${values[k]}`).join(";");
}
