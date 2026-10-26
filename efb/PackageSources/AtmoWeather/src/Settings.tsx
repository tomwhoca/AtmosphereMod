// AtmosphereMod's settings in the EFB: the main tunables.ini values as switches
// and sliders, applied by the add-on at once and saved into tunables.ini
// (addon/src/settings_bridge.h has the CommBus messages).
//
// Sliders show friendly units (degrees, %, km); each setting converts to and
// from the raw tunables.ini value. The manual haze sliders are greyed out while
// the haze comes from the METAR, and then show the values the METAR set.

import { ElementAccordion, Slider, Switch, TabSelector, TTButton } from "@efb/efb-api";
import {
  ComponentProps,
  DisplayComponent,
  FSComponent,
  MappedSubject,
  Subject,
  Subscribable,
  Subscription,
  VNode,
} from "@microsoft/msfs-sdk";

import { DEFAULTS } from "./defaults";

export const EVENT_SETTINGS = "AtmosphereMod.Settings";
export const EVENT_STATE = "AtmosphereMod.State";

// ---------------------------------------------------------------- the settings

interface SliderSpec {
  kind: "slider";
  key: string;
  label: string;
  /** In display units. */
  min: number;
  max: number;
  step: number;
  toDisplay: (raw: number) => number;
  toRaw: (display: number) => number;
  format: (display: number) => string;
  /** Greyed out while the haze comes from the METAR (T13), showing the METAR's value. */
  manualHaze?: boolean;
  /** Greyed out while this setting is 0 (no effect then). */
  needs?: string;
  more?: boolean;
}

interface SwitchSpec {
  kind: "switch";
  key: string;
  label: string;
  more?: boolean;
}

type Spec = SliderSpec | SwitchSpec;

interface TabSpec {
  name: string;
  settings: Spec[];
  /** The "More" section's title. */
  more?: string;
}

const clamp = (x: number, lo: number, hi: number): number => Math.min(Math.max(x, lo), hi);
const fixed = (digits: number) => (d: number): string => d.toFixed(digits);
const identity = (x: number): number => x;

/** A raw 0..scale value shown as 0-100 %. */
function percent(key: string, label: string, scale: number, more?: boolean): SliderSpec {
  return {
    kind: "slider", key, label, min: 0, max: 100, step: 5, more,
    toDisplay: (raw) => (raw / scale) * 100,
    toRaw: (d) => (d / 100) * scale,
    format: (d) => `${Math.round(d)} %`,
  };
}

/** A raw value shown as it is, with "off" at 0. */
function plain(key: string, label: string, min: number, max: number, step: number, digits: number,
               extra: Partial<SliderSpec> = {}): SliderSpec {
  return {
    kind: "slider", key, label, min, max, step, toDisplay: identity, toRaw: identity,
    format: (d) => (d <= 0 && min <= 0 ? "off" : d.toFixed(digits)), ...extra,
  };
}

/** The silver lining's half width: ~0.77 (1 - g) rad, so g = 1 - angle / 44.1 deg. */
const LOBE_DEG = 0.77 * (180 / Math.PI);
/** The glory's first red ring: ~24 deg / droplet radius (um). */
const GLORY_DEG_UM = 24;
/** The sim's sunlight deep inside clouds (C5 = 0). */
const SIM_DEEP_LIGHT = 0.033;
/** The sim's forward glare g (T9 = 0), and the slider's range. */
const SIM_GLARE = 0.8, GLARE_LO = 0.5, GLARE_HI = 0.85;

const HAZE_COLOURS: [number, string][] = [[0, "grey"], [0.3, "humid white"], [1.3, "continental blue"], [2, "smoke"]];

export const TABS: TabSpec[] = [
  {
    name: "Haze",
    more: "Manual haze (when not from the METAR)",
    settings: [
      { kind: "switch", key: "T0", label: "Atmosphere (haze and sky)" },
      { kind: "switch", key: "T13", label: "Haze from METAR" },
      { kind: "switch", key: "T11", label: "Distant clouds fade creamy, not grey" },
      {
        kind: "slider", key: "T9", label: "Sun glare in haze", min: 0, max: 100, step: 5,
        toDisplay: (raw) => (clamp(raw > 0 ? raw : SIM_GLARE, GLARE_LO, GLARE_HI) - GLARE_LO) / (GLARE_HI - GLARE_LO) * 100,
        toRaw: (d) => GLARE_LO + (d / 100) * (GLARE_HI - GLARE_LO),
        format: (d) => `${Math.round(d)} %`,
      },
      plain("T3", "Haze near the ground", 1, 15, 0.5, 1, { format: (d) => `×${d.toFixed(1)}`, manualHaze: true, more: true }),
      plain("T1", "Haze layer top", 0.5, 4, 0.1, 1, { format: (d) => `${d.toFixed(1)} km`, manualHaze: true, more: true }),
      plain("T4", "Haze above the layer", 0, 2, 0.1, 1, { format: (d) => `×${d.toFixed(1)}`, manualHaze: true, more: true }),
      plain("T6", "Brown haze (pollution)", 0, 0.4, 0.01, 2, { manualHaze: true, more: true }),
      plain("T10", "Haze colour", 0, 2, 0.1, 1, {
        manualHaze: true, more: true,
        format: (d) => {
          const nearest = HAZE_COLOURS.reduce((a, b) => (Math.abs(b[0] - d) < Math.abs(a[0] - d) ? b : a));
          return `${d.toFixed(1)} ${nearest[1]}`;
        },
      }),
    ],
  },
  {
    name: "Sky & light",
    settings: [
      { kind: "switch", key: "T8", label: "Real-world sky colours" },
      { kind: "switch", key: "C22", label: "Daylight white balance" },
    ],
  },
  {
    name: "Cloud lighting",
    settings: [
      { kind: "switch", key: "C0", label: "Cloud lighting" },
      percent("C2", "Silver lining strength", 0.5),
      {
        kind: "slider", key: "C1", label: "Silver lining size", min: 1, max: 7, step: 0.1,
        toDisplay: (raw) => clamp((1 - raw) * LOBE_DEG, 1, 7),
        toRaw: (d) => 1 - d / LOBE_DEG,
        format: (d) => `${d.toFixed(1)}°`,
      },
      plain("C9", "Glory", 0, 2, 0.1, 1),
      percent("C12", "Your aircraft's shadow on clouds", 1),
      percent("C3", "Bounced light at cloud edges", 1, true),
      {
        kind: "slider", key: "C5", label: "Light deep inside clouds", min: 1, max: 6, step: 0.1, more: true,
        toDisplay: (raw) => clamp((raw > 0 ? raw : SIM_DEEP_LIGHT) / SIM_DEEP_LIGHT, 1, 6),
        toRaw: (d) => d * SIM_DEEP_LIGHT,
        format: (d) => `×${d.toFixed(1)}`,
      },
      percent("C6", "Powder effect (grey rims, sun behind you)", 1, true),
      plain("C8", "Crisper cloud edges", 0, 4, 0.25, 2, { more: true }),
      {
        kind: "slider", key: "C10", label: "Glory ring size", min: 1.5, max: 8, step: 0.5, more: true,
        toDisplay: (raw) => clamp(GLORY_DEG_UM / (raw > 0 ? raw : 10), 1.5, 8),
        toRaw: (d) => GLORY_DEG_UM / d,
        format: (d) => `${d.toFixed(1)}°`,
      },
    ],
  },
  {
    name: "Clouds",
    settings: [
      percent("C20", "Clouds follow the METARs", 1),
      plain("C21", "Station radius", 10, 100, 5, 0, { format: (d) => `${Math.round(d)} km` }),
      plain("C16", "Solid clouds", 0, 4, 0.25, 2, { format: (d) => (d <= 0 ? "the sim's" : d.toFixed(2)) }),
      percent("C23", "Cloud types", 1),
      percent("C28", "Smooth cloud bases", 1),
      {
        kind: "slider", key: "C17", label: "Cloud bases: soft to solid", min: 0, max: 1, step: 0.05, more: true,
        toDisplay: (raw) => clamp((1 - (raw > 0 ? raw : 1)) / 0.67, 0, 1),
        toRaw: (d) => 1 - 0.67 * d,
        format: (d) => (d <= 0.01 ? "the sim's" : d >= 0.99 ? "real" : `${Math.round(d * 100)} %`),
      },
      plain("C19", "High-cloud thickness", 0.5, 3, 0.1, 1, { format: (d) => `×${d.toFixed(1)}`, needs: "C16", more: true }),
      { ...percent("C24", "Stratocumulus thickness", 1, true), needs: "C23" },
      { ...percent("C25", "Stratus thickness", 1, true), needs: "C23" },
      { ...percent("C26", "Altocumulus thickness", 1, true), needs: "C23" },
      { ...percent("C27", "Altostratus thickness", 1, true), needs: "C23" },
    ],
  },
];

// ---------------------------------------------------------------- the model

interface State {
  enabled?: boolean;
  T?: number[];
  C?: number[];
  TE?: number[];
  weather?: string;
}

/** Local edits win over the add-on's echo for this long. */
const EDIT_HOLD_MS = 1500;
/** Slider drags are sent at most this often. */
const SEND_MS = 100;

/** The settings as the add-on has them, and the changes the user makes. */
export class SettingsModel {
  /** The add-on has answered. */
  public readonly connected = Subject.create(false);
  /** The Ctrl+Shift+F9 switch. */
  public readonly enabled = Subject.create(true);
  public readonly weather = Subject.create("");
  private readonly raw = new Map<string, Subject<number>>();
  private readonly effective = new Map<string, Subject<number>>();
  private readonly editedAt = new Map<string, number>();
  private pending: Record<string, number> = {};
  private timer?: number;

  public constructor(private readonly send: (data: unknown) => void) {}

  /** tunables.ini's value. */
  public value(key: string): Subject<number> {
    let s = this.raw.get(key);
    if (s === undefined) {
      s = Subject.create(DEFAULTS[key] ?? 0);
      this.raw.set(key, s);
    }
    return s;
  }

  /** The value the shaders get (the METAR's haze blended in); T only. */
  public effectiveValue(key: string): Subject<number> {
    let s = this.effective.get(key);
    if (s === undefined) {
      s = Subject.create(this.value(key).get());
      this.effective.set(key, s);
    }
    return s;
  }

  public requestState(): void {
    this.send({ get: true });
  }

  public onState(args: string): void {
    let state: State;
    try {
      state = JSON.parse(args) as State;
    } catch {
      return;
    }
    const now = Date.now();
    const apply = (prefix: string, values: number[] | undefined, target: (key: string) => Subject<number>, edits: boolean): void => {
      values?.forEach((v, i) => {
        const key = `${prefix}${i}`;
        if (!edits || now - (this.editedAt.get(key) ?? 0) > EDIT_HOLD_MS) {
          target(key).set(v);
        }
      });
    };
    apply("T", state.T, (k) => this.value(k), true);
    apply("C", state.C, (k) => this.value(k), true);
    apply("T", state.TE, (k) => this.effectiveValue(k), false);
    if (state.enabled !== undefined) {
      this.enabled.set(state.enabled);
    }
    this.weather.set(state.weather ?? "");
    this.connected.set(true);
  }

  /** Sets tunables.ini's value: at once here, in the add-on within SEND_MS. */
  public set(key: string, raw: number): void {
    const rounded = Math.round(raw * 1e4) / 1e4;
    this.value(key).set(rounded);
    this.editedAt.set(key, Date.now());
    this.pending[key] = rounded;
    if (this.timer === undefined) {
      this.timer = window.setTimeout(() => {
        this.timer = undefined;
        const set = this.pending;
        this.pending = {};
        this.send({ set });
      }, SEND_MS);
    }
  }

  public setEnabled(on: boolean): void {
    this.enabled.set(on);
    this.send({ enabled: on });
  }

  /** As Ctrl+Shift+F10: the add-on saves pending changes, then reloads tunables.ini and its shaders. */
  public reload(): void {
    this.send({ reload: true });
  }

  public reset(keys: string[]): void {
    for (const key of keys) {
      if (DEFAULTS[key] !== undefined) {
        this.set(key, DEFAULTS[key]);
      }
    }
  }
}

// ---------------------------------------------------------------- the view

/** Returns a value to show without it counting as the user's change. */
class Quiet {
  private depth = 0;
  public get active(): boolean {
    return this.depth > 0;
  }
  public run(f: () => void): void {
    this.depth++;
    try {
      f();
    } finally {
      this.depth--;
    }
  }
}

interface RowProps<S extends Spec> extends ComponentProps {
  model: SettingsModel;
  spec: S;
  /** The add-on has answered: the controls work. */
  live: Subscribable<boolean>;
}

class SliderRow extends DisplayComponent<RowProps<SliderSpec>> {
  private readonly display = Subject.create(0);
  private readonly text = Subject.create("");
  private readonly quiet = new Quiet();
  private readonly disabled: MappedSubject<[boolean, number, number], boolean>;
  private readonly subs: Subscription[] = [];

  public constructor(props: RowProps<SliderSpec>) {
    super(props);
    const { model, spec } = props;
    this.disabled = MappedSubject.create(
      ([live, metar, needed]) => !live || (spec.manualHaze === true && metar >= 0.5) || (spec.needs !== undefined && needed <= 0),
      props.live,
      model.value("T13"),
      model.value(spec.needs ?? "T0"),
    );
  }

  public onAfterRender(node: VNode): void {
    super.onAfterRender(node);
    const { model, spec } = this.props;
    const shown = MappedSubject.create(
      ([raw, effective, metar]) => spec.toDisplay(spec.manualHaze === true && metar >= 0.5 ? effective : raw),
      model.value(spec.key),
      spec.manualHaze === true ? model.effectiveValue(spec.key) : model.value(spec.key),
      model.value("T13"),
    );
    this.subs.push(shown.sub((d) => this.quiet.run(() => this.display.set(d)), true));
    this.subs.push(this.display.sub((d) => this.text.set(spec.format(d)), true));
  }

  private onChange(d: number): void {
    if (!this.quiet.active && !this.disabled.get()) {
      this.props.model.set(this.props.spec.key, this.props.spec.toRaw(d));
    }
  }

  public render(): VNode {
    const { spec } = this.props;
    return (
      <div class={{ "atmo-row": true, "atmo-disabled": this.disabled }}>
        <div class="atmo-label">{spec.label}</div>
        <div class="atmo-control">
          <Slider value={this.display} min={spec.min} max={spec.max} step={spec.step} disabled={this.disabled}
                  onValueChange={(d) => this.onChange(d)} />
        </div>
        <div class="atmo-value">{this.text}</div>
      </div>
    );
  }

  public destroy(): void {
    this.subs.forEach((s) => s.destroy());
    super.destroy();
  }
}

class SwitchRow extends DisplayComponent<RowProps<SwitchSpec>> {
  private readonly checked = Subject.create(false);
  private readonly quiet = new Quiet();
  private readonly disabled = this.props.live.map((live) => !live);
  private sub?: Subscription;

  public onAfterRender(node: VNode): void {
    super.onAfterRender(node);
    this.sub = this.props.model.value(this.props.spec.key).sub((v) => this.quiet.run(() => this.checked.set(v >= 0.5)), true);
  }

  private onChange(on: boolean): void {
    if (!this.quiet.active) {
      this.props.model.set(this.props.spec.key, on ? 1 : 0);
    }
  }

  public render(): VNode {
    return (
      <div class={{ "atmo-row": true, "atmo-disabled": this.disabled }}>
        <div class="atmo-label">{this.props.spec.label}</div>
        <div class="atmo-control atmo-switch">
          <Switch checked={this.checked} disabled={this.disabled} callback={(on) => this.onChange(on)} />
        </div>
      </div>
    );
  }

  public destroy(): void {
    this.sub?.destroy();
    super.destroy();
  }
}

function row(model: SettingsModel, spec: Spec, live: Subscribable<boolean>): VNode {
  return spec.kind === "slider" ? <SliderRow model={model} spec={spec} live={live} />
    : <SwitchRow model={model} spec={spec} live={live} />;
}

interface SettingsViewProps extends ComponentProps {
  model: SettingsModel;
}

/** The master switch, the weather line, the tabs and their settings. */
export class SettingsView extends DisplayComponent<SettingsViewProps> {
  private readonly tab = Subject.create(0);
  private readonly masterOn = Subject.create(true);
  private readonly quiet = new Quiet();
  private readonly subs: Subscription[] = [];

  public onAfterRender(node: VNode): void {
    super.onAfterRender(node);
    this.subs.push(this.props.model.enabled.sub((on) => this.quiet.run(() => this.masterOn.set(on)), true));
  }

  private keys(tab: TabSpec): string[] {
    return tab.settings.map((s) => s.key);
  }

  public render(): VNode {
    const { model } = this.props;
    const live = model.connected;
    const notLive = live.map((c) => !c);
    return (
      <div class="atmo-settings">
        <div class="atmo-row atmo-master">
          <div class="atmo-label">AtmosphereMod <span class="atmo-hint">(Ctrl+Shift+F9)</span></div>
          <div class="atmo-control atmo-switch">
            <Switch checked={this.masterOn} disabled={notLive}
                    callback={(on) => { if (!this.quiet.active) model.setEnabled(on); }} />
          </div>
        </div>
        <div class="atmo-weather">
          {MappedSubject.create(
            ([c, w]) => (!c ? "Waiting for AtmosphereMod (is the sim running with ReShade and the mod installed?)"
              : w !== "" ? `Weather: ${w}` : "Weather: waiting for the first METAR"),
            live, model.weather,
          )}
        </div>
        <div class="atmo-tabs">
          {TABS.map((t, i) => (
            <TabSelector tabName={t.name} active={this.tab.map((n) => n === i)} hidden={Subject.create(false)}
                         callback={() => this.tab.set(i)} />
          ))}
        </div>
        {TABS.map((t, i) => (
          <div class={{ "atmo-page": true, "atmo-hidden": this.tab.map((n) => n !== i) }}>
            {t.settings.filter((s) => !s.more).map((s) => row(model, s, live))}
            {t.settings.some((s) => s.more) ? (
              <ElementAccordion header={<span class="atmo-more">{t.more ?? "More"}</span>}>
                {t.settings.filter((s) => s.more).map((s) => row(model, s, live))}
              </ElementAccordion>
            ) : null}
            <div class="atmo-buttons">
              <TTButton key="Reset this tab" type="secondary" disabled={notLive} callback={() => model.reset(this.keys(t))} />
              <TTButton key="Reset all" type="secondary" disabled={notLive}
                        callback={() => TABS.forEach((tab) => model.reset(this.keys(tab)))} />
              <TTButton key="Reload tunables.ini" type="secondary" disabled={notLive} callback={() => model.reload()} />
            </div>
          </div>
        ))}
      </div>
    );
  }

  public destroy(): void {
    this.subs.forEach((s) => s.destroy());
    super.destroy();
  }
}

/** The page's layout (the EFB styles the controls themselves). */
export const SETTINGS_CSS = `
.atmo-settings { padding: 96px 32px 24px; color: #e6e6e6; font-size: 20px; height: 100%; overflow-y: auto; box-sizing: border-box; }
.atmo-row { display: flex; align-items: center; min-height: 52px; }
.atmo-label { flex: 0 0 40%; padding-right: 16px; }
.atmo-control { flex: 1 1 auto; }
.atmo-control > .slider { width: 100%; }
.atmo-switch { display: flex; justify-content: flex-end; }
.atmo-value { flex: 0 0 170px; text-align: right; padding-left: 16px; font-variant-numeric: tabular-nums; }
.atmo-disabled .atmo-label, .atmo-disabled .atmo-value { opacity: 0.45; }
.atmo-master { border-bottom: 1px solid rgba(255, 255, 255, 0.15); margin-bottom: 8px; font-size: 22px; }
.atmo-hint { opacity: 0.6; font-size: 16px; }
.atmo-weather { opacity: 0.8; font-size: 17px; margin-bottom: 16px; }
.atmo-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.atmo-hidden { display: none; }
.atmo-more { font-size: 18px; }
.atmo-buttons { display: flex; gap: 12px; margin-top: 20px; }
`;
