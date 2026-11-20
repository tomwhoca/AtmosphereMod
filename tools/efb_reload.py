"""Reload the redesigned EFB app's UI (efb/PackageSources/AtmoWeather/src/ui) in the running sim, without a restart;
and check its layout at every tablet size and orientation.

    python tools/efb_reload.py                    # build the UI into the installed package, mount it anew
    python tools/efb_reload.py --audit            # ... then each of the six layouts: what sticks out of the app,
                                                  # what's clipped, which controls sit under the sim's status bar
                                                  # or dock button (the tablet is set back as it was)
    python tools/efb_reload.py --audit --no-build # check what's in the sim now

Needs the sim with Dev Mode's Coherent debugger on (tools/coherent.py), and the app installed once as a developer
build with the new UI, with the sim closed:

    python tools/build_efb.py --new-ui --dev
    python tools/deploy.py

The sim serves the package's files as they are on disk (measured 2026-11-20, notes/efb-ui.md), so writing AtmoUI.js
there and evaluating it again is a full reload of the UI. A new file would need a restart: AtmoUI.js has to be in
the package from the install.
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import coherent  # noqa: E402
from deploy import community_folder  # noqa: E402

NODE = ROOT / "tools" / "node" / "node.exe"
APP = ROOT / "efb" / "PackageSources" / "AtmoWeather"
ESBUILD = APP / "node_modules" / "esbuild" / "bin" / "esbuild"
UI_FILE = Path("atmospheremod-weather/html_ui/efb_ui/efb_apps/AtmoWeather/AtmoUI.js")
LAYOUTS = [(0, 0, "small, vertical"), (0, 1, "small, horizontal"), (1, 0, "medium, vertical"),
           (1, 1, "medium, horizontal"), (2, 0, "large, vertical"), (2, 1, "large, horizontal")]

RELOAD = """(function () {
  if (typeof window.__atmoUiReload !== 'function') { return 'NO_HOOK'; }
  window.__atmoUiReloadResult = null;
  window.__atmoUiReload().then(function (r) { window.__atmoUiReloadResult = String(r); },
                               function (e) { window.__atmoUiReloadResult = 'ERROR ' + e; });
  return 'started';
})()"""

TABLET_GET = ("(function(){var m=EFB_API.efbSettingsManager;return JSON.stringify([m.getSetting('efbSize').get(),"
              "m.getSetting('orientationMode').get()]);})()")
TABLET_SET = ("(function(){var m=EFB_API.efbSettingsManager;m.getSetting('efbSize').set(%d);"
              "m.getSetting('orientationMode').set(%d);return 'ok';})()")

# What's wrong with the layout, measured in the engine itself (ES5: the page's own dialect).
AUDIT = r"""(function () {
  function rect(el) { var b = el.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height }; }
  function name(el) {
    var c = el.className && el.className.baseVal !== undefined ? el.className.baseVal : String(el.className || '');
    var text = el.children.length === 0 ? (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 32) : '';
    return el.tagName.toLowerCase() + (c ? '.' + c.split(/\s+/).slice(0, 2).join('.') : '') + (text ? ' "' + text + '"' : '');
  }
  var ui = document.querySelector('.atmo-ui');
  if (!ui) { return JSON.stringify({ error: 'the new UI is not mounted (no .atmo-ui)' }); }
  var R = rect(ui), out = { area: [Math.round(R.w), Math.round(R.h)], u: getComputedStyle(ui).getPropertyValue('--u').trim(), problems: [] };
  var bar = document.querySelector('.status-bar'), statusH = bar ? rect(bar).h : 0;
  var d = document.elementFromPoint(R.l + R.w / 2, R.b - 3);
  while (d && !(String(d.className || '').indexOf('dock-item') >= 0)) { d = d.parentElement; }
  var D = d ? rect(d) : null;
  out.status = Math.round(statusH);
  out.dock = D ? [Math.round(D.l - R.l), Math.round(D.t - R.t), Math.round(D.w), Math.round(D.h)] : null;
  function scroller(el) {
    for (var p = el.parentElement; p && p !== ui; p = p.parentElement) {
      var o = getComputedStyle(p).overflowY; if (o === 'auto' || o === 'scroll') { return p; }
    }
    return null;
  }
  var all = ui.querySelectorAll('*');
  for (var i = 0; i < all.length && out.problems.length < 40; i++) {
    var el = all[i], r = rect(el);
    if (r.w === 0 || r.h === 0 || el instanceof SVGElement && el.tagName.toLowerCase() !== 'svg') { continue; }
    var cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') { continue; }
    var sc = scroller(el), box = sc ? rect(sc) : R;
    if (r.r > R.r + 1 || r.l < R.l - 1) { out.problems.push('sticks out sideways: ' + name(el) + ' [' + Math.round(r.l - R.l) + '..' + Math.round(r.r - R.l) + ' of ' + Math.round(R.w) + ']'); }
    else if (!sc && (r.b > R.b + 1 || r.t < R.t - 1)) { out.problems.push('sticks out: ' + name(el)); }
    if ((cs.overflowX === 'hidden' || cs.textOverflow === 'ellipsis') && el.children.length === 0 && el.scrollWidth > el.clientWidth + 1) {
      out.problems.push('clipped: ' + name(el) + ' (' + el.scrollWidth + ' in ' + el.clientWidth + ')');
    }
    if (cs.cursor === 'pointer') {
      var top = Math.max(r.t, box.t), bottom = Math.min(r.b, box.b);
      if (bottom > top) {
        if (top < R.t + statusH) { out.problems.push('under the status bar: ' + name(el)); }
        if (D && r.r > D.l && r.l < D.r && bottom > D.t) { out.problems.push('under the dock button: ' + name(el)); }
      }
    }
  }
  return JSON.stringify(out);
})()"""


def build_into(target: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    subprocess.run([str(NODE), str(ESBUILD), str(APP / "src" / "ui" / "main.ts"), "--bundle", "--format=iife",
                    "--target=es2017", f"--outfile={target}", f'--define:UI_BUILD="{stamp}"', "--log-level=warning"],
                   check=True, cwd=APP)


def reload_ui() -> str:
    started = coherent.evaluate(coherent.TABLET, RELOAD)
    if started == "NO_HOOK":
        raise SystemExit("the installed app has no reload hook: install a developer build with the new UI "
                         "(python tools/build_efb.py --new-ui --dev, then tools/deploy.py with the sim closed)")
    for _ in range(40):
        time.sleep(0.25)
        result = coherent.evaluate(coherent.TABLET, "window.__atmoUiReloadResult")
        if result:
            return str(result)
    return "no answer from the reload"


def audit() -> int:
    before = json.loads(coherent.evaluate(coherent.TABLET, TABLET_GET))
    worst = 0
    try:
        for size, orientation, name in LAYOUTS:
            coherent.evaluate(coherent.TABLET, TABLET_SET % (size, orientation))
            time.sleep(1.5)  # (the EFB lays out; the UI measures again 250 ms after)
            res = json.loads(coherent.evaluate(coherent.TABLET, AUDIT))
            if "error" in res:
                print(f"{name}: {res['error']}")
                return 1
            problems = res["problems"]
            worst = max(worst, len(problems))
            print(f"{name:20s} {res['area'][0]} x {res['area'][1]}, u {res['u']}, status bar {res['status']}, dock "
                  f"{res['dock']}: {'OK' if not problems else f'{len(problems)} problem(s)'}")
            for p in problems[:12]:
                print(f"    {p}")
    finally:
        coherent.evaluate(coherent.TABLET, TABLET_SET % tuple(before))
    return 1 if worst else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audit", action="store_true", help="check the layout at all six tablet sizes and orientations")
    ap.add_argument("--no-build", action="store_true", help="don't build and reload, only check")
    args = ap.parse_args()
    if not args.no_build:
        target = community_folder() / UI_FILE
        if not target.exists():
            raise SystemExit(f"{target} isn't installed: python tools/build_efb.py --new-ui --dev, then "
                             "tools/deploy.py with the sim closed (a new file needs the sim restarted once)")
        build_into(target)
        print(reload_ui())
    if args.audit:
        sys.exit(audit())


if __name__ == "__main__":
    main()
