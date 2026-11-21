// The UI's one link to the world (the redesign, 2026-11): in the sim ui/sim.ts implements it (on the app's shell,
// AtmoWeather.tsx), the browser harness fakes it (efb/harness). The UI module itself uses no SDK or EFB component,
// only the DOM, so the same bundle runs in both.

import type { OtherHotkey } from "../hotkeys";
import type { Transport } from "../report";

/** The add-on's state message (AtmosphereMod.State, addon/src/settings_bridge.h); every field may be missing from
 * an older add-on. */
export interface AddonState {
  enabled?: boolean;
  /** The T and C tunables, by index (T0.., C0..), and the settings the weather overrides (TE). */
  T?: number[];
  C?: number[];
  TE?: number[];
  weather?: string;
  version?: string;
  presets?: string[];
  preset?: string;
  presetSaved?: boolean;
  presetsMissing?: Record<string, string[]>;
  presetNote?: string;
  /** The performance report (src/report.ts AddonReport), while one is asked for. */
  report?: unknown;
  /** By action (src/hotkeys.ts), as text ("" for none). */
  hotkeys?: Record<string, string>;
  /** Other add-ons' keys (Glass Cockpit DLSS Fix's, while it's loaded). */
  otherHotkeys?: OtherHotkey[];
  /** The action whose keys the add-on waits for ("" for none). */
  learning?: string;
  /** The add-on's note on the last hotkey change (src/hotkeys.ts hotkeyNoteText). */
  hotkeyNote?: string;
  cloudFogOff?: boolean;
}

/** The tablet as the app sees it: its area (px) and the EFB's size and orientation settings. */
export interface Panel {
  width: number;
  height: number;
  vertical: boolean;
  /** 0 small, 1 medium, 2 large (from the area's short side: 624, 745, 863). */
  size: number;
}

export interface TextEvents {
  text?: (text: string) => void;
  outside?: () => void;
}

export interface Host {
  /** The mod's version (the EFB app's). */
  readonly version: string;
  /** A message to the add-on (AtmosphereMod.Settings): {get}, {set: {T3: 4.5}}, {enabled}, ... */
  send(data: unknown): void;
  /** Each state message from the add-on as it arrives. Returns the unsubscribe. */
  onState(callback: (state: AddonState) => void): () => void;
  /** The METAR of the airport nearest the aircraft ("" before the first), now and as it changes. */
  onMetar(callback: (metar: string) => void): () => void;
  /** The METAR link's status line, now and as it changes. */
  onLinkStatus(callback: (status: string) => void): () => void;
  /** The app's area, now and whenever the tablet's size or orientation changes. */
  onPanel(callback: (panel: Panel) => void): () => void;
  /** A SimVar (the performance report's altitude). */
  simvar(name: string, unit: string): number;
  /** A web page in the PC's browser (the newer release's page). */
  openUrl(url: string): void;
  /** A text field takes the keyboard (`focused`) or gives it back. In the sim, keys are the sim's commands unless a
   * field has asked for them. `events.text`: what was typed on the OS's own keyboard (a gamepad's) for the field;
   * `events.outside`: a click outside the tablet, where the field should let go. */
  textFocus(focused: boolean, events?: TextEvents): void;
  /** How a performance report travels, if not to the sheet (the harness: a stand-in, so nothing reaches it). */
  readonly reportTransport?: Transport;
  /** Small values kept across flights (What's new: the version last seen). */
  load(key: string): string | undefined;
  save(key: string, value: string): void;
}

/** A guess at the tablet's size from the app area's short side (624, 745, 863 px at one sim window), for when the
 * EFB's setting can't be read: the sim's window scales those pixels too (x 1.43 in another session). */
export function sizeOf(width: number, height: number): number {
  const short = Math.min(width, height);
  return short < 685 ? 0 : short < 805 ? 1 : 2;
}
