// The settings the EFB app shows: their names, help texts, ranges and friendly units, by topic. No SDK: the UI module
// (src/ui), src/presets.ts and the tests use them.

export interface SliderSpec {
  kind: "slider";
  key: string;
  label: string;
  /** Its description, in paragraphs split at line breaks: what happens in reality, then what the setting does. */
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
  /** Still an experiment: the app marks it with an icon. */
  experimental?: boolean;
}

export interface SwitchSpec {
  kind: "switch";
  key: string;
  label: string;
  /** Has no effect while this setting is 0 (greyed out then, with why). */
  needs?: string;
  /** Its description, in paragraphs split at line breaks: what happens in reality, then what the setting does. */
  tip: string;
  experimental?: boolean;
}

export type Spec = SliderSpec | SwitchSpec;

export interface TabSpec {
  name: string;
  settings: Spec[];
}

export const clamp = (x: number, lo: number, hi: number): number => Math.min(Math.max(x, lo), hi);
const fixed = (digits: number) => (d: number): string => d.toFixed(digits);
const identity = (x: number): number => x;

/** A raw 0..scale value shown as 0-100 %. */
function percent(key: string, label: string, tip: string, scale: number,
                 extra: Partial<SliderSpec> = {}): SliderSpec {
  return {
    kind: "slider", key, label, tip, min: 0, max: 100, step: 5, ...extra,
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
    name: "",
    settings: [
      {
        kind: "switch", key: "T0", label: "Mod atmosphere",
        tip: "Real air is never perfectly clear. A layer of haze near the ground softens the distance and turns "
          + "far hills blue-grey, the sky above it deepens to blue, and its colours shift through the day with "
          + "the sun.\nThis switch turns on the mod's atmosphere: haze that follows the weather reports as you "
          + "fly, or your own values below, and real-world sky colours from light scattering through the whole "
          + "atmosphere, with ozone by season and region. Off, you see the sim's own haze and sky, exactly as "
          + "without the mod.",
      },
    ],
  },
  {
    name: "Haze",
    settings: [
      {
        kind: "switch", key: "T13", label: "Haze from METAR",
        needs: "T0",
        tip: "Weather stations report how far you can see and why: humid mist, a dry continental haze, dust or "
          + "smoke. Together, these reports describe the haze over a whole region.\nThe haze follows these "
          + "reports (METARs) from the sim's live or historical weather, blended between the stations around you "
          + "as you fly: how much there is, how high it reaches and what kind it is. Off, your own values under "
          + "Manual Haze apply.",
      },
      {
        kind: "slider", key: "T16", label: "Haze strength", min: 0, max: 200, step: 5, metarOnly: true,
        needs: "T0",
        tip: "A report's visibility is measured at one spot near the ground, so the haze drawn from it can look a "
          + "little stronger or weaker than the view you know from that place.\nScales the reported haze: 100 % "
          + "is as reported, 50 % about doubles the visibility, 200 % about halves it. The sim's own fog and mist "
          + "stay as they are.",
        toDisplay: (raw) => raw * 100,
        toRaw: (d) => d / 100,
        format: (d) => `${Math.round(d)} %`,
      },
      {
        kind: "switch", key: "T11", label: "Distant clouds fade creamy, not grey",
        needs: "T0",
        tip: "Far-off clouds don't just turn grey: the haze between you and them scatters their blue light out of "
          + "view, so they take on a warm, creamy tint with distance.\nDraws distant clouds that way. Off, they "
          + "dim to grey as in the sim.",
      },
      {
        kind: "slider", key: "T9", label: "Sun glare in haze", min: 0, max: 100, step: 5,
        needs: "T0",
        tip: "Looking toward the sun through haze, you see a bright glow around it, because haze particles scatter "
          + "sunlight mostly forward, toward you.\nHigher values make the glow brighter and tighter; the sim's "
          + "own is about 85 %. With Haze from METAR, the reported haze adds to it.",
        toDisplay: (raw) => (clamp(raw > 0 ? raw : SIM_GLARE, GLARE_LO, GLARE_HI) - GLARE_LO) / (GLARE_HI - GLARE_LO) * 100,
        toRaw: (d) => GLARE_LO + (d / 100) * (GLARE_HI - GLARE_LO),
        format: (d) => `${Math.round(d)} %`,
      },
    ],
  },
  {
    name: "Manual Haze",
    settings: [
      plain("T3", "Haze near the ground",
            "Most haze sits in the lowest one or two kilometres of the air, where dust, moisture and pollution "
            + "collect: thin on a clear mountain day, thick over humid or polluted lowlands.\nHow hazy that layer "
            + "is, as a multiple of the sim's own haze: about ×4 on a clear day in the mountains, ×8–14 over hazy "
            + "or polluted lowlands.",
            1, 15, 0.5, 1, { format: (d) => `×${d.toFixed(1)}`, needs: "T0", manualHaze: true }),
      plain("T1", "Haze layer top",
            "The hazy layer has a top. Climb through it and the air suddenly clears and the sky turns deep blue, "
            + "while below you the haze lies like a flat sheet with a sharp line on the horizon.\nWhere that top "
            + "is, in km above sea level: often 1–2 km, higher on a hot summer afternoon.",
            0.5, 4, 0.1, 1, { format: (d) => `${d.toFixed(1)} km`, needs: "T0", manualHaze: true }),
      plain("T4", "Haze above the layer",
            "Above the hazy layer the air is much cleaner, though a thin haze remains and fades with height.\nHow "
            + "hazy it is up there, as a multiple of the sim's own: lower gives clearer views and a deeper blue sky "
            + "at altitude.",
            0, 2, 0.1, 1, { format: (d) => `×${d.toFixed(1)}`, needs: "T0", manualHaze: true }),
      plain("T6", "Brown haze (pollution)",
            "Soot and smog don't just scatter light, they absorb it, which gives polluted air its dark, brownish "
            + "tint.\nHow much of that light-absorbing haze is in the layer near the ground: 0 is none, 0.15–0.3 "
            + "suits polluted lowlands.",
            0, 0.4, 0.01, 2, { needs: "T0", manualHaze: true }),
      plain("T10", "Haze colour",
            "Haze takes its colour from its particles: the large droplets of humid air make it white or grey, "
            + "tiny dry particles scatter more blue and make it bluish, and smoke turns it ochre-brown.\nSets that "
            + "colour: grey (0), humid or sea air (0.3), continental (1.3), smoke (2).",
            0, 2, 0.1, 1, {
              needs: "T0", manualHaze: true,
              format: (d) => {
                const nearest = HAZE_COLOURS.reduce((a, b) => (Math.abs(b[0] - d) < Math.abs(a[0] - d) ? b : a));
                return `${d.toFixed(1)} ${nearest[1]}`;
              },
            }),
    ],
  },
  {
    name: "Colours",
    settings: [
      {
        kind: "switch", key: "T8", label: "Real-world sky colours",
        needs: "T0",
        tip: "The sky's colours come from sunlight scattered by the air, colour by colour across the whole "
          + "spectrum. At sunset the low sun turns orange, while the ozone layer keeps the sky overhead "
          + "blue.\nDraws the sky with colours fitted to a sky computed across the whole spectrum, closest to the "
          + "real one at low sun and dusk, with the ozone layer by season and latitude. Off, the sim's own "
          + "colours.",
      },
      {
        kind: "switch", key: "C22", label: "Daylight white balance",
        needs: "T0",
        tip: "Your eyes adapt to daylight, so sunlit clouds look pure white to you. The sim's sun has the colour "
          + "of sunlight above the atmosphere, which looks slightly warm on a screen, so sunlit clouds look "
          + "cream.\nBalances the picture to sunlight, as your eyes do. Sky, clouds and ground shift alike, and a "
          + "low sun still glows orange. Off, the sim's own balance.",
      },
    ],
  },
  {
    name: "",
    settings: [
      {
        kind: "switch", key: "C0", label: "Mod clouds",
        tip: "Real clouds owe their look to how sunlight travels through them and to how dense they are: rims that "
          + "light up near the sun, a coloured ring around your shadow, a soft glow from within, solid cores and "
          + "thick fog when you fly into one.\nThis switch turns on the mod's way of drawing clouds: their light "
          + "(silver linings, glory, shadow and the rest) and their shape (solid clouds, cloud types, bases, "
          + "edges and the fog inside). Off, the sim draws its own clouds, exactly as without the mod. The cloud "
          + "layers from the METARs and the thunderstorms stay either way.",
      },
    ],
  },
  {
    name: "Sunlight",
    settings: [
      percent("C2", "Silver lining strength",
              "When a cloud drifts in front of the sun, its thin edges light up in a bright rim: the silver lining. "
              + "Water droplets pass most of the light they catch only slightly onward, so the rim glows only near "
              + "the sun.\nRaise it for brighter rims; clouds farther from the sun give up a little light in return. "
              + "0 % keeps the sim's own.",
              0.5, { needs: "C0" }),
      {
        kind: "slider", key: "C1", label: "Silver lining size", min: 1, max: 7, step: 0.1,
        needs: "C2",
        tip: "A real silver lining hugs the sun and fades within a degree or two of it, because cloud droplets are "
          + "far larger than the wavelength of light.\nSmall angles give the tight, intense rim of real clouds; "
          + "larger ones spread a softer glow over more of the cloud.",
        toDisplay: (raw) => clamp((1 - raw) * LOBE_DEG, 1, 7),
        toRaw: (d) => 1 - d / LOBE_DEG,
        format: (d) => `${d.toFixed(1)}°`,
      },
      plain("C9", "Glory",
            "Fly above clouds with the sun at your back and you may spot a small rainbow ring around your "
            + "aircraft's shadow on the cloud tops, blue inside and red outside. This is the glory, made by light "
            + "the droplets send straight back toward the sun.\nSets how vivid the rings are; 0 switches the glory "
            + "off.",
            0, 2, 0.1, 1, { needs: "C0" }),
      {
        kind: "slider", key: "C10", label: "Glory ring size", min: 1.5, max: 8, step: 0.5,
        needs: "C9",
        tip: "How big a glory looks depends on the droplets: the smaller they are, the wider its rings.\nSet as "
          + "the angle from the centre to the first red ring.",
        toDisplay: (raw) => clamp(GLORY_DEG_UM / (raw > 0 ? raw : 10), 1.5, 8),
        toRaw: (d) => GLORY_DEG_UM / d,
        format: (d) => `${d.toFixed(1)}°`,
      },
      percent("C12", "Your aircraft's shadow on clouds",
              "Above sunlit clouds your aircraft throws its shadow onto them, right in the middle of the glory. "
              + "It's crisp when you're close and blurs as you climb away, because the sun is a disc, not a point: an "
              + "airliner's shadow loses its sharp core at about 400 m.\nSets how dark the shadow's core is; 0 % "
              + "hides it.",
              1, { needs: "C0" }),
      percent("C3", "Bounced light at cloud edges",
              "Light that enters a cloud bounces between droplets many times before it escapes, so a cloud's sunlit "
              + "side glows softly all over, not only where the sun strikes it.\nMore bounced light brightens cloud "
              + "sides when the sun is off to one side, and takes the same share from the silver lining.",
              1, { needs: "C0" }),
      percent("C36", "Sun-facing cloud sides",
              "Seen from the side, a thick cumulus is brightest where it faces the sun, white as a sunlit wall, and "
              + "much darker on its shaded flanks.\nSets how bright the sun-facing sides get; 0 % is off. It uses a "
              + "larger version of the cloud shader, which costs a little performance, and it clashes with the Powder "
              + "effect: set that to 0 % while you use this.",
              1, { needs: "C0" }),
      {
        kind: "slider", key: "C5", label: "Light deep inside clouds", min: 1, max: 6, step: 0.1,
        needs: "C0",
        tip: "Light keeps scattering deep into thick clouds, so their bases and cores glow a luminous grey instead "
          + "of turning dark.\nA multiple of the sim's own: higher values brighten bases and interiors, while "
          + "sunlit edges stay as they are.",
        toDisplay: (raw) => clamp((raw > 0 ? raw : SIM_DEEP_LIGHT) / SIM_DEEP_LIGHT, 1, 6),
        toRaw: (d) => d * SIM_DEEP_LIGHT,
        format: (d) => `×${d.toFixed(1)}`,
      },
      percent("C6", "Powder effect (grey rims, sun behind you)",
              "With the sun behind you, cumulus turrets can look dusted with powder. Light needs a few metres "
              + "inside a cloud to build up, so thin edges turn grey and dense cores bright white, setting each "
              + "turret apart from the next.\nSets how strong the effect is. It fades out toward the sun and leaves "
              + "silver linings untouched.",
              1, { needs: "C0" }),
    ],
  },
  {
    name: "Shape",
    settings: [
      plain("C16", "Solid clouds",
            "Real clouds are dense: a cumulus is opaque, its base is a solid grey floor, and an overcast hides "
            + "the sun completely. The sim's clouds are far thinner, so their bases and edges are see-through and "
            + "the sun glows through an overcast.\nSets how dense clouds get, measured at a cumulus core: 1 matches "
            + "the sim's core, higher is denser. Together with Cloud bases it fills in the thin bases and edges. "
            + "Only what you see changes, not the light, so clouds become more solid without turning darker.",
            0, 4, 0.25, 2, { needs: "C0", format: (d) => (d <= 0 ? "the sim's" : d.toFixed(2)) }),
      {
        kind: "slider", key: "C17", label: "Cloud bases: soft to solid", min: 0, max: 1, step: 0.05,
        needs: "C16",
        tip: "A real cloud is dense from its base up, so from below it shows a solid underside. In the sim, "
          + "density builds up over 3 km above the base, which leaves bases and thin decks nearly "
          + "transparent.\n“Real” makes clouds solid from the base up; toward “the sim's” they keep the slow "
          + "build-up.",
        toDisplay: (raw) => clamp((1 - (raw > 0 ? raw : 1)) / 0.67, 0, 1),
        toRaw: (d) => 1 - 0.67 * d,
        format: (d) => (d <= 0.01 ? "the sim's" : d >= 0.99 ? "real" : `${Math.round(d * 100)} %`),
      },
      plain("C19", "High-cloud thickness",
            "High clouds are made of ice crystals, so cirrus and thin sheets stay delicate and translucent, with "
            + "soft edges.\nToward 6,000 m the mod lets clouds return to the sim's translucent look, and this sets "
            + "how visible they are there. Higher values show more cirrus, but thick icy layers at cruise altitude "
            + "turn more fog-like.",
            0.5, 3, 0.1, 1, { format: (d) => `×${d.toFixed(1)}`, needs: "C16" }),
      percent("C28", "Smooth cloud bases",
              "Cumulus have flat bases, where rising air reaches the height at which its moisture condenses, while "
              + "their tops keep billowing.\nFades the sim's round puffs out toward each cloud's base, so the "
              + "underside turns flat and the tops keep their shape. 0 % keeps the sim's rounded bases.",
              1, { needs: "C0" }),
      plain("C8", "Crisper cloud edges",
            "Real cumulus have sharp, clean edges, but the sim fades every cloud's edge over 220 m or more, which "
            + "makes them look soft.\n1 halves that fade, 3 cuts it to a quarter. Clouds keep their size, and "
            + "distant ones keep softer edges to avoid flicker. Very high values can make edges look grainy. With "
            + "Cloud types on, cumulus get the crispness of 1 anyway and layered clouds keep the sim's soft edges, "
            + "so only values above 1 make a difference.",
            0, 4, 0.25, 2, { needs: "C0" }),
      percent("C23", "Cloud types",
              "Clouds come in kinds: dense, crisp cumulus heaps; lumpy stratocumulus decks; flat, grey stratus; "
              + "mid-level altocumulus patches; and veil-like altostratus. Each looks different up close.\nLets the "
              + "mod tell them apart by height, depth and cover, and draw each its own way: cumulus dense and crisp, "
              + "the others thinner and softer. 0 % draws every cloud like cumulus. Live weather only.",
              1, { needs: "C0" }),
      percent("C24", "Stratocumulus thickness",
              "Stratocumulus are the lumpy grey-and-white low decks of a typical overcast day, with patches of blue "
              + "between their rolls.\nHow solid they look, as a share of Solid clouds.", 1, { needs: "C23" }),
      percent("C25", "Stratus thickness",
              "Stratus is a flat, featureless grey layer close to the ground, like lifted fog.\nHow solid it looks, "
              + "as a share of Solid clouds.", 1, { needs: "C23" }),
      percent("C26", "Altocumulus thickness",
              "Altocumulus are small white puffs and rolls at mid level, often in rows or patches.\nHow solid they "
              + "look, as a share of Solid clouds.", 1, { needs: "C23" }),
      percent("C27", "Altostratus thickness",
              "Altostratus is a grey veil at mid level, through which the sun shows as if through frosted "
              + "glass.\nHow solid it looks, as a share of Solid clouds.", 1, { needs: "C23" }),
      plain("C33", "Visibility inside clouds",
            "Inside a real cloud you can barely see your wingtips: visibility drops to 20–150 m. The sim's clouds "
            + "are so thin inside that you still see hundreds of metres.\nSets the visibility inside clouds, around "
            + "your aircraft only, so clouds seen from outside keep their look. Ice clouds stay thin. It starts at "
            + "the reported cloud base, because the sim's low decks can begin well below it. A hotkey can switch it "
            + "off for a while (Keys page).",
            0, 200, 10, 0, { needs: "C0", format: (d) => (d <= 0 ? "the sim's" : `${Math.round(d)} m`) }),
      percent("C34", "Structure inside clouds",
              "A real cloud is never an even grey inside: you fly through denser and thinner patches, brighter "
              + "where more sunlight gets through.\nSets how strong these patches are; 0 % is an even fog.",
              1, { needs: "C33" }),
    ],
  },
  {
    name: "Weather",
    settings: [
      percent("C20", "Clouds follow the METARs",
              "Weather stations report the cloud layers above them: the base, how much of the sky they cover, and "
              + "towering cumulus or thunderclouds. The sim's live clouds often look different from what they "
              + "report.\nNear the stations, the sim's clouds take on the reported layers, blended between up to "
              + "eight stations around you; new stations fade in over a few minutes. 100 % follows the reports fully "
              + "within the station radius. Live weather only: weather presets and other weather add-ons keep their "
              + "own clouds.",
              1),
      plain("C21", "Station radius",
            "A weather report describes the sky around its station, and the farther you are from it, the less it "
            + "tells you.\nEach station's clouds apply fully within this distance and fade out over the same "
            + "distance beyond, so the sim's own weather takes over at about twice the radius.",
            10, 100, 5, 0, { format: (d) => `${Math.round(d)} km` }),
      {
        kind: "switch", key: "C32", label: "Thunderstorms", experimental: true,
        tip: "A thunderstorm grows from a cumulus into a towering cumulonimbus that reaches the top of the weather "
          + "layer, the tropopause, and spreads out there into a flat anvil.\nGrows such storms where a station "
          + "nearby reports thunder or CB/TCU, or where the sim's rain is very heavy; each lives 45–80 minutes. "
          + "They're for the eyes only, with no turbulence or other effect on your flight. Live weather only.",
      },
    ],
  },
];
