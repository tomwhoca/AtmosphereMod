// Community Presets: the shared presets on the mod's server (server/presets, a Cloudflare Worker with a D1
// database). Listing sends nothing but the request (a page of them, sorted, filtered by the version they were made
// with, searched); sharing sends the preset's look (T and C values), its name, the author and description typed, and
// the mod's version. A share's edit key (the server's answer) stays with this PC: it updates or unshares that preset,
// and nothing else can. Bodies as text/plain JSON (no CORS preflight in the tablet's browser); every answer is
// readable JSON {ok, ...}. No SDK: the UI module and the tests use it.

import { DEFAULTS } from "./defaults";
import { settingsInWords } from "./presets";
import { TABS } from "./specs";

export const COMMUNITY_URL = "https://atmospheremod-presets.tomwhoca.workers.dev";
/** A request gives up after this long. */
const TIMEOUT_MS = 15000;

export interface SharedPreset {
  id: string;
  name: string;
  author: string;
  description: string;
  version: string;
  created: number;
  /** When its owner last changed it (0: never). */
  updated?: number;
  downloads: number;
  values: Record<string, number>;
}

export type SortBy = "new" | "added" | "name";
/** The sorts in the app's order, as it names them. */
export const SORTS: readonly { by: SortBy; label: string }[] = [
  { by: "new", label: "Newest" }, { by: "added", label: "Most added" }, { by: "name", label: "A\u2013Z" },
];
/** A page of the list holds this many. */
export const PER_PAGE = 10;
/** A search: at most this long (the server's limit). */
export const QUERY_MAX = 60;

export interface ListQuery {
  sort: SortBy;
  /** Only those made with these versions (none: all). */
  versions: string[];
  /** In the name, author or description, any case ("" for all). */
  q: string;
  /** From 1. */
  page: number;
}

export interface ListPage {
  presets: SharedPreset[];
  /** How many match, on every page. */
  total: number;
  page: number;
  per: number;
}

export interface VersionCount {
  version: string;
  count: number;
  /** The settings its presets have (all of them together). */
  keys: string[];
}


export interface NewPreset {
  name: string;
  author: string;
  description: string;
  version: string;
  values: Record<string, number>;
}

/** A request's answer: its value, or the server's words (and its status: 403 not the owner, 404 gone). */
export type Answer<T> = { ok: true; value: T } | { ok: false; error: string; status?: number };

/** A share's answer: its id, and the edit key that updates or unshares it. */
export interface Shared {
  id: string;
  key: string;
}

/** A preset shared from this PC, as the app keeps it (MY_SHARES_KEY): the server's id and edit key, the name and
 * texts it was shared with, the user's own preset it came from, and when. */
export interface MyShare {
  id: string;
  key: string;
  name: string;
  author: string;
  description: string;
  /** The user's preset it was shared (last) from. */
  local: string;
  at: number;
}
/** Where the app keeps them (the host's stored data, as JSON {id: MyShare}). */
export const MY_SHARES_KEY = "atmo-ui-shared";

/** The presets shared from this PC, read safely from what's stored. */
export function mySharesOf(stored: string | undefined): Record<string, MyShare> {
  const out: Record<string, MyShare> = {};
  try {
    const v = JSON.parse(stored || "{}") as Record<string, Record<string, unknown>>;
    for (const id of Object.keys(v)) {
      const s = v[id] || {};
      const text = (x: unknown) => (typeof x === "string" ? x : "");
      if (typeof s.key === "string" && s.key && text(s.id) === id) {
        out[id] = { id, key: s.key, name: text(s.name), author: text(s.author), description: text(s.description),
                    local: text(s.local), at: typeof s.at === "number" ? s.at : 0 };
      }
    }
  } catch (e) {
    return {};
  }
  return out;
}

/** How the app reaches Community Presets (the harness: a stand-in, so nothing reaches the server). */
export interface CommunityApi {
  list(query: ListQuery): Promise<Answer<ListPage>>;
  /** The versions the presets were made with, the most used first. */
  versions(): Promise<Answer<VersionCount[]>>;
  share(preset: NewPreset): Promise<Answer<Shared>>;
  /** The owner's new version (the edit key): name, author, description, version and values. */
  update(id: string, key: string, preset: NewPreset): Promise<Answer<true>>;
  /** Out of the list (the edit key). */
  unshare(id: string, key: string): Promise<Answer<true>>;
  added(id: string): Promise<void>;
}

/** The list's address for a query (what's left at its default is left out). */
export function listPath(query: ListQuery): string {
  const parts = [`sort=${query.sort}`, `page=${Math.max(1, Math.floor(query.page))}`, `per=${PER_PAGE}`];
  if (query.versions.length) {
    parts.push(`version=${query.versions.map(encodeURIComponent).join(",")}`);
  }
  const q = query.q.trim().slice(0, QUERY_MAX);
  if (q) {
    parts.push(`q=${encodeURIComponent(q)}`);
  }
  return `/v1/presets?${parts.join("&")}`;
}

/** How many pages `total` makes (at least 1). */
export function pageCount(total: number, per: number): number {
  return Math.max(1, Math.ceil(total / Math.max(1, per)));
}

/** The list as the server makes it, from presets in memory (the harness's stand-in and the tests). */
export function queryPresets(all: SharedPreset[], query: ListQuery, per = PER_PAGE): ListPage {
  const q = query.q.trim().slice(0, QUERY_MAX).toLowerCase();
  const found = all.filter((p) => (!query.versions.length || query.versions.indexOf(p.version) >= 0) && (!q
    || [p.name, p.author, p.description].some((t) => t.toLowerCase().indexOf(q) >= 0)));
  const newest = (a: SharedPreset, b: SharedPreset) => b.created - a.created;
  const order: Record<SortBy, (a: SharedPreset, b: SharedPreset) => number> = {
    new: newest,
    added: (a, b) => b.downloads - a.downloads || newest(a, b),
    name: (a, b) => (a.name.toLowerCase() < b.name.toLowerCase() ? -1 : a.name.toLowerCase() > b.name.toLowerCase()
      ? 1 : newest(a, b)),
  };
  found.sort(order[query.sort] || newest);
  const page = Math.max(1, Math.floor(query.page));
  return { presets: found.slice((page - 1) * per, page * per), total: found.length, page, per };
}

async function call<T>(method: string, path: string, body: unknown, pick: (data: Record<string, unknown>) => T)
    : Promise<Answer<T>> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("no answer")), TIMEOUT_MS);
    });
    const response = await Promise.race([
      fetch(`${COMMUNITY_URL}${path}`, body === undefined ? { method }
        : { method, headers: { "Content-Type": "text/plain;charset=utf-8" }, body: JSON.stringify(body) }),
      timeout,
    ]);
    const data = (await response.json()) as Record<string, unknown>;
    if (data && data.ok === true) {
      return { ok: true, value: pick(data) };
    }
    return { ok: false, error: typeof data.error === "string" ? data.error : `the server answered ${response.status}`,
             status: response.status };
  } catch (e) {
    return { ok: false, error: "Community Presets can't be reached right now" };
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  }
}

export const WEB_COMMUNITY: CommunityApi = {
  list: (query) => call("GET", listPath(query), undefined, (d) => ({
    presets: (d.presets as SharedPreset[]) || [],
    total: Number(d.total) || 0, page: Number(d.page) || 1, per: Number(d.per) || PER_PAGE,
  })),
  versions: () => call("GET", "/v1/versions", undefined, (d) => ((d.versions as VersionCount[]) || []).map((v) => ({
    version: String(v.version), count: Number(v.count) || 0, keys: Array.isArray(v.keys) ? v.keys.map(String) : [],
  }))),
  share: (preset) => call("POST", "/v1/presets", preset, (d) => ({ id: String(d.id), key: String(d.key || "") })),
  update: (id, key, preset) => call("POST", `/v1/presets/${encodeURIComponent(id)}/update`, { ...preset, key },
                                    () => true as const),
  unshare: (id, key) => call("POST", `/v1/presets/${encodeURIComponent(id)}/unshare`, { key }, () => true as const),
  added: async (id) => {
    await call("POST", `/v1/presets/${encodeURIComponent(id)}/download`, {}, () => true);
  },
};

// ---- versions: which have the same settings as the one installed

/** The settings a preset of this version shares: the look (T and C), not C13-C15 (the aircraft's) nor R (the
 * weather radar's), as the add-on's export and the server do. */
export const OWN_KEYS: readonly string[] = Object.keys(DEFAULTS).filter((k) => {
  const m = /^([TC])(\d+)$/.exec(k);
  return !!m && (m[1] === "T" || +m[2] < 13 || +m[2] > 15);
});

export interface SettingsDiff {
  /** Settings this version has that the preset (or version) doesn't. */
  lacks: string[];
  /** Settings it has that this version doesn't know. */
  extra: string[];
}

/** How a preset's (or a version's) settings differ from this version's. */
export function settingsDiff(keys: readonly string[]): SettingsDiff {
  const theirs: Record<string, boolean> = {};
  keys.forEach((k) => { theirs[k] = true; });
  const mine: Record<string, boolean> = {};
  OWN_KEYS.forEach((k) => { mine[k] = true; });
  return { lacks: OWN_KEYS.filter((k) => !theirs[k]), extra: keys.filter((k) => !mine[k]) };
}

export const sameSettings = (d: SettingsDiff): boolean => d.lacks.length === 0 && d.extra.length === 0;

/** The versions with the same settings as this one. */
export const compatibleVersions = (versions: readonly VersionCount[]): string[] =>
  versions.filter((v) => sameSettings(settingsDiff(v.keys))).map((v) => v.version);

const settingsCount = (n: number) => `${n} setting${n === 1 ? "" : "s"}`;

/** A version's difference in a few words, for the filter ("" when it has the same settings). */
export function diffShort(d: SettingsDiff): string {
  return [d.lacks.length ? `lacks ${settingsCount(d.lacks.length)} yours has` : "",
          d.extra.length ? `${settingsCount(d.extra.length)} yours doesn't have` : ""].filter((x) => x).join(", ");
}

/** A preset's difference in a sentence, under it ("" when it has the same settings). */
export function diffNote(d: SettingsDiff): string {
  return [d.lacks.length ? `Lacks ${settingsCount(d.lacks.length)} your version has (${settingsInWords(d.lacks)}): `
    + `adding it sets ${d.lacks.length === 1 ? "it" : "them"} to the mod's defaults.` : "",
          d.extra.length ? `Has ${settingsCount(d.extra.length)} your version doesn't know: adding it leaves `
    + `${d.extra.length === 1 ? "it" : "them"} out.` : ""].filter((x) => x).join(" ");
}

/** The settings the app shows, by key, in its pages' order. */
const SPECS = TABS.reduce<{ key: string; label: string; text: (raw: number) => string }[]>((all, tab) => {
  for (const s of tab.settings) {
    all.push({
      key: s.key, label: s.label,
      text: s.kind === "switch" ? (raw) => (raw > 0 ? "on" : "off") : (raw) => s.format(s.toDisplay(raw)),
    });
  }
  return all;
}, []);

/** What a preset changes from the mod's shipping defaults, in the settings' own words and units: one line each
 * ("Solid clouds: 1.50"), in the app's order, and how many it changes that the app doesn't show. */
export function changesFromDefault(values: Record<string, number>): { lines: string[]; hidden: number } {
  const differs = (key: string) => values[key] !== undefined && DEFAULTS[key] !== undefined
    && Math.abs(values[key] - DEFAULTS[key]) > 1e-6;
  const lines = SPECS.filter((s) => differs(s.key)).map((s) => `${s.label}: ${s.text(values[s.key])}`);
  const shown = new Set(SPECS.map((s) => s.key));
  const hidden = Object.keys(values).filter((k) => !shown.has(k) && differs(k)).length;
  return { lines, hidden };
}

/** What adding and loading a shared preset would change in the settings as they are (`now`), in the settings' own
 * words and units: one line each ("Solid clouds: 1.50 (now 0.80)"), in the app's order, and how many it changes that
 * the app doesn't show. Settings it lacks count at the mod's defaults (adding it fills them so); ones this version
 * doesn't know are left out. `was` names what it's compared with ("now"; "default" before the add-on has answered). */
export function changesFromCurrent(values: Record<string, number>, now: (key: string) => number, was = "now")
    : { lines: string[]; hidden: number } {
  const own: Record<string, boolean> = {};
  OWN_KEYS.forEach((k) => { own[k] = true; });
  const after = (k: string) => (values[k] !== undefined ? values[k] : DEFAULTS[k]);
  // (the app's settings: different as shown, not by a float's last digit)
  const lines = SPECS.filter((s) => own[s.key] && s.text(after(s.key)) !== s.text(now(s.key)))
    .map((s) => `${s.label}: ${s.text(after(s.key))} (${was} ${s.text(now(s.key))})`);
  const shown = new Set(SPECS.map((s) => s.key));
  const hidden = OWN_KEYS.filter((k) => !shown.has(k)
    && Math.abs(after(k) - now(k)) > 1e-6 * Math.max(1, Math.abs(after(k)))).length;
  return { lines, hidden };
}
