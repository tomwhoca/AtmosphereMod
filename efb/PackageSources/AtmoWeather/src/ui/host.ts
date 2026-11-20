// The UI's one link to the world (the redesign, 2026-11): the sim's EFB app implements it (AtmoWeather.tsx), the
// browser harness fakes it (efb/harness). The UI module itself uses no SDK or EFB component, only the DOM, so the
// same bundle runs in both.

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
  report?: unknown;
  hotkeys?: unknown;
  otherHotkeys?: unknown;
  learning?: unknown;
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
