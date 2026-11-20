// The browser harness for the redesigned EFB app (efb/PackageSources/AtmoWeather/src/ui): the UI module mounted in
// a tablet of the size the sim gives the app, with a fake add-on behind the same Host the sim's app gives it.
// tools/efb_harness.py serves it with live reload: on every save the UI module is evaluated again and mounted
// anew, the harness keeping its state (the tablet, the fake add-on's settings).

import type { AddonState, Host, Panel } from "../PackageSources/AtmoWeather/src/ui/host";

interface Layout {
  name: string;
  /** the app's area and where it sits in the tablet's border (measured in the sim 2026-11-20, notes/efb-ui.md), at
   * the sim window of the first measurement; the window's size scales all of it (x 1.43 in the second session) */
  w: number;
  h: number;
  border: [number, number];
  status: number;
  dock: number;
}
const LAYOUTS: Layout[] = [
  { name: "Small, vertical", w: 624, h: 929, border: [32, 13], status: 33, dock: 46 },
  { name: "Small, horizontal", w: 929, h: 624, border: [13, 32], status: 33, dock: 46 },
  { name: "Medium, vertical", w: 745, h: 1108, border: [38, 16], status: 39, dock: 55 },
  { name: "Medium, horizontal", w: 1108, h: 745, border: [16, 38], status: 39, dock: 55 },
  { name: "Large, vertical", w: 863, h: 1284, border: [44, 18], status: 46, dock: 64 },
  { name: "Large, horizontal", w: 1284, h: 863, border: [18, 44], status: 46, dock: 64 },
];

interface Fixture {
  state: AddonState;
  metar: string;
  link: string;
}
interface UiModule {
  mount(root: HTMLElement, host: Host): () => void;
  build: string;
}

const $ = (id: string) => document.getElementById(id)!;
const stored = (key: string, fallback: string) => {
  try {
    return localStorage.getItem(`atmo-harness-${key}`) ?? fallback;
  } catch {
    return fallback;
  }
};
const store = (key: string, value: string) => {
  try {
    localStorage.setItem(`atmo-harness-${key}`, value);
  } catch {
    // (private window: not kept)
  }
};

// ---------------------------------------------------------------- the fake add-on

class FakeAddon {
  public state!: AddonState;
  public answers = true;
  private readonly states: ((s: AddonState) => void)[] = [];
  private fixture!: Fixture;
  private measuring?: ReturnType<typeof setInterval>;

  public async load(): Promise<Fixture> {
    this.fixture = (await (await fetch(`fixture.json?t=${Date.now()}`)).json()) as Fixture;
    this.reset();
    return this.fixture;
  }

  public reset(): void {
    this.state = JSON.parse(JSON.stringify(this.fixture.state)) as AddonState;
    this.tell();
  }

  public onState(cb: (s: AddonState) => void): () => void {
    this.states.push(cb);
    return () => {
      const i = this.states.indexOf(cb);
      if (i >= 0) {
        this.states.splice(i, 1);
      }
    };
  }

  /** A message from the app, as the add-on's settings_bridge handles it (roughly). */
  public receive(data: Record<string, unknown>): void {
    log(JSON.stringify(data));
    if (!this.answers) {
      return;
    }
    const s = this.state;
    if (data.set && typeof data.set === "object") {
      for (const [key, value] of Object.entries(data.set as Record<string, number>)) {
        const list = key[0] === "C" ? s.C : key[0] === "T" ? s.T : undefined;
        const index = parseInt(key.slice(1), 10);
        if (list && index >= 0) {
          list[index] = value;
        }
      }
      s.presetSaved = false;
    }
    if (typeof data.enabled === "boolean") {
      s.enabled = data.enabled;
    }
    if (typeof data.savePreset === "string") {
      if (!(s.presets || []).includes(data.savePreset)) {
        s.presets = [...(s.presets || []), data.savePreset];
      }
      s.preset = data.savePreset;
      s.presetSaved = true;
      s.presetNote = `saved "${data.savePreset}"`;
    }
    if (typeof data.loadPreset === "string") {
      s.preset = data.loadPreset;
      s.presetSaved = true;
      s.presetNote = `loaded "${data.loadPreset}"`;
    }
    if (typeof data.deletePreset === "string") {
      s.presets = (s.presets || []).filter((p) => p !== data.deletePreset);
      s.presetNote = `deleted "${data.deletePreset}"`;
    }
    if (data.measure === true) {
      this.measure();
    } else if (data.measure === false && this.measuring) {
      clearInterval(this.measuring);
      this.measuring = undefined;
      s.report = { phase: "stopped", progress: 0 };
    }
    setTimeout(() => this.tell(), 40);  // (an answer over CommBus, a moment later)
  }

  /** A performance report as the add-on runs it: on, off, on again (about 36 s in the sim; 6 s here). */
  private measure(): void {
    const phases = ["settle_on", "on", "settle_off", "off", "settle_on2", "on2"];
    const done = this.fixture.state.report as { result?: unknown; system?: unknown } | undefined;
    let t = 0;
    if (this.measuring) {
      clearInterval(this.measuring);
    }
    this.measuring = setInterval(() => {
      t += 0.05;
      if (t >= 1) {
        clearInterval(this.measuring!);
        this.measuring = undefined;
        this.state.report = { phase: "done", progress: 1, result: done && done.result, system: done && done.system };
      } else {
        this.state.report = { phase: phases[Math.floor(t * phases.length)], progress: t };
      }
      this.tell();
    }, 300);
  }

  private tell(): void {
    const copy = JSON.parse(JSON.stringify(this.state)) as AddonState;
    for (const cb of this.states.slice()) {
      cb(copy);
    }
  }
}

const lines: string[] = [];
function log(line: string): void {
  lines.unshift(`${new Date().toTimeString().slice(0, 8)} ${line}`);
  lines.length = Math.min(lines.length, 40);
  $("log").textContent = lines.join("\n");
}

// ---------------------------------------------------------------- the host the UI gets

class HarnessHost implements Host {
  public readonly version = "dev (harness)";
  private readonly panels: ((p: Panel) => void)[] = [];

  public constructor(private readonly addon: FakeAddon, private readonly fixture: Fixture,
                     private readonly layout: () => Layout) {}

  public send(data: unknown): void {
    this.addon.receive(data as Record<string, unknown>);
  }

  public onState(cb: (s: AddonState) => void): () => void {
    return this.addon.onState(cb);
  }

  public onMetar(cb: (m: string) => void): () => void {
    cb(this.fixture.metar);
    return () => undefined;
  }

  public onLinkStatus(cb: (s: string) => void): () => void {
    cb(this.fixture.link);
    return () => undefined;
  }

  public onPanel(cb: (p: Panel) => void): () => void {
    this.panels.push(cb);
    cb(this.panel());
    return () => {
      const i = this.panels.indexOf(cb);
      if (i >= 0) {
        this.panels.splice(i, 1);
      }
    };
  }

  public relayout(): void {
    const p = this.panel();
    for (const cb of this.panels.slice()) {
      cb(p);
    }
  }

  private panel(): Panel {
    const l = this.layout();
    return { width: l.w, height: l.h, vertical: l.h >= l.w, size: Math.floor(layoutIndex / 2) };
  }

  public simvar(name: string): number {
    return name === "PLANE ALTITUDE" ? 5012 : name === "PLANE ALT ABOVE GROUND" ? 4890 : 0;
  }

  public load(key: string): string | undefined {
    return stored(`data-${key}`, "") || undefined;
  }

  public save(key: string, value: string): void {
    store(`data-${key}`, value);
  }
}

// ---------------------------------------------------------------- the tablet

let layoutIndex = Math.min(LAYOUTS.length - 1, Math.max(0, parseInt(stored("layout", "0"), 10) || 0));
let windowScale = parseFloat(stored("window", "1")) || 1;

/** The layout as the app gets it: the measured one at the sim window's scale. */
function current(): Layout {
  const l = LAYOUTS[layoutIndex], k = windowScale;
  return { name: l.name, w: Math.round(l.w * k), h: Math.round(l.h * k), status: Math.round(l.status * k),
           dock: Math.round(l.dock * k), border: [Math.round(l.border[0] * k), Math.round(l.border[1] * k)] };
}
let unmount: (() => void) | undefined;
let appEl: HTMLElement;

function drawTablet(): void {
  const l = current();
  const scaler = $("scaler");
  scaler.innerHTML = "";
  const bw = l.w + 2 * l.border[0], bh = l.h + 2 * l.border[1];
  const tablet = document.createElement("div");
  tablet.className = "tablet";
  tablet.style.cssText = `left: 24px; top: 24px; width: ${bw}px; height: ${bh}px`;
  appEl = document.createElement("div");
  appEl.className = "app";
  appEl.style.cssText = `left: ${l.border[0]}px; top: ${l.border[1]}px; width: ${l.w}px; height: ${l.h}px`;
  tablet.appendChild(appEl);
  const overlays = ($("overlays") as HTMLInputElement).checked;
  if (overlays) {
    // the sim's own status bar over the app's top strip (the clock in the middle), and the dock button
    const bar = document.createElement("div");
    bar.className = "statusbar";
    bar.style.cssText = `left: ${l.border[0]}px; top: ${l.border[1]}px; width: ${l.w}px; height: ${l.status}px; ` +
      `font-size: ${Math.round(l.status * 0.45)}px`;
    bar.textContent = "12:50";
    const bell = document.createElement("div");
    bell.className = "bell";
    bell.style.cssText = `left: ${Math.round(l.status * 0.45)}px; top: ${Math.round(l.status * 0.3)}px`;
    bar.appendChild(bell);
    tablet.appendChild(bar);
    const dock = document.createElement("div");
    dock.className = "dock";
    dock.title = "the sim's dock button (home)";
    dock.style.cssText = `left: ${l.border[0] + Math.round((l.w - l.dock) / 2)}px; top: ${l.border[1] + l.h - l.dock}px; ` +
      `width: ${l.dock}px; height: ${l.dock}px`;
    tablet.appendChild(dock);
  }
  scaler.appendChild(tablet);
  fit();
}

function fit(): void {
  const l = current();
  const scaler = $("scaler");
  const stage = $("stage");
  const bw = l.w + 2 * l.border[0] + 48, bh = l.h + 2 * l.border[1] + 48;
  const k = ($("fit") as HTMLInputElement).checked ? Math.min(1, stage.clientWidth / bw, stage.clientHeight / bh) : 1;
  scaler.style.transform = `scale(${k})`;
  stage.style.overflow = k < 1 ? "hidden" : "auto";
  scaler.style.width = `${bw}px`;
  scaler.style.height = `${bh}px`;
}

async function loadUi(): Promise<UiModule> {
  const text = await (await fetch(`build/AtmoUI.js?t=${Date.now()}`)).text();
  (0, eval)(text);
  return (window as unknown as { AtmoUI: UiModule }).AtmoUI;
}

/** GitHub's API as the harness's selector says (src/ui/update.ts asks it for the latest release). */
function fakeGitHub(): void {
  const real = window.fetch.bind(window);
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url.indexOf("api.github.com/repos/tomwhoca/AtmosphereMod/releases/latest") < 0) {
      return real(input, init);
    }
    const tag = ($("latest") as HTMLSelectElement).value;
    log(`GitHub asked for the latest release: ${tag || "unreachable"}`);
    return tag ? Promise.resolve(new Response(JSON.stringify({ tag_name: tag }), { status: 200 }))
      : Promise.reject(new TypeError("network error (the harness's unreachable)"));
  }) as typeof window.fetch;
}

async function main(): Promise<void> {
  fakeGitHub();
  try {
    localStorage.removeItem("atmo-harness-data-atmo-ui-latest");  // (the UI keeps a day's answer: the selector's, now)
  } catch {
    // (private window)
  }
  const addon = new FakeAddon();
  const fixture = await addon.load();
  const host = new HarnessHost(addon, fixture, current);
  // (one mount at a time: a size and a variant changed at once mounted twice, the second racing the first)
  let mounting: Promise<void> = Promise.resolve();
  const mountUi = (): Promise<void> => {
    mounting = mounting.then(async () => {
      const ui = await loadUi();
      if (unmount) {
        unmount();
      }
      unmount = ui.mount(appEl, host);
      log(`mounted AtmoUI ${ui.build}`);
    }).catch((e) => log(`mount failed: ${e}`));
    return mounting;
  };

  const sizes = $("sizes");
  LAYOUTS.forEach((l, i) => {
    const b = document.createElement("button");
    b.textContent = `${l.name}  ${l.w} x ${l.h}`;
    b.onclick = () => {
      layoutIndex = i;
      store("layout", String(i));
      [...sizes.children].forEach((c, j) => c.classList.toggle("on", j === i));
      drawTablet();
      void mountUi();
    };
    b.classList.toggle("on", i === layoutIndex);
    sizes.appendChild(b);
  });
  const win = $("window") as HTMLSelectElement;
  win.value = String(windowScale);
  win.onchange = () => {
    windowScale = parseFloat(win.value) || 1;
    store("window", String(windowScale));
    drawTablet();
    void mountUi();
  };
  // (the UI keeps its variant in its stored data, Host.save "atmo-ui-theme": set it, mount anew)
  const themeSel = $("theme") as HTMLSelectElement;
  themeSel.value = stored("data-atmo-ui-theme", "dark");
  themeSel.onchange = () => {
    store("data-atmo-ui-theme", themeSel.value);
    void mountUi();
  };
  // (the UI keeps the latest release a day: a new choice clears that, then mounts anew)
  const latestSel = $("latest") as HTMLSelectElement;
  latestSel.value = stored("latest", "v0.8.0");
  latestSel.onchange = () => {
    store("latest", latestSel.value);
    try {
      localStorage.removeItem("atmo-harness-data-atmo-ui-latest");
    } catch {
      // (private window)
    }
    void mountUi();
  };
  $("fit").onchange = fit;
  $("overlays").onchange = () => {
    drawTablet();
    void mountUi();
  };
  ($("connected") as HTMLInputElement).onchange = (e) => {
    addon.answers = (e.target as HTMLInputElement).checked;
  };
  $("reset").onclick = () => addon.reset();
  window.addEventListener("resize", fit);
  drawTablet();
  await mountUi();

  // live reload (esbuild's server): the UI module mounts anew, the harness itself reloads the page
  const events = new EventSource("/esbuild");
  events.addEventListener("change", (e) => {
    const change = JSON.parse((e as MessageEvent).data) as { added: string[]; removed: string[]; updated: string[] };
    const files = [...change.added, ...change.updated];
    if (files.some((f) => f.indexOf("harness.js") >= 0)) {
      location.reload();
    } else if (files.some((f) => f.indexOf("AtmoUI.js") >= 0)) {
      void mountUi();
    }
  });
  (window as unknown as { harness: unknown }).harness = { addon, host, layouts: LAYOUTS, remount: mountUi };
}

void main();
