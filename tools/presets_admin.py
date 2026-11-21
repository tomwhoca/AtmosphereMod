"""Community Presets, the admin's side (server/presets): what's shared and how often it was added, and hiding,
restoring, removing or editing a preset. Uses the admin key in server/presets/.admin-token (git-ignored; never print it).

    python tools/presets_admin.py                     # every preset, newest first
    python tools/presets_admin.py hidden | public | unshared | removed
    python tools/presets_admin.py show <id>           # one preset, with its values against the mod's defaults
    python tools/presets_admin.py hide <id>           # out of the list until restored
    python tools/presets_admin.py restore <id>
    python tools/presets_admin.py remove <id>
    python tools/presets_admin.py edit <id> --name "New name" --description "New text"
    python tools/presets_admin.py --local ...         # against wrangler dev (127.0.0.1:8787, .dev.vars' key)
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "presets"
LIVE = "https://atmospheremod-presets.tomwhoca.workers.dev"
LOCAL = "http://127.0.0.1:8787"
DEFAULTS_INI = ROOT / "shaders" / "tunables.defaults.ini"


def token(local: bool) -> str:
    if local:
        for line in (SERVER / ".dev.vars").read_text(encoding="utf-8").splitlines():
            if line.startswith("ADMIN_TOKEN="):
                return line.split("=", 1)[1].strip()
        raise SystemExit("no ADMIN_TOKEN in server/presets/.dev.vars")
    path = SERVER / ".admin-token"
    if not path.exists():
        raise SystemExit("server/presets/.admin-token is missing (the admin key set as the Worker's ADMIN_TOKEN)")
    return path.read_text(encoding="utf-8").strip()


def call(base: str, key: str, method: str, path: str, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("Content-Type", "text/plain;charset=utf-8")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("User-Agent", "AtmosphereMod-admin/1.0")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        data = json.loads(e.read() or b"null") or {}
        raise SystemExit(f"{e.code}: {data.get('error', 'error')}")


def defaults() -> dict:
    values = {}
    for line in DEFAULTS_INI.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"\s*([TC]\d+)\s*=\s*([-+0-9.eE]+)", line):
            values[m[1]] = float(m[2])
    return values


def when(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def out(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", nargs="?", default="all")
    ap.add_argument("id", nargs="?")
    ap.add_argument("--name")
    ap.add_argument("--description")
    ap.add_argument("--local", action="store_true")
    a = ap.parse_args(argv)
    base, key = (LOCAL if a.local else LIVE), token(a.local)
    if a.what in ("all", "public", "hidden", "unshared", "removed", "show"):
        res = call(base, key, "GET", f"/v1/admin/presets?status={'all' if a.what == 'show' else a.what}")
        presets = res["presets"]
        if a.what == "show":
            p = next((x for x in presets if x["id"] == a.id), None)
            if not p:
                raise SystemExit(f"no preset {a.id}")
            d = defaults()
            out(f"{p['name']}  ({p['id']}, {p['status']})  by {p['author']}, v{p['version'] or '?'}, {when(p['created'])}")
            if p["description"]:
                out(f"  {p['description']}")
            out(f"  added {p['downloads']}x")
            for k, v in sorted(p["values"].items(), key=lambda kv: (kv[0][0], int(kv[0][1:]))):
                mark = "" if k in d and abs(d[k] - v) < 1e-6 else f"   (default {d.get(k, '?')})"
                out(f"  {k:<4} = {v}{mark}")
            return 0
        if not presets:
            out("none")
        for p in presets:
            flag = "" if p["status"] == "public" else f" [{p['status']}]"
            out(f"{p['id']}  {when(p['created'])}  {p['name']!r} by {p['author']}{flag}  "
                f"(v{p['version'] or '?'}, added {p['downloads']}x)")
        return 0
    if a.what in ("restore", "hide", "remove", "edit"):
        if not a.id:
            raise SystemExit(f"{a.what}: which preset? (its id, from the list)")
        body = {"action": a.what}
        if a.what == "edit":
            if a.name is None and a.description is None:
                raise SystemExit("edit: --name and/or --description")
            if a.name is not None:
                body["name"] = a.name
            if a.description is not None:
                body["description"] = a.description
        res = call(base, key, "POST", f"/v1/admin/presets/{a.id}", body)
        out(f"{a.id}: {res.get('status', 'edited')}")
        return 0
    raise SystemExit(f"unknown: {a.what} (python tools/presets_admin.py --help)")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
