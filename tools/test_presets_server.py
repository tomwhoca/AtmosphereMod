"""Tests the Community Presets server (server/presets) without Cloudflare: its checks on their own, then the API
against wrangler dev with a local D1 database (a fresh one each run).

    python tools/test_presets_server.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "presets"
NODE = ROOT / "tools" / "node" / "node.exe"
WRANGLER = SERVER / "node_modules" / "wrangler" / "bin" / "wrangler.js"
ESBUILD = ROOT / "efb" / "PackageSources" / "AtmoWeather" / "node_modules" / "esbuild" / "bin" / "esbuild"
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"

failures = 0


def check(ok: bool, what: str) -> None:
    global failures
    print(f"{'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        failures += 1


def env() -> dict:
    return {**os.environ, "PATH": f"{NODE.parent};{os.environ['PATH']}", "WRANGLER_SEND_METRICS": "false", "CI": "1"}


def rules_tests() -> None:
    """The checks (src/rules.ts), bundled for Node."""
    out = SERVER / ".wrangler" / "rules-test.cjs"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(NODE), str(ESBUILD), str(SERVER / "src" / "rules.ts"), "--bundle", "--platform=node",
                    "--format=cjs", f"--outfile={out}", "--log-level=error"], check=True)
    script = r"""
const r = require(process.argv[1]);
const good = { name: "Evening haze", author: "tomwhoca", description: "Soft light for late flights.",
               version: "0.8.0", values: { T0: 1, T3: 6, T9: 0.7, C0: 1, C16: 0.75, C23: 1, R3: 5, C14: 2 } };
const out = {};
const ok = r.checkSubmission(good);
out.good = ok.ok && Object.keys(ok.preset.values).join() === "T0,T3,T9,C0,C16,C23";
const bad = (patch) => r.checkSubmission({ ...good, ...patch });
out.noAuthor = bad({ author: " " }).error;
out.link = bad({ description: "see https://example.com" }).error;
out.www = bad({ author: "www.spam" }).error;
out.fsto = bad({ author: "tom (flightsim.to)" }).ok;
out.word = bad({ name: "Fuck clouds" }).error;
out.scunthorpe = bad({ description: "Scunthorpe sunset, shitake-free" }).ok;
out.control = bad({ description: "two\nlines" }).error;
out.long = bad({ description: "x".repeat(301) }).error;
out.badName = bad({ name: "a/b" }).error;
out.range = bad({ values: { ...good.values, C16: 99 } }).error;
out.zero = bad({ values: { ...good.values, C5: 0, C17: 0, T9: 0 } }).ok;
out.notNum = bad({ values: { ...good.values, T3: "6" } }).error;
out.few = bad({ values: { T0: 1, C0: 1 } }).error;
out.shareable = [r.shareable("C37"), r.shareable("C13"), r.shareable("R1"), r.shareable("T14")];
out.canon = r.canonical({ C10: 1, C2: 2, T1: 3 });
console.log(JSON.stringify(out));
"""
    res = json.loads(subprocess.run([str(NODE), "-e", script, str(out)], capture_output=True, text=True,
                                    check=True).stdout)
    check(res["good"], "rules: a good preset passes; radar (R) and aircraft (C13-C15) values are left out")
    check(res["noAuthor"] == "author: needed", f"rules: author required ({res['noAuthor']})")
    check(res["link"] == "description: no links" and res["www"] == "author: no links",
          "rules: no links in the texts")
    check(res["fsto"], "rules: a flightsim.to name is not a link")
    check(res["word"] == "name: please choose other words", "rules: blocked words")
    check(res["scunthorpe"], "rules: whole words only (Scunthorpe, shitake)")
    check(res["control"] == "description: plain text only", "rules: no line breaks or control characters")
    check(res["long"] == "description: at most 300 characters", "rules: description length")
    check(res["badName"].startswith("name:"), "rules: the add-on's name rules")
    check("C16" in (res["range"] or "") and "outside" in res["range"], f"rules: ranges ({res['range']})")
    check(res["zero"], "rules: 0 is always allowed (the sim's own)")
    check(res["notNum"] == "T3: not a number", "rules: numbers only")
    check(res["few"] == "values: too few to be a preset", "rules: a preset has values")
    check(res["shareable"] == [True, False, False, True], f"rules: shareable keys ({res['shareable']})")
    check(res["canon"] == "C2=2;C10=1;T1=3", f"rules: canonical order ({res['canon']})")


def call(method: str, path: str, body=None, token: str = "", ip: str = ""):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "text/plain;charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if ip:
        req.add_header("CF-Connecting-IP", ip)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"null"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), dict(e.headers)


def api_tests() -> None:
    state = SERVER / ".wrangler" / "state"
    shutil.rmtree(state, ignore_errors=True)
    subprocess.run([str(NODE), str(WRANGLER), "d1", "migrations", "apply", "DB", "--local"], cwd=SERVER, env=env(),
                   check=True, capture_output=True, input="y\n", text=True, encoding="utf-8", errors="replace")
    token = next(l.split("=", 1)[1] for l in (SERVER / ".dev.vars").read_text().splitlines()
                 if l.startswith("ADMIN_TOKEN="))
    log = open(SERVER / ".wrangler" / "dev.log", "w")
    dev = subprocess.Popen([str(NODE), str(WRANGLER), "dev", "--port", str(PORT), "--ip", "127.0.0.1"], cwd=SERVER,
                           env=env(), stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                if call("GET", "/")[0] == 200:
                    break
            except OSError:
                time.sleep(1)
        else:
            raise SystemExit("wrangler dev didn't start: server/presets/.wrangler/dev.log")
        values = {"T0": 1, "T3": 6, "T9": 0.7, "C0": 1, "C16": 0.75, "C23": 1}
        st, res, hd = call("GET", "/v1/presets")
        check(st == 200 and res["presets"] == [] and hd.get("Access-Control-Allow-Origin") == "*",
              "api: an empty list, open to any origin")
        st, res, _ = call("POST", "/v1/presets", {"name": "Evening haze", "author": "tomwhoca", "description": "Soft.",
                                                  "version": "0.8.0", "values": values}, ip="1.1.1.1")
        check(st == 200 and res["ok"] and len(res["id"]) == 10, f"api: shared ({res})")
        pid = res["id"]
        st, res, _ = call("GET", "/v1/presets")
        p = res["presets"][0] if res["presets"] else {}
        check(p.get("name") == "Evening haze" and p.get("values", {}).get("C16") == 0.75 and p.get("author") == "tomwhoca",
              "api: public at once, with its values")
        st, res, _ = call("POST", "/v1/presets", {"name": "Copy", "author": "x", "values": values}, ip="1.1.1.2")
        check(st == 409 and "Evening haze" in res["error"], "api: the same settings aren't listed twice")
        st, res, _ = call("POST", "/v1/presets", {"name": "Bad", "author": "", "values": values}, ip="1.1.1.2")
        check(st == 400 and res["error"] == "author: needed", "api: the checks apply")
        codes = [call("POST", "/v1/presets", {"name": f"P{i}", "author": "spam", "values": {**values, "T3": 9 + i}},
                      ip="9.9.9.9")[0] for i in range(6)]
        check(codes == [200] * 5 + [429], f"api: 5 shares a day per client ({codes})")
        st, res, _ = call("POST", f"/v1/presets/{pid}/download")
        check(call("GET", "/v1/presets")[1]["presets"][-1]["downloads"] == 1, "api: a download counted")
        r1 = call("POST", f"/v1/presets/{pid}/report", {"reason": "rude"}, ip="2.2.2.1")[1]
        r1b = call("POST", f"/v1/presets/{pid}/report", {}, ip="2.2.2.1")[1]
        r2 = call("POST", f"/v1/presets/{pid}/report", {}, ip="2.2.2.2")[1]
        r3 = call("POST", f"/v1/presets/{pid}/report", {}, ip="2.2.2.3")[1]
        listed = [x["id"] for x in call("GET", "/v1/presets")[1]["presets"]]
        check(not r1["hidden"] and r1b.get("already") and not r2["hidden"] and r3["hidden"] and pid not in listed,
              "api: one report per client; hidden at 3 reports")
        check(call("GET", "/v1/admin/presets")[0] == 403 and call("GET", "/v1/admin/presets", token="x" * 48)[0] == 403,
              "api: admin needs the token")
        st, res, _ = call("GET", "/v1/admin/presets?status=hidden", token=token)
        check(st == 200 and [x["id"] for x in res["presets"]] == [pid] and res["presets"][0]["reasons"] == ["rude"],
              "api: the admin sees the hidden preset and the reasons")
        st, res, _ = call("POST", f"/v1/admin/presets/{pid}", {"action": "restore"}, token=token)
        check(st == 200 and pid in [x["id"] for x in call("GET", "/v1/presets")[1]["presets"]], "api: restored")
        st, res, _ = call("POST", f"/v1/admin/presets/{pid}", {"action": "edit", "description": "Soft light."},
                          token=token)
        check(st == 200 and call("GET", "/v1/presets")[1]["presets"][-1]["description"] == "Soft light.", "api: edited")
        st, res, _ = call("POST", f"/v1/admin/presets/{pid}", {"action": "remove"}, token=token)
        check(st == 200 and pid not in [x["id"] for x in call("GET", "/v1/presets")[1]["presets"]], "api: removed")
        check(call("POST", f"/v1/presets/{pid}/report", {}, ip="2.2.2.9")[0] == 404, "api: a removed one can't be reported")
        check(call("GET", "/v1/nothing")[0] == 404 and call("POST", "/v1/presets", None)[0] == 400, "api: errors")
    finally:
        dev.terminate()
        try:
            dev.wait(10)
        except subprocess.TimeoutExpired:
            dev.kill()
        log.close()


if __name__ == "__main__":
    rules_tests()
    api_tests()
    print("FAILED" if failures else "all checks passed")
    sys.exit(1 if failures else 0)
