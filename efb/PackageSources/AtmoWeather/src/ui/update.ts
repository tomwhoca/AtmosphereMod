// Is there a newer release on GitHub? (the redesign, 2026-11-20, the user's wish): the latest release's tag from
// GitHub's API, at most once a day (kept with the host's stored data), against the add-on's version. A request to
// api.github.com, nothing sent but the request itself; offline or refused, nothing shows.

import { Value } from "./dom";
import { Host } from "./host";

const LATEST = "https://api.github.com/repos/tomwhoca/AtmosphereMod/releases/latest";
const KEY = "atmo-ui-latest";
const DAY_MS = 24 * 3600 * 1000;
const TIMEOUT_MS = 10000;

/** "v0.8.0" or "0.8.0" as [0, 8, 0]; undefined if it isn't a version. */
export function parseVersion(text: string | undefined): number[] | undefined {
  const m = /^v?\.?(\d+)\.(\d+)\.(\d+)/.exec((text || "").trim());
  return m ? [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)] : undefined;
}

/** Whether `latest` is a newer version than `current`. */
export function newer(latest: string | undefined, current: string | undefined): boolean {
  const a = parseVersion(latest), b = parseVersion(current);
  if (!a || !b) {
    return false;
  }
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) {
      return a[i] > b[i];
    }
  }
  return false;
}

/** The latest release's tag ("v0.8.0"), from today's check or a new one; undefined until it's known. */
export function latestRelease(host: Host): Value<string | undefined> {
  const out = new Value<string | undefined>(undefined);
  let kept: { at: number; tag: string } | undefined;
  try {
    kept = JSON.parse(host.load(KEY) || "null") || undefined;
  } catch {
    kept = undefined;
  }
  if (kept && typeof kept.tag === "string") {
    out.set(kept.tag);
  }
  if (kept && Date.now() - kept.at < DAY_MS) {
    return out;
  }
  const timeout = new Promise<undefined>((resolve) => setTimeout(() => resolve(undefined), TIMEOUT_MS));
  const ask = fetch(LATEST, { headers: { Accept: "application/vnd.github+json" } })
    .then((r) => (r.ok ? r.json() : undefined))
    .then((j: { tag_name?: string } | undefined) => (j && typeof j.tag_name === "string" ? j.tag_name : undefined))
    .catch(() => undefined);
  Promise.race([ask, timeout]).then((tag) => {
    if (tag) {
      host.save(KEY, JSON.stringify({ at: Date.now(), tag }));
      out.set(tag);
    }
  });
  return out;
}
