// The mod's hotkeys (the Hotkeys page): the actions, the add-on's notes on a change, other add-ons' keys that clash.
// The add-on learns the keys from the keyboard as it reads the hotkeys (addon/src/hotkeys.h), so a key it takes is
// one it sees, and keeps them in its hotkeys.ini. Another add-on that uses the same keys (Glass Cockpit DLSS Fix:
// the add-on reads its keys from its log) is pointed out. No SDK: the UI module (src/ui) and the tests use it.

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
  /** For development: shown only when the add-on lists it, on a developer's install (hotkeys.h kDeveloperOnly). */
  developer?: boolean;
}

/** The add-on's waiting time for the keys (hotkeys.h Learner::kTimeoutSeconds). */
export const LEARN_SECONDS = 15;

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
    name: "pause", label: "Pause on/off", default: "", developer: true,
    tip: "Development: the sim's full pause, on and off, for measuring. In any pause the mod's METAR changes "
      + "wait until the sim runs again.",
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

/** The shipping keys, until the add-on says (no development ones). */
export const DEFAULT_HOTKEYS: Readonly<Record<string, string>> = HOTKEY_ACTIONS.filter((a) => !a.developer)
  .reduce<Record<string, string>>((all, a) => ({ ...all, [a.name]: a.default }), {});

export const labelOf = (name: string): string => HOTKEY_ACTIONS.find((a) => a.name === name)?.label ?? name;

export const KEYS_ALLOWED = "F1 to F12 with any of Ctrl, Shift and Alt, or a letter or digit with Ctrl or Alt";

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
