// The pages of the overview and the settings by topic (from src/specs.ts), and the in-cloud fog's notice. The other
// pages: presetsPage.ts, hotkeysPage.ts, toolsPage.ts.

import { TABS, TabSpec } from "../specs";
import { button, card, settingRow, twoTaps } from "./controls";
import { h, Scope, Value } from "./dom";
import { icon } from "./icons";
import { decode, Decoded, layersText } from "./metar";
import { PRESET_DEFAULT, sameName, Store } from "./model";
import { newer, RELEASE_PAGE } from "./update";

export type Go = (page: string) => void;

function watchText<T>(scope: Scope, el: HTMLElement, value: Value<T>, text: () => string): HTMLElement {
  scope.add(value.watch(() => { el.textContent = text(); }));
  return el;
}


// ---------------------------------------------------------------- overview


/** Where a donation goes: the README's and flightsim.to's coffee link, and the user's PayPal donation page (a
 * hosted button: no address in the link). */
const SUPPORT = {
  coffee: "https://buymeacoffee.com/tomwhoca",
  paypal: "https://www.paypal.com/donate/?hosted_button_id=CHZN2J8XPLNJU",
};

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
        const s = store.state.get();
        if (s && s.presetsMissing && (s.presetsMissing[name] || []).length > 0) {
          go("presets");  // (it lacks newer settings: the Presets page asks how to fill them in)
          return;
        }
        if ((!store.presetSaved || store.unconfirmed) && !armed) {
          armed = true;
          chip.className = "chip armed";
          chip.textContent = `Discard changes? Tap again`;
          return;
        }
        store.load(name, !store.presetSaved || store.unconfirmed);
      });
      chips.appendChild(chip);
    }
  }));
  const presetCard = card("Presets", h("div", { class: "muted", text: "Load one with a tap:" }), chips,
                          h("div", { style: "margin-top: 12u" }, button(scope, "Manage presets", "", () => go("presets"))));

  // ---- about the mod, and how to support it (the user's wish, 2026-11-21)
  const coffee = button(scope, "Buy me a coffee", "primary", () => store.host.openUrl(SUPPORT.coffee));
  coffee.insertBefore(icon("coffee"), coffee.firstChild);
  const paypal = button(scope, "PayPal", "", () => store.host.openUrl(SUPPORT.paypal));
  paypal.insertBefore(icon("heart"), paypal.firstChild);
  const about = card("About this mod",
    h("p", { class: "about", text: "AtmosphereMod aims to bring you a more realistic experience in MSFS 2024 by "
                                   + "improving the sim's atmospheric rendering. It's an experimental project that I "
                                   + "develop in my free time, made possible by the community's engagement and "
                                   + "support." }),
    h("p", { class: "about", text: "If you enjoy it, a small donation helps me keep improving it, and it's much "
                                   + "appreciated. Thank you!" }),
    h("div", { class: "actions" }, coffee, paypal));

  return h("div", null, fogNotice(scope, store), status, weather, presetCard, about);
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

export function settingsPage(scope: Scope, store: Store, tabs: TabSpec[], fog = false): HTMLElement {
  const page = h("div");
  if (fog) {
    page.appendChild(fogNotice(scope, store));
  }
  // (a card per group, every setting in view: no folded sections, the user's wish; a group without a name is a
  // main switch alone, as Mod clouds)
  for (const tab of tabs) {
    const body = h("div", { class: "body" }, ...tab.settings.map((s) => settingRow(scope, store, s)));
    page.appendChild(h("div", { class: "card" }, tab.name ? h("h2", { text: tab.name }) : null, body));
  }
  const keys = tabs.reduce<string[]>((all, t) => all.concat(t.settings.map((s) => s.key)), []);
  const live = new Value(false);
  scope.add(store.state.watch((s) => live.set(!!s)));
  page.appendChild(h("div", { class: "actions end" },
    twoTaps(scope, "Reset this page", "This page to the defaults? Tap again", "", () => store.reset(keys), () => true,
            live)));
  return page;
}

/** While the in-cloud fog is off for now (its hotkey, or here): says so, with a button to switch it on again. */
export function fogNotice(scope: Scope, store: Store): HTMLElement {
  const text = h("span");
  const el = h("div", { class: "notice" }, text, button(scope, "Switch it on", "small", () => store.setCloudFogOff(false)));
  scope.add(store.state.watch((s) => {
    const key = s && s.hotkeys ? s.hotkeys.cloudFog : "";
    text.textContent = `The fog inside clouds is off for now${key ? ` (${key})` : ""}.`;
    el.style.display = s && s.cloudFogOff ? "" : "none";
  }));
  return el;
}

/** The Sky & Haze page's groups (Mod atmosphere, Haze, Manual haze, Colours) and the Clouds page's (Mod clouds,
 * Sunlight, Shape, Weather). */
export const SKY_TABS = (): TabSpec[] => TABS.slice(0, 4);
export const CLOUD_TABS = (): TabSpec[] => TABS.slice(4);
