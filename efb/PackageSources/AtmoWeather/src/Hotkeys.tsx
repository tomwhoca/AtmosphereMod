// The Hotkeys tab: the mod's hotkeys, changed by pressing the new keys in the sim. The add-on learns them
// from the keyboard as it reads the hotkeys (addon/src/hotkeys.h), so a key it takes is one it sees, and
// keeps them in its hotkeys.ini. Another add-on that uses the same keys (Glass Cockpit DLSS Fix: the add-on
// reads its keys from its log) is pointed out.

import { TTButton } from "@efb/efb-api";
import { ComponentProps, DisplayComponent, FSComponent, MappedSubject, Subscribable, VNode } from "@microsoft/msfs-sdk";

/** Another add-on's hotkey, as the add-on found it. */
export interface OtherHotkey {
  addon: string;
  /** What the key does there: "its green tint". */
  what: string;
  keys: string;
}

interface HotkeyAction {
  /** The add-on's name for it (hotkeys.h kNames). */
  name: string;
  label: string;
  tip: string;
  /** "" for none. */
  default: string;
}

/** The add-on's waiting time for the keys (hotkeys.h Learner::kTimeoutSeconds). */
const LEARN_SECONDS = 15;

/** The actions, in the order the tab lists them. */
export const HOTKEY_ACTIONS: HotkeyAction[] = [
  {
    name: "toggle", label: "Mod on/off", default: "Ctrl+Shift+F9",
    tip: "The whole mod on or off, as the switch at the top: the sim's own shaders, to compare.",
  },
  {
    name: "cloudFog", label: "In-cloud fog on/off", default: "",
    tip: "Switches the fog inside clouds (Visibility inside clouds, in the Clouds tab) off for now, and on "
      + "again: for when it shows just below a cloud. Your setting stays as it is, and the fog is on again "
      + "at the next start. No key until you choose one.",
  },
  {
    name: "reload", label: "Reload tunables.ini", default: "Ctrl+Shift+F10",
    tip: "Reads tunables.ini and the mod's shaders again, as the button on each tab: after editing the "
      + "file by hand.",
  },
  {
    name: "capture", label: "Capture", default: "Ctrl+Shift+F11",
    tip: "Saves what the mod's shaders see in this frame, with a number in the mod's log: when I ask for "
      + "one with a bug report.",
  },
  {
    name: "gpuTimes", label: "GPU times", default: "Ctrl+Shift+F12",
    tip: "Saves the GPU time of each draw in the next frames: when I ask for them with a performance "
      + "question.",
  },
];

/** The shipping keys, until the add-on says. */
export const DEFAULT_HOTKEYS: Readonly<Record<string, string>> =
  HOTKEY_ACTIONS.reduce<Record<string, string>>((all, a) => ({ ...all, [a.name]: a.default }), {});

const labelOf = (name: string): string => HOTKEY_ACTIONS.find((a) => a.name === name)?.label ?? name;

const KEYS_ALLOWED = "F1 to F12 with any of Ctrl, Shift and Alt, or a letter or digit with Ctrl or Alt";

/** The add-on's note on the last change (hotkeys.h Learner), as a line ("" for none). */
export function hotkeyNoteText(note: string, hotkeys: Readonly<Record<string, string>>): string {
  const colon = note.indexOf(":");
  const kind = colon < 0 ? note : note.slice(0, colon);
  const arg = colon < 0 ? "" : note.slice(colon + 1);
  switch (kind) {
    case "set":
      return `${labelOf(arg)}: ${hotkeys[arg] || "no key"}.`;
    case "cleared":
      return `${labelOf(arg)}: no key now.`;
    case "timeout":
      return `No keys came within ${LEARN_SECONDS} seconds: nothing changed.`;
    case "unusable":
      return `${arg} can't be a hotkey. Press others: ${KEYS_ALLOWED}.`;
    case "taken":
      return `Those keys already belong to ${labelOf(arg)}: choose others, or clear that one first.`;
    default:
      return "";
  }
}

/** Where another add-on uses one of the mod's keys, a line each. */
export function hotkeyClashes(hotkeys: Readonly<Record<string, string>>, others: readonly OtherHotkey[]): string[] {
  const same = (a: string, b: string): boolean => a !== "" && a.replace(/\s/g, "").toLowerCase() === b.replace(/\s/g, "").toLowerCase();
  const lines: string[] = [];
  for (const action of HOTKEY_ACTIONS) {
    const keys = hotkeys[action.name] ?? "";
    for (const other of others) {
      if (same(keys, other.keys)) {
        lines.push(`${other.addon} uses ${keys} for ${other.what} too, so the key does both. Give ${action.label} `
          + "other keys here, or change that add-on's in its own tab in the ReShade overlay.");
      }
    }
  }
  return lines;
}

/** What the tab needs of the settings' model (Settings.tsx SettingsModel). */
export interface HotkeysModel {
  readonly connected: Subscribable<boolean>;
  /** The add-on has hotkeys to change (0.7.3 on). */
  readonly hasHotkeys: Subscribable<boolean>;
  readonly hotkeys: Subscribable<Readonly<Record<string, string>>>;
  /** The action whose keys the add-on waits for ("" for none). */
  readonly learning: Subscribable<string>;
  readonly hotkeyNote: Subscribable<string>;
  readonly otherHotkeys: Subscribable<readonly OtherHotkey[]>;
  learnHotkey(name: string): void;
  setHotkey(name: string, keys: string): void;
}

interface HotkeysPageProps extends ComponentProps {
  model: HotkeysModel;
}

export class HotkeysPage extends DisplayComponent<HotkeysPageProps> {
  public render(): VNode {
    const { model } = this.props;
    const off = MappedSubject.create(([c, has]) => !c || !has, model.connected, model.hasHotkeys);
    const older = MappedSubject.create(([c, has]) => !c || has, model.connected, model.hasHotkeys);
    const note = MappedSubject.create(([n, h]) => hotkeyNoteText(n, h), model.hotkeyNote, model.hotkeys);
    const clashes = MappedSubject.create(([h, o]) => hotkeyClashes(h, o).join("\n"), model.hotkeys, model.otherHotkeys);
    return (
      <div class="atmo-hotkeys">
        <div class="atmo-note atmo-hotkeys-intro">
          {`Change, then press the new keys in the sim within ${LEARN_SECONDS} seconds: ${KEYS_ALLOWED}. `
            + "The keys work while the sim's window is in front."}
        </div>
        {HOTKEY_ACTIONS.map((action) => {
          const keys = model.hotkeys.map((h) => h[action.name] ?? "");
          const learning = model.learning.map((l) => l === action.name);
          const shown = MappedSubject.create(([k, l]) => (l ? "Press the new keys..." : k === "" ? "none" : k), keys, learning);
          const change = learning.map((l) => (l ? "Cancel" : "Change"));
          return (
            <div class={{ "atmo-row": true, "atmo-hotkey": true, "atmo-disabled": off }}>
              <div class="atmo-label">
                <span class="atmo-name">{action.label}</span>
                <div class="atmo-tip">{action.tip}</div>
              </div>
              <div class={{ "atmo-hotkey-keys": true, "atmo-learning": learning }}>{shown}</div>
              <div class="atmo-hotkey-buttons">
                <TTButton key={change} type="secondary" disabled={off}
                          callback={() => model.learnHotkey(learning.get() ? "" : action.name)} />
                <TTButton key="Clear" type="secondary" disabled={off} callback={() => model.setHotkey(action.name, "")} />
                {action.default !== "" ? (
                  <TTButton key="Default" type="secondary" disabled={off}
                            callback={() => model.setHotkey(action.name, action.default)} />
                ) : null}
              </div>
            </div>
          );
        })}
        <div class={{ "atmo-note": true, "atmo-hidden": note.map((n) => n === "") }}>{note}</div>
        <div class={{ "atmo-note": true, "atmo-unsaved": true, "atmo-lines": true, "atmo-hidden": clashes.map((c) => c === "") }}>
          {clashes}
        </div>
        <div class={{ "atmo-note": true, "atmo-hidden": older }}>
          Changing the hotkeys needs the add-on of this version: install the mod again.
        </div>
      </div>
    );
  }
}

export const HOTKEYS_CSS = `
.atmo-hotkeys-intro { margin: 0 0 12px; }
.atmo-hotkey { flex-wrap: wrap; border-top: 1px solid rgba(255, 255, 255, 0.12); padding: 6px 0; }
.atmo-hotkey-keys { flex: 1 1 auto; margin-right: 12px; font-variant-numeric: tabular-nums; }
.atmo-learning { color: #f0b429; }
.atmo-hotkey-buttons { display: flex; flex-wrap: wrap; margin-left: auto; }
.atmo-app .atmo-hotkey-buttons > * { margin: 3px 0 3px 12px; }
.atmo-lines { white-space: pre-wrap; }
.atmo-narrow .atmo-hotkey-keys { text-align: right; margin-right: 0; }
.atmo-narrow .atmo-hotkey-buttons { flex: 1 0 100%; justify-content: flex-end; }
`;
