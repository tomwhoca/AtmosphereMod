// The redesigned EFB app's UI: mount(root, host) draws it into root and returns the unmount. Phase 1 (2026-11-20)
// is the layout check, which proves the harness, the in-sim reload and the six tablet layouts; the design follows.

import { h, Scope, Value } from "./dom";
import { AddonState, Host, Panel } from "./host";
import { UI_CSS } from "./styles";

const STYLE_ID = "atmo-ui-style";
const SIZE_NAMES = ["small", "medium", "large"];
/** The small tablet's short side (px), against which --u scales every length. */
const BASE_PX = 624;
/** Until the add-on answers, the state is asked for this often. */
const STATE_RETRY_MS = 3000;

export function mount(root: HTMLElement, host: Host): () => void {
  const scope = new Scope();
  let style = document.getElementById(STYLE_ID) as HTMLStyleElement | null;
  if (!style) {
    style = h("style");
    style.id = STYLE_ID;
    document.head.appendChild(style);
  }
  style.textContent = UI_CSS;

  const state = new Value<AddonState | undefined>(undefined);
  const panel = new Value<Panel>({ width: 0, height: 0, vertical: true, size: 0 });
  const metar = new Value("");
  const link = new Value("");
  scope.add(host.onState((s) => state.set(s)));
  scope.add(host.onPanel((p) => panel.set(p)));
  scope.add(host.onMetar((m) => metar.set(m)));
  scope.add(host.onLinkStatus((s) => link.set(s)));
  host.send({ get: true });
  const retry = setInterval(() => {
    if (state.get() === undefined) {
      host.send({ get: true });
    }
  }, STATE_RETRY_MS);
  scope.add(() => clearInterval(retry));

  const ui = h("div", { class: "atmo-ui atmo-ui-check" });
  scope.add(panel.watch((p) => {
    const u = p.width > 0 ? Math.min(p.width, p.height) / BASE_PX : 1;
    ui.style.setProperty("--u", u.toFixed(4));
    ui.className = `atmo-ui atmo-ui-check atmo-ui-${p.vertical ? "v" : "h"} atmo-ui-s${p.size}`;
  }));
  ui.appendChild(checkScreen(scope, host, state, panel, metar, link));
  root.appendChild(ui);

  return () => {
    scope.dispose();
    if (ui.parentNode) {
      ui.parentNode.removeChild(ui);
    }
  };
}

function fact(scope: Scope, name: string, value: Value<string>): HTMLElement {
  const span = h("span");
  scope.add(value.watch((v) => { span.textContent = v; }));
  return h("div", { class: "atmo-ui-fact" }, h("b", { text: name }), span);
}

function derived<T>(scope: Scope, from: Value<T>, map: (v: T) => string): Value<string> {
  const out = new Value(map(from.get()));
  scope.add(from.watch((v) => out.set(map(v))));
  return out;
}

/** The layout check: what the UI sees of the tablet and the add-on, and three test controls. */
function checkScreen(scope: Scope, host: Host, state: Value<AddonState | undefined>, panel: Value<Panel>,
                     metar: Value<string>, link: Value<string>): HTMLElement {
  const area = derived(scope, panel, (p) => `${p.width} x ${p.height} px`);
  const tablet = derived(scope, panel, (p) => `${SIZE_NAMES[p.size] || "?"}, ${p.vertical ? "vertical" : "horizontal"}, ` +
    `u = ${(p.width > 0 ? Math.min(p.width, p.height) / BASE_PX : 1).toFixed(3)}`);
  const addon = derived(scope, state, (s) => (s === undefined ? "waiting for the add-on ..."
    : `connected, add-on ${s.version || "?"}, the mod ${s.enabled ? "on" : "off"}`));
  const preset = derived(scope, state, (s) => (s === undefined ? "-"
    : `${s.preset || "(none)"}${s.presetSaved ? "" : " (changed)"}; ${(s.presets || []).length} presets`));

  // three controls drawn from plain elements: mouse events only (the engine has no pointer events)
  const toggle = h("div", { class: "atmo-ui-toggle" });
  scope.add(state.watch((s) => { toggle.className = `atmo-ui-toggle${s && s.enabled ? " on" : ""}`; }));
  scope.listen(toggle, "click", () => {
    const s = state.get();
    if (s !== undefined) {
      host.send({ enabled: !s.enabled });
    }
  });
  const sliderValue = new Value(50);
  const slider = h("div", { class: "atmo-ui-slider" }, h("div", { class: "track" }), h("div", { class: "fill" }),
                   h("div", { class: "thumb" }));
  const readout = h("div", { class: "atmo-ui-value" });
  scope.add(sliderValue.watch((v) => {
    (slider.children[1] as HTMLElement).style.width = `${v}%`;
    (slider.children[2] as HTMLElement).style.left = `${v}%`;
    readout.textContent = `${v} %`;
  }));
  const drag = (e: MouseEvent) => {
    const r = slider.getBoundingClientRect();
    sliderValue.set(Math.round(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * 20) * 5);
  };
  scope.listen(slider, "mousedown", (e) => {
    drag(e as MouseEvent);
    const move = (m: Event) => drag(m as MouseEvent);
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
  const clicks = new Value(0);
  const button = h("div", { class: "atmo-ui-btn", text: "Ask the add-on again" });
  scope.listen(button, "click", () => {
    clicks.set(clicks.get() + 1);
    host.send({ get: true });
  });
  const clicked = h("span", { style: "margin-left: 1em; opacity: 0.6" });
  scope.add(clicks.watch((n) => { clicked.textContent = n > 0 ? `asked ${n} time${n === 1 ? "" : "s"}` : ""; }));

  return h("div", { style: "display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0" },
    h("div", { class: "atmo-ui-status", text: "status bar: kept free" }),
    h("div", { class: "atmo-ui-body" },
      h("div", { class: "atmo-ui-title", text: "AtmosphereMod" }),
      h("div", { class: "atmo-ui-sub", text: `New UI, layout check (phase 1) - EFB app ${host.version}` }),
      h("div", { class: "atmo-ui-facts" },
        fact(scope, "App area", area),
        fact(scope, "Tablet", tablet),
        fact(scope, "Add-on", addon),
        fact(scope, "Preset", preset),
        fact(scope, "METAR", derived(scope, metar, (m) => m || "-")),
        fact(scope, "METAR link", derived(scope, link, (l) => l || "-"))),
      h("div", { class: "atmo-ui-controls" },
        h("div", { class: "atmo-ui-row" }, h("div", { class: "atmo-ui-label", text: "The mod on or off (sends it)" }), toggle),
        h("div", { class: "atmo-ui-row" }, h("div", { class: "atmo-ui-label", text: "A slider (sends nothing)" }), slider, readout),
        h("div", { class: "atmo-ui-row" }, h("div", { class: "atmo-ui-label" }, button, clicked)))),
    h("div", { class: "atmo-ui-dock", text: "dock button: kept free" }));
}
