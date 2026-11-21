// A METAR in plain words, for the overview's "Weather now" (the redesign, 2026-11-20): the report's station and
// time, wind, visibility, weather, clouds, temperature and pressure, and its flight category. Only the observation:
// trends (NOSIG, BECMG, TEMPO) and remarks (RMK) end it. What it doesn't know it leaves out.

export interface Layer {
  cover: string;       // "few", "scattered", "broken", "overcast"
  feet: number;        // its base above the station
  type?: string;       // "CB", "TCU"
}

export interface Decoded {
  station?: string;
  time?: string;       // "20th, 12:50 UTC"
  auto?: boolean;
  wind?: string;
  visibility?: string;
  weather: string[];
  layers: Layer[];
  sky?: string;        // what the clouds' groups said when they gave no layer: "no significant cloud", "clear", ...
  temperature?: string;
  pressure?: string;
  /** The visibility in metres (9999 and CAVOK: 10,000; "M1/4SM": the quarter mile), for the flight category. */
  visibilityM?: number;
  /** The ceiling: the lowest broken or overcast layer, or the vertical visibility, in feet (none: no ceiling). */
  ceilingFt?: number;
}

/** The flight categories, the worst first. */
export type Category = "LIFR" | "IFR" | "MVFR" | "VFR";
export const CATEGORIES: readonly Category[] = ["LIFR", "IFR", "MVFR", "VFR"];
const METRES_PER_MILE = 1609.344;

/** A METAR's flight category, by the usual limits (the worse of ceiling and visibility): LIFR below 500 ft or 1 mile,
 * IFR below 1,000 ft or 3 miles, MVFR up to 3,000 ft or 5 miles, VFR above. Undefined without either. */
export function flightCategory(d: Decoded): Category | undefined {
  const miles = d.visibilityM === undefined ? undefined : d.visibilityM / METRES_PER_MILE;
  const ceil = d.ceilingFt;
  if (miles === undefined && ceil === undefined && d.layers.length === 0 && !d.sky) {
    return undefined;
  }
  const below = (ft: number, mi: number) => (ceil !== undefined && ceil < ft) || (miles !== undefined && miles < mi);
  if (below(500, 1)) {
    return "LIFR";
  }
  if (below(1000, 3)) {
    return "IFR";
  }
  if ((ceil !== undefined && ceil <= 3000) || (miles !== undefined && miles <= 5)) {
    return "MVFR";
  }
  return miles === undefined ? undefined : "VFR";
}

/** Where the weather is on the flight category scale, 0-100: LIFR at zero visibility 0, VFR at CAVOK (10 km or
 * more, no cloud below 5,000 ft) 100, each category a quarter. Within one, how far into its range the ceiling and
 * the visibility are (ceiling 0-500-1,000-3,000-5,000 ft, visibility 0-1-3-5 miles-10 km), the worse of the two, as
 * for the category itself. Undefined without a category. */
export function categoryPosition(d: Decoded): number | undefined {
  const cat = flightCategory(d);
  if (!cat) {
    return undefined;
  }
  // (a value's place on a scale of limits, each step a quarter)
  const place = (x: number, limits: number[]): number => {
    for (let i = 1; i < limits.length; i++) {
      if (x < limits[i]) {
        return 25 * (i - 1 + Math.max(0, x - limits[i - 1]) / (limits[i] - limits[i - 1]));
      }
    }
    return 100;
  };
  const miles = d.visibilityM === undefined ? undefined : d.visibilityM / METRES_PER_MILE;
  const byVis = miles === undefined ? 100 : place(miles, [0, 1, 3, 5, 10000 / METRES_PER_MILE]);
  const byCeil = d.ceilingFt === undefined ? 100 : place(d.ceilingFt, [0, 500, 1000, 3000, 5000]);
  // (inside its category's quarter: MVFR's limits are inclusive, the line itself is MVFR)
  const low = 25 * CATEGORIES.indexOf(cat);
  return Math.min(Math.max(Math.min(byVis, byCeil), low), low + (cat === "VFR" ? 25 : 24.9));
}

/** The add-on's reading of the haze, "EDDK (+3): continental haze, visibility ~25 km, humidity 87%", taken apart. */
export interface HazeReading {
  kind: string;          // "continental haze", "smoke (FU)", ...
  visibility?: string;   // "~25 km"
  humidity?: string;     // "87%"
  stations: number;      // the METARs it's made of (1, or more with the haze field)
}

export function hazeReading(line: string | undefined): HazeReading | undefined {
  const m = /^\s*([A-Z0-9]{3,4})?(?:\s*\(\+(\d+)\))?\s*:\s*(.+)$/.exec(line || "");
  if (!m) {
    return undefined;
  }
  const parts = m[3].split(/,\s*/);
  const vis = parts.map((p) => /^visibility\s+(.+)$/.exec(p)).filter((x) => x)[0];
  const hum = parts.map((p) => /^humidity\s+(.+)$/.exec(p)).filter((x) => x)[0];
  return { kind: parts[0], visibility: vis ? vis[1] : undefined, humidity: hum ? hum[1] : undefined,
           stations: 1 + (m[2] ? parseInt(m[2], 10) : 0) };
}

const COVER: Record<string, string> = { FEW: "few", SCT: "scattered", BKN: "broken", OVC: "overcast" };
const INTENSITY: Record<string, string> = { "-": "light", "+": "heavy" };
/** Descriptors before what they describe (SH and TS have their own word order: weatherWords). */
const DESCRIPTOR: Record<string, string> = {
  MI: "shallow", PR: "partial", BC: "patches of", DR: "low drifting", BL: "blowing", SH: "", TS: "", FZ: "freezing",
};
const PHENOMENON: Record<string, string> = {
  DZ: "drizzle", RA: "rain", SN: "snow", SG: "snow grains", IC: "ice crystals", PL: "ice pellets", GR: "hail",
  GS: "small hail", UP: "precipitation", BR: "mist", FG: "fog", FU: "smoke", VA: "volcanic ash", DU: "dust",
  SA: "sand", HZ: "haze", PY: "spray", PO: "dust whirls", SQ: "squalls", FC: "funnel cloud", SS: "sandstorm",
  DS: "duststorm",
};
const SKY: Record<string, string> = { NSC: "no significant cloud", NCD: "no cloud detected", CLR: "clear", SKC: "clear" };
const WEATHER = new RegExp(`^(-|\\+|VC)?(${Object.keys(DESCRIPTOR).join("|")})?((?:${Object.keys(PHENOMENON).join("|")})*)$`);

const ordinal = (n: number): string =>
  `${n}${n % 10 === 1 && n !== 11 ? "st" : n % 10 === 2 && n !== 12 ? "nd" : n % 10 === 3 && n !== 13 ? "rd" : "th"}`;
/** "1,500 ft" (by hand: the tablet's engine needn't have Intl's locales). */
export const feet = (n: number): string => `${String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",")} ft`;
const temp = (t: string): number => (t[0] === "M" ? -parseInt(t.slice(1), 10) : parseInt(t, 10));

/** "-SHRA" light rain showers, "+TSRA" heavy thunderstorm with rain, "FZRA" freezing rain, "VCSH" showers nearby. */
function weatherWords(token: string): string | undefined {
  const m = WEATHER.exec(token);
  if (!m || (!m[2] && !m[3])) {
    return undefined;
  }
  const things = (m[3].match(/../g) || []).map((p) => PHENOMENON[p]).join(" and ");
  const what = m[2] === "SH" ? (things ? `${things} showers` : "showers")
    : m[2] === "TS" ? (things ? `thunderstorm with ${things}` : "thunderstorm")
    : m[2] ? `${DESCRIPTOR[m[2]]} ${things}`.trim() : things;
  return m[1] === "VC" ? `${what} nearby` : m[1] ? `${INTENSITY[m[1]]} ${what}` : what;
}

export function decode(metar: string): Decoded {
  const out: Decoded = { weather: [], layers: [] };
  const tokens = metar.trim().split(/\s+/);
  let i = 0;
  if (tokens[i] === "METAR" || tokens[i] === "SPECI") {
    i++;
  }
  if (/^[A-Z][A-Z0-9]{3}$/.test(tokens[i] || "")) {
    out.station = tokens[i++];
  }
  for (; i < tokens.length; i++) {
    const t = tokens[i];
    let m: RegExpExecArray | null;
    if (t === "RMK" || t === "NOSIG" || t === "BECMG" || t === "TEMPO") {
      break;
    } else if ((m = /^(\d{2})(\d{2})(\d{2})Z$/.exec(t))) {
      out.time = `${ordinal(parseInt(m[1], 10))}, ${m[2]}:${m[3]} UTC`;
    } else if (t === "AUTO") {
      out.auto = true;
    } else if ((m = /^(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)$/.exec(t))) {
      const unit = m[4] === "KT" ? "kt" : "m/s";
      const speed = parseInt(m[2], 10);
      out.wind = speed === 0 ? "calm"
        : `${m[1] === "VRB" ? "variable" : `${m[1]}°`} ${speed} ${unit}${m[3] ? `, gusts ${parseInt(m[3], 10)} ${unit}` : ""}`;
    } else if ((m = /^(\d{3})V(\d{3})$/.exec(t)) && out.wind) {
      out.wind += ` (varying ${m[1]}-${m[2]}°)`;
    } else if (t === "CAVOK") {
      out.visibility = "10 km or more";
      out.visibilityM = 10000;
      out.sky = "no cloud below 5,000 ft";
    } else if ((m = /^(\d{4})(NDV)?$/.exec(t)) && out.visibility === undefined) {
      const n = parseInt(m[1], 10);
      out.visibility = n >= 9999 ? "10 km or more" : n >= 5000 ? `${n / 1000} km` : `${n} m`;
      out.visibilityM = n >= 9999 ? 10000 : n;
    } else if ((m = /^(M)?(\d+)?(?:(\d)\/(\d))?SM$/.exec(t)) && out.visibility === undefined) {
      const whole = m[2] ? parseInt(m[2], 10) : 0;
      // (a whole number and a fraction come as two tokens: "1 1/2SM")
      const prev = /^\d+$/.test(tokens[i - 1] || "") ? parseInt(tokens[i - 1], 10) : 0;
      const miles = whole + prev + (m[3] ? parseInt(m[3], 10) / parseInt(m[4], 10) : 0);
      out.visibility = `${m[1] ? "less than " : ""}${miles} mi${miles >= 10 ? " or more" : ""}`;
      out.visibilityM = miles * METRES_PER_MILE * (m[1] ? 0.99 : 1);
    } else if (/^\d$/.test(t) && /SM$/.test(tokens[i + 1] || "")) {
      continue;  // (the whole miles before a fraction)
    } else if (/^R\d{2}/.test(t)) {
      continue;  // (runway visual range)
    } else if ((m = /^(FEW|SCT|BKN|OVC)(\d{3}|\/\/\/)(CB|TCU|\/\/\/)?$/.exec(t))) {
      if (m[2] !== "///") {
        const ft = parseInt(m[2], 10) * 100;
        out.layers.push({ cover: COVER[m[1]], feet: ft, type: m[3] && m[3] !== "///" ? m[3] : undefined });
        if ((m[1] === "BKN" || m[1] === "OVC") && (out.ceilingFt === undefined || ft < out.ceilingFt)) {
          out.ceilingFt = ft;
        }
      }
    } else if ((m = /^VV(\d{3})$/.exec(t))) {
      out.sky = `sky obscured, vertical visibility ${feet(parseInt(m[1], 10) * 100)}`;
      out.ceilingFt = Math.min(out.ceilingFt === undefined ? Infinity : out.ceilingFt, parseInt(m[1], 10) * 100);
    } else if (SKY[t]) {
      out.sky = SKY[t];
    } else if ((m = /^(M?\d{2})\/(M?\d{2})?$/.exec(t))) {
      out.temperature = `${temp(m[1])} °C${m[2] ? `, dew point ${temp(m[2])} °C` : ""}`;
    } else if ((m = /^Q(\d{4})$/.exec(t))) {
      out.pressure = `${parseInt(m[1], 10)} hPa`;
    } else if ((m = /^A(\d{4})$/.exec(t))) {
      out.pressure = `${(parseInt(m[1], 10) / 100).toFixed(2)} inHg`;
    } else {
      const w = weatherWords(t);
      if (w) {
        out.weather.push(w);
      }
    }
  }
  return out;
}

/** The clouds as words: "broken 1,500 ft, broken 1,900 ft (CB)". */
export function layersText(d: Decoded): string {
  if (d.layers.length === 0) {
    return d.sky || "";
  }
  return d.layers.map((l) => `${l.cover} ${feet(l.feet)}${l.type ? ` (${l.type})` : ""}`).join(", ");
}
