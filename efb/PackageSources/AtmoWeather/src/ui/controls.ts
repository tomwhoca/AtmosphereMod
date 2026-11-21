// The redesign's controls, from plain elements: a switch, a button, the value bar (a slider without a handle: a bar
// as high as the row, filled to the value, the value written inside it, minus and plus at its sides), and a
// setting's row (its name, its help on request, its control, a reset to the shipped default). Mouse events only:
// the tablet's engine has no pointer events.

import { SliderSpec, Spec, TABS } from "../specs";
import { h, Scope, Value } from "./dom";
import { Host } from "./host";
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

/** A card: a title over its body. */
export function card(title: string, ...children: (Node | null)[]): HTMLElement {
  return h("div", { class: "card" }, h("h2", { text: title }), h("div", { class: "body" }, ...children));
}

/** A tap that asks for a second one before it acts (if `needed`): the button reads `armedText` meanwhile. */
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

/** A row of options, one chosen (`selected`; "" for none yet). */
export function choice(scope: Scope, options: readonly string[], selected: Value<string>): HTMLElement {
  const row = h("div", { class: "choice" });
  for (const o of options) {
    const chip = h("div", { class: "chip", text: o });
    scope.listen(chip, "click", () => selected.set(o));
    scope.add(selected.watch((s) => { chip.className = `chip${s === o ? " on" : ""}`; }));
    row.appendChild(chip);
  }
  return row;
}

export interface MenuItem {
  id: string;
  label: string;
  /** A second line, smaller; `warn` colours it. */
  sub?: string;
  warn?: boolean;
}

/** An icon that opens a menu under it, at its right: the items, the chosen one ticked. A pick closes it, as does a
 * click anywhere else. The icon is marked while the choice isn't `normal`. */
export function iconMenu(scope: Scope, iconName: string, title: string, items: Value<readonly MenuItem[]>,
                         chosen: Value<string>, normal: string): HTMLElement {
  const btn = h("div", { class: "tool", title }, icon(iconName));
  const menu = h("div", { class: "menu" });
  const wrap = h("div", { class: "drop" }, btn, menu);
  const open = new Value(false);
  let rows = new Scope();
  scope.add(() => rows.dispose());
  const render = () => {
    rows.dispose();
    rows = new Scope();
    menu.innerHTML = "";
    for (const it of items.get()) {
      const on = it.id === chosen.get();
      const row = h("div", { class: `item${on ? " on" : ""}` },
        h("span", { class: "tick" }, on ? icon("check") : null),
        h("div", { class: "text" }, h("div", { text: it.label }),
          it.sub ? h("div", { class: `sub${it.warn ? " warn" : ""}`, text: it.sub }) : null));
      rows.listen(row, "click", () => {
        open.set(false);
        chosen.set(it.id);
      });
      menu.appendChild(row);
    }
  };
  const mark = () => {
    btn.className = `tool${open.get() ? " open" : ""}${chosen.get() !== normal ? " set" : ""}`;
    menu.style.display = open.get() ? "" : "none";
  };
  scope.add(items.watch(render));
  scope.add(chosen.watch(() => {
    render();
    mark();
  }));
  scope.add(open.watch(mark));
  scope.listen(btn, "click", () => open.set(!open.get()));
  scope.listen(document, "mousedown", (e) => {
    if (open.get() && !wrap.contains(e.target as Node)) {
      open.set(false);
    }
  });
  return wrap;
}

export interface FieldOptions {
  placeholder: string;
  value: Value<string>;
  /** The characters it takes (others typed are ignored). */
  accepts?: (c: string) => boolean;
  max?: number;
  /** Enter pressed, or the text came from the OS's keyboard. */
  enter?: () => void;
}

/** A text field. While it's focused, it has the keyboard (Host.textFocus): in the sim, keys are the sim's commands
 * otherwise. Enter (or text from the OS's keyboard) confirms, Escape or a click outside the tablet lets go. */
export function textField(scope: Scope, host: Host, o: FieldOptions): HTMLInputElement {
  const input = h("input", { class: "field" });
  input.type = "text";
  input.placeholder = o.placeholder;
  if (o.max) {
    input.maxLength = o.max;
  }
  scope.add(o.value.watch((v) => {
    if (input.value !== v) {
      input.value = v;
    }
  }));
  let focused = false;
  const letGo = () => {
    if (focused) {
      focused = false;
      host.textFocus(false);
    }
  };
  scope.listen(input, "focus", () => {
    focused = true;
    host.textFocus(true, {
      text: (t) => {
        o.value.set(t);
        input.blur();
        if (o.enter) {
          o.enter();
        }
      },
      outside: () => input.blur(),
    });
  });
  scope.listen(input, "blur", letGo);
  scope.listen(input, "input", () => o.value.set(input.value));
  scope.listen(input, "keypress", (e) => {
    const k = e as KeyboardEvent;
    const code = k.charCode || k.keyCode;
    if (o.accepts && code >= 32 && !o.accepts(String.fromCharCode(code))) {
      e.preventDefault();
    }
  });
  scope.listen(input, "keydown", (e) => {
    const code = (e as KeyboardEvent).keyCode;
    if (code === 13) {
      e.preventDefault();
      input.blur();
      if (o.enter) {
        o.enter();
      }
    } else if (code === 27) {
      input.blur();
    }
  });
  scope.add(letGo);
  return input;
}

/** A sheet over the app (What's new, the performance report): its title and a close icon over a body that scrolls.
 * Returns the sheet and its body (app.ts lets it scroll by dragging). */
export function sheet(scope: Scope, title: string, close: () => void,
                      ...children: (Node | null)[]): { el: HTMLElement; body: HTMLElement } {
  const x = h("div", { class: "icon-btn", title: "Close" }, icon("close"));
  scope.listen(x, "click", close);
  const body = h("div", { class: "sheet-body" }, ...children);
  const el = h("div", { class: "sheet" },
    h("div", { class: "sheet-card" }, h("div", { class: "sheet-head" }, h("h2", { text: title }), x), body));
  return { el, body };
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

/** Every setting the app shows, by key. */
const SPECS = new Map<string, Spec>();
TABS.forEach((t) => t.settings.forEach((s) => SPECS.set(s.key, s)));

/** Why a setting has no effect now, in words, or "" if it has: a manual haze setting while the haze comes from the
 * METAR (T13); a setting whose `needs` is at 0, along the chain (Cloud bases needs Solid clouds, which needs Mod
 * clouds). */
export function blocked(store: Store, spec: Spec): string {
  // (the chain first: with Mod atmosphere off, that's the reason, not Haze from METAR)
  const need = spec.needs;
  if (need) {
    const other = SPECS.get(need);
    if (!(store.raw(need) > 0)) {
      const name = other ? other.label : need;
      const at = other && other.kind === "slider" ? other.format(other.toDisplay(0)) : "off";
      return `Has no effect while “${name}” is ${at === "off" ? "off" : `at ${at}`}`;
    }
    const why = other ? blocked(store, other) : "";
    if (why) {
      return why;
    }
  }
  if (spec.kind === "slider" && spec.manualHaze && store.raw("T13") > 0) {
    return "Set by the METAR while “Haze from METAR” is on";
  }
  return "";
}

/** Whether a setting has an effect now (blocked: why not). */
export function active(store: Store, spec: Spec): boolean {
  return blocked(store, spec) === "";
}

/** The settings a setting's effect depends on: its `needs`, theirs, and T13 for the haze settings. */
function dependsOn(spec: Spec): string[] {
  const keys: string[] = [];
  let s: Spec | undefined = spec;
  while (s && s.needs && keys.indexOf(s.needs) < 0) {
    keys.push(s.needs);
    s = SPECS.get(s.needs);
  }
  if (spec.kind === "slider" && (spec.manualHaze || spec.metarOnly)) {
    keys.push("T13");
  }
  return keys;
}

/** Shown at all: METAR-only settings while the haze comes from the METAR. */
export function shown(store: Store, spec: Spec): boolean {
  return !(spec.kind === "slider" && spec.metarOnly && !(store.raw("T13") > 0));
}

/** A setting's name, an experimental one's with its mark. */
export function labelOf(spec: Spec): HTMLElement {
  const name = h("span", { text: spec.label });
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
  // (a description in paragraphs, split at line breaks: what happens in reality, then what the setting does)
  const tip = h("div", { class: "tip" }, ...spec.tip.split("\n").map((para) => h("p", { text: para })));
  const info = h("div", { class: "icon-btn", title: "What it does" }, icon("info"));
  const reset = h("div", { class: "icon-btn reset", title: "Back to the default" }, icon("reset"));
  const why = h("div", { class: "why" });
  const row = h("div", { class: "row" },
    h("div", { class: "text" }, h("div", { class: "label" }, labelOf(spec), info), why, tip));
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
    const reason = blocked(store, spec);
    setClass(reset, `icon-btn reset${changed && !reason ? "" : " faded"}`);
    setClass(row, `row${row.classList.contains("open") ? " open" : ""}${reason ? " off" : ""}`);
    if (why.textContent !== reason) {
      why.textContent = reason;
    }
    const display = shown(store, spec) ? "" : "none";
    if (row.style.display !== display) {
      row.style.display = display;
    }
  });
  const run = () => refresh.forEach((f) => f());
  scope.add(store.state.watch(run));
  // (its own setting, and those its effect depends on: a change there redraws these rows only)
  [spec.key, ...dependsOn(spec)].forEach((k) => scope.add(store.key(k).watch(run)));
  return row;
}
