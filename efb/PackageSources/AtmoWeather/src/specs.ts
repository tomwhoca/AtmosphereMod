// The settings the EFB app shows (moved from Settings.tsx, 2026-11-20): their names, help texts, ranges and
// friendly units, by tab. No SDK: the old UI (Settings.tsx) and the redesign's (src/ui) both use them.

export interface SliderSpec {
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
  /** Shown only while the haze comes from the METAR (T13). */
  metarOnly?: boolean;
  more?: boolean;
  /** Still an experiment: the redesign marks it with an icon (its label says so in words for the old UI). */
  experimental?: boolean;
}

export interface SwitchSpec {
  kind: "switch";
  key: string;
  label: string;
  tip: string;
  more?: boolean;
  experimental?: boolean;
}

export type Spec = SliderSpec | SwitchSpec;

export interface TabSpec {
  name: string;
  settings: Spec[];
  /** The "More" section's title. */
  more?: string;
}

export const clamp = (x: number, lo: number, hi: number): number => Math.min(Math.max(x, lo), hi);
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
        kind: "slider", key: "T16", label: "Haze strength", min: 0, max: 200, step: 5, metarOnly: true,
        tip: "How much of the haze the reports describe is drawn. 100 % is as reported; lower gives clearer air and "
          + "longer visibility than reported (50 % about doubles it), higher gives more haze. The sim's own fog and "
          + "mist stay as they are.",
        toDisplay: (raw) => raw * 100,
        toRaw: (d) => d / 100,
        format: (d) => `${Math.round(d)} %`,
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
      percent("C36", "Sun-facing cloud sides",
              "Light on the sides of clouds that face the sun, worked out as real, thick cloud reflects it. It fades "
              + "out where the cloud is shaded. 0 % is off. With it on, "
              + "set \"Powder effect (grey rims, sun behind you)\" to 0 %: the two don't go well together.",
              1),
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
      plain("C33", "Visibility inside clouds",
            "How far you see inside a cloud, around your aircraft: the wings fade into it as in a real cloud. "
            + "The sim's clouds are so thin inside that you see for hundreds of metres. Only near you, so clouds "
            + "seen from outside keep their look; ice clouds stay thin. Not below the reported cloud base (the "
            + "nearest METAR's, or a weather preset's lowest layer): the sim's low decks can start well below it. "
            + "A hotkey can switch it off for now (the Hotkeys tab).",
            0, 200, 10, 0, { format: (d) => (d <= 0 ? "the sim's" : `${Math.round(d)} m`) }),
      {
        ...percent("C34", "Structure inside clouds",
                   "Patches of denser and thinner cloud around you that you fly through, each shaded by the cloud "
                   + "toward the sun: thinner parts brighter. 0 % is an even fog.",
                   1),
        needs: "C33",
      },
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
        kind: "switch", key: "C32", label: "Thunderstorms (experimental)", experimental: true,
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
