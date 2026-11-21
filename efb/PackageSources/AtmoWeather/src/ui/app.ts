// The EFB app's UI: mount(root, host) draws it into root and returns the unmount. The frame: the sim's status bar
// over the top strip; navigation (a list at the side when horizontal, a bar at the top when vertical); the page,
// its head with the preset at the right (its name and state, Undo and Save, on every page: the user's wish); the
// bottom strip, where the sim's dock button sits, left empty as the sim's own apps do. Over it all, a sheet when
// one is open: What's new (once per release that has news), the performance report.

import { WEB_COMMUNITY } from "../community";
import { AddonReport, Context, PerfReportModel } from "../report";
import { closedValue, WHATS_NEW_KEY, whatsNewDue } from "../whatsnew";
import { button, switchEl, twoTaps } from "./controls";
import { h, Scope, Value } from "./dom";
import { Host } from "./host";
import { hotkeysPage } from "./hotkeysPage";
import { icon } from "./icons";
import { PRESET_DEFAULT, sameName, Store } from "./model";
import { CLOUD_TABS, overviewPage, settingsPage, SKY_TABS } from "./pages";
import { presetsPage } from "./presetsPage";
import { reportSheet, shareSheet, Sheet, whatsNewSheet } from "./sheets";
import { THEME_CHOICES, UI_CSS } from "./theme";
import { SheetName, toolsPage } from "./toolsPage";
import { latestRelease } from "./update";

/** Developer builds (tools/build_efb.py --dev, tools/efb_reload.py): the hook for tools/shots.py. Never in a release. */
declare const DEV: boolean;

const STYLE_ID = "atmo-ui-style";
const PAGE_KEY = "atmo-ui-page";
const THEME_KEY = "atmo-ui-theme";
/** With the theme on auto, the sim's time of day is read this often. */
const AUTO_THEME_MS = 30000;
/** The small tablet's short side (px) at the first measured window: --u scales every length against it. */
const BASE_PX = 624;

interface PageDef {
  id: string;
  title: string;
  short: string;
  sub: string;
}
const PAGES: PageDef[] = [
  { id: "overview", title: "Overview", short: "Home", sub: "See the mod at a glance" },
  { id: "sky", title: "Sky & Haze", short: "Sky & Haze", sub: "Customize the haze and the sky's colours" },
  { id: "clouds", title: "Clouds", short: "Clouds", sub: "Change the clouds' lighting, shape and weather" },
  { id: "presets", title: "Presets", short: "Presets", sub: "Manage, share and discover presets" },
  { id: "keys", title: "Hotkeys", short: "Keys", sub: "Choose your own keys for the mod" },
  { id: "tools", title: "Tools", short: "Tools", sub: "Measure performance, report a bug and more" },
];
const ICON_OF: Record<string, string> = { overview: "overview", sky: "sky", clouds: "clouds",
                                          presets: "presets", keys: "keys", tools: "tools" };

/** How far (px at u = 1) the mouse moves with the button down before it's a drag of the page, not a click. */
const DRAG_SLOP = 6;

/** A page scrolls by dragging it, as on a tablet (and by the wheel, as before). A drag that started on a control
 * that drags itself (a value bar, plus and minus) is that control's; a click after a drag of the page is swallowed. */
function dragToScroll(scope: Scope, el: HTMLElement): void {
  let startY = 0, startTop = 0, dragging = false, down = false;
  const slop = () => DRAG_SLOP * (parseFloat(getComputedStyle(el).getPropertyValue("--u")) || 1);
  const move = (e: Event) => {
    const dy = (e as MouseEvent).clientY - startY;
    if (!dragging && Math.abs(dy) > slop()) {
      dragging = true;
    }
    if (dragging) {
      el.scrollTop = startTop - dy;
      e.preventDefault();
    }
  };
  const up = () => {
    down = false;
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    if (dragging) {
      setTimeout(() => { dragging = false; }, 0);  // (after the click this release makes)
    }
  };
  scope.listen(el, "mousedown", (e) => {
    const m = e as MouseEvent;
    if (m.button !== 0 || (m.target as HTMLElement).tagName === "INPUT") {
      return;
    }
    down = true;
    dragging = false;
    startY = m.clientY;
    startTop = el.scrollTop;
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
  // (the click that ends a drag: not a click on what's under the mouse)
  const swallow = (e: Event) => {
    if (dragging) {
      e.stopPropagation();
      e.preventDefault();
    }
  };
  el.addEventListener("click", swallow, true);
  scope.add(() => {
    el.removeEventListener("click", swallow, true);
    if (down) {
      up();
    }
  });
}

export function mount(root: HTMLElement, host: Host): () => void {
  const scope = new Scope();
  let style = document.getElementById(STYLE_ID) as HTMLStyleElement | null;
  if (!style) {
    style = h("style");
    style.id = STYLE_ID;
    document.head.appendChild(style);
  }
  style.textContent = UI_CSS;
  const store = new Store(host);
  scope.add(() => store.dispose());
  // (developers: tools/shots.py sets the settings from outside, through the sim's debugger. Only in a developer's
  // build, and only where the shell has the developers' reload hook, which it sets right after this mount)
  const devHook = setTimeout(() => {
    const w = window as unknown as Record<string, unknown>;
    if (DEV && typeof w.__atmoUiReload === "function") {
      w.__atmoDev = {
        state: () => JSON.stringify(store.state.get() || null),
        set: (values: Record<string, number>) => store.setMany(values),
        saveAs: (name: string) => store.saveAs(name),
        load: (name: string) => store.load(name, true),
        reload: () => store.reload(),
      };
    }
  }, 0);
  scope.add(() => {
    clearTimeout(devHook);
    if (DEV) {
      delete (window as unknown as Record<string, unknown>).__atmoDev;
    }
  });

  const saved = host.load(PAGE_KEY);
  const page = new Value(PAGES.some((p) => p.id === saved) ? saved! : "overview");
  scope.add(page.watch((p) => host.save(PAGE_KEY, p)));
  const focusName = new Value(0);
  const latest = latestRelease(host);
  // (the light or the dark variant: the user compares them, Tools > Appearance)
  // (dark, light, or auto: light by day in the sim, dark at dawn, dusk and night, when a bright tablet dazzles in a
  // dim cockpit; the sim's E:TIME OF DAY is 0 dawn, 1 day, 2 dusk, 3 night)
  const storedTheme = host.load(THEME_KEY);
  const theme = new Value(storedTheme && THEME_CHOICES.indexOf(storedTheme) >= 0 ? storedTheme : "auto");
  scope.add(theme.watch((t) => host.save(THEME_KEY, t)));
  const variant = new Value("dark");
  const pickVariant = () => variant.set(theme.get() !== "auto" ? theme.get()
    : Number(host.simvar("E:TIME OF DAY", "enum")) === 1 ? "light" : "dark");
  scope.add(theme.watch(pickVariant));
  const dayTimer = setInterval(() => theme.get() === "auto" && pickVariant(), AUTO_THEME_MS);
  scope.add(() => clearInterval(dayTimer));
  /** Where on the next page to go (presetsPage: "community"); cleared once there. */
  const jump = new Value("");
  const go = (id: string, target = "") => {
    jump.set(target);
    page.set(id);
  };

  // ---- the sheets: What's new when this release's is new to the user; the performance report from Tools
  const sheetName = new Value<SheetName>(whatsNewDue(host.load(WHATS_NEW_KEY)) ? "news" : "");
  const report = new PerfReportModel((data) => host.send(data), (): Context => {
    const s = store.state.get();
    return {
      version: host.version,
      metar: store.metar.get(),
      weather: (s && s.weather) || "",
      altitudeFt: host.simvar("PLANE ALTITUDE", "feet"),
      aglFt: host.simvar("PLANE ALT ABOVE GROUND", "feet"),
      changed: store.changedFromDefaults(),
      defaultPreset: sameName(store.preset, PRESET_DEFAULT) && store.presetSaved && !store.unconfirmed,
    };
  }, host.reportTransport);
  scope.add(store.state.watch((s) => report.onReport(s ? (s.report as AddonReport | undefined) : undefined)));
  scope.add(report.open.watch((o) => {
    if (o) {
      sheetName.set("report");
    } else if (sheetName.get() === "report") {
      sheetName.set("");
    }
  }));
  const openSheet = (s: SheetName) => {
    if (s === "report") {
      report.show();
    } else {
      sheetName.set(s);
    }
  };
  // (Community Presets: the server, or the host's stand-in; the preset a Share button asked to share)
  const community = host.communityApi || WEB_COMMUNITY;
  let sharing = "", sharingTarget = "";
  /** The share sheet for one of the user's presets (`target`: a shared preset of this PC's to update). */
  const openShare = (name: string, target = "") => {
    sharing = name;
    sharingTarget = target;
    sheetName.set("share");
  };

  // ---- navigation, with the brand and the mod's switch
  const enabled = new Value(false);
  scope.add(store.state.watch((s) => enabled.set(!!s && !!s.enabled)));
  const stateText = h("span", { class: "state" });
  scope.add(store.state.watch((s) => { stateText.textContent = !s ? "waiting" : s.enabled ? "On" : "Off"; }));
  const brand = h("div", { class: "brand" }, h("div", { class: "mark" }, icon("brand")),
                  h("div", { class: "name", text: "AtmosphereMod" }));
  const modRow = h("div", { class: "modrow" }, h("span", { class: "modlabel", text: "Mod" }), stateText,
                   switchEl(scope, enabled, () => store.setEnabled(!enabled.get())));
  scope.add(store.state.watch((s) => {
    const key = s && s.hotkeys ? s.hotkeys.toggle : "Ctrl+Shift+F9";
    modRow.title = `The whole mod on or off${key ? ` (${key})` : ""}. Off: the sim's own shaders, to compare.`;
  }));
  const tabs = h("div", { class: "tabs" });
  for (const p of PAGES) {
    const tab = h("div", { class: "tab", title: p.title }, icon(ICON_OF[p.id]), h("span", { text: "" }));
    scope.listen(tab, "click", () => go(p.id));
    scope.add(page.watch((cur) => { tab.className = `tab${cur === p.id ? " on" : ""}`; }));
    tabs.appendChild(tab);
  }
  const nav = h("div", { class: "nav" }, brand, modRow, tabs);

  // ---- the page
  const title = h("h1");
  const sub = h("p");
  const scroll = h("div", { class: "scroll" });

  // ---- the preset, beside the title on every page
  const pname = h("div", { class: "pname" });
  const pstate = h("span", { class: "pstate" });
  const unsaved = new Value(false);
  const undo = twoTaps(scope, "Undo", "Undo all? Tap again", "", () => store.undo(), () => true, unsaved);
  const saveLabel = new Value("Save");
  const save = button(scope, saveLabel, "primary", () => {
    if (store.canSaveInPlace) {
      store.saveInPlace();
    } else {
      page.set("presets");
      focusName.set(focusName.get() + 1);
    }
  });
  const preset = h("div", { class: "preset saved" },
    h("div", { class: "pinfo" }, h("div", { class: "plabel", text: "PRESET" }), pname), pstate, undo, save);
  scope.add(store.state.watch((s) => {
    const changed = !!s && s.presetSaved === false;
    unsaved.set(changed);
    pname.textContent = !s ? "waiting for the add-on" : store.preset || "none";
    pstate.textContent = !s || !store.preset ? "" : changed ? "Unsaved" : "Saved";
    saveLabel.set(store.canSaveInPlace ? "Save" : "Save as ...");
    preset.className = `preset ${changed ? "unsaved" : "saved"}`;
  }));
  const main = h("div", { class: "main" },
    h("div", { class: "head" }, h("div", { class: "titles" }, title, sub), preset), scroll);
  let pageScope: Scope | undefined;
  const ui = h("div", { class: "atmo-ui" }, nav, main);

  const render = () => {
    const def = PAGES.filter((p) => p.id === page.get())[0] || PAGES[0];
    if (pageScope) {
      pageScope.dispose();
    }
    pageScope = new Scope();
    title.textContent = def.title;
    sub.textContent = def.sub;
    scroll.innerHTML = "";
    scroll.scrollTop = 0;
    const ps = pageScope;
    scroll.appendChild(def.id === "overview" ? overviewPage(ps, store, go, latest)
      : def.id === "sky" ? settingsPage(ps, store, SKY_TABS())
      : def.id === "clouds" ? settingsPage(ps, store, CLOUD_TABS(), true)
      : def.id === "presets" ? presetsPage(ps, store, focusName, community, openShare, jump)
      : def.id === "keys" ? hotkeysPage(ps, store)
      : toolsPage(ps, store, theme, openSheet));
  };
  scope.add(page.watch(render));
  dragToScroll(scope, scroll);
  scope.add(() => pageScope && pageScope.dispose());

  let sheetScope: Scope | undefined, shown: Sheet | undefined;
  const closeNews = () => {
    host.save(WHATS_NEW_KEY, closedValue());
    sheetName.set("");
  };
  scope.add(sheetName.watch((name) => {
    if (sheetScope) {
      sheetScope.dispose();
      sheetScope = undefined;
    }
    if (shown && shown.el.parentNode) {
      shown.el.parentNode.removeChild(shown.el);
    }
    shown = undefined;
    if (name === "") {
      return;
    }
    sheetScope = new Scope();
    shown = name === "news" ? whatsNewSheet(sheetScope, closeNews, (url) => host.openUrl(url))
      : name === "share" ? shareSheet(sheetScope, store, sharing, community, () => sheetName.set(""), sharingTarget)
      : reportSheet(sheetScope, store, report);
    dragToScroll(sheetScope, shown.body);
    ui.appendChild(shown.el);
  }));
  scope.add(() => sheetScope && sheetScope.dispose());

  // ---- the tablet: orientation and scale
  const classes = () => {
    const p = store.panel.get();
    // (pg-*: the page's colour, theme.ts)
    ui.className = `atmo-ui ${p.vertical ? "v" : "h"} s${p.size} ${variant.get()} pg-${page.get()}`;
  };
  scope.add(variant.watch(classes));
  scope.add(page.watch(classes));
  // (the bottom strip takes the EFB's own dock colour, read from the EFB's page; theme.ts has it as measured, for
  // the harness and should the EFB not have its dock where it was)
  const dockColour = () => {
    const dock = document.querySelector(".efb .dock") as HTMLElement | null;
    const c = dock ? getComputedStyle(dock).backgroundColor : "";
    if (c && c !== "transparent" && !/^rgba\(.*,\s*0\)$/.test(c)) {
      ui.style.setProperty("--efb-dock", c);
    }
  };
  scope.add(store.panel.watch((p) => {
    const u = p.width > 0 ? Math.min(p.width, p.height) / BASE_PX : 1;
    ui.style.setProperty("--u", u.toFixed(4));
    classes();
    dockColour();
    // (tab labels: short under the icons when vertical, the full title beside them when horizontal)
    for (let i = 0; i < PAGES.length; i++) {
      (tabs.children[i].lastChild as HTMLElement).textContent = p.vertical ? PAGES[i].short : PAGES[i].title;
    }
  }));

  root.appendChild(ui);
  return () => {
    scope.dispose();
    if (ui.parentNode) {
      ui.parentNode.removeChild(ui);
    }
  };
}
