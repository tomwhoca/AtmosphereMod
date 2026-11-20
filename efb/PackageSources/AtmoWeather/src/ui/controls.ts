// The redesign's controls, from plain elements: a switch, a button, the value bar (a slider without a handle: a bar
// as high as the row, filled to the value, the value written inside it, minus and plus at its sides), and a
// setting's row (its name, its help on request, its control, a reset to the shipped default). Mouse events only:
// the tablet's engine has no pointer events.

import type { SliderSpec, Spec } from "../specs";
import { h, Scope, Value } from "./dom";
import { icon } from "./icons";
import { Store } from "./model";

/** While plus or minus is held: the first repeat after this long, then every REPEAT_MS. */
const REPEAT_AFTER_MS = 400, REPEAT_MS = 90;

/** A switch showing `on` and calling `toggle` on a click. */
export function switchEl(scope: Scope, on: Value<boolean>, toggle: () => void): HTMLElement {
  const el = h("div", { class: "switch" });
  scope.add(on.watch((v) => { el.className = `switch${v ? " on" : ""}`; }));
  scope.listen(el, "click", (e) => {
    e.stopPropagation();
    toggle();
  });
  return el;
}

/** A button; `enabled` greys it out and ignores clicks while false. */
export function button(scope: Scope, label: Value<string> | string, kind: string, onClick: () => void,
                       enabled?: Value<boolean>): HTMLElement {
  const el = h("div", { class: `btn ${kind}` });
  if (typeof label === "string") {
    el.textContent = label;
  } else {
    scope.add(label.watch((t) => { el.textContent = t; }));
  }
  if (enabled) {
    scope.add(enabled.watch((on) => { el.className = `btn ${kind}${on ? "" : " inert"}`; }));
  }
  scope.listen(el, "click", () => {
    if (!enabled || enabled.get()) {
      onClick();
    }
  });
  return el;
}

/** A button that acts on a click and again and again while held. */
function repeatButton(scope: Scope, name: string, act: () => void, done: () => void): HTMLElement {
  const el = h("div", { class: "sbtn", title: name === "minus" ? "Less" : "More" }, icon(name));
  let wait: ReturnType<typeof setTimeout> | undefined, every: ReturnType<typeof setInterval> | undefined;
  const stop = () => {
    if (wait !== undefined || every !== undefined) {
      clearTimeout(wait);
      clearInterval(every);
      wait = every = undefined;
      document.removeEventListener("mouseup", stop);
      done();
    }
  };
  scope.listen(el, "mousedown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    act();
    wait = setTimeout(() => { every = setInterval(act, REPEAT_MS); }, REPEAT_AFTER_MS);
    document.addEventListener("mouseup", stop);
  });
  scope.add(stop);
  return el;
}

export interface BarRange {
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
}

/** The value bar: drag or tap on it, or step with minus and plus; `change` while it moves (`final` false) and once
 * more when it stops. The value is written inside, twice: in the text's colour over the empty part and, clipped to
 * the fill, in the colour on the accent. While dragged, the fill follows the mouse (the value snaps to its steps,
 * the fill to the value on release): steps of 5 % moved it in jumps. */
export function valueBar(scope: Scope, value: Value<number>, r: BarRange,
                         change: (v: number, final: boolean) => void): HTMLElement {
  const fill = h("div", { class: "bfill" });
  const text = h("div", { class: "btext" });
  const onText = h("div", { class: "btext on" });
  const clip = h("div", { class: "bclip" }, onText);
  const bar = h("div", { class: "bar" }, fill, text, clip);
  const snap = (v: number) => +Math.max(r.min, Math.min(r.max, r.min + Math.round((v - r.min) / r.step) * r.step)).toFixed(6);
  let shownPct = "", shownText = "", dragging = false;
  const place = (f: number) => {
    const pct = `${(f * 100).toFixed(2)}%`;
    if (pct !== shownPct) {
      shownPct = pct;
      fill.style.width = pct;
      clip.style.width = pct;  // (the copy in it as wide as the bar: in the theme, .btext.on)
    }
  };
  const show = (v: number) => {
    if (!dragging) {
      place(r.max > r.min ? Math.max(0, Math.min(1, (v - r.min) / (r.max - r.min))) : 0);
    }
    const t = r.format(v);
    if (t !== shownText) {
      shownText = t;
      text.textContent = onText.textContent = t;
    }
  };
  scope.add(value.watch(show));
  /** Where the mouse is along the bar (0..1), the fill placed there, and the value there. */
  const at = (e: MouseEvent): number => {
    const b = bar.getBoundingClientRect();
    const f = b.width > 0 ? Math.max(0, Math.min(1, (e.clientX - b.left) / b.width)) : 0;
    place(f);
    return snap(r.min + f * (r.max - r.min));
  };
  scope.listen(bar, "mousedown", (down) => {
    down.preventDefault();
    down.stopPropagation();  // (not a drag of the page)
    dragging = true;
    let last = at(down as MouseEvent);
    change(last, false);
    const move = (m: Event) => {
      const v = at(m as MouseEvent);
      if (v !== last) {
        last = v;
        change(v, false);
      }
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      dragging = false;
      change(last, true);
      show(value.get());  // (the fill to the value it snapped to)
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
  const step = (sign: number) => () => change(snap(value.get() + sign * r.step), false);
  const settle = () => change(value.get(), true);
  return h("div", { class: "stepper" }, repeatButton(scope, "minus", step(-1), settle), bar,
           repeatButton(scope, "plus", step(1), settle));
}

/** Whether a setting has an effect now: not while the setting it needs is 0, nor a manual haze setting while the
 * haze comes from the METAR (T13). */
export function active(store: Store, spec: Spec): boolean {
  if (spec.kind === "slider" && spec.needs && !(store.raw(spec.needs) > 0)) {
    return false;
  }
  return !(spec.kind === "slider" && spec.manualHaze && store.raw("T13") > 0);
}

/** Shown at all: METAR-only settings while the haze comes from the METAR. */
export function shown(store: Store, spec: Spec): boolean {
  return !(spec.kind === "slider" && spec.metarOnly && !(store.raw("T13") > 0));
}

/** A setting's name: an experimental one's without "(experimental)", which its icon says instead. */
export function labelOf(spec: Spec): HTMLElement {
  const name = h("span", { text: spec.experimental ? spec.label.replace(/\s*\(experimental\)\s*$/i, "") : spec.label });
  return spec.experimental
    ? h("span", { class: "named" }, name, h("span", { class: "exp", title: "Experimental" }, icon("lab")))
    : name;
}

/** An element's class or style only when it changes: every write makes the engine restyle, at each step of a drag. */
function setClass(el: HTMLElement, cls: string): void {
  if (el.className !== cls) {
    el.className = cls;
  }
}

/** A setting's row: watches its setting (and those it depends on), sends its changes. */
export function settingRow(scope: Scope, store: Store, spec: Spec): HTMLElement {
  const tip = h("div", { class: "tip", text: spec.tip });
  const info = h("div", { class: "icon-btn", title: "What it does" }, icon("info"));
  const reset = h("div", { class: "icon-btn reset", title: "Back to the default" }, icon("reset"));
  const row = h("div", { class: "row" },
    h("div", { class: "text" }, h("div", { class: "label" }, labelOf(spec), info), tip));
  scope.listen(info, "click", () => row.classList.toggle("open"));
  scope.listen(reset, "click", () => store.set(spec.key, store.defaultOf(spec.key)));

  const refresh: (() => void)[] = [];
  if (spec.kind === "switch") {
    const on = new Value(false);
    row.appendChild(h("div", { class: "control switchctl" }, reset, h("span", { class: "spacer" }),
                      switchEl(scope, on, () => store.set(spec.key, on.get() ? 0 : 1))));
    refresh.push(() => on.set(store.raw(spec.key) > 0));
  } else {
    const s = spec as SliderSpec;
    const display = new Value(s.toDisplay(store.raw(s.key)));
    let dragging = false;
    const bar = valueBar(scope, display, { min: s.min, max: s.max, step: s.step, format: s.format }, (d, final) => {
      dragging = !final;
      display.set(d);
      store.set(s.key, s.toRaw(d), !final);
    });
    row.appendChild(h("div", { class: "control" }, bar, reset));
    refresh.push(() => {
      if (dragging) {
        return;  // (the bar shows the drag's own value, not its echo through the raw units)
      }
      // (a manual haze setting while the METAR sets the haze: the METAR's value, greyed)
      const weather = s.manualHaze && store.raw("T13") > 0 ? store.fromWeather(s.key) : undefined;
      display.set(s.toDisplay(weather !== undefined ? weather : store.raw(s.key)));
    });
  }
  refresh.push(() => {
    const changed = Math.abs(store.raw(spec.key) - store.defaultOf(spec.key)) > 1e-6;
    setClass(reset, `icon-btn reset${changed && active(store, spec) ? "" : " faded"}`);
    setClass(row, `row${row.classList.contains("open") ? " open" : ""}${active(store, spec) ? "" : " off"}`);
    const display = shown(store, spec) ? "" : "none";
    if (row.style.display !== display) {
      row.style.display = display;
    }
  });
  const run = () => refresh.forEach((f) => f());
  scope.add(store.state.watch(run));
  // (its own setting, and those its look depends on: a change here redraws these rows only)
  const deps = [spec.key];
  if (spec.kind === "slider" && spec.needs) {
    deps.push(spec.needs);
  }
  if (spec.kind === "slider" && (spec.manualHaze || spec.metarOnly)) {
    deps.push("T13");
  }
  deps.forEach((k) => scope.add(store.key(k).watch(run)));
  return row;
}
