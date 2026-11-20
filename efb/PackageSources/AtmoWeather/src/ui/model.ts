// The redesigned UI's model: what the add-on says (its state message) and what the UI asks of it. Settings change
// at once on screen (the add-on's answer follows a moment later and confirms them).

import { DEFAULTS } from "../defaults";
import { Value } from "./dom";
import { AddonState, Host, Panel } from "./host";

export const PRESET_DEFAULT = "Default";
/** Until the add-on answers, the state is asked for this often. */
const STATE_RETRY_MS = 3000;
/** While a slider is dragged, its value goes to the add-on at most this often (and once more on release). */
const SEND_EVERY_MS = 150;
/** A value set here stands until the add-on's state has it, or for this long after its last change: the add-on's
 * answers to the sends while dragging carry older values, which made the bar jump back (2026-11-20). */
const SETTLE_MS = 1500;

export const sameName = (a: string, b: string): boolean => a.toLowerCase() === b.toLowerCase();

export class Store {
  public readonly state = new Value<AddonState | undefined>(undefined);
  public readonly panel = new Value<Panel>({ width: 0, height: 0, vertical: true, size: 0 });
  public readonly metar = new Value("");
  public readonly link = new Value("");
  /** Changes made here the add-on hasn't confirmed yet: the settings show these. */
  private readonly pending = new Map<string, { value: number; at: number }>();
  /** Each setting's value as a Value of its own: a change redraws its row, not the page. */
  private readonly keys = new Map<string, Value<number>>();
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly drops: (() => void)[] = [];

  public constructor(public readonly host: Host) {
    this.drops.push(host.onState((s) => this.accept(s)));
    this.drops.push(host.onPanel((p) => this.panel.set(p)));
    this.drops.push(host.onMetar((m) => this.metar.set(m)));
    this.drops.push(host.onLinkStatus((l) => this.link.set(l)));
    host.send({ get: true });
    const retry = setInterval(() => {
      if (this.state.get() === undefined) {
        host.send({ get: true });
      }
    }, STATE_RETRY_MS);
    this.drops.push(() => clearInterval(retry));
  }

  /** The add-on's state: a value set here stays until the state has it (or it settled); then each setting's Value. */
  private accept(s: AddonState): void {
    const now = Date.now();
    this.pending.forEach((p, key) => {
      const v = Store.valueIn(s, key);
      // (the same within a float's precision: the add-on keeps floats)
      if ((v !== undefined && Math.abs(v - p.value) <= 1e-6 * Math.max(1, Math.abs(p.value)))
          || now - p.at > SETTLE_MS) {
        this.pending.delete(key);
      }
    });
    this.state.set(this.pending.size > 0 && s.presetSaved ? { ...s, presetSaved: false } : s);
    this.keys.forEach((v, key) => v.set(this.raw(key)));
  }

  private static valueIn(s: AddonState | undefined, key: string): number | undefined {
    const list = s && (key[0] === "C" ? s.C : key[0] === "T" ? s.T : undefined);
    const v = list ? list[parseInt(key.slice(1), 10)] : undefined;
    return typeof v === "number" ? v : undefined;
  }

  /** A setting's value (raw), as a Value that changes with it: from here at once, from the add-on when it's not
   * one set here a moment ago. */
  public key(key: string): Value<number> {
    let v = this.keys.get(key);
    if (!v) {
      v = new Value(this.raw(key));
      this.keys.set(key, v);
    }
    return v;
  }

  public dispose(): void {
    this.drops.forEach((d) => d());
    this.timers.forEach((t) => clearTimeout(t));
  }

  public get connected(): boolean {
    return this.state.get() !== undefined;
  }

  /** A setting's raw value (T3, C16, ...): the one just set here, else the add-on's, else its default. */
  public raw(key: string): number {
    const p = this.pending.get(key);
    if (p) {
      return p.value;
    }
    const v = Store.valueIn(this.state.get(), key);
    return v !== undefined ? v : this.defaultOf(key);
  }

  public defaultOf(key: string): number {
    return DEFAULTS[key] !== undefined ? DEFAULTS[key] : 0;
  }

  /** What the weather makes of a setting the haze comes from (TE), if it does. */
  public fromWeather(key: string): number | undefined {
    const s = this.state.get();
    if (!s || !s.TE || key[0] !== "T") {
      return undefined;
    }
    const v = s.TE[parseInt(key.slice(1), 10)];
    return typeof v === "number" ? v : undefined;
  }

  /** Sets a setting: on screen now, to the add-on now or (`soon`, while dragging) within SEND_EVERY_MS. */
  public set(key: string, raw: number, soon = false): void {
    this.pending.set(key, { value: raw, at: Date.now() });
    this.key(key).set(raw);
    const s = this.state.get();
    if (s && s.presetSaved !== false) {
      this.state.set({ ...s, presetSaved: false });  // (unsaved at once; the add-on agrees a moment later)
    }
    const send = () => {
      this.timers.delete(key);
      const p = this.pending.get(key);
      this.host.send({ set: { [key]: p ? p.value : raw } });
    };
    if (!soon) {
      const t = this.timers.get(key);
      if (t !== undefined) {
        clearTimeout(t);
      }
      send();
    } else if (!this.timers.has(key)) {
      this.timers.set(key, setTimeout(send, SEND_EVERY_MS));
    }
  }

  public setEnabled(on: boolean): void {
    this.host.send({ enabled: on });
    const s = this.state.get();
    if (s) {
      this.state.set({ ...s, enabled: on });
    }
  }

  // ---- presets

  public get preset(): string {
    const s = this.state.get();
    return (s && s.preset) || "";
  }

  public get presetSaved(): boolean {
    const s = this.state.get();
    return !s || s.presetSaved !== false;
  }

  /** Whether the changes can be saved into the preset they were made to (not Default, not none). */
  public get canSaveInPlace(): boolean {
    return this.preset !== "" && !sameName(this.preset, PRESET_DEFAULT);
  }

  public saveInPlace(): void {
    if (this.canSaveInPlace) {
      this.host.send({ savePreset: this.preset });
    }
  }

  public saveAs(name: string): void {
    this.host.send({ savePreset: name });
  }

  /** The changes undone: the preset loaded again as it was saved. */
  public undo(): void {
    if (this.preset !== "") {
      this.host.send({ loadPreset: this.preset, discard: true });
    }
  }

  public load(name: string, discard: boolean): void {
    this.host.send({ loadPreset: name, ...(discard ? { discard: true } : {}) });
  }

  public remove(name: string): void {
    this.host.send({ deletePreset: name });
  }
}
