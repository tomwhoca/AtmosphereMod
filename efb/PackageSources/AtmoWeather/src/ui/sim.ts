// The UI in the sim: the Host on the EFB app's shell (AtmoWeather.tsx), which keeps only what can't be reloaded: the
// app's place in the EFB, CommBus and the METAR link. All else is here, in the UI module, so tools/efb_reload.py
// replaces it in the running sim (since 2026-11-21: a change to the shell needs a restart). The tablet's globals
// (PanelInfo, EFB_API, SimVar, GetStoredData, Coherent) are read where they're used, without the SDK's types.

import { AddonState, Host, Panel, sizeOf, TextEvents } from "./host";

/** What the shell gives the UI (AtmoWeather.tsx makes it). */
export interface SimBridge {
  /** The mod's version (the EFB app's). */
  readonly version: string;
  /** A CommBus event to the add-on (SimConnect clients). */
  send(event: string, data: unknown): void;
  /** A CommBus event from the add-on. Returns the unsubscribe. */
  listen(event: string, callback: (args: string) => void): () => void;
  /** The METAR of the airport nearest the aircraft, now and as it changes. */
  onAirport(callback: (metar: string) => void): () => void;
  /** The METAR link's status line, now and as it changes. */
  onStatus(callback: (status: string) => void): () => void;
  /** The tablet's size or orientation changed, or the app was shown again. */
  onRelayout(callback: () => void): () => void;
}

export const EVENT_SETTINGS = "AtmosphereMod.Settings";
export const EVENT_STATE = "AtmosphereMod.State";

/** After the tablet changes, its area is read again this late, once the EFB has laid it out. */
const RELAYOUT_MS = 250;

interface Readable<T> {
  get(): T;
}
interface TabletGlobals {
  PanelInfo?: { size: { width: Readable<number>; height: Readable<number> }; orientation: Readable<number> };
  EFB_API?: { efbSettingsManager?: { getSetting(name: string): Readable<unknown> } };
  SimVar?: { GetSimVarValue(name: string, unit: string): unknown };
  GetStoredData?: (key: string) => unknown;
  SetStoredData?: (key: string, value: string) => void;
  Coherent?: {
    call?: (name: string, ...args: unknown[]) => unknown;
    trigger?: (name: string, ...args: unknown[]) => void;
    on?: (name: string, callback: (...args: never[]) => void) => unknown;
    off?: (name: string, callback: (...args: never[]) => void) => void;
  };
}
const tablet = (): TabletGlobals => window as unknown as TabletGlobals;

/** The id the sim gets for the focused field (the SDK's own fields send their uuid; any string does). */
const FIELD_ID = "atmo-ui-text";

export class SimHost implements Host {
  public readonly version: string;
  private readonly drops: (() => void)[] = [];
  private readonly states: ((s: AddonState) => void)[] = [];
  private readonly panels: ((p: Panel) => void)[] = [];
  private text?: TextEvents;
  private readonly fromOs = (t: string) => this.text && this.text.text && this.text.text(String(t));
  private readonly outside = () => this.text && this.text.outside && this.text.outside();

  public constructor(private readonly bridge: SimBridge, private readonly area: () => HTMLElement | null) {
    this.version = bridge.version;
    this.drops.push(bridge.listen(EVENT_STATE, (args) => {
      let s: AddonState;
      try {
        s = JSON.parse(args) as AddonState;
      } catch {
        return;
      }
      this.states.slice().forEach((cb) => cb(s));
    }));
    this.drops.push(bridge.onRelayout(() => this.relayout()));
  }

  /** The UI unmounted (or reloaded): every listener off, the keyboard given back. */
  public dispose(): void {
    if (this.text) {
      this.textFocus(false);
    }
    this.drops.forEach((d) => d());
    this.drops.length = 0;
  }

  public send(data: unknown): void {
    this.bridge.send(EVENT_SETTINGS, data);
  }

  public onState(callback: (state: AddonState) => void): () => void {
    this.states.push(callback);
    return () => drop(this.states, callback);
  }

  public onMetar(callback: (metar: string) => void): () => void {
    return this.bridge.onAirport(callback);
  }

  public onLinkStatus(callback: (status: string) => void): () => void {
    return this.bridge.onStatus(callback);
  }

  public onPanel(callback: (panel: Panel) => void): () => void {
    this.panels.push(callback);
    callback(this.panel());
    return () => drop(this.panels, callback);
  }

  /** The tablet changed: the area again, now and once the EFB has laid it out. */
  private relayout(): void {
    const tell = () => {
      const p = this.panel();
      this.panels.slice().forEach((cb) => cb(p));
    };
    tell();
    setTimeout(tell, RELAYOUT_MS);
  }

  private panel(): Panel {
    const g = tablet();
    const info = g.PanelInfo;
    const area = this.area();
    const w = info ? info.size.width.get() : area ? area.offsetWidth : 0;
    const h = info ? info.size.height.get() : area ? area.offsetHeight : 0;
    // (the size from the EFB's own setting: the pixels follow the sim's window too)
    const efb = g.EFB_API && g.EFB_API.efbSettingsManager;
    const setting = efb ? Number(efb.getSetting("efbSize").get()) : NaN;
    return { width: w, height: h, vertical: info ? info.orientation.get() === 0 : h >= w,
             size: setting >= 0 && setting <= 2 ? setting : sizeOf(w, h) };
  }

  public simvar(name: string, unit: string): number {
    const sv = tablet().SimVar;
    return sv ? Number(sv.GetSimVarValue(name, unit)) : 0;
  }

  public openUrl(url: string): void {
    // (the PC's default browser. The sim's own OpenBrowser(url), in every UI view, is Coherent.trigger of this:
    // the tablet ignores that; as a call it opens (measured 2026-11-21))
    const c = tablet().Coherent;
    if (c && c.call) {
      c.call("OPEN_WEB_BROWSER", url);
    }
  }

  public textFocus(focused: boolean, events?: TextEvents): void {
    // (as the EFB SDK's own text fields do: the sim sends the keys to the tablet while one has asked)
    const c = tablet().Coherent;
    if (!c || !c.trigger) {
      return;
    }
    if (focused) {
      if (!this.text) {
        c.trigger("FOCUS_INPUT_FIELD", { title: "Title", description: "", default_text: "", is_numeric: false,
                                         is_efb: true });
        if (c.on) {
          c.on("mousePressOutsideView", this.outside);
          c.on("SetInputTextFromOS", this.fromOs as (...args: never[]) => void);
        }
      }
      this.text = events || {};
    } else if (this.text) {
      this.text = undefined;
      c.trigger("UNFOCUS_INPUT_FIELD", FIELD_ID);
      if (c.off) {
        c.off("mousePressOutsideView", this.outside);
        c.off("SetInputTextFromOS", this.fromOs as (...args: never[]) => void);
      }
    }
  }

  public load(key: string): string | undefined {
    const get = tablet().GetStoredData;
    const v = get ? get(key) : undefined;
    return typeof v === "string" && v !== "" ? v : undefined;
  }

  public save(key: string, value: string): void {
    const set = tablet().SetStoredData;
    if (set) {
      set(key, value);
    }
  }
}

function drop<T>(list: T[], item: T): void {
  const i = list.indexOf(item);
  if (i >= 0) {
    list.splice(i, 1);
  }
}
