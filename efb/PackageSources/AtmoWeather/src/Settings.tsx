// AtmosphereMod's settings in the EFB: the main tunables.ini values as switches
// and sliders, applied by the add-on at once and saved into tunables.ini
// (addon/src/settings_bridge.h has the CommBus messages).
//
// Sliders show friendly units (degrees, %, km); each setting converts to and
// from the raw tunables.ini value. The manual haze sliders are greyed out while
// the haze comes from the METAR, and then show the values the METAR set.
// Each setting explains itself in a tip while the mouse is over its name.

import { ElementAccordion, Slider, Switch, TabSelector, TextBox, TTButton } from "@efb/efb-api";
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
import { DEFAULT_HOTKEYS, HotkeysPage, OtherHotkey } from "./Hotkeys";

export const EVENT_SETTINGS = "AtmosphereMod.Settings";
export const EVENT_STATE = "AtmosphereMod.State";

// ---------------------------------------------------------------- the settings: src/specs.ts

import { SliderSpec, SwitchSpec, Spec, TabSpec, TABS } from "./specs";
export { TABS };

// ---------------------------------------------------------------- the model

interface State {
  enabled?: boolean;
  T?: number[];
  C?: number[];
  TE?: number[];
  weather?: string;
  /** The add-on's version (from 0.6.0 on). */
  version?: string;
  /** The saved presets, the one the settings are ("" for none) and whether it has them as they are,
   * and why the last preset request failed or what an import did (from 0.6.0 on). */
  presets?: string[];
  preset?: string;
  presetSaved?: boolean;
  /** Per preset, the settings Default has and it doesn't (only presets that lack some). */
  presetsMissing?: Record<string, string[]>;
  presetNote?: string;
  /** The performance report (PerfReport.tsx), once one was asked for (from 0.7.1 on). */
  report?: unknown;
  /** The hotkeys by action, the one whose keys the add-on waits for, its note on the last change, the
   * in-cloud fog switched off by its hotkey, and other add-ons' keys (Hotkeys.tsx; from 0.7.3 on). */
  hotkeys?: Record<string, string>;
  learning?: string;
  hotkeyNote?: string;
  cloudFogOff?: boolean;
  otherHotkeys?: OtherHotkey[];
}

/** Each setting the app shows, by key: its name, and its place (the tabs' order). */
const SETTING_NAMES = new Map<string, { label: string; order: number }>();
TABS.forEach((t) => t.settings.forEach((s) => SETTING_NAMES.set(s.key, { label: s.label, order: SETTING_NAMES.size })));

/** A preset's missing settings in a line: "Atmo" has no value for 3 settings: Glory, Station radius and 1
 * the app doesn't show. (At most MISSING_NAMED by name.) */
const MISSING_NAMED = 4;
export function missingText(name: string, keys: readonly string[]): string {
  const shown = keys.filter((k) => SETTING_NAMES.has(k))
    .sort((a, b) => SETTING_NAMES.get(a)!.order - SETTING_NAMES.get(b)!.order)
    .map((k) => SETTING_NAMES.get(k)!.label);
  const hidden = keys.length - shown.length;
  const settings = `${keys.length} setting${keys.length === 1 ? "" : "s"}`;
  if (shown.length === 0) {
    return `"${name}" has no value for ${settings} the app doesn't show.`;
  }
  const parts = shown.slice(0, MISSING_NAMED);
  if (shown.length > MISSING_NAMED) {
    parts.push(`${keys.length - MISSING_NAMED} more`);
  } else if (hidden > 0) {
    parts.push(`${hidden} the app doesn't show`);
  }
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  return `"${name}" has no value for ${settings}: ${list}.`;
}

/** The add-on's own preset (settings_bridge.h): the shipping defaults. */
export const PRESET_DEFAULT = "Default";
const PRESET_NAME_MAX = 40;
const PRESET_CHARS = /^[A-Za-z0-9 \-_().,+']*$/;

/** A character a preset's name may have. */
export const presetChar = (c: string): boolean => c.length === 1 && PRESET_CHARS.test(c);

/** The name without its spaces at the ends, or "" if the add-on wouldn't take it (it checks again, also
 * for Windows' device names). */
export function presetName(raw: string): string {
  const name = raw.replace(/^ +| +$/g, "");
  return name.length <= PRESET_NAME_MAX && PRESET_CHARS.test(name) && !name.startsWith(".") && !name.endsWith(".")
    ? name : "";
}

/** Preset names are file names: case doesn't count. */
export const sameName = (a: string, b: string): boolean => a.toLowerCase() === b.toLowerCase();

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
  /** The add-on's version, "" until it says (or if it's older than 0.6.0). */
  public readonly addonVersion = Subject.create("");
  /** The saved presets: Default first. */
  public readonly presets = Subject.create<readonly string[]>([]);
  /** The add-on has presets (0.6.0 on). */
  public readonly hasPresets = Subject.create(false);
  /** The preset the settings are, or were before they changed ("" for none). */
  public readonly preset = Subject.create("");
  /** The settings are exactly a preset's (`preset`): loading another loses nothing. */
  public readonly presetSaved = Subject.create(false);
  /** Per preset, the settings Default has and it doesn't: loading one asks what to do about them. */
  public readonly presetMissing = Subject.create<Readonly<Record<string, readonly string[]>>>({});
  /** Why the add-on didn't do the last preset request, or what an import did ("" if there's nothing to say). */
  public readonly presetNote = Subject.create("");
  /** Each state's performance report (undefined while none was asked for), for PerfReport.tsx. */
  public onReport?: (report: unknown) => void;
  /** The hotkeys (Hotkeys.tsx): by action, as text ("" for none); the shipping ones until the add-on says. */
  public readonly hotkeys = Subject.create<Readonly<Record<string, string>>>(DEFAULT_HOTKEYS);
  public readonly hasHotkeys = Subject.create(false);
  /** The action whose keys the add-on waits for ("" for none). */
  public readonly learning = Subject.create("");
  /** The add-on's note on the last hotkey change (Hotkeys.tsx hotkeyNoteText). */
  public readonly hotkeyNote = Subject.create("");
  /** The in-cloud fog switched off for now (its hotkey). */
  public readonly cloudFogOff = Subject.create(false);
  /** Other add-ons' hotkeys (Glass Cockpit DLSS Fix's, while it's loaded). */
  public readonly otherHotkeys = Subject.create<readonly OtherHotkey[]>([]);
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

  /** The settings that differ from the shipping defaults, as "C16 = 2 (0.5)" (the default in brackets). */
  public changedFromDefaults(): string[] {
    return Object.keys(DEFAULTS).filter((k) => this.raw.has(k) && Math.abs(this.raw.get(k)!.get() - DEFAULTS[k]) > 1e-6)
      .map((k) => `${k} = ${+this.raw.get(k)!.get().toPrecision(6)} (${DEFAULTS[k]})`);
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
    this.addonVersion.set(state.version ?? "");
    const presets = state.presets ?? [];
    if (presets.join("\n") !== this.presets.get().join("\n")) {
      this.presets.set(presets);
    }
    this.hasPresets.set(state.presets !== undefined);
    this.preset.set(state.preset ?? "");
    this.presetSaved.set(state.presetSaved ?? false);
    const missing = state.presetsMissing ?? {};
    if (JSON.stringify(missing) !== JSON.stringify(this.presetMissing.get())) {
      this.presetMissing.set(missing);
    }
    this.presetNote.set(state.presetNote ?? "");
    if (state.hotkeys !== undefined && JSON.stringify(state.hotkeys) !== JSON.stringify(this.hotkeys.get())) {
      this.hotkeys.set(state.hotkeys);
    }
    this.hasHotkeys.set(state.hotkeys !== undefined);
    this.learning.set(state.learning ?? "");
    this.hotkeyNote.set(state.hotkeyNote ?? "");
    this.cloudFogOff.set(state.cloudFogOff ?? false);
    const others = state.otherHotkeys ?? [];
    if (JSON.stringify(others) !== JSON.stringify(this.otherHotkeys.get())) {
      this.otherHotkeys.set(others);
    }
    this.onReport?.(state.report);
    this.connected.set(true);
  }

  /** Sets tunables.ini's value: at once here, in the add-on within SEND_MS. */
  public set(key: string, raw: number): void {
    const rounded = Math.round(raw * 1e4) / 1e4;
    this.value(key).set(rounded);
    this.editedAt.set(key, Date.now());
    this.pending[key] = rounded;
    if (this.timer === undefined) {
      this.timer = window.setTimeout(() => this.flush(), SEND_MS);
    }
  }

  /** Changes wait for SEND_MS: the add-on's presetSaved doesn't know them yet. */
  public unsent(): boolean {
    return Object.keys(this.pending).length > 0;
  }

  /** Sends the changes still waiting for SEND_MS, now. */
  private flush(): void {
    window.clearTimeout(this.timer);
    this.timer = undefined;
    if (Object.keys(this.pending).length > 0) {
      const set = this.pending;
      this.pending = {};
      this.send({ set });
    }
  }

  /** Saves the settings as preset `name` (replacing one of that name), with the changes not sent yet. */
  public savePreset(name: string): void {
    this.flush();
    this.send({ savePreset: name });
  }

  /** Loads preset `name`: the add-on writes its values into tunables.ini and reloads; its next state
   * shows them, even right after an edit. Unless `discard`, only while the settings are a preset's
   * (the add-on checks: settings no preset has aren't replaced unasked). The settings it lacks stay as
   * they are, or with `fill` are first added to the preset: Default's values or the current ones. */
  public loadPreset(name: string, discard = false, fill?: "defaults" | "current"): void {
    this.flush();
    this.editedAt.clear();
    this.send({ loadPreset: name, ...(discard ? { discard: true } : {}), ...(fill !== undefined ? { fill } : {}) });
  }

  public deletePreset(name: string): void {
    this.send({ deletePreset: name });
  }

  /** The add-on opens a file dialog over the sim (the EFB can't open files) and adds the file chosen
   * as a new preset. */
  public importPreset(): void {
    this.send({ importPreset: true });
  }

  public setEnabled(on: boolean): void {
    this.enabled.set(on);
    this.send({ enabled: on });
  }

  /** As Ctrl+Shift+F10: the add-on saves pending changes, then reloads tunables.ini and its shaders. */
  public reload(): void {
    this.send({ reload: true });
  }

  /** The add-on takes the next keys pressed in the sim as the action's hotkey ("": it stops waiting). */
  public learnHotkey(name: string): void {
    this.learning.set(name);
    this.send({ learnHotkey: name });
  }

  /** The action's hotkey as text ("Ctrl+Shift+F9"; "" for none). */
  public setHotkey(name: string, keys: string): void {
    this.send({ setHotkey: { [name]: keys } });
  }

  /** The in-cloud fog off for now, or on again (as its hotkey). */
  public setCloudFogOff(off: boolean): void {
    this.cloudFogOff.set(off);
    this.send({ cloudFogOff: off });
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
  private readonly hidden: MappedSubject<[number], boolean>;
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
    this.hidden = MappedSubject.create(([metar]) => spec.metarOnly === true && metar < 0.5, model.value("T13"));
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
      <div class={{ "atmo-row": true, "atmo-slider-row": true, "atmo-disabled": this.disabled, "atmo-hidden": this.hidden }}>
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

/** A second tap within this long confirms (replacing a preset, deleting one, loading over unsaved settings). */
const CONFIRM_MS = 4000;

interface PresetsPageProps extends ComponentProps {
  model: SettingsModel;
}

/** The Presets tab: the preset the settings are, saving them under a name, the presets to load or
 * delete, and importing one from a file (settings_bridge.h presets). Replacing or deleting a preset, or
 * loading one over unsaved settings, takes a second tap. */
class PresetsPage extends DisplayComponent<PresetsPageProps> {
  private readonly name = Subject.create("");
  private readonly list = FSComponent.createRef<HTMLDivElement>();
  /** "save:<preset>", "load:<preset>" or "delete:<preset>" while it waits for its second tap. */
  private readonly armed = Subject.create("");
  /** The preset whose missing settings the page asks about (under its row), and the question. */
  private readonly asking = Subject.create("");
  private readonly question = Subject.create("");
  /** The question said the unsaved settings will be lost. */
  private askedUnsaved = false;
  private armTimer?: number;
  /** Why the app didn't send a name; shown instead of the add-on's note until the next request. */
  private readonly localNote = Subject.create("");
  private readonly off = MappedSubject.create(([c, has]) => !c || !has, this.props.model.connected,
                                              this.props.model.hasPresets);
  private rows: VNode[] = [];
  private rowSubs: { destroy(): void }[] = [];
  private readonly subs: Subscription[] = [];

  public onAfterRender(node: VNode): void {
    super.onAfterRender(node);
    this.subs.push(this.props.model.presets.sub((names) => this.showList(names), true));
    this.subs.push(this.name.sub(() => {
      if (this.armed.get().startsWith("save:")) {
        this.disarm();
      }
    }));
  }

  /** Whether this is the second tap on `what`; the first arms it for CONFIRM_MS. */
  private confirmed(what: string): boolean {
    if (this.armed.get() === what) {
      this.disarm();
      return true;
    }
    window.clearTimeout(this.armTimer);
    this.asking.set("");
    this.armed.set(what);
    this.armTimer = window.setTimeout(() => this.armed.set(""), CONFIRM_MS);
    return false;
  }

  /** No second tap waited for, and no question asked. */
  private disarm(): void {
    window.clearTimeout(this.armTimer);
    this.armTimer = undefined;
    this.armed.set("");
    this.asking.set("");
  }

  private save(): void {
    const name = presetName(this.name.get());
    if (name === "") {
      this.localNote.set(`A name: letters, digits, spaces and - _ ( ) . , + ', up to ${PRESET_NAME_MAX} characters`);
      return;
    }
    if (sameName(name, PRESET_DEFAULT)) {
      this.localNote.set(`"${PRESET_DEFAULT}" can't be changed: choose another name`);
      return;
    }
    const existing = this.props.model.presets.get().find((p) => sameName(p, name));
    if (existing !== undefined && !this.confirmed(`save:${existing}`)) {
      return;
    }
    this.disarm();
    this.localNote.set("");
    this.props.model.savePreset(existing ?? name);
  }

  /** The changes saved into the preset they were made to. */
  private saveChanges(): void {
    this.disarm();
    this.localNote.set("");
    this.props.model.savePreset(this.props.model.preset.get());
  }

  /** Over settings no preset has, only at the second tap (they're lost then). A preset that lacks some
   * settings asks first: fill them into it with the mod's defaults, or with the current values (the
   * question also tells of unsaved settings: answering is the second tap). */
  private load(name: string): void {
    const { model } = this.props;
    const unsaved = !model.presetSaved.get() || model.unsent();
    const missing = model.presetMissing.get()[name] ?? [];
    if (missing.length > 0) {
      this.disarm();
      this.localNote.set("");
      const them = missing.length === 1 ? "it" : "them";
      this.question.set(`${missingText(name, missing)}${unsaved ? " Your unsaved changes will be lost." : ""}`
        + ` Fill ${them} in with the mod's defaults, or with your current values?`);
      this.askedUnsaved = unsaved;
      this.asking.set(name);
      return;
    }
    if (unsaved && !this.confirmed(`load:${name}`)) {
      return;
    }
    this.disarm();
    this.localNote.set("");
    model.loadPreset(name, unsaved);
  }

  /** The question's answer: the settings the preset lacks added to it, with Default's values or the
   * current ones, then loaded. */
  private answer(name: string, fill: "defaults" | "current"): void {
    const { model } = this.props;
    const unsaved = !model.presetSaved.get() || model.unsent();
    if (unsaved && !this.askedUnsaved) {
      this.load(name);  // (a setting changed since it asked: again, with the warning)
      return;
    }
    this.disarm();
    model.loadPreset(name, unsaved, fill);
  }

  private importFile(): void {
    this.disarm();
    this.localNote.set("");
    this.props.model.importPreset();
  }

  private remove(name: string): void {
    if (this.confirmed(`delete:${name}`)) {
      this.localNote.set("");
      this.props.model.deletePreset(name);
    }
  }

  /** The presets' rows, made again when the list changes. */
  private showList(names: readonly string[]): void {
    const list = this.list.getOrDefault();
    if (list === null) {
      return;
    }
    this.disarm();
    this.rows.forEach((r) => FSComponent.shallowDestroy(r));
    this.rowSubs.forEach((s) => s.destroy());
    this.rows = [];
    this.rowSubs = [];
    list.innerHTML = "";
    for (const name of names) {
      const loadKey = this.armed.map((a) => (a === `load:${name}` ? "Discard changes? Tap again" : "Load"));
      const deleteKey = this.armed.map((a) => (a === `delete:${name}` ? "Delete? Tap again" : "Delete"));
      const notAsked = this.asking.map((a) => a !== name);
      this.rowSubs.push(loadKey, deleteKey, notAsked);
      const row = (
        <div class="atmo-row atmo-preset">
          <div class="atmo-preset-name">{name}</div>
          <div class="atmo-preset-buttons">
            <TTButton key={loadKey} type="secondary" disabled={this.off} callback={() => this.load(name)} />
            {name !== PRESET_DEFAULT ? (
              <TTButton key={deleteKey} type="secondary" disabled={this.off} callback={() => this.remove(name)} />
            ) : null}
          </div>
          <div class={{ "atmo-ask": true, "atmo-hidden": notAsked }}>
            <div class="atmo-ask-text">{this.question}</div>
            <div class="atmo-ask-buttons">
              <TTButton key="Use defaults" type="secondary" disabled={this.off}
                        callback={() => this.answer(name, "defaults")} />
              <TTButton key="Keep current" type="secondary" disabled={this.off}
                        callback={() => this.answer(name, "current")} />
              <TTButton key="Cancel" type="secondary" disabled={this.off} callback={() => this.disarm()} />
            </div>
          </div>
        </div>
      );
      FSComponent.render(row, list);
      this.rows.push(row);
    }
  }

  public render(): VNode {
    const { model } = this.props;
    const saveKey = this.armed.map((a) => (a.startsWith("save:") ? `Replace "${a.slice(5)}"? Tap again` : "Save"));
    const note = MappedSubject.create(
      ([armed, local, added]) => (armed.startsWith("load:") ? "Your changes aren't saved: save them first to keep them"
        : local !== "" ? local : added),
      this.armed, this.localNote, model.presetNote,
    );
    const older = MappedSubject.create(([c, has]) => !c || has, model.connected, model.hasPresets);
    // the preset the settings are: "Tom's", "Tom's (unsaved changes)", "unsaved settings"
    const unsaved = MappedSubject.create(([name, saved]) => (saved ? "" : name === "" ? "unsaved settings"
      : " (unsaved changes)"), model.preset, model.presetSaved);
    const canSaveChanges = MappedSubject.create(
      ([name, saved, off]) => !off && !saved && name !== "" && !sameName(name, PRESET_DEFAULT),
      model.preset, model.presetSaved, this.off,
    );
    return (
      <div class="atmo-presets">
        <div class={{ "atmo-row": true, "atmo-current": true, "atmo-hidden": this.off }}>
          <div class="atmo-current-name">
            <span class="atmo-hint">Active: </span>{model.preset}
            <span class={{ "atmo-unsaved": true, "atmo-hidden": unsaved.map((u) => u === "") }}>{unsaved}</span>
          </div>
          <div class={{ "atmo-hidden": canSaveChanges.map((c) => !c) }}>
            <TTButton key="Save changes" type="secondary" disabled={this.off} callback={() => this.saveChanges()} />
          </div>
        </div>
        <div class={{ "atmo-row": true, "atmo-save": true, "atmo-disabled": this.off }}>
          <div class="atmo-save-name">
            <TextBox model={this.name} placeholder="A name for your settings" charFilter={presetChar}
                     disabled={this.off} showDeleteIcon={true} />
          </div>
          <TTButton key={saveKey} type="secondary" disabled={this.off} callback={() => this.save()} />
        </div>
        <div ref={this.list} class="atmo-preset-list" />
        <div class={{ "atmo-buttons": true, "atmo-hidden": this.off }}>
          <TTButton key="Import from a file" type="secondary" disabled={this.off} callback={() => this.importFile()} />
        </div>
        <div class={{ "atmo-note": true, "atmo-hidden": note.map((n) => n === "") }}>{note}</div>
        <div class={{ "atmo-note": true, "atmo-hidden": older }}>
          Presets need the add-on of this version: install the mod again.
        </div>
      </div>
    );
  }

  public destroy(): void {
    this.disarm();
    this.rows.forEach((r) => FSComponent.shallowDestroy(r));
    this.rowSubs.forEach((s) => s.destroy());
    this.subs.forEach((s) => s.destroy());
    super.destroy();
  }
}

interface SettingsViewProps extends ComponentProps {
  model: SettingsModel;
  /** The METAR of the airport nearest the aircraft, under the weather line ("" while there's none). */
  metar?: Subscribable<string>;
}

/** The master switch, the weather line and the airport's METAR, the tabs and their settings, then
 * the children (in the same scrolling page). */
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
    const toggleHint = model.hotkeys.map((h) => (h.toggle ? `(${h.toggle})` : ""));
    const fogText = model.hotkeys.map((h) => `The fog inside clouds is off for now${h.cloudFog ? ` (${h.cloudFog})` : ""}`);
    return (
      <div class="atmo-settings">
        <div class="atmo-row atmo-master">
          <Label text={<span>AtmosphereMod <span class="atmo-hint">{toggleHint}</span></span>}
                 tip="The whole mod on or off, also with its hotkey (the Hotkeys tab). Off: the sim's own shaders, to compare." />
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
        <div class={{ "atmo-row": true, "atmo-fog-off": true, "atmo-hidden": model.cloudFogOff.map((o) => !o) }}>
          <div class="atmo-fog-text">{fogText}</div>
          <TTButton key="Switch it on" type="secondary" disabled={notLive} callback={() => model.setCloudFogOff(false)} />
        </div>
        {this.props.metar !== undefined ? (
          <div class={{ "atmo-metar": true, "atmo-hidden": this.props.metar.map((m) => m === "") }}>
            {this.props.metar}
          </div>
        ) : null}
        <div class="atmo-tabs">
          {TABS.map((t, i) => (
            <TabSelector tabName={t.name} active={this.tab.map((n) => n === i)} hidden={Subject.create(false)}
                         callback={() => this.tab.set(i)} />
          ))}
          <TabSelector tabName="Presets" active={this.tab.map((n) => n === TABS.length)} hidden={Subject.create(false)}
                       callback={() => this.tab.set(TABS.length)} />
          <TabSelector tabName="Hotkeys" active={this.tab.map((n) => n === TABS.length + 1)} hidden={Subject.create(false)}
                       callback={() => this.tab.set(TABS.length + 1)} />
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
        <div class={{ "atmo-page": true, "atmo-hidden": this.tab.map((n) => n !== TABS.length) }}>
          <PresetsPage model={model} />
        </div>
        <div class={{ "atmo-page": true, "atmo-hidden": this.tab.map((n) => n !== TABS.length + 1) }}>
          <HotkeysPage model={model} />
        </div>
        {this.props.children}
      </div>
    );
  }

  public destroy(): void {
    this.subs.forEach((s) => s.destroy());
    super.destroy();
  }
}

/** The page's layout (the EFB styles the controls themselves). The page is placed by its edges,
 * not by percentage heights in a flex column (which the EFB's tablet sizes didn't always
 * resolve: the page then didn't scroll), and scrolls between the EFB's status bar (the date) and
 * its dock, which lie over the app's area: about 85-95 px each at all six tablet sizes and
 * orientations (936 x 1392 to 1924 x 1294 px; screenshots of 3 Nov 2026). With .atmo-narrow on
 * an ancestor (a narrow tablet), each slider's name goes above it and its value. Those rows are
 * plain blocks, with no flex-wrap: a slider has no width of its own, so wrapped, it stayed at 0 px
 * on its name's full line and only its thumb showed (measured in the live EFB, 5 Nov 2026; check
 * this layout with the sim's UI debugger, a browser preview doesn't show it). That engine also
 * ignores gap on a flex box, so groups of buttons are spaced by margins. */
export const SETTINGS_CSS = `
.atmo-app { position: absolute; top: 0; right: 0; bottom: 0; left: 0; overflow: hidden; }
.atmo-settings { position: absolute; top: 96px; right: 0; bottom: 96px; left: 0; overflow-y: auto;
  box-sizing: border-box; padding: 0 32px 24px; color: #e6e6e6; font-size: 28px; }
.atmo-row { display: flex; align-items: center; min-height: 64px; position: relative; }
.atmo-label { flex: 0 0 40%; padding-right: 20px; }
.atmo-name { border-bottom: 1px dotted rgba(255, 255, 255, 0.35); }
.atmo-tip { display: none; position: absolute; left: 40%; right: 0; top: 2px; z-index: 20; pointer-events: none;
  padding: 14px 18px; background-color: rgb(15, 20, 27); border: 1px solid rgb(63, 67, 73); border-radius: 6px;
  color: #e6e6e6; font-size: 24px; line-height: 1.35; }
.atmo-label:hover .atmo-tip { display: block; }
.atmo-control { flex: 1 1 auto; }
.atmo-control > .slider { width: 100%; }
.atmo-switch { display: flex; justify-content: flex-end; }
.atmo-value { flex: 0 0 230px; text-align: right; padding-left: 20px; font-variant-numeric: tabular-nums; }
.atmo-disabled .atmo-name, .atmo-disabled .atmo-value { opacity: 0.45; }
.atmo-master { border-bottom: 1px solid rgba(255, 255, 255, 0.15); margin-bottom: 10px; font-size: 31px; }
.atmo-hint { opacity: 0.6; font-size: 22px; }
.atmo-weather { opacity: 0.8; font-size: 24px; margin-bottom: 20px; }
.atmo-fog-off { flex-wrap: wrap; margin: -8px 0 16px; font-size: 24px; color: #f0b429; }
.atmo-fog-text { flex: 1 1 auto; margin-right: 12px; }
.atmo-metar { opacity: 0.8; font-size: 21px; font-family: monospace; white-space: pre-wrap; margin: -12px 0 20px; }
.atmo-tabs { display: flex; flex-wrap: wrap; margin-bottom: 12px; }
.atmo-hidden { display: none; }
.atmo-more { font-size: 25px; }
.atmo-buttons { display: flex; flex-wrap: wrap; margin-top: 20px; }
.atmo-app .atmo-buttons > *, .atmo-app .atmo-ask-buttons > * { margin: 0 12px 12px 0; }
.atmo-current { flex-wrap: wrap; margin-top: 4px; }
.atmo-current-name { flex: 1 1 auto; margin-right: 12px; overflow-wrap: anywhere; }
.atmo-unsaved { color: #f0b429; }
.atmo-save { flex-wrap: wrap; margin-bottom: 12px; }
.atmo-save-name { flex: 1 1 400px; margin-right: 12px; }
.atmo-save-name > .textbox { width: 100%; }
.atmo-preset { flex-wrap: wrap; border-top: 1px solid rgba(255, 255, 255, 0.12); padding: 6px 0; }
.atmo-preset-name { flex: 1 1 auto; overflow-wrap: anywhere; }
.atmo-preset-buttons { display: flex; flex-wrap: wrap; margin-left: auto; }
.atmo-app .atmo-preset-buttons > * { margin: 3px 0 3px 12px; }
.atmo-ask { flex: 1 0 100%; box-sizing: border-box; margin: 4px 0 8px; padding: 14px 18px; border-radius: 6px;
  border: 1px solid rgba(240, 180, 41, 0.6); background-color: rgba(240, 180, 41, 0.08); }
.atmo-ask-text { font-size: 24px; line-height: 1.35; margin-bottom: 14px; }
.atmo-ask-buttons { display: flex; flex-wrap: wrap; margin-bottom: -12px; }
.atmo-note { margin-top: 16px; font-size: 24px; opacity: 0.8; }
.atmo-narrow .atmo-settings { padding-left: 20px; padding-right: 20px; }
.atmo-narrow .atmo-label { flex: 1 1 auto; }
.atmo-narrow .atmo-slider-row { display: block; padding: 10px 0 6px; }
.atmo-narrow .atmo-slider-row .atmo-label { padding: 0 0 6px; }
.atmo-narrow .atmo-slider-row .atmo-control { margin-right: 150px; min-height: 44px; }
.atmo-narrow .atmo-slider-row .atmo-value { position: absolute; right: 0; bottom: 18px; width: 140px; padding-left: 0; }
.atmo-narrow .atmo-tip { left: 0; top: 100%; }
`;
