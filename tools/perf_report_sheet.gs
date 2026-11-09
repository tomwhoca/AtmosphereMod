// AtmosphereMod performance reports: a Google Apps Script web app that adds
// each report the EFB app sends (efb/.../src/PerfReport.tsx, reportFields)
// to the sheet "Reports" of the spreadsheet it's attached to.
//
// Setup (once, in the Google account that owns the spreadsheet):
//   1. Open the spreadsheet, Extensions > Apps Script, replace Code.gs with
//      this file, save.
//   2. Deploy > New deployment > type Web app; Execute as: Me; Who has
//      access: Anyone. Deploy, and allow the access it asks for (only this
//      spreadsheet: @OnlyCurrentDoc below).
//   3. The web app URL (https://script.google.com/macros/s/.../exec) goes
//      into REPORT_URL in PerfReport.tsx.
// After changing this file: Deploy > Manage deployments > edit > Version: New
// version, so the URL stays the same.
//
// The URL is public (it's in the app), so anything can be posted to it. A
// report is kept only if it looks like one (kind, version, frame rates,
// the impression), every text is cut to a length, text that a sheet would
// take as a formula is kept as text, and at most RATE_PER_MINUTE reports a
// minute and MAX_ROWS in all are kept. Apps Script doesn't give a web app the
// sender's IP address, so the sheet can't hold one.
//
// POST takes the report as JSON (text/plain, so a browser sends it without a
// CORS preflight) and answers {"ok":true} or {"ok":false,"error":...}.
//
// The sim's tablet browser (Coherent GT) can't read that answer: Apps Script
// answers with a redirect to a one-time URL on script.googleusercontent.com,
// and after a redirect to another site Coherent fetches the target twice (the
// second time with Origin: null), so the second fetch fails. The POST itself
// arrives. So a report carries a random "id", the answer for it is kept for
// ID_SECONDS, and GET ?confirm=<id>&callback=<name> gives it as JSONP (a
// script, which Coherent loads through the redirect once): {"ok":true}, the
// reason it was turned away, or "not received".
//
// GET without parameters answers {"ok":true,...} (to check that the URL
// works). Nothing but a POST writes to the sheet.

/** @OnlyCurrentDoc  (the script asks for access to this spreadsheet only) */

const SHEET = "Reports";
const RATE_PER_MINUTE = 20;
const MAX_ROWS = 50000;
const MAX_TEXT = 1000;
const ID_SECONDS = 600;
const IMPRESSIONS = ["no noticeable difference", "noticeable but fine", "too slow to use"];

// [key in the report, column header]; "received", "changePct", "gpuChangePct" and "extra" are this script's.
// New columns go at the end, so rows already in the sheet keep theirs.
const COLUMNS = [
  ["received", "Received (UTC)"],
  ["version", "Mod version"],
  ["fpsOn", "FPS with the mod"],
  ["fpsOff", "FPS without"],
  ["changePct", "Change with the mod (%)"],
  ["p99On", "99 % of frames under (ms), with"],
  ["p99Off", "99 % of frames under (ms), without"],
  ["gpuMsOn", "GPU ms per frame, with"],
  ["gpuMsOff", "GPU ms per frame, without"],
  ["gpuChangePct", "GPU time change with the mod (%)"],
  ["impression", "How it runs"],
  ["stutters", "Stutters"],
  ["limitedBy", "Limited by (sim's counter)"],
  ["weather", "Weather in the view"],
  ["vr", "VR"],
  ["gpu", "GPU"],
  ["vramGB", "GPU memory (GB)"],
  ["driver", "GPU driver"],
  ["cpu", "CPU"],
  ["threads", "CPU threads"],
  ["ramGB", "RAM (GB)"],
  ["windows", "Windows"],
  ["store", "MSFS store"],
  ["simVersion", "MSFS version"],
  ["output", "Picture size"],
  ["antialiasing", "Anti-aliasing / upscaler"],
  ["frameGeneration", "Frame generation"],
  ["renderScaling", "Render scaling (%)"],
  ["vsync", "V-Sync (sim)"],
  ["frameLimiter", "Frame limit (sim)"],
  ["preset", "Graphics preset"],
  ["cloudQuality", "Cloud quality"],
  ["rtShadows", "Ray-traced shadows"],
  ["reshade", "ReShade"],
  ["reshadeEffects", "ReShade effects on"],
  ["reshadeAddons", "Other ReShade add-ons"],
  ["registerSpace", "Register space"],
  ["packages", "Community packages"],
  ["modSettings", "Mod settings"],
  ["station", "Station"],
  ["altitudeFt", "Altitude (ft)"],
  ["aglFt", "Above the ground (ft)"],
  ["metar", "METAR"],
  ["fpsOn1", "FPS with, 1st part"],
  ["fpsOn2", "FPS with, 2nd part"],
  ["driftPct", "The parts with the mod differ by (%)"],
  ["framesOn", "Frames timed with"],
  ["framesOff", "Frames timed without"],
  ["gpuMsOn1", "GPU ms, 1st part with"],
  ["gpuMsOn2", "GPU ms, 2nd part with"],
  ["gpuFramesOn", "GPU frames timed with"],
  ["gpuFramesOff", "GPU frames timed without"],
  ["simSettings", "Sim settings (all)"],
  ["gpuVendor", "GPU vendor"],
  ["comment", "Comment"],
  ["contact", "Contact"],
  ["extra", "Other fields"],
];
const NUMBERS = ["fpsOn", "fpsOff", "p99On", "p99Off", "fpsOn1", "fpsOn2", "driftPct", "framesOn", "framesOff",
                 "gpuMsOn", "gpuMsOff", "gpuChangePct", "gpuMsOn1", "gpuMsOn2", "gpuFramesOn", "gpuFramesOff",
                 "vramGB", "threads", "ramGB", "renderScaling", "reshadeEffects", "registerSpace", "altitudeFt", "aglFt"];

/** The answer as JSON, or as JSONP when the request names a callback (a plain identifier only). */
function answer(object, callback) {
  const json = JSON.stringify(object);
  if (callback) {
    return ContentService.createTextOutput(callback + "(" + json + ");")
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService.createTextOutput(json).setMimeType(ContentService.MimeType.JSON);
}

const ID = /^[A-Za-z0-9]{8,40}$/;
const CALLBACK = /^[A-Za-z_$][A-Za-z0-9_$]{0,63}$/;

function doGet(e) {
  const p = (e && e.parameter) || {};
  const callback = CALLBACK.test(p.callback || "") ? p.callback : "";
  if (p.confirm !== undefined) {
    if (!ID.test(p.confirm)) {
      return answer({ ok: false, error: "no such report" }, callback);
    }
    const kept = CacheService.getScriptCache().get("id-" + p.confirm);
    return answer(kept ? JSON.parse(kept) : { ok: false, error: "not received" }, callback);
  }
  return answer({ ok: true, service: "AtmosphereMod performance reports" }, callback);
}

function doPost(e) {
  let report;
  try {
    report = JSON.parse(e && e.postData ? e.postData.contents : "");
  } catch (err) {
    return answer({ ok: false, error: "not JSON" });
  }
  const id = report !== null && typeof report === "object" && ID.test(String(report.id || "")) ? report.id : "";
  const cache = CacheService.getScriptCache();
  // the answer, kept for the id's confirmation (the sim can't read this one)
  const reply = (object) => {
    if (id) cache.put("id-" + id, JSON.stringify(object), ID_SECONDS);
    return answer(object);
  };
  const problem = check(report);
  if (problem) {
    return reply({ ok: false, error: problem });
  }
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) {
    return reply({ ok: false, error: "busy, try again" });
  }
  try {
    if (id && cache.get("id-" + id) === JSON.stringify({ ok: true })) {
      return answer({ ok: true });  // (the same report again: kept once)
    }
    const minute = "reports-" + Math.floor(Date.now() / 60000);
    const count = Number(cache.get(minute) || 0);
    if (count >= RATE_PER_MINUTE) {
      return reply({ ok: false, error: "too many reports this minute, try again later" });
    }
    const sheet = reportsSheet();
    if (sheet.getLastRow() > MAX_ROWS) {
      return reply({ ok: false, error: "the sheet is full" });
    }
    sheet.appendRow(row(report));
    cache.put(minute, String(count + 1), 120);
    return reply({ ok: true });
  } finally {
    lock.releaseLock();
  }
}

/** Why this isn't a report, or "" if it looks like one. */
function check(r) {
  if (r === null || typeof r !== "object" || Array.isArray(r)) return "not an object";
  if (r.kind !== "performance-report") return "not a performance report";
  if (typeof r.version !== "string" || !/^\d+\.\d+\.\d+$/.test(r.version)) return "no version";
  for (const key of ["fpsOn", "fpsOff"]) {
    if (typeof r[key] !== "number" || !(r[key] > 0 && r[key] <= 1000)) return "no frame rate " + key;
  }
  if (IMPRESSIONS.indexOf(r.impression) < 0) return "no impression";
  return "";
}

/** The sheet, with its header row (made, or extended with new columns, as needed). */
function reportsSheet() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = book.getSheetByName(SHEET) || book.insertSheet(SHEET);
  const labels = COLUMNS.map((c) => c[1]);
  const width = Math.max(sheet.getLastColumn(), 1);
  const have = sheet.getRange(1, 1, 1, width).getValues()[0];
  if (have.length < labels.length || labels.some((l, i) => have[i] !== l)) {
    sheet.getRange(1, 1, 1, labels.length).setValues([labels]).setFontWeight("bold");
    sheet.setFrozenRows(1);
  }
  return sheet;
}

/** A sheet cell for a value: numbers stay numbers, text is cut and kept as text. */
function cell(key, value) {
  if (value === undefined || value === null) return "";
  if (NUMBERS.indexOf(key) >= 0) {
    const n = Number(value);
    return isFinite(n) ? n : "";
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  let text = Array.isArray(value) ? value.map(String).join(", ") : typeof value === "object" ? JSON.stringify(value)
    : String(value);
  text = text.slice(0, key === "simSettings" || key === "extra" ? 4 * MAX_TEXT : MAX_TEXT);
  return /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
}

function row(r) {
  const known = {};
  COLUMNS.forEach((c) => { known[c[0]] = true; });
  const values = Object.assign({}, r);
  values.received = Utilities.formatDate(new Date(), "UTC", "yyyy-MM-dd HH:mm:ss");
  values.changePct = Math.round((r.fpsOn / r.fpsOff - 1) * 1000) / 10;
  values.gpuChangePct = typeof r.gpuMsOn === "number" && typeof r.gpuMsOff === "number" && r.gpuMsOff > 0
    ? Math.round((r.gpuMsOn / r.gpuMsOff - 1) * 1000) / 10 : undefined;
  const extra = {};
  Object.keys(r).forEach((k) => {
    if (!known[k] && k !== "kind" && k !== "schema" && k !== "id") extra[k] = r[k];
  });
  values.extra = Object.keys(extra).length > 0 ? extra : "";
  return COLUMNS.map((c) => (c[0] === "changePct" ? values.changePct : cell(c[0], values[c[0]])));
}
