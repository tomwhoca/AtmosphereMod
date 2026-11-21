// What's new: shown over the app once per release that has news, until it's closed; the earlier releases' news
// folded away under it. Which release's was closed last is kept in the sim's stored data under WHATS_NEW_KEY, as
// JSON text (the SDK's DataStore wrote it so up to 0.7.3), so it shows again only for the next release's. No SDK.

/** The release the popup is for, and what it says: change it with every release that has news for the
 * tablet (a release without news leaves it, so nothing shows again). */
export const WHATS_NEW = {
  release: "0.7.3",
  title: "New in AtmosphereMod 0.7.3",
  items: [
    ["Hotkeys",
      "The new Hotkeys tab changes the mod's keys: tap Change, then press the new keys in the sim. Glass Cockpit "
      + "DLSS Fix, one of OptiShade's components, uses Ctrl+Shift+F9 and F11 by default: while it's loaded, the "
      + "tab tells you if its keys are the same as the mod's."],
    ["In-cloud fog on/off",
      "A key of your choice switches the fog inside clouds off for now, and on again: for when it shows just "
      + "below a cloud. It has no key until you give it one in the Hotkeys tab."],
    ["Historical weather",
      "The haze and the clouds now follow historical weather, and switching between live and historical "
      + "weather in flight brings the other weather at once. The haze could also stay with a faraway station "
      + "for the rest of a flight: fixed."],
    ["Installer",
      "Updating and uninstalling no longer stop at a read-only folder in Community (\"Access is denied\"), "
      + "and the installer also runs under Wine on Linux."],
  ] as [string, string][],
};

/** Earlier releases' changes, newest first, folded away under "Changes in previous versions", for those who
 * skipped a release or two. When WHATS_NEW gets a new release, its items so far go on top here. */
export const PREVIOUS: { release: string; items: [string, string][] }[] = [
  {
    release: "0.7.2",
    items: [
      ["Low cloud bases", "The in-cloud fog starts at the reported cloud base, so a low overcast no longer fogs "
        + "the air down to the runway."],
      ["Performance report", "Counts the mod's shaders that ran, asks for your username, and doesn't file the "
        + "same report twice."],
    ],
  },
  {
    release: "0.7.1",
    items: [
      ["Performance report", "A button at the end of the app's page measures what the mod costs on your PC, with "
        + "and without it, and, if you like, sends me the result."],
      ["What's new", "Also lists the changes in earlier versions, folded away."],
    ],
  },
  {
    release: "0.7.0",
    items: [
      ["Varied cloud tops", "Clouds in one layer no longer all end at the same height: where the air rises more, "
        + "they grow taller. On by default (C37 in tunables.ini)."],
      ["Sun-facing cloud sides", "A slider in the Cloud lighting tab, off by default: light on the sides of clouds "
        + "that face the sun. If you turn it on, set Powder effect to 0 %."],
      ["Haze strength", "A slider in the Haze tab, under Haze from METAR, for when the reported haze looks too "
        + "strong or too weak. 100 % is as reported."],
      ["Smoother haze changes", "When a station's report changes between an open-ended visibility (9999, 10SM, "
        + "CAVOK) and a number, the haze fades over 5 minutes instead of jumping."],
      ["With RenoDX", "An installer parameter lets the two mods work together: see \"With RenoDX\" in the README."],
      ["What's new", "This window, once after each update."],
    ],
  },
  {
    release: "0.6.1",
    items: [
      ["Sliders on narrow tablets", "Fixed: they collapsed to a lone thumb that couldn't be dragged."],
      ["Installer", "No more Windows freeze while patching. It also finds PowerShell when it isn't on the PATH, "
        + "recognises ReShade's standard build, and puts the add-on where ReShade's AddonPath points."],
      ["METAR stations", "Near many stations the mod no longer keeps swapping the same ones every couple of "
        + "minutes."],
    ],
  },
  {
    release: "0.6.0",
    items: [
      ["Presets", "The Presets tab saves all your settings under a name, loads them again, and imports presets "
        + "others share."],
      ["Fly-through clouds", "Around your aircraft, liquid clouds get a real cloud's density, so the wingtips fade "
        + "into the cloud (Visibility inside clouds, in the Clouds tab)."],
      ["Smoother in cloud", "The once-a-second stutter in cloudy weather is gone, and the tint on distant clouds "
        + "is cheaper to draw."],
      ["Tablet layout", "Laid out for every tablet size and orientation, with larger text."],
      ["Solid clouds", "The default is 0.5 (was 2). An existing install kept its value: load Default on the "
        + "Presets tab to get it."],
    ],
  },
  {
    release: "0.5.1",
    items: [
      ["OptiShade", "The installer accepts OptiShade's ReShade, and warns when it finds two ReShades."],
    ],
  },
  {
    release: "0.5.0",
    items: [
      ["Haze from the stations around you", "Every part of the view gets the haze of the METAR stations nearest "
        + "to it, so the haze changes gradually from one area to the next."],
      ["Dust and smoke in colour", "Dust looks beige, smoke ochre-brown and dry haze neutral, instead of grey."],
      ["Cloud decks", "Altocumulus breaks up into finer cloudlets, stratocumulus into rows, and the sim's "
        + "stratiform layers become decks with lumpy tops."],
    ],
  },
  {
    release: "0.4.0",
    items: [
      ["Thunderstorms (experimental)", "Off by default (Clouds tab): cumulonimbus with anvils grow where the "
        + "weather shows a storm, and each lives 45 to 80 minutes."],
      ["Sky colours", "Fitted to the sky computed at 95 wavelengths: blue overhead at sunset, the right orange "
        + "for a low sun, and ozone by season and latitude."],
      ["Explanations", "Hover over a setting's name for an explanation."],
      ["Cleaner clouds", "No small white puffs in clear air, no stripes on far layers, and no repeating pattern in "
        + "cloud decks."],
    ],
  },
  {
    release: "0.3.0",
    items: [
      ["Cloud types", "Stratus, stratocumulus, altocumulus and altostratus are thinner with soft edges, while "
        + "cumulus stay solid. Each layer's type comes from its height, depth and cover."],
    ],
  },
  {
    release: "0.2.0",
    items: [
      ["Settings on the tablet", "The main settings as switches and sliders, applied at once and saved to "
        + "tunables.ini."],
      ["Fixes", "The mod no longer switches itself off when other software opens a graphics device of its own, "
        + "and stations reporting a layer without its base no longer put clouds on the ground."],
    ],
  },
  {
    release: "0.1.1",
    items: [
      ["Installer", "Works on non-English Windows, finds more installations, and asks when it can't find the sim "
        + "or the Community folder."],
    ],
  },
  {
    release: "0.1.0",
    items: [
      ["First release", "Haze with a top, set from the METARs; real-world sky colours; cloud lighting with silver "
        + "linings, a glory and your aircraft's shadow; and clouds anchored to the METARs near stations."],
    ],
  },
];

/** The data store's key (the release whose popup was closed last). */
export const WHATS_NEW_KEY = "atmospheremod.whats-new-closed";

/** Whether What's new shows: it has news, and its release isn't the one closed last (`stored`: the stored value). */
export function whatsNewDue(stored: string | undefined): boolean {
  let closed: unknown;
  try {
    closed = stored === undefined ? undefined : JSON.parse(stored);
  } catch {
    closed = stored;
  }
  return WHATS_NEW.items.length > 0 && closed !== WHATS_NEW.release;
}

/** The value to store when it's closed. */
export const closedValue = (): string => JSON.stringify(WHATS_NEW.release);
