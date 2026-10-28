// AtmosphereMod's settings in the EFB: the main tunables.ini values as switches
// and sliders, applied by the add-on at once and saved into tunables.ini
// (addon/src/settings_bridge.h has the CommBus messages).
//
// Sliders show friendly units (degrees, %, km); each setting converts to and
// from the raw tunables.ini value. The manual haze sliders are greyed out while
// the haze comes from the METAR, and then show the values the METAR set.
// Each setting explains itself in a tip while the mouse is over its name.

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
  /** What it does, shown while the mouse is over the label. */
  tip: string;
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
  tip: string;
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
function percent(key: string, label: string, tip: string, scale: number, more?: boolean): SliderSpec {
  return {
    kind: "slider", key, label, tip, min: 0, max: 100, step: 5, more,
    toDisplay: (raw) => (raw / scale) * 100,
    toRaw: (d) => (d / 100) * scale,
    format: (d) => `${Math.round(d)} %`,
  };
}

/** A raw value shown as it is, with "off" at 0. */
function plain(key: string, label: string, tip: string, min: number, max: number, step: number, digits: number,
               extra: Partial<SliderSpec> = {}): SliderSpec {
  return {
    kind: "slider", key, label, tip, min, max, step, toDisplay: identity, toRaw: identity,
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
      {
        kind: "switch", key: "T0", label: "Atmosphere (haze and sky)",
        tip: "The mod's haze and sky. Off: the sim's own atmosphere, exactly.",
      },
      {
        kind: "switch", key: "T13", label: "Haze from METAR",
        tip: "The haze follows the sim's own weather reports (METARs): its amount, height and colour, blended "
          + "between stations as you fly. Off: the manual haze settings below.",
      },
      {
        kind: "switch", key: "T11", label: "Distant clouds fade creamy, not grey",
        tip: "Distant clouds fade colour by colour, as the haze scatters blue light out of the view, so they "
          + "turn creamy like real ones. Off: the sim's grey dimming.",
      },
      {
        kind: "slider", key: "T9", label: "Sun glare in haze", min: 0, max: 100, step: 5,
        tip: "How much the haze glares around the sun: higher gives a brighter, tighter glow looking toward it "
          + "(the sim's own is about 85 %). With haze from METAR, the weather adds its share to this.",
        toDisplay: (raw) => (clamp(raw > 0 ? raw : SIM_GLARE, GLARE_LO, GLARE_HI) - GLARE_LO) / (GLARE_HI - GLARE_LO) * 100,
        toRaw: (d) => GLARE_LO + (d / 100) * (GLARE_HI - GLARE_LO),
        format: (d) => `${Math.round(d)} %`,
      },
      plain("T3", "Haze near the ground",
            "How hazy the air near the ground is, as a multiple of the sim's own surface haze: about ×4 on a "
            + "clean mountain day, ×8-14 in hazy or polluted lowlands.",
            1, 15, 0.5, 1, { format: (d) => `×${d.toFixed(1)}`, manualHaze: true, more: true }),
      plain("T1", "Haze layer top",
            "The height above sea level where the hazy layer near the ground ends. Above it the air is "
            + "clearer and the sky bluer.",
            0.5, 4, 0.1, 1, { format: (d) => `${d.toFixed(1)} km`, manualHaze: true, more: true }),
      plain("T4", "Haze above the layer",
            "How hazy the air is above that layer, as a multiple of the sim's surface haze: lower gives clearer "
            + "views at altitude.",
            0, 2, 0.1, 1, { format: (d) => `×${d.toFixed(1)}`, manualHaze: true, more: true }),
      plain("T6", "Brown haze (pollution)",
            "Light-absorbing particles such as soot and smog in the layer near the ground: a darker, brownish "
            + "haze. 0 is none; 0.15-0.3 suits polluted lowlands.",
            0, 0.4, 0.01, 2, { manualHaze: true, more: true }),
      plain("T10", "Haze colour",
            "The haze's colour, set by its particle size: grey (0), humid or sea-air white (0.3), continental "
            + "blue (1.3), smoke (2).",
            0, 2, 0.1, 1, {
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
      {
        kind: "switch", key: "T8", label: "Real-world sky colours",
        tip: "Sky colours fitted to the real sky across the whole spectrum: a blue sky overhead at sunset and the "
          + "right orange of a low sun, with ozone by season and latitude. Off: the sim's own colours.",
      },
      {
        kind: "switch", key: "C22", label: "Daylight white balance",
        tip: "The sim's sunlight has its colour from above the atmosphere, slightly warm on screen, so sunlit "
          + "clouds look cream. On: sunlight looks white, as your eyes see it; sky, clouds and ground shift alike.",
      },
    ],
  },
  {
    name: "Cloud lighting",
    settings: [
      {
        kind: "switch", key: "C0", label: "Cloud lighting",
        tip: "How the mod draws clouds: this tab's lighting, solid clouds and cloud types. Off: the sim's own "
          + "clouds, exactly. (The cloud shapes from METARs and the thunderstorms stay; see the Clouds tab.)",
      },
      percent("C2", "Silver lining strength",
              "How bright the silver lining is: the glowing rim of clouds close to the sun. 0 % is the sim's own.",
              0.5),
      {
        kind: "slider", key: "C1", label: "Silver lining size", min: 1, max: 7, step: 0.1,
        tip: "How far from the sun the silver lining reaches (its half width). Real cloud droplets give about 1-2°.",
        toDisplay: (raw) => clamp((1 - raw) * LOBE_DEG, 1, 7),
        toRaw: (d) => 1 - d / LOBE_DEG,
        format: (d) => `${d.toFixed(1)}°`,
      },
      plain("C9", "Glory",
            "Coloured rings around the point opposite the sun, where your shadow falls on cloud tops: blue inside, "
            + "red outside. Off at 0.",
            0, 2, 0.1, 1),
      percent("C12", "Your aircraft's shadow on clouds",
              "Your aircraft's shadow on the clouds below, at the centre of the glory: how dark its core is. It "
              + "blurs with distance, as a real shadow does.",
              1),
      percent("C3", "Bounced light at cloud edges",
              "Light that has bounced around inside the cloud, already at its sunlit edges: brighter cloud sides "
              + "with the sun to one side, and a slightly smaller silver lining.",
              1, true),
      {
        kind: "slider", key: "C5", label: "Light deep inside clouds", min: 1, max: 6, step: 0.1, more: true,
        tip: "How much sunlight reaches deep inside clouds, as a multiple of the sim's own: brighter interiors "
          + "and bases.",
        toDisplay: (raw) => clamp((raw > 0 ? raw : SIM_DEEP_LIGHT) / SIM_DEEP_LIGHT, 1, 6),
        toRaw: (d) => d * SIM_DEEP_LIGHT,
        format: (d) => `×${d.toFixed(1)}`,
      },
      percent("C6", "Powder effect (grey rims, sun behind you)",
              "With the sun behind you, cloud edges and thin parts look grey and dense cores white, which "
              + "outlines each turret. It fades out toward the sun.",
              1, true),
      plain("C8", "Crisper cloud edges",
            "Sharper cloud outlines: the sim fades every cloud edge over 220 m or more; 1 halves that, 3 quarters "
            + "it. Far clouds keep softer edges, against flicker.",
            0, 4, 0.25, 2, { more: true }),
      {
        kind: "slider", key: "C10", label: "Glory ring size", min: 1.5, max: 8, step: 0.5, more: true,
        tip: "The glory's size, as the angle of its first red ring: smaller cloud droplets make larger rings.",
        toDisplay: (raw) => clamp(GLORY_DEG_UM / (raw > 0 ? raw : 10), 1.5, 8),
        toRaw: (d) => GLORY_DEG_UM / d,
        format: (d) => `${d.toFixed(1)}°`,
      },
    ],
  },
  {
    name: "Clouds",
    settings: [
      percent("C20", "Clouds follow the METARs",
              "Near weather stations, the sim's live clouds take the layers the stations report: base, cover, "
              + "CB and TCU. 100 % is the reported layers within the station radius. Live weather only.",
              1),
      plain("C21", "Station radius",
            "Each station's clouds apply fully within this distance and fade out over the same distance beyond.",
            10, 100, 5, 0, { format: (d) => `${Math.round(d)} km` }),
      plain("C16", "Solid clouds",
            "Clouds as thick as real ones: the sim's are see-through at bases and edges, and the sun glows "
            + "through an overcast. Thickness at a cumulus core (1 is the sim's); only the look, not the lighting.",
            0, 4, 0.25, 2, { format: (d) => (d <= 0 ? "the sim's" : d.toFixed(2)) }),
      percent("C23", "Cloud types",
              "Tells cloud types apart by height, depth and cover: dense, crisp cumulus; thinner, softer "
              + "stratocumulus, stratus, altocumulus and altostratus. 0 % draws every cloud like cumulus. Live "
              + "weather only.",
              1),
      percent("C28", "Smooth cloud bases",
              "Flat cloud bases: the sim's round puffs fade out toward each cloud's base, while the tops keep "
              + "their billows.",
              1),
      {
        kind: "switch", key: "C32", label: "Thunderstorms (experimental)",
        tip: "Storm clouds where a station nearby reports thunder or CB/TCU, or the sim's rain is very heavy: "
          + "towers up to the tropopause with anvils, each living 45-80 min. Visual only; live weather only.",
      },
      {
        kind: "slider", key: "C17", label: "Cloud bases: soft to solid", min: 0, max: 1, step: 0.05, more: true,
        tip: "How quickly clouds get dense above their base. The sim's build up over 3 km, so bases and thin "
          + "decks are nearly transparent; \"real\" is solid from the base up.",
        toDisplay: (raw) => clamp((1 - (raw > 0 ? raw : 1)) / 0.67, 0, 1),
        toRaw: (d) => 1 - 0.67 * d,
        format: (d) => (d <= 0.01 ? "the sim's" : d >= 0.99 ? "real" : `${Math.round(d * 100)} %`),
      },
      plain("C19", "High-cloud thickness",
            "How thick high clouds look (cirrus and thin sheets, which keep the sim's translucent look): higher "
            + "shows more cirrus, but icy layers at cruise get more fog-like.",
            0.5, 3, 0.1, 1, { format: (d) => `×${d.toFixed(1)}`, needs: "C16", more: true }),
      {
        ...percent("C24", "Stratocumulus thickness",
                   "How solid stratocumulus (lumpy low decks) is, as a share of Solid clouds.", 1, true),
        needs: "C23",
      },
      {
        ...percent("C25", "Stratus thickness",
                   "How solid stratus (flat, grey low decks) is, as a share of Solid clouds.", 1, true),
        needs: "C23",
      },
      {
        ...percent("C26", "Altocumulus thickness",
                   "How solid altocumulus (mid-level patches and rolls) is, as a share of Solid clouds.", 1, true),
        needs: "C23",
      },
      {
        ...percent("C27", "Altostratus thickness",
                   "How solid altostratus (a mid-level veil the sun can shine through) is, as a share of Solid "
                   + "clouds.", 1, true),
        needs: "C23",
      },
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

interface LabelProps extends ComponentProps {
  text: string | VNode;
  tip: string;
}

/** A setting's name; while the mouse is over it, its explanation shows over the row's controls. */
class Label extends DisplayComponent<LabelProps> {
  public render(): VNode {
    return (
      <div class="atmo-label">
        <span class="atmo-name">{this.props.text}</span>
        <div class="atmo-tip">{this.props.tip}</div>
      </div>
    );
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
        <Label text={spec.label} tip={spec.tip} />
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
        <Label text={this.props.spec.label} tip={this.props.spec.tip} />
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
          <Label text={<span>AtmosphereMod <span class="atmo-hint">(Ctrl+Shift+F9)</span></span>}
                 tip="The whole mod on or off, also with Ctrl+Shift+F9. Off: the sim's own shaders, to compare." />
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
.atmo-row { display: flex; align-items: center; min-height: 52px; position: relative; }
.atmo-label { flex: 0 0 40%; padding-right: 16px; }
.atmo-name { border-bottom: 1px dotted rgba(255, 255, 255, 0.35); }
.atmo-tip { display: none; position: absolute; left: 40%; right: 0; top: 2px; z-index: 20; pointer-events: none;
  padding: 10px 14px; background-color: rgb(15, 20, 27); border: 1px solid rgb(63, 67, 73); border-radius: 6px;
  color: #e6e6e6; font-size: 17px; line-height: 1.35; }
.atmo-label:hover .atmo-tip { display: block; }
.atmo-control { flex: 1 1 auto; }
.atmo-control > .slider { width: 100%; }
.atmo-switch { display: flex; justify-content: flex-end; }
.atmo-value { flex: 0 0 170px; text-align: right; padding-left: 16px; font-variant-numeric: tabular-nums; }
.atmo-disabled .atmo-name, .atmo-disabled .atmo-value { opacity: 0.45; }
.atmo-master { border-bottom: 1px solid rgba(255, 255, 255, 0.15); margin-bottom: 8px; font-size: 22px; }
.atmo-hint { opacity: 0.6; font-size: 16px; }
.atmo-weather { opacity: 0.8; font-size: 17px; margin-bottom: 16px; }
.atmo-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.atmo-hidden { display: none; }
.atmo-more { font-size: 18px; }
.atmo-buttons { display: flex; gap: 12px; margin-top: 20px; }
`;
