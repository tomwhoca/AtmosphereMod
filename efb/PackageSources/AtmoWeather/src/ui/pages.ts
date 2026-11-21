// The pages of the overview and the settings by topic (from src/specs.ts), and the in-cloud fog's notice. The other
// pages: presetsPage.ts, hotkeysPage.ts, toolsPage.ts.

import { Spec, TABS, TabSpec } from "../specs";
import { button, card, section, settingRow, switchEl, tileLabel, twoTaps } from "./controls";
import { h, Scope, Value } from "./dom";
import { icon } from "./icons";
import { aboutTile } from "./about";
import { CATEGORIES, decode, Decoded, flightCategory, hazeReading, layersText } from "./metar";
import { PRESET_DEFAULT, sameName, Store } from "./model";
import { newer, RELEASE_PAGE, TROUBLESHOOTING } from "./update";

/** To a page; `target` a place on it ("community": the Presets page's Community Presets). */
export type Go = (page: string, target?: string) => void;

function watchText<T>(scope: Scope, el: HTMLElement, value: Value<T>, text: () => string): HTMLElement {
  scope.add(value.watch(() => { el.textContent = text(); }));
  return el;
}


// ---------------------------------------------------------------- overview


/** The add-on answers within a second or two of the app's start; after this long without an answer, it's a
 * failure (Home's Add-on tile in storm red, with the way to the troubleshooting section). */
const ADDON_WAIT_MS = 20000;

/** Where the METARs come from (the sim's weather now; other sources, as ActiveSky or StrataWx, may follow). */
const METAR_SOURCE = "the sim";

export function overviewPage(scope: Scope, store: Store, go: Go, latest: Value<string | undefined>): HTMLElement {
  // ---- the weather now, as it's drawn: the visibility with the haze (from the METAR, by hand, or the sim's own, as
  // the settings and the mod's switch have it), the clouds and how they're drawn, the rest of the METAR, its flight
  // category from LIFR to VFR, and the METAR as it came
  const wxWhere = h("span", { class: "right" });
  const wxBig = h("div", { class: "big" });
  const wxSub = h("div", { class: "bigsub" });
  const cloudsV = h("div", { class: "lay" });
  const cloudsHow = h("div", { class: "how" });
  const clouds = h("div", { class: "tline" }, h("span", { class: "k", text: "Clouds" }),
                   h("div", { class: "v" }, cloudsV, cloudsHow));
  const wxStats = h("div", { class: "stats" });
  const segs = CATEGORIES.map((c) => h("div", { class: `seg ${c.toLowerCase()}` }, h("div", { class: "segticks" }),
                                       h("div", { class: "seglabel" }, h("span", { text: c }))));
  const conditions = h("span", { class: "right" });
  const scale = h("div", { class: "fc" },
    h("div", { class: "fc-title" }, h("span", { text: "Flight category, from the METAR" }), conditions),
    h("div", { class: "segs" }, ...segs));
  const raw = h("div", { class: "traw" });
  const wxTile = h("div", { class: "tile sky wide" },
    h("div", { class: "tlabel" }, icon("sky"), h("span", { text: "Weather Now" }), wxWhere),
    wxBig, wxSub, clouds, wxStats, scale, raw);
  const fill = () => {
    const m = store.metar.get();
    const d = m && /^(METAR|SPECI)?\s*[A-Z]/.test(m) ? decode(m) : undefined;
    const drawn = depiction(store, d);
    wxWhere.textContent = d ? [d.station, d.time, d.auto ? "automatic" : ""].filter((x) => x).join(" \u00b7 ") : "";
    wxBig.textContent = drawn.big;
    wxSub.textContent = drawn.sub;
    cloudsV.textContent = d ? layersText(d) || "None reported" : "";
    cloudsHow.textContent = drawn.clouds;
    clouds.style.display = d || drawn.clouds ? "" : "none";
    wxStats.innerHTML = "";
    if (d) {
      const stat = (k: string, v: string | undefined) => {
        if (v) {
          wxStats.appendChild(h("div", { class: "stat" }, h("div", { class: "k", text: k }), h("div", { class: "v", text: v })));
        }
      };
      stat("Wind", d.wind);
      stat("Temperature", d.temperature);
      stat("Pressure", d.pressure);
      stat("Weather", d.weather.join(", "));
    }
    const cat = d ? flightCategory(d) : undefined;
    segs.forEach((el, i) => {
      el.className = `seg ${CATEGORIES[i].toLowerCase()}${CATEGORIES[i] === cat ? " on" : ""}`;
    });
    conditions.textContent = cat === "VFR" || cat === "MVFR" ? "VMC" : cat ? "IMC" : "";
    scale.style.display = cat ? "" : "none";
    raw.textContent = m || "";
    raw.style.display = m ? "" : "none";
  };
  scope.add(store.metar.watch(fill));
  scope.add(store.state.watch(fill));

  // ---- the add-on: connected (its version), waiting (just started), or no answer (a failure: storm red, and the
  // way to the README's troubleshooting); a newer release on GitHub
  const addonBig = h("div", { class: "big" });
  const addonSub = h("div", { class: "bigsub" });
  const updateText = h("span");
  const update = h("div", { class: "tlink", title: "The release on GitHub" }, updateText, icon("out"));
  scope.listen(update, "click", () => store.host.openUrl(RELEASE_PAGE));
  const help = button(scope, "Troubleshooting", "primary trail", () => store.host.openUrl(TROUBLESHOOTING));
  help.appendChild(icon("out"));
  const helpRow = h("div", { class: "actions" }, help);
  const addonTile = h("div", { class: "tile aurora" },
    h("div", { class: "tlabel" }, icon("tools"), h("span", { text: "Add-on" })), addonBig, addonSub, update, helpRow);
  let lateTimer: ReturnType<typeof setTimeout> | undefined;
  scope.add(() => lateTimer && clearTimeout(lateTimer));
  const showConn = () => {
    const s = store.state.get();
    const waited = Date.now() - store.startedAt;
    const late = !s && waited >= ADDON_WAIT_MS;
    if (!s && !late && !lateTimer) {
      lateTimer = setTimeout(() => {
        lateTimer = undefined;
        showConn();
      }, ADDON_WAIT_MS - waited + 50);
    }
    addonTile.className = `tile ${s ? "aurora" : late ? "storm" : "sun"}`;
    addonBig.textContent = s ? "Connected" : late ? "No answer" : "Waiting";
    addonSub.textContent = s ? (s.version ? `Version ${s.version}` : "In the sim")
      : late ? "The add-on in the sim isn't answering: most often ReShade didn't load it."
      : "for the add-on in the sim ...";
    helpRow.style.display = late ? "" : "none";
    // (a newer release on GitHub than the add-on: update.ts)
    const tag = latest.get();
    const ahead = !!s && newer(tag, s.version);
    updateText.textContent = ahead ? `Version ${(tag || "").replace(/^v\.?/, "")} is out` : "";
    update.style.display = ahead ? "" : "none";
  };
  scope.add(store.state.watch(showConn));
  scope.add(latest.watch(showConn));

  // ---- the METAR link (the app's own): live while it polls, its trouble in words otherwise
  const live = h("span", { class: "live" });
  const linkBig = h("span");
  const linkSub = h("div", { class: "bigsub" });
  const linkTile = h("div", { class: "tile dusk", title: "The METAR link: the sim's weather reports to the add-on" },
    h("div", { class: "tlabel" }, icon("link"), h("span", { text: "METAR Link" })),
    h("div", { class: "big", style: "display: flex; align-items: center" }, live, linkBig), linkSub);
  scope.add(store.link.watch((l) => {
    const polling = /^running/.test(l) && !/failed/.test(l);
    // (a poll failed; or the link couldn't start: "CommBus unavailable: ...", the shell's words)
    const failed = /failed: (.*)$/.exec(l) || /^(CommBus unavailable.*)$/.exec(l);
    linkTile.className = `tile ${polling ? "dusk" : failed ? "storm" : "sun"}`;
    live.style.display = polling ? "" : "none";
    linkBig.textContent = polling ? "Live" : failed ? "Failed" : "Not started";
    linkSub.textContent = polling ? `Pulling METARs from ${METAR_SOURCE}`
      : failed ? (/^CommBus/.test(failed[1]) ? failed[1] : `Last poll: ${failed[1]}`) : l || "";
  }));

  // ---- the presets: a tap loads one; at most two rows of them, then "N more", Manage presets and Community Presets
  const chips = h("div", { class: "chips" });
  const more = h("div", { class: "chip ghost", title: "All your presets" });
  scope.listen(more, "click", () => go("presets"));
  const manage = h("div", { class: "chip ghost" }, icon("presets"), h("span", { text: "Manage presets" }));
  scope.listen(manage, "click", () => go("presets"));
  const shared = h("div", { class: "chip ghost" }, icon("globe"), h("span", { text: "Community Presets" }));
  scope.listen(shared, "click", () => go("presets", "community"));
  let presetEls: HTMLElement[] = [];
  /** Presets hidden from the end (not the active one) until the row of pills is two rows high at most; false while
   * the page isn't on screen yet (nothing to measure). */
  const fit = (): boolean => {
    presetEls.forEach((el) => { el.style.display = ""; });
    more.style.display = "none";
    if (!chips.offsetParent) {
      return false;
    }
    const rows = () => {
      const tops: number[] = [];
      for (let i = 0; i < chips.children.length; i++) {
        const el = chips.children[i] as HTMLElement;
        if (el.style.display !== "none" && !tops.some((y) => Math.abs(y - el.offsetTop) < 4)) {
          tops.push(el.offsetTop);
        }
      }
      return tops.length;
    };
    let hidden = 0;
    for (let i = presetEls.length - 1; i >= 0 && rows() > 2; i--) {
      if (/\bon\b/.test(presetEls[i].className)) {
        continue;
      }
      presetEls[i].style.display = "none";
      hidden++;
      more.textContent = `${hidden} more`;
      more.style.display = "";
    }
    return true;
  };
  // (once the page is on screen: tried again a few times until it is)
  let fitTimer: ReturnType<typeof setTimeout> | undefined;
  scope.add(() => fitTimer && clearTimeout(fitTimer));
  const fitSoon = (tries = 20) => {
    if (fitTimer) {
      clearTimeout(fitTimer);
    }
    fitTimer = setTimeout(() => {
      fitTimer = undefined;
      if (!fit() && tries > 0) {
        fitSoon(tries - 1);
      }
    }, tries === 20 ? 0 : 100);
  };
  // (made again when what they show changes, not at each state: a second tap would be lost)
  let shownKey = "";
  scope.add(store.state.watch(() => {
    const s = store.state.get();
    const names = (s && s.presets) || [];
    const key = JSON.stringify([names, store.preset, store.presetSaved, store.unconfirmed, s && s.presetsMissing]);
    if (key === shownKey) {
      return;
    }
    shownKey = key;
    chips.innerHTML = "";
    presetEls = names.map((name) => {
      const current = sameName(name, store.preset);
      const chip = h("div", { class: `chip${current ? " on" : ""}`, text: name });
      let armed = false;
      scope.listen(chip, "click", () => {
        if (current) {
          return;
        }
        const st = store.state.get();
        if (st && st.presetsMissing && (st.presetsMissing[name] || []).length > 0) {
          go("presets");  // (it lacks newer settings: the Presets page asks how to fill them in)
          return;
        }
        if ((!store.presetSaved || store.unconfirmed) && !armed) {
          armed = true;
          chip.className = "chip armed";
          chip.textContent = "Discard changes? Tap again";
          fitSoon();
          return;
        }
        store.load(name, !store.presetSaved || store.unconfirmed);
      });
      chips.appendChild(chip);
      return chip;
    });
    chips.appendChild(more);
    chips.appendChild(manage);
    chips.appendChild(shared);
    fitSoon();
  }));
  scope.add(store.panel.watch(() => fitSoon()));
  const presetTile = h("div", { class: "tile glow wide" },
    h("div", { class: "tlabel" }, icon("presets"), h("span", { text: "Presets" }),
      h("span", { class: "right", text: "Tap one to load it" })),
    chips);

  // ---- about the mod, and how to support it (the user's wish, 2026-11-21; also at the end of What's New)
  const about = aboutTile(scope, (url) => store.host.openUrl(url));

  return h("div", null, fogNotice(scope, store),
           h("div", { class: "tiles" }, wxTile, addonTile, linkTile, presetTile, about));
}

/** The weather as the sim draws it now, by the mod's switch and settings: the visibility (the haze's, with the
 * add-on's reading of it, when the haze comes from the METAR; else the reported one) and what makes it, and how the
 * clouds are drawn. */
function depiction(store: Store, d: Decoded | undefined): { big: string; sub: string; clouds: string } {
  const s = store.state.get();
  const reported = d ? d.visibility || "Visibility not given" : "No METAR yet";
  if (!d && !s) {
    return { big: reported, sub: "The link polls the sim's weather every minute.", clouds: "" };
  }
  const sims = (why: string) => ({ big: reported, sub: `Reported visibility, in the sim's own haze: ${why}`,
                                   clouds: `The sim's own clouds: ${why}` });
  if (!s) {
    return { big: reported, sub: "Reported visibility", clouds: "" };
  }
  if (!s.enabled) {
    return sims("the mod is off.");
  }
  const radius = specValue("C21", store.raw("C21"));
  const clouds = store.raw("C20") > 0
    ? `Anchored to the reported layers near stations${radius ? `, within ${radius}` : ""} (live weather only).`
    : `The sim's own layers${store.raw("C0") > 0 ? ", in the mod's lighting" : ""}.`;
  if (!(store.raw("T0") > 0)) {
    return { big: reported, sub: "Reported visibility, in the sim's own haze: Mod atmosphere is off.", clouds };
  }
  if (!(store.raw("T13") > 0)) {
    return { big: reported, sub: "Reported visibility; the haze is your manual settings, not from the METAR.", clouds };
  }
  const r = hazeReading(s.weather);
  if (!r || !r.visibility) {
    return { big: reported, sub: "Reported visibility; the haze follows the METAR once the add-on has one.", clouds };
  }
  const from = r.stations > 1 ? `from ${r.stations} stations' METARs` : "from the METAR";
  return {
    big: r.visibility,
    sub: `Visibility in ${r.kind}${r.humidity ? `, humidity ${r.humidity}` : ""}, ${from}`
      + `${d && d.visibility ? ` (reported: ${d.visibility})` : ""}`,
    clouds,
  };
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
  // main switch alone, as Mod clouds: a tile in the page's colour)
  for (const tab of tabs) {
    if (!tab.name && tab.settings.length === 1 && tab.settings[0].kind === "switch") {
      page.appendChild(h("div", { class: "tiles" }, masterTile(scope, store, tab.settings[0])));
      continue;
    }
    page.appendChild(section(tab.name, SECTION_ICON[tab.name] || "tools",
                             ...tab.settings.map((s) => settingRow(scope, store, s))));
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

/** The Sky & Haze page's groups (Mod atmosphere, Haze, Manual Haze, Colours) and the Clouds page's (Mod clouds,
 * Sunlight, Shape, Weather). */
/** Each group's mark. */
const SECTION_ICON: Record<string, string> = {
  "Haze": "haze", "Manual Haze": "sliders", "Colours": "palette", "Sunlight": "sun", "Shape": "clouds", "Weather": "rain",
};

/** A page's main switch (Mod atmosphere, Mod clouds) as a tile in the page's colour: on or off in large, what that
 * means now in a line, its description on request. */
function masterTile(scope: Scope, store: Store, spec: Spec): HTMLElement {
  const sky = spec.key === "T0";
  const on = new Value(false);
  const big = h("div", { class: "big" });
  const sub = h("div", { class: "bigsub" });
  const tip = h("div", { class: "ttip", style: "display: none" }, ...spec.tip.split("\n").map((p) => h("p", { text: p })));
  const info = h("span", { class: "tinfo", title: "What it does" }, icon("info"));
  scope.listen(info, "click", () => { tip.style.display = tip.style.display === "none" ? "" : "none"; });
  const show = () => {
    const s = store.state.get();
    const v = store.raw(spec.key) > 0;
    on.set(v);
    big.textContent = v ? "On" : "Off";
    sub.textContent = !s ? "Waiting for the add-on ..."
      : !s.enabled ? "The whole mod is off: its switch is at the top."
      : sky ? (!v ? "The sim's own haze and sky."
        : store.raw("T13") > 0 ? "Haze from the METARs, and real-world sky colours."
        : "Your own haze settings, and real-world sky colours.")
      : v ? "The mod's cloud light, solidity, types and bases." : "The sim's own cloud light and shape.";
  };
  scope.add(store.state.watch(show));
  scope.add(store.key(spec.key).watch(show));
  return h("div", { class: `tile wide ${sky ? "sun" : "dusk"}` },
    tileLabel(sky ? "sky" : "clouds", spec.label, info),
    h("div", { class: "tmain" }, big, switchEl(scope, on, () => store.set(spec.key, on.get() ? 0 : 1))),
    sub, tip);
}

export const SKY_TABS = (): TabSpec[] => TABS.slice(0, 4);
export const CLOUD_TABS = (): TabSpec[] => TABS.slice(4);
