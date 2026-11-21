// The redesign's pages (phase 2 prototype, 2026-11-20): the overview, the settings by topic, presets, hotkeys and
// tools. The settings come from src/specs.ts; what isn't wired yet says so.

import { TABS, TabSpec } from "../specs";
import { button, labelOf, settingRow, switchEl } from "./controls";
import { h, Scope, Value } from "./dom";
import { icon } from "./icons";
import { decode, Decoded, layersText } from "./metar";
import { PRESET_DEFAULT, sameName, Store } from "./model";
import { newer, RELEASE_PAGE } from "./update";

export type Go = (page: string) => void;

function card(title: string, ...children: (Node | null)[]): HTMLElement {
  return h("div", { class: "card" }, h("h2", { text: title }), h("div", { class: "body" }, ...children));
}

function watchText<T>(scope: Scope, el: HTMLElement, value: Value<T>, text: () => string): HTMLElement {
  scope.add(value.watch(() => { el.textContent = text(); }));
  return el;
}

/** A tap that asks for a second one before it acts: the button reads `armedText` meanwhile. */
export function twoTaps(scope: Scope, label: string, armedText: string, kind: string, act: () => void,
                 needed: () => boolean, enabled?: Value<boolean>): HTMLElement {
  const text = new Value(label);
  let armed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const disarm = () => {
    armed = false;
    text.set(label);
  };
  const el = button(scope, text, kind, () => {
    if (!needed() || armed) {
      if (timer) {
        clearTimeout(timer);
      }
      disarm();
      act();
      return;
    }
    armed = true;
    text.set(armedText);
    el.classList.add("armed");
    timer = setTimeout(disarm, 4000);
  }, enabled);
  scope.add(text.watch((t) => {
    if (t === label) {
      el.classList.remove("armed");
    }
  }));
  scope.add(() => timer && clearTimeout(timer));
  return el;
}

// ---------------------------------------------------------------- overview

/** The main switches, one per topic, with their section's accent. */
const QUICK: [key: string, label: string, section: string][] = [
  ["T0", "Atmosphere", "sky"], ["T13", "Haze from METAR", "sky"], ["T8", "Real-world sky colours", "sky"],
  ["C0", "Cloud lighting", "light"], ["C20", "Clouds follow METARs", "clouds"], ["C23", "Cloud types", "clouds"],
  ["C32", "Thunderstorms", "clouds"],
];
const SECTION_NAMES: Record<string, string> = { sky: "SKY & HAZE", light: "CLOUD LIGHT", clouds: "CLOUDS" };

/** Where the METARs come from (the sim's weather now; other sources, as ActiveSky or StrataWx, may follow). */
const METAR_SOURCE = "the sim";

export function overviewPage(scope: Scope, store: Store, go: Go, latest: Value<string | undefined>): HTMLElement {
  const dot = h("span", { class: "dot wait" });
  const conn = h("span");
  const updateText = h("span");
  const update = h("span", { class: "update", title: "The release on GitHub" }, updateText, icon("out"));
  scope.listen(update, "click", () => store.host.openUrl(RELEASE_PAGE));
  // (one slim line: the add-on's connection, the METAR link; with a newer release, in a vertical tablet, two)
  const statusBody = h("div", { class: "body" });
  const showConn = () => {
    const s = store.state.get();
    dot.className = `dot ${s ? "ok" : "wait"}`;
    conn.textContent = !s ? "Waiting for the add-on ..." : s.version ? `Connected (v.${s.version})` : "Connected";
    // (a newer release on GitHub than the add-on: update.ts)
    const tag = latest.get();
    const ahead = !!s && newer(tag, s.version);
    updateText.textContent = ahead ? `Newer version available (v.${(tag || "").replace(/^v\.?/, "")})` : "";
    update.style.display = ahead ? "" : "none";
    statusBody.className = `body${ahead ? " ahead" : ""}`;
  };
  scope.add(store.state.watch(showConn));
  scope.add(latest.watch(showConn));
  // the METAR link (the app's own): a pulse while it polls, its trouble in words otherwise
  const live = h("span", { class: "live" });
  const linkText = h("span");
  const linkRow = h("div", { class: "linkrow", title: "The METAR link: the sim's weather reports to the add-on" },
                    icon("link"), live, linkText);
  scope.add(store.link.watch((l) => {
    const polling = /^running/.test(l) && !/failed/.test(l);
    const failed = /failed: (.*)$/.exec(l);
    linkRow.className = `linkrow${polling ? "" : " fail"}`;
    live.style.display = polling || failed ? "" : "none";
    linkText.textContent = polling ? `Pulling METARs from ${METAR_SOURCE}` : failed ? `Last poll failed: ${failed[1]}` : l || "Not started";
  }));
  statusBody.appendChild(h("div", { class: "label", style: "display: flex; align-items: center; min-width: 0" },
                           dot, conn, update));
  statusBody.appendChild(linkRow);
  const status = h("div", { class: "card statusline" }, statusBody);

  // the METAR, decoded, and what the mod draws of it against the sim's own
  const raw = h("div", { class: "metar-raw" });
  const decoded = h("table", { class: "kv" });
  const compare = h("div", { class: "compare" });
  const fill = () => {
    const m = store.metar.get();
    const d = m && /^(METAR|SPECI)?\s*[A-Z]/.test(m) ? decode(m) : undefined;
    raw.textContent = m || "No METAR yet: the link polls the sim's weather every minute.";
    decoded.innerHTML = "";
    if (d) {
      const row = (k: string, v: string | undefined) => {
        if (v) {
          decoded.appendChild(h("tr", null, h("td", { text: k }), h("td", { text: v })));
        }
      };
      row("Station", [d.station, d.time, d.auto ? "automatic" : ""].filter((x) => x).join(", "));
      row("Wind", d.wind);
      row("Visibility", d.visibility);
      row("Weather", d.weather.join(", "));
      row("Clouds", layersText(d));
      row("Temperature", d.temperature);
      row("Pressure", d.pressure);
    }
    compare.innerHTML = "";
    for (const line of comparison(store, d)) {
      compare.appendChild(h("p", null, h("b", { text: `${line[0]}: ` }), line[1]));
    }
    compare.style.display = compare.children.length > 0 ? "" : "none";
  };
  scope.add(store.metar.watch(fill));
  scope.add(store.state.watch(fill));
  const weather = card("Weather now", raw, decoded, compare);

  const tiles = h("div", { class: "tiles" });
  for (const [key, label, section] of QUICK) {
    const on = new Value(false);
    scope.add(store.key(key).watch((v) => on.set(v > 0)));
    const spec = findSpec(key);
    tiles.appendChild(h("div", { class: "tile" },
      h("div", { class: "t" }, h("small", { text: SECTION_NAMES[section] }),
        spec && spec.experimental ? labelOf({ ...spec, label }) : h("span", { text: label })),
      switchEl(scope, on, () => store.set(key, on.get() ? 0 : 1))));
  }
  const quick = card("Quick switches", tiles);

  const chips = h("div", { class: "chips" });
  scope.add(store.state.watch(() => {
    chips.innerHTML = "";
    for (const name of (store.state.get() && store.state.get()!.presets) || []) {
      const current = sameName(name, store.preset);
      const chip = h("div", { class: `chip${current ? " on" : ""}`, text: name });
      let armed = false;
      chip.addEventListener("click", () => {
        if (current) {
          return;
        }
        if (!store.presetSaved && !armed) {
          armed = true;
          chip.className = "chip armed";
          chip.textContent = `Discard changes? Tap again`;
          return;
        }
        store.load(name, !store.presetSaved);
      });
      chips.appendChild(chip);
    }
  }));
  const presetCard = card("Presets", h("div", { class: "muted", text: "Load one with a tap:" }), chips,
                          h("div", { style: "margin-top: 12u" }, button(scope, "Manage presets", "", () => go("presets"))));

  return h("div", null, status, weather, quick, presetCard);
}

/** What the mod draws of the weather, against the sim's own (the settings' own words for "off"; the add-on's reading
 * of the haze: "EDDK: continental haze, visibility ~25 km, humidity 87%"). */
function comparison(store: Store, d: Decoded | undefined): [string, string][] {
  const s = store.state.get();
  if (!s) {
    return [];
  }
  const out: [string, string][] = [];
  if (!s.enabled) {
    return [["The mod is off", "the sim's own haze, sky and clouds."]];
  }
  if (!(store.raw("T0") > 0)) {
    out.push(["Haze and sky", "the sim's own."]);
  } else if (store.raw("T13") > 0) {
    const reading = (s.weather || "").replace(/^[^:]*:\s*/, "");
    out.push(["Haze", reading ? `from the METAR, ${reading}.` : "from the METAR, once the add-on has one."]);
  } else {
    out.push(["Haze", "the mod's manual settings, not from the METAR."]);
  }
  if (store.raw("C20") > 0) {
    const radius = specValue("C21", store.raw("C21"));
    const layers = d ? layersText(d) : "";
    out.push(["Clouds", `near stations they take the reported layers${layers ? ` (${layers})` : ""}${
      radius ? `, within ${radius}` : ""} (live weather only).`]);
  } else {
    out.push(["Clouds", "the sim's own layers" + (store.raw("C0") > 0 ? ", in the mod's lighting." : ".")]);
  }
  return out;
}

/** A setting's definition by key. */
function findSpec(key: string) {
  for (const tab of TABS) {
    for (const spec of tab.settings) {
      if (spec.key === key) {
        return spec;
      }
    }
  }
  return undefined;
}

/** A setting's value in its friendly units (the settings' own formatting). */
function specValue(key: string, raw: number): string {
  for (const tab of TABS) {
    for (const spec of tab.settings) {
      if (spec.key === key && spec.kind === "slider") {
        return spec.format(spec.toDisplay(raw));
      }
    }
  }
  return "";
}

// ---------------------------------------------------------------- settings by topic

export function settingsPage(scope: Scope, store: Store, tabs: TabSpec[]): HTMLElement {
  const page = h("div");
  for (const tab of tabs) {
    const body = h("div", { class: "body" });
    for (const s of tab.settings.filter((x) => !x.more)) {
      body.appendChild(settingRow(scope, store, s));
    }
    const more = tab.settings.filter((x) => x.more);
    if (more.length > 0) {
      const box = h("div", { style: "display: none" });
      more.forEach((s) => box.appendChild(settingRow(scope, store, s)));
      const chevron = h("span", null, icon("down"));
      const toggle = h("div", { class: "more" }, h("span", { text: `${tab.more || "More settings"} (${more.length})` }), chevron);
      scope.listen(toggle, "click", () => {
        const open = box.style.display === "none";
        box.style.display = open ? "" : "none";
        chevron.innerHTML = "";
        chevron.appendChild(icon(open ? "up" : "down"));
      });
      body.appendChild(toggle);
      body.appendChild(box);
    }
    page.appendChild(h("div", { class: "card" }, h("h2", { text: tab.name }), body));
  }
  return page;
}

export const SKY_TABS = (): TabSpec[] => [TABS[0], TABS[1]];
export const LIGHT_TABS = (): TabSpec[] => [TABS[2]];
export const CLOUD_TABS = (): TabSpec[] => [TABS[3]];

// ---------------------------------------------------------------- presets

const NAME_OK = /^[A-Za-z0-9 _\-().,+']{1,40}$/;

export function presetsPage(scope: Scope, store: Store, focusName: Value<number>): HTMLElement {
  const field = h("input", { class: "field" }) as HTMLInputElement;
  field.placeholder = "A name for the settings as they are";
  const note = h("div", { class: "muted", style: "margin-top: 8u; min-height: 1.3em" });
  scope.add(store.state.watch((s) => { note.textContent = (s && s.presetNote) || ""; }));
  const save = () => {
    const name = field.value.trim();
    if (!NAME_OK.test(name)) {
      note.textContent = "A name: letters, digits, spaces and - _ ( ) . , + ', up to 40 characters";
      return;
    }
    if (sameName(name, PRESET_DEFAULT)) {
      note.textContent = `"${PRESET_DEFAULT}" can't be changed: choose another name`;
      return;
    }
    const existing = ((store.state.get() && store.state.get()!.presets) || []).find((p) => sameName(p, name));
    store.saveAs(existing || name);
    field.value = "";
  };
  scope.add(focusName.watch((n) => {
    if (n > 0) {
      setTimeout(() => field.focus(), 0);
    }
  }));
  const saveCard = card("Save as a new preset",
    h("div", { style: "display: flex; align-items: center" }, h("div", { style: "flex: 1 1 auto; margin-right: 10u" }, field),
      button(scope, "Save", "primary", save)),
    note);

  const list = h("div");
  scope.add(store.state.watch((s) => {
    list.innerHTML = "";
    const missing = (s && s.presetsMissing) || {};
    for (const name of (s && s.presets) || []) {
      const current = sameName(name, store.preset);
      const pill = current ? h("span", { class: `tag${store.presetSaved ? "" : " unsaved"}`,
                                         text: store.presetSaved ? "ACTIVE" : "ACTIVE, UNSAVED" }) : null;
      const lacks = missing[name] ? h("div", { class: "muted", style: "font-size: 0.85em",
                                               text: `No value for ${missing[name].length} newer setting(s): the defaults fill in` }) : null;
      const actions = h("div", { style: "flex: 0 0 auto; display: flex" },
        current ? null : twoTaps(scope, "Load", "Discard changes? Tap again", "", () => store.load(name, !store.presetSaved),
                                 () => !store.presetSaved),
        sameName(name, PRESET_DEFAULT) ? null
          : twoTaps(scope, "Delete", "Delete? Tap again", "danger", () => store.remove(name), () => true));
      list.appendChild(h("div", { class: "row" },
        h("div", { class: "text" }, h("div", { class: "label" }, h("span", { text: name, style: "margin-right: 10u" }), pill), lacks),
        actions));
    }
  }));
  return h("div", null, saveCard, card("Your presets", list));
}

// ---------------------------------------------------------------- hotkeys

const HOTKEY_NAMES: Record<string, string> = {
  toggle: "The mod on or off", reload: "Reload shaders and settings", capture: "Capture (developers)",
  gpuTimes: "GPU times (developers)", cloudFog: "In-cloud fog on or off", pause: "Pause (developers)",
};

export function hotkeysPage(scope: Scope, store: Store): HTMLElement {
  const list = h("div");
  scope.add(store.state.watch((s) => {
    list.innerHTML = "";
    const keys = (s && s.hotkeys) as Record<string, string> | undefined;
    if (!keys) {
      list.appendChild(h("div", { class: "muted", text: "The add-on hasn't said which keys it has (an older add-on?)." }));
      return;
    }
    for (const action of Object.keys(keys)) {
      list.appendChild(h("div", { class: "row" },
        h("div", { class: "text" }, h("div", { class: "label", text: HOTKEY_NAMES[action] || action })),
        h("span", { class: "tag", style: "margin-right: 10u", text: keys[action] || "none" }),
        button(scope, "Change", "inert", () => undefined)));
    }
  }));
  return h("div", null, card("Keys in the sim", list,
    h("div", { class: "muted", style: "margin-top: 10u", text: "Changing a key: wired in phase 3." })));
}

// ---------------------------------------------------------------- tools

interface Report {
  phase?: string;
  progress?: number;
  result?: { fpsOn?: number; fpsOff?: number; gpuOn?: number; gpuOff?: number; passes?: Record<string, [number, number]> };
}

export function toolsPage(scope: Scope, store: Store, theme: Value<string>): HTMLElement {
  const bar = h("div", { class: "progress" }, h("div", { style: "width: 0%" }));
  const phase = h("div", { class: "muted" });
  const table = h("table", { class: "kv" });
  const running = new Value(false);
  scope.add(store.state.watch((s) => {
    const r = (s && s.report) as Report | undefined;
    const p = r && typeof r.progress === "number" ? r.progress : 0;
    (bar.firstChild as HTMLElement).style.width = `${(p * 100).toFixed(1)}%`;
    running.set(!!r && r.phase !== "done" && r.phase !== "stopped" && r.phase !== "idle" && r.phase !== undefined);
    phase.textContent = !r ? "Measures the frame rate and GPU time with the mod and without, about 40 s."
      : r.phase === "done" ? "Done: here's what it found." : r.phase === "stopped" ? "Stopped."
      : `Measuring (${r.phase}) ... keep the view still.`;
    table.innerHTML = "";
    const res = r && r.phase === "done" ? r.result : undefined;
    if (res) {
      const row = (k: string, v: string) => table.appendChild(h("tr", null, h("td", { text: k }), h("td", { text: v })));
      row("Frames per second, with / without", `${(res.fpsOn || 0).toFixed(1)} / ${(res.fpsOff || 0).toFixed(1)}`);
      row("GPU ms per frame, with / without", `${(res.gpuOn || 0).toFixed(2)} / ${(res.gpuOff || 0).toFixed(2)}`);
      for (const k of Object.keys(res.passes || {})) {
        const [a, b] = res.passes![k];
        row(`  ${k}`, `${a.toFixed(2)} / ${b.toFixed(2)}`);
      }
    }
  }));
  const report = card("Performance report", phase, bar, table,
    h("div", { style: "margin-top: 12u" },
      button(scope, "Start", "primary", () => store.host.send({ measure: true }), new Value(true)),
      button(scope, "Stop", "", () => store.host.send({ measure: false }), running),
      button(scope, "Send ...", "inert", () => undefined)),
    h("div", { class: "muted", style: "margin-top: 8u", text: "Sending: wired in phase 3." }));

  const about = h("table", { class: "kv" });
  const fill = () => {
    about.innerHTML = "";
    const s = store.state.get();
    const row = (k: string, v: string) => about.appendChild(h("tr", null, h("td", { text: k }), h("td", { text: v })));
    row("EFB app", store.host.version);
    row("Add-on in the sim", s ? s.version || "an older one" : "not connected");
    row("METAR link", store.link.get() || "-");
  };
  scope.add(store.state.watch(fill));
  scope.add(store.link.watch(fill));
  const look = (name: string, label: string) => {
    const b = button(scope, label, "", () => theme.set(name));
    scope.add(theme.watch((t) => { b.className = `btn${t === name ? " chosen" : ""}`; }));
    return b;
  };
  const appearance = card("Appearance", h("div", { class: "muted", style: "margin-bottom: 8u",
                                                   text: "Two variants of the same design, to compare." }),
                          h("div", null, look("dark", "Dark"), look("light", "Light")));
  return h("div", null, appearance, report,
    h("div", { class: "cols" }, card("What's new", h("div", { class: "muted", text: "The release notes: wired in phase 3." })),
      card("About", about)));
}
