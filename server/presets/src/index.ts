// Community Presets: the AtmosphereMod EFB app's shared presets, a Cloudflare Worker with a D1 database
// (notes/presets-library.md). Public at once; the admin hides or removes what doesn't belong (tools/presets_admin.py).
//
//   GET  /v1/presets?sort=&version=&q=&page=&per=   the public presets with their values, a page of them:
//                                    sort new | added | name; version: only those made with it (or with
//                                    any of several: 0.8.0,0.7.5); q: in the name, author or description; per page
//                                    1-50 (10)
//   GET  /v1/versions                the versions public presets were made with, the newest first: how many each,
//                                    and the settings they have
//   POST /v1/presets                 share one: {name, author, description, version, values} (text/plain JSON);
//                                    answers {id, key}: the key updates or unshares it, and only it can
//   POST /v1/presets/:id/update      {key, name, author, description, version, values}: the owner's new version
//   POST /v1/presets/:id/unshare     {key}: out of the list (status unshared; the admin still sees it)
//   POST /v1/presets/:id/download    count an "Add to my presets"
//   GET  /v1/admin/presets?status=   (Authorization: Bearer ADMIN_TOKEN) hidden | public | unshared | removed | all
//   POST /v1/admin/presets/:id       {action: restore | hide | remove | edit, name?, description?}
//
// No IP address is stored: for the share limit a client is a keyed hash of its address and the day (HMAC with the
// secret SALT). Bodies are text/plain JSON, so the tablet's browser sends them without a CORS preflight. The lists
// aren't cached (no-store): a preset shared shows at once.

import { canonical, checkSubmission, DESCRIPTION_MAX, NAME_MAX, textProblem, versionOrder } from "./rules";

interface D1Result<T> {
  results: T[];
}
interface D1Statement {
  bind(...values: unknown[]): D1Statement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
  run(): Promise<unknown>;
}
interface D1Database {
  prepare(sql: string): D1Statement;
  batch(statements: D1Statement[]): Promise<unknown>;
}
export interface Env {
  DB: D1Database;
  SALT: string;
  ADMIN_TOKEN: string;
}

/** Shares per client and day. */
const SUBMISSIONS_PER_DAY = 5;
/** Updates per client and day (an owner changing their shared presets). */
const UPDATES_PER_DAY = 20;
/** A share's edit key: this many characters from the id's alphabet. */
const KEY_LENGTH = 24;
/** What no longer counts as shared (for the same-settings check, and for an owner's changes). */
const GONE = "('removed', 'unshared')";
/** A page of the list: this many by default, at most PER_MAX. */
const PER_DEFAULT = 10;
const PER_MAX = 50;
/** A search: at most this long. */
const QUERY_MAX = 60;
/** The versions listed, and asked for in one list. */
const VERSIONS_MAX = 30;
const SORTS: Record<string, string> = {
  new: "created DESC",
  added: "downloads DESC, created DESC",
  name: "name COLLATE NOCASE ASC, created DESC",
};
/** The lists: never from a cache, so a share shows at once. */
const FRESH = { "Cache-Control": "no-store" };
/** The largest body taken. */
const BODY_MAX = 16 * 1024;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Access-Control-Max-Age": "86400",
};

function json(data: unknown, status = 200, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), {
    status, headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...extra },
  });
}
const fail = (error: string, status = 400) => json({ ok: false, error }, status);

async function hmac(secret: string, text: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret || "local"),
                                            { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(text));
  return Array.from(new Uint8Array(sig)).slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The client for the share limit: a keyed hash of its address and the day (so it can't be followed across days). */
function clientKey(env: Env, req: Request, day: string): Promise<string> {
  const ip = req.headers.get("CF-Connecting-IP") || "local";
  return hmac(env.SALT, `${ip}|${day}`);
}

function randomText(length: number): string {
  const alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}
const newId = (): string => randomText(10);

/** An edit key's keyed hash: what the database keeps of it. */
const ownerHash = (env: Env, key: string): Promise<string> => hmac(env.SALT, `owner|${key}`);

/** Two texts the same, compared in constant time. */
function sameText(a: string, b: string): boolean {
  if (a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

async function bodyOf(req: Request): Promise<unknown> {
  const text = await req.text();
  if (text.length > BODY_MAX) {
    throw new Error("too large");
  }
  return JSON.parse(text);
}

interface Row {
  id: string; name: string; author: string; description: string; version: string; vals: string;
  status: string; created: number; downloads: number; owner_hash: string; updated: number;
}
const publicView = (r: Row) => ({
  id: r.id, name: r.name, author: r.author, description: r.description, version: r.version,
  created: r.created, updated: r.updated, downloads: r.downloads, values: JSON.parse(r.vals),
});

async function listPublic(env: Env, url: URL): Promise<Response> {
  const p = url.searchParams;
  const sort = SORTS[p.get("sort") || "new"] || SORTS.new;
  const per = Math.min(PER_MAX, Math.max(1, parseInt(p.get("per") || "", 10) || PER_DEFAULT));
  const page = Math.max(1, parseInt(p.get("page") || "", 10) || 1);
  const where = ["status = 'public'"];
  const binds: unknown[] = [];
  // (one version, or several separated by commas: the app's "compatible with yours")
  const wanted = (p.get("version") || "").split(",").map((v) => v.trim().slice(0, 20)).filter((v) => v && v !== "all")
    .slice(0, VERSIONS_MAX);
  if (wanted.length) {
    where.push(`version IN (${wanted.map(() => "?").join(", ")})`);
    binds.push(...wanted);
  }
  const q = (p.get("q") || "").trim().slice(0, QUERY_MAX);
  if (q) {
    // (LIKE ignores case for ASCII; % _ and the escape itself are taken literally)
    const like = `%${q.replace(/[\\%_]/g, (c) => `\\${c}`)}%`;
    where.push("(name LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')");
    binds.push(like, like, like);
  }
  const clause = where.join(" AND ");
  const total = await env.DB.prepare(`SELECT COUNT(*) AS n FROM presets WHERE ${clause}`).bind(...binds)
    .first<{ n: number }>();
  const rows = await env.DB.prepare(`SELECT * FROM presets WHERE ${clause} ORDER BY ${sort} LIMIT ? OFFSET ?`)
    .bind(...binds, per, (page - 1) * per).all<Row>();
  return json({ ok: true, presets: rows.results.map(publicView), total: total ? total.n : 0, page, per }, 200,
              FRESH);
}

/** The versions list made again into the summary table (one row, 'versions'): run in the same batch as every change
 * that can alter it (share, update, unshare, the admin's restore, hide, remove), so it's never behind. Making it reads
 * every public preset's every value (json_each: about 1,200 rows with a dozen presets); a GET /v1/versions only reads
 * the one row (migrations/0005_summary.sql: it read all of them on each call, 97% of the database's reads). */
const VERSIONS_REFRESH =
  "INSERT INTO summary (name, body, updated) "
  + "SELECT 'versions', json_group_array(json_object('version', v.version, 'count', v.count, 'keys', json(v.keys))), ? "
  + "FROM (SELECT p.version AS version, COUNT(DISTINCT p.id) AS count, json_group_array(DISTINCT j.key) AS keys "
  + "FROM presets p, json_each(p.vals) j WHERE p.status = 'public' AND p.version != '' GROUP BY p.version) v "
  + "WHERE true ON CONFLICT (name) DO UPDATE SET body = excluded.body, updated = excluded.updated";
const refreshVersions = (env: Env): D1Statement => env.DB.prepare(VERSIONS_REFRESH).bind(Date.now());

/** The versions public presets were made with, the newest first: how many each, and the settings their presets have
 * (all of them together), so the app can tell which have the same settings as its own. From the summary table. */
async function versions(env: Env): Promise<Response> {
  const kept = () => env.DB.prepare("SELECT body FROM summary WHERE name = 'versions'").first<{ body: string }>();
  let row = await kept();
  if (!row) {
    await refreshVersions(env).run();
    row = await kept();
  }
  const rows = JSON.parse(row ? row.body : "[]") as { version: string; count: number; keys: string[] }[];
  const out = rows.map((r) => ({ version: r.version, count: r.count, keys: [...r.keys].sort() }))
    .sort((a, b) => versionOrder(b.version, a.version)).slice(0, VERSIONS_MAX);
  return json({ ok: true, versions: out }, 200, FRESH);
}

async function share(env: Env, req: Request): Promise<Response> {
  let body: unknown;
  try {
    body = await bodyOf(req);
  } catch {
    return fail("not a preset");
  }
  const checked = checkSubmission(body);
  if (!checked.ok) {
    return fail(checked.error);
  }
  const p = checked.preset;
  const day = new Date().toISOString().slice(0, 10);
  const client = await clientKey(env, req, day);
  const used = await env.DB.prepare("SELECT count FROM submissions WHERE client = ? AND day = ?").bind(client, day)
    .first<{ count: number }>();
  if (used && used.count >= SUBMISSIONS_PER_DAY) {
    return fail(`at most ${SUBMISSIONS_PER_DAY} presets a day: please try again tomorrow`, 429);
  }
  const valsHash = await hmac("values", canonical(p.values));
  const same = await env.DB.prepare(`SELECT name FROM presets WHERE vals_hash = ? AND status NOT IN ${GONE} LIMIT 1`)
    .bind(valsHash).first<{ name: string }>();
  if (same) {
    return fail(`the same settings are shared already, as "${same.name}"`, 409);
  }
  const id = newId();
  const key = randomText(KEY_LENGTH);
  await env.DB.batch([
    env.DB.prepare("INSERT INTO presets (id, name, author, description, version, vals, vals_hash, created, owner_hash) "
                   + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(id, p.name, p.author, p.description, p.version, JSON.stringify(p.values), valsHash, Date.now(),
            await ownerHash(env, key)),
    env.DB.prepare("INSERT INTO submissions (client, day, count) VALUES (?, ?, 1) "
                   + "ON CONFLICT (client, day) DO UPDATE SET count = count + 1").bind(client, day),
    refreshVersions(env),
  ]);
  return json({ ok: true, id, key });
}

/** The owner's preset, by its edit key: its row, or why not (404 gone, 403 not the key). */
async function owned(env: Env, id: string, key: unknown): Promise<{ status: string } | Response> {
  const row = await env.DB.prepare("SELECT status, owner_hash FROM presets WHERE id = ?").bind(id)
    .first<{ status: string; owner_hash: string }>();
  if (!row || row.status === "removed" || row.status === "unshared") {
    return fail("no such preset", 404);
  }
  if (typeof key !== "string" || !key || !row.owner_hash || !sameText(row.owner_hash, await ownerHash(env, key))) {
    return fail("only the PC that shared it can change it", 403);
  }
  return row;
}

/** The owner's new version of a shared preset: checked as a share is; its id, downloads and date stay. */
async function update(env: Env, req: Request, id: string): Promise<Response> {
  let body: unknown;
  try {
    body = await bodyOf(req);
  } catch {
    return fail("not a preset");
  }
  const mine = await owned(env, id, body && typeof body === "object" ? (body as Record<string, unknown>).key : undefined);
  if (mine instanceof Response) {
    return mine;
  }
  const checked = checkSubmission(body);
  if (!checked.ok) {
    return fail(checked.error);
  }
  const p = checked.preset;
  const day = new Date().toISOString().slice(0, 10);
  const client = await clientKey(env, req, `${day}|update`);
  const used = await env.DB.prepare("SELECT count FROM submissions WHERE client = ? AND day = ?").bind(client, day)
    .first<{ count: number }>();
  if (used && used.count >= UPDATES_PER_DAY) {
    return fail(`at most ${UPDATES_PER_DAY} updates a day: please try again tomorrow`, 429);
  }
  const valsHash = await hmac("values", canonical(p.values));
  const same = await env.DB.prepare(`SELECT name FROM presets WHERE vals_hash = ? AND id != ? AND status NOT IN ${GONE} `
                                    + "LIMIT 1").bind(valsHash, id).first<{ name: string }>();
  if (same) {
    return fail(`the same settings are shared already, as "${same.name}"`, 409);
  }
  await env.DB.batch([
    env.DB.prepare("UPDATE presets SET name = ?, author = ?, description = ?, version = ?, vals = ?, vals_hash = ?, "
                   + "updated = ? WHERE id = ?")
      .bind(p.name, p.author, p.description, p.version, JSON.stringify(p.values), valsHash, Date.now(), id),
    env.DB.prepare("INSERT INTO submissions (client, day, count) VALUES (?, ?, 1) "
                   + "ON CONFLICT (client, day) DO UPDATE SET count = count + 1").bind(client, day),
    refreshVersions(env),
  ]);
  return json({ ok: true });
}

/** The owner takes a shared preset out of the list (kept as unshared: the admin still sees it). */
async function unshare(env: Env, req: Request, id: string): Promise<Response> {
  let key: unknown;
  try {
    key = ((await bodyOf(req)) as Record<string, unknown>).key;
  } catch {
    key = undefined;
  }
  const mine = await owned(env, id, key);
  if (mine instanceof Response) {
    return mine;
  }
  await env.DB.batch([
    env.DB.prepare("UPDATE presets SET status = 'unshared', updated = ? WHERE id = ?").bind(Date.now(), id),
    refreshVersions(env),
  ]);
  return json({ ok: true });
}

async function download(env: Env, id: string): Promise<Response> {
  await env.DB.prepare("UPDATE presets SET downloads = downloads + 1 WHERE id = ? AND status = 'public'").bind(id).run();
  return json({ ok: true });
}

/** Whether the request carries the admin's token (compared in constant time). */
function isAdmin(env: Env, req: Request): boolean {
  const given = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const want = env.ADMIN_TOKEN || "";
  if (want.length < 16 || given.length !== want.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < want.length; i++) {
    diff |= want.charCodeAt(i) ^ given.charCodeAt(i);
  }
  return diff === 0;
}

async function adminList(env: Env, status: string): Promise<Response> {
  const where = status === "all" ? "1 = 1" : "status = ?";
  let stmt = env.DB.prepare(`SELECT * FROM presets WHERE ${where} ORDER BY created DESC LIMIT 1000`);
  if (where === "status = ?") {
    stmt = stmt.bind(status);
  }
  const rows = await stmt.all<Row>();
  return json({ ok: true, presets: rows.results.map((r) => ({ ...publicView(r), status: r.status })) });
}

async function adminAct(env: Env, req: Request, id: string): Promise<Response> {
  let b: Record<string, unknown>;
  try {
    b = (await bodyOf(req)) as Record<string, unknown>;
  } catch {
    return fail("no action");
  }
  const action = b.action;
  if (action === "restore" || action === "hide" || action === "remove") {
    const status = action === "restore" ? "public" : action === "hide" ? "hidden" : "removed";
    await env.DB.batch([env.DB.prepare("UPDATE presets SET status = ? WHERE id = ?").bind(status, id),
                        refreshVersions(env)]);
    return json({ ok: true, status });
  }
  if (action === "edit") {
    const name = typeof b.name === "string" ? b.name : undefined;
    const description = typeof b.description === "string" ? b.description : undefined;
    for (const [text, what, max] of [[name, "name", NAME_MAX], [description, "description", DESCRIPTION_MAX]] as const) {
      if (text !== undefined) {
        const problem = textProblem(text, what, max, what === "name");
        if (problem) {
          return fail(problem);
        }
      }
    }
    if (name !== undefined) {
      await env.DB.prepare("UPDATE presets SET name = ? WHERE id = ?").bind(name.trim(), id).run();
    }
    if (description !== undefined) {
      await env.DB.prepare("UPDATE presets SET description = ? WHERE id = ?").bind(description.trim(), id).run();
    }
    return json({ ok: true });
  }
  return fail("unknown action");
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }
    const url = new URL(req.url);
    const path = url.pathname.replace(/\/+$/, "");
    try {
      if (path === "/v1/presets" && req.method === "GET") {
        return await listPublic(env, url);
      }
      if (path === "/v1/versions" && req.method === "GET") {
        return await versions(env);
      }
      if (path === "/v1/presets" && req.method === "POST") {
        return await share(env, req);
      }
      let m = /^\/v1\/presets\/([a-z0-9]{6,16})\/(download|update|unshare)$/.exec(path);
      if (m && req.method === "POST") {
        return m[2] === "download" ? await download(env, m[1])
          : m[2] === "update" ? await update(env, req, m[1]) : await unshare(env, req, m[1]);
      }
      if (path.startsWith("/v1/admin/")) {
        if (!isAdmin(env, req)) {
          return fail("not allowed", 403);
        }
        if (path === "/v1/admin/presets" && req.method === "GET") {
          return await adminList(env, url.searchParams.get("status") || "all");
        }
        m = /^\/v1\/admin\/presets\/([a-z0-9]{6,16})$/.exec(path);
        if (m && req.method === "POST") {
          return await adminAct(env, req, m[1]);
        }
      }
      if (path === "" || path === "/") {
        return json({ ok: true, service: "AtmosphereMod Community Presets", api: "/v1/presets" });
      }
      return fail("not found", 404);
    } catch (e) {
      return fail(`server error: ${e instanceof Error ? e.message : String(e)}`, 500);
    }
  },
};
