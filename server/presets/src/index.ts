// Community Presets: the AtmosphereMod EFB app's shared presets, a Cloudflare Worker with a D1 database
// (notes/presets-library.md). Public at once; users report what doesn't belong, and a preset reported by
// HIDE_AT different clients is hidden until the admin restores or removes it (tools/presets_admin.py).
//
//   GET  /v1/presets                 the public presets, newest first, with their values
//   POST /v1/presets                 share one: {name, author, description, version, values} (text/plain JSON)
//   POST /v1/presets/:id/download    count an "Add to my presets"
//   POST /v1/presets/:id/report      {reason}: one per client
//   GET  /v1/admin/presets?status=   (Authorization: Bearer ADMIN_TOKEN) hidden | reported | public | removed | all
//   POST /v1/admin/presets/:id       {action: restore | hide | remove | edit, name?, description?}
//
// No IP address is stored: a client is a keyed hash of its address (HMAC with the secret SALT), for submissions
// one that changes daily. Bodies are text/plain JSON, so the tablet's browser sends them without a CORS preflight.

import { canonical, checkSubmission, DESCRIPTION_MAX, NAME_MAX, textProblem } from "./rules";

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
/** Reports from different clients that hide a preset. */
const HIDE_AT = 3;
/** The largest body taken. */
const BODY_MAX = 16 * 1024;
const REASON_MAX = 200;

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

/** The client as a keyed hash of its address: for reports a lasting one, for submissions one per day. */
function clientKey(env: Env, req: Request, day = ""): Promise<string> {
  const ip = req.headers.get("CF-Connecting-IP") || "local";
  return hmac(env.SALT, day ? `${ip}|${day}` : ip);
}

function newId(): string {
  const alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(10));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
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
  status: string; created: number; downloads: number; reports: number;
}
const publicView = (r: Row) => ({
  id: r.id, name: r.name, author: r.author, description: r.description, version: r.version,
  created: r.created, downloads: r.downloads, values: JSON.parse(r.vals),
});

async function listPublic(env: Env): Promise<Response> {
  const rows = await env.DB.prepare("SELECT * FROM presets WHERE status = 'public' ORDER BY created DESC LIMIT 500")
    .all<Row>();
  return json({ ok: true, presets: rows.results.map(publicView) }, 200, { "Cache-Control": "public, max-age=120" });
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
  const same = await env.DB.prepare("SELECT name FROM presets WHERE vals_hash = ? AND status != 'removed' LIMIT 1")
    .bind(valsHash).first<{ name: string }>();
  if (same) {
    return fail(`the same settings are shared already, as "${same.name}"`, 409);
  }
  const id = newId();
  await env.DB.batch([
    env.DB.prepare("INSERT INTO presets (id, name, author, description, version, vals, vals_hash, created) "
                   + "VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(id, p.name, p.author, p.description, p.version, JSON.stringify(p.values), valsHash, Date.now()),
    env.DB.prepare("INSERT INTO submissions (client, day, count) VALUES (?, ?, 1) "
                   + "ON CONFLICT (client, day) DO UPDATE SET count = count + 1").bind(client, day),
  ]);
  return json({ ok: true, id });
}

async function download(env: Env, id: string): Promise<Response> {
  await env.DB.prepare("UPDATE presets SET downloads = downloads + 1 WHERE id = ? AND status = 'public'").bind(id).run();
  return json({ ok: true });
}

async function report(env: Env, req: Request, id: string): Promise<Response> {
  const preset = await env.DB.prepare("SELECT status FROM presets WHERE id = ?").bind(id).first<{ status: string }>();
  if (!preset || preset.status === "removed") {
    return fail("no such preset", 404);
  }
  let reason = "";
  try {
    const b = (await bodyOf(req)) as Record<string, unknown>;
    reason = typeof b.reason === "string" ? b.reason.trim().slice(0, REASON_MAX) : "";
  } catch {
    reason = "";
  }
  const client = await clientKey(env, req);
  const known = await env.DB.prepare("SELECT 1 AS x FROM reports WHERE preset_id = ? AND client = ?").bind(id, client)
    .first();
  if (known) {
    return json({ ok: true, already: true });
  }
  await env.DB.batch([
    env.DB.prepare("INSERT INTO reports (preset_id, client, reason, created) VALUES (?, ?, ?, ?)")
      .bind(id, client, reason, Date.now()),
    env.DB.prepare("UPDATE presets SET reports = reports + 1, status = CASE WHEN reports + 1 >= ? AND status = 'public' "
                   + "THEN 'hidden' ELSE status END WHERE id = ?").bind(HIDE_AT, id),
  ]);
  const now = await env.DB.prepare("SELECT status FROM presets WHERE id = ?").bind(id).first<{ status: string }>();
  return json({ ok: true, hidden: !!now && now.status === "hidden" });
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
  const where = status === "all" ? "1 = 1" : status === "reported" ? "reports > 0" : "status = ?";
  let stmt = env.DB.prepare(`SELECT * FROM presets WHERE ${where} ORDER BY created DESC LIMIT 1000`);
  if (where === "status = ?") {
    stmt = stmt.bind(status);
  }
  const rows = await stmt.all<Row>();
  const reasons = await env.DB.prepare("SELECT preset_id, reason, created FROM reports ORDER BY created DESC LIMIT 2000")
    .all<{ preset_id: string; reason: string; created: number }>();
  return json({
    ok: true,
    presets: rows.results.map((r) => ({
      ...publicView(r), status: r.status, reports: r.reports,
      reasons: reasons.results.filter((x) => x.preset_id === r.id).map((x) => x.reason).filter((x) => x),
    })),
  });
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
    await env.DB.prepare("UPDATE presets SET status = ? WHERE id = ?").bind(status, id).run();
    if (action === "restore") {
      // (restored: its reports so far don't count toward hiding it again)
      await env.DB.prepare("UPDATE presets SET reports = 0 WHERE id = ?").bind(id).run();
    }
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
        return await listPublic(env);
      }
      if (path === "/v1/presets" && req.method === "POST") {
        return await share(env, req);
      }
      let m = /^\/v1\/presets\/([a-z0-9]{6,16})\/(download|report)$/.exec(path);
      if (m && req.method === "POST") {
        return m[2] === "download" ? await download(env, m[1]) : await report(env, req, m[1]);
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
