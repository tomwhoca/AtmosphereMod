// A METAR in plain words, for the overview's "Weather now" (the redesign, 2026-11-20): the report's station and
// time, wind, visibility, weather, clouds, temperature and pressure. Only the observation: trends (NOSIG, BECMG,
// TEMPO) and remarks (RMK) end it. What it doesn't know it leaves out.

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
      out.sky = "no cloud below 5,000 ft";
    } else if ((m = /^(\d{4})(NDV)?$/.exec(t)) && out.visibility === undefined) {
      const n = parseInt(m[1], 10);
      out.visibility = n >= 9999 ? "10 km or more" : n >= 5000 ? `${n / 1000} km` : `${n} m`;
    } else if ((m = /^(M)?(\d+)?(?:(\d)\/(\d))?SM$/.exec(t)) && out.visibility === undefined) {
      const whole = m[2] ? parseInt(m[2], 10) : 0;
      // (a whole number and a fraction come as two tokens: "1 1/2SM")
      const prev = /^\d+$/.test(tokens[i - 1] || "") ? parseInt(tokens[i - 1], 10) : 0;
      const miles = whole + prev + (m[3] ? parseInt(m[3], 10) / parseInt(m[4], 10) : 0);
      out.visibility = `${m[1] ? "less than " : ""}${miles} mi${miles >= 10 ? " or more" : ""}`;
    } else if (/^\d$/.test(t) && /SM$/.test(tokens[i + 1] || "")) {
      continue;  // (the whole miles before a fraction)
    } else if (/^R\d{2}/.test(t)) {
      continue;  // (runway visual range)
    } else if ((m = /^(FEW|SCT|BKN|OVC)(\d{3}|\/\/\/)(CB|TCU|\/\/\/)?$/.exec(t))) {
      if (m[2] !== "///") {
        out.layers.push({ cover: COVER[m[1]], feet: parseInt(m[2], 10) * 100, type: m[3] && m[3] !== "///" ? m[3] : undefined });
      }
    } else if ((m = /^VV(\d{3})$/.exec(t))) {
      out.sky = `sky obscured, vertical visibility ${feet(parseInt(m[1], 10) * 100)}`;
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
