// Screenshots of the EFB app's pages for the README (Screenshots/efb-*.png): from the browser harness, each page
// whole (the tablet's screen grown to the page's height, so nothing has to scroll), at twice the resolution.
// Headless Chrome over its DevTools protocol (Node's own WebSocket: no packages).
//
//   tools/node/node.exe tools/app_shots.mjs        (the harness running: tools/efb_harness.py, port 8078)
//
// The harness shows a release's versions with ?app=&addon= (efb/harness/harness.ts), and GitHub's latest release
// as the same, so no update notice shows.

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const VERSION = process.argv[2] || "0.8.0";
const HARNESS = `http://127.0.0.1:8078/?app=${VERSION}&addon=${VERSION}`;
const OUT = new URL("../Screenshots/", import.meta.url);
const PORT = 9333;
/** Each shot: its file, the tab (Home 0, Sky & Haze 1, Clouds 2, Presets 3, Keys 4, Tools 5), the sim's time of day
 * (1 day: light; 3 night: dark), a setting whose description is open, and how many Community Presets show (the
 * rest of the page hidden: the pager below still tells how many there are). The README shows the dark theme only. */
const SHOTS = [
  { file: "efb-home.png", tab: 0, daytime: "3" },
  { file: "efb-sky-haze.png", tab: 1, daytime: "3", open: "Haze strength" },
  { file: "efb-clouds-page.png", tab: 2, daytime: "3" },
  { file: "efb-presets-page.png", tab: 3, daytime: "3", shared: 2 },
  { file: "efb-tools.png", tab: 5, daytime: "3" },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const profile = mkdtempSync(join(tmpdir(), "atmo-shots-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
                              "--hide-scrollbars", "--window-size=1400,1200", "about:blank"], { stdio: "ignore" });
try {
  let targets;
  for (let i = 0; i < 60 && !targets; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    } catch {
      await sleep(250);
    }
  }
  const page = targets && targets.find((t) => t.type === "page");
  if (!page) {
    throw new Error("Chrome didn't start");
  }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve);
    ws.addEventListener("error", reject);
  });
  let next = 0;
  const pending = new Map();
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, (m) => (m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result)));
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) {
      throw new Error(r.exceptionDetails.exception ? r.exceptionDetails.exception.description : r.exceptionDetails.text);
    }
    return r.result.value;
  };

  for (const shot of SHOTS) {
    await send("Emulation.setDeviceMetricsOverride", { width: 1400, height: 1200, deviceScaleFactor: 2, mobile: false });
    await send("Page.navigate", { url: HARNESS });
    await sleep(2500);
    const rect = await evaluate(`(async () => {
      const wait = (ms) => new Promise((r) => setTimeout(r, ms));
      const set = (id, v) => {
        const el = document.getElementById(id);
        if (el.type === "checkbox") { el.checked = v; } else { el.value = v; }
        el.dispatchEvent(new Event("change", { bubbles: true }));
      };
      set("fit", false);
      set("latest", "v${VERSION}");
      set("theme", "auto");
      set("daytime", ${JSON.stringify(shot.daytime)});
      [...document.querySelectorAll("button")].find((b) => /Small, vertical/.test(b.textContent)).click();
      await wait(1800);
      // (What's New shows on a first start: closed)
      const got = [...document.querySelectorAll(".sheet .btn")].find((b) => b.textContent === "Got it");
      if (got) { got.click(); await wait(300); }
      document.querySelectorAll(".atmo-ui .tab")[${shot.tab}].click();
      await wait(1500);
      const open = ${JSON.stringify(shot.open || "")};
      if (open) {
        const row = [...document.querySelectorAll(".card .row")]
          .find((r) => r.querySelector(".label").textContent.trim().indexOf(open) === 0);
        row.querySelector(".icon-btn.info").click();
        await wait(300);
      }
      const shared = ${shot.shared || 0};
      if (shared) {
        const all = [...document.querySelectorAll(".tablet .shared-list > .shared")];
        if (all.length <= shared) { throw new Error("only " + all.length + " Community Presets to show"); }
        all.slice(shared).forEach((e) => { e.style.display = "none"; });
        await wait(300);
      }
      // the screen as tall as the page: nothing scrolls, the dock button at its foot
      const app = document.querySelector(".tablet .app"), tablet = document.querySelector(".tablet");
      const scroll = app.querySelector(".atmo-ui .scroll");
      const extra = Math.max(0, scroll.scrollHeight - scroll.clientHeight);
      app.style.height = (app.offsetHeight + extra) + "px";
      tablet.style.height = (tablet.offsetHeight + extra) + "px";
      const dock = tablet.querySelector(".dock");
      if (dock) { dock.style.top = (dock.offsetTop + extra) + "px"; }
      const stage = document.getElementById("stage"), scaler = document.getElementById("scaler");
      scaler.style.height = (tablet.offsetHeight + 48) + "px";
      stage.style.overflow = "visible";
      stage.style.height = "auto";
      document.body.style.overflow = "visible";
      await wait(800);
      const r = app.getBoundingClientRect();
      return { x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height };
    })()`);
    await send("Emulation.setDeviceMetricsOverride", { width: 1400, height: Math.ceil(rect.y + rect.height + 40),
                                                        deviceScaleFactor: 2, mobile: false });
    await sleep(500);
    const png = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
                                                       clip: { ...rect, scale: 1 } });
    writeFileSync(new URL(shot.file, OUT), Buffer.from(png.data, "base64"));
    console.log(`${shot.file}: ${Math.round(rect.width)} x ${Math.round(rect.height)} (x2)`);
  }
  ws.close();
} finally {
  chrome.kill();
  await sleep(500);
  try {
    rmSync(profile, { recursive: true, force: true });
  } catch {
    // (Chrome may still hold a file a moment)
  }
}
