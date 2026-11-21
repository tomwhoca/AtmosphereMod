// Presets: the rules for their names (the add-on checks them again: addon/src/settings_bridge.h, namespace presets)
// and the settings a preset lacks, in words. No SDK: the UI module (src/ui) and the tests use it.

import { TABS } from "./specs";

/** The add-on's own preset: the shipping defaults. It can't be changed or deleted. */
export const PRESET_DEFAULT = "Default";
export const PRESET_NAME_MAX = 40;
const PRESET_CHARS = /^[A-Za-z0-9 \-_().,+']*$/;

/** A character a preset's name may have. */
export const presetChar = (c: string): boolean => c.length === 1 && PRESET_CHARS.test(c);

/** The name without its spaces at the ends, or "" if the add-on wouldn't take it (it checks again, also for
 * Windows' device names). */
export function presetName(raw: string): string {
  const name = raw.replace(/^ +| +$/g, "");
  return name.length <= PRESET_NAME_MAX && PRESET_CHARS.test(name) && !name.startsWith(".") && !name.endsWith(".")
    ? name : "";
}

/** Preset names are file names: case doesn't count. */
export const sameName = (a: string, b: string): boolean => a.toLowerCase() === b.toLowerCase();

/** What a name may be, for the note when one isn't. */
export const NAME_RULE = `A name: letters, digits, spaces and - _ ( ) . , + ', up to ${PRESET_NAME_MAX} characters`;

/** Each setting the app shows, by key: its name, and its place (the pages' order). */
const SETTING_NAMES = new Map<string, { label: string; order: number }>();
TABS.forEach((t) => t.settings.forEach((s) => SETTING_NAMES.set(s.key, { label: s.label, order: SETTING_NAMES.size })));

/** Settings in words, in the pages' order: "Glory, Station radius and 1 the app doesn't show" (at most
 * MISSING_NAMED by name). */
const MISSING_NAMED = 4;
export function settingsInWords(keys: readonly string[]): string {
  const shown = keys.filter((k) => SETTING_NAMES.has(k))
    .sort((a, b) => SETTING_NAMES.get(a)!.order - SETTING_NAMES.get(b)!.order)
    .map((k) => SETTING_NAMES.get(k)!.label);
  const hidden = keys.length - shown.length;
  if (shown.length === 0) {
    return `${hidden} the app doesn't show`;
  }
  const parts = shown.slice(0, MISSING_NAMED);
  if (shown.length > MISSING_NAMED) {
    parts.push(`${keys.length - MISSING_NAMED} more`);
  } else if (hidden > 0) {
    parts.push(`${hidden} the app doesn't show`);
  }
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
}

/** A preset's missing settings in a line: "Atmo" has no value for 3 settings: Glory, Station radius and 1 the app
 * doesn't show. */
export function missingText(name: string, keys: readonly string[]): string {
  const settings = `${keys.length} setting${keys.length === 1 ? "" : "s"}`;
  if (keys.every((k) => !SETTING_NAMES.has(k))) {
    return `"${name}" has no value for ${settings} the app doesn't show.`;
  }
  return `"${name}" has no value for ${settings}: ${settingsInWords(keys)}.`;
}
