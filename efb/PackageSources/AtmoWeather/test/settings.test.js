// Checks src/Settings.tsx without the sim: bundles it for Node with stand-ins
// for the MSFS and EFB SDKs (test/stubs), then tests the friendly units against
// the shipping defaults and the model's messages. Run by tools/test_efb_settings.py.

const esbuild = require("esbuild");
const path = require("path");
const os = require("os");
const fs = require("fs");
const vm = require("vm");

const out = path.join(os.tmpdir(), "atmo-settings-test.js");
esbuild.buildSync({
  entryPoints: [path.join(__dirname, "..", "src", "Settings.tsx")],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile: out,
  jsxFactory: "FSComponent.buildComponent",
  jsxFragment: "FSComponent.Fragment",
  alias: {
    "@microsoft/msfs-sdk": path.join(__dirname, "stubs", "msfs-sdk.js"),
    "@efb/efb-api": path.join(__dirname, "stubs", "efb-api.js"),
  },
  logLevel: "error",
});
const outDefaults = path.join(os.tmpdir(), "atmo-defaults-test.js");
esbuild.buildSync({
  entryPoints: [path.join(__dirname, "..", "src", "defaults.ts")],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile: outDefaults,
  logLevel: "error",
});
const outNews = path.join(os.tmpdir(), "atmo-whatsnew-test.js");
esbuild.buildSync({
  entryPoints: [path.join(__dirname, "..", "src", "WhatsNew.tsx")],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile: outNews,
  jsxFactory: "FSComponent.buildComponent",
  jsxFragment: "FSComponent.Fragment",
  alias: {
    "@microsoft/msfs-sdk": path.join(__dirname, "stubs", "msfs-sdk.js"),
    "@efb/efb-api": path.join(__dirname, "stubs", "efb-api.js"),
  },
  logLevel: "error",
});
const outReport = path.join(os.tmpdir(), "atmo-perfreport-test.js");
esbuild.buildSync({
  entryPoints: [path.join(__dirname, "..", "src", "PerfReport.tsx")],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile: outReport,
  jsxFactory: "FSComponent.buildComponent",
  jsxFragment: "FSComponent.Fragment",
  alias: {
    "@microsoft/msfs-sdk": path.join(__dirname, "stubs", "msfs-sdk.js"),
    "@efb/efb-api": path.join(__dirname, "stubs", "efb-api.js"),
  },
  logLevel: "error",
});
global.window = { setTimeout, clearTimeout };
const { TABS, SettingsModel, PRESET_DEFAULT, presetChar, presetName, sameName, missingText } = require(out);
const { DEFAULTS } = require(outDefaults);
const { WHATS_NEW, WHATS_NEW_KEY, WhatsNewModel } = require(outNews);
const perf = require(outReport);

let failures = 0;
function check(ok, what) {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failures++;
}

// friendly units: each default shows inside its slider's range and converts back
for (const tab of TABS) {
  for (const s of tab.settings) {
    check(DEFAULTS[s.key] !== undefined, `${s.key} (${s.label}) has a shipping default`);
    if (s.kind !== "slider") {
      continue;
    }
    const raw = DEFAULTS[s.key];
    const d = s.toDisplay(raw);
    const back = s.toRaw(d);
    check(d >= s.min - 1e-9 && d <= s.max + 1e-9, `${s.key}: default ${raw} shows as ${s.format(d)}, inside ${s.min}..${s.max}`);
    check(Math.abs(back - raw) <= 1e-6 * Math.max(1, Math.abs(raw)), `${s.key}: ${s.format(d)} converts back to ${back}`);
    for (const end of [s.min, s.max]) {
      check(Number.isFinite(s.toRaw(end)) && s.toRaw(end) >= 0, `${s.key}: the slider's end ${s.format(end)} is ${s.toRaw(end)}`);
    }
  }
}
const spec = (key) => TABS.flatMap((t) => t.settings).find((s) => s.key === key);
check(spec("C1").format(spec("C1").toDisplay(0.95)) === "2.2°", "silver lining: g 0.95 is 2.2°");
check(Math.abs(spec("C10").toRaw(4.8) - 5) < 1e-9, "glory ring 4.8° is a 5 um droplet");
check(spec("C17").format(spec("C17").toDisplay(0)) === "the sim's", "bases: C17 = 0 (the sim's profile) shows as the sim's");
check(spec("C5").format(spec("C5").toDisplay(0)) === "×1.0", "deep light: C5 = 0 (the sim's) shows as ×1.0");
check(spec("T10").format(1.3) === "1.3 continental blue" && spec("T10").format(0.2) === "0.2 humid white", "haze colour names");
check(spec("C9").format(0) === "off" && spec("C16").format(0) === "the sim's", "off and the sim's at 0");
check(spec("T3").manualHaze && spec("T10").manualHaze && !spec("T9").manualHaze, "manual haze: T3..T10 but not the glare");
check(spec("C19").needs === "C16", "high-cloud thickness needs solid clouds");
check(spec("T16").metarOnly && !spec("T3").metarOnly && spec("T16").toDisplay(1) === 100 && spec("T16").toRaw(50) === 0.5 &&
      spec("T16").min === 0 && spec("T16").max === 200 && spec("T16").format(100) === "100 %",
      "haze strength: 100 % is as reported, 0-200 %, only with the METAR's haze");

// the model
const sent = [];
const model = new SettingsModel((data) => sent.push(data));
model.requestState();
check(JSON.stringify(sent.pop()) === '{"get":true}', "asks for the state");
check(!model.connected.get(), "not connected before the add-on answers");
const T = Array.from({ length: 14 }, (_, i) => DEFAULTS[`T${i}`] ?? 0);
const C = Array.from({ length: 32 }, (_, i) => DEFAULTS[`C${i}`] ?? 0);
const TE = T.slice();
TE[3] = 7.5;
model.onState(JSON.stringify({ enabled: false, T, C, TE, weather: "KIND: continental haze" }));
check(model.connected.get() && !model.enabled.get() && model.weather.get() === "KIND: continental haze", "state: connected, F9, weather");
check(model.value("T3").get() === 4 && model.effectiveValue("T3").get() === 7.5, "state: T3 from the ini, 7.5 from the METAR");

(async () => {
  model.set("C2", 0.3);
  model.set("C2", 0.35);
  model.set("T9", 0.6);
  check(sent.length === 0, "a drag isn't sent at once");
  await new Promise((r) => setTimeout(r, 150));
  check(sent.length === 1 && JSON.stringify(sent[0]) === '{"set":{"C2":0.35,"T9":0.6}}', `coalesced: ${JSON.stringify(sent[0])}`);
  const echo = C.slice();
  echo[2] = 0.5;  // the add-on's state from before the change
  model.onState(JSON.stringify({ T, C: echo, TE }));
  check(model.value("C2").get() === 0.35, "an echo right after an edit doesn't undo it");
  model.onState(JSON.stringify({ T, C: echo, TE }));
  await new Promise((r) => setTimeout(r, 1600));
  model.onState(JSON.stringify({ T, C: echo, TE }));
  check(model.value("C2").get() === 0.5, "after the hold, the add-on's value counts (e.g. F10 reloaded the file)");
  sent.length = 0;
  model.reset(["C2", "C21", "T13"]);
  await new Promise((r) => setTimeout(r, 150));
  check(JSON.stringify(sent[0]) === '{"set":{"C2":0.5,"C21":50,"T13":1}}', `reset sends the defaults: ${JSON.stringify(sent[0])}`);
  model.setEnabled(true);
  check(JSON.stringify(sent[1]) === '{"enabled":true}' && model.enabled.get(), "the master switch");
  model.reload();
  check(JSON.stringify(sent[2]) === '{"reload":true}', "the Reload button");

  // presets
  check(!model.hasPresets.get() && model.presets.get().length === 0, "presets: none from an add-on without them");
  const presets = ["Default", "Summer"];
  model.onState(JSON.stringify({ T, C, TE, presets, preset: "Summer", presetSaved: false,
                                 presetNote: "Not loaded: your settings aren't saved" }));
  check(model.hasPresets.get() && model.presets.get().join() === "Default,Summer" &&
        model.preset.get() === "Summer" && !model.presetSaved.get() &&
        model.presetNote.get() === "Not loaded: your settings aren't saved", "presets: listed, the current one, the note");
  const listed = model.presets.get();
  model.onState(JSON.stringify({ T, C, TE, presets, preset: "Summer", presetSaved: true }));
  check(model.presets.get() === listed && model.presetSaved.get() && model.presetNote.get() === "",
        "presets: the same list again isn't a change");
  sent.length = 0;
  model.set("C2", 0.4);
  model.savePreset("Winter");
  check(sent.length === 2 && JSON.stringify(sent[0]) === '{"set":{"C2":0.4}}' &&
        JSON.stringify(sent[1]) === '{"savePreset":"Winter"}', "save: the change still waiting is sent first");
  await new Promise((r) => setTimeout(r, 150));
  check(sent.length === 2, "... and not again");
  model.set("C2", 0.45);
  check(model.unsent(), "unsent: a change waiting");
  model.loadPreset("Default", true);
  check(sent.length === 4 && JSON.stringify(sent[2]) === '{"set":{"C2":0.45}}' &&
        JSON.stringify(sent[3]) === '{"loadPreset":"Default","discard":true}' && !model.unsent(),
        "load: the change still waiting is sent first; discarding");
  const loaded = C.slice();
  loaded[2] = 0.5;
  model.onState(JSON.stringify({ T, C: loaded, TE, presets }));
  check(model.value("C2").get() === 0.5, "load: the preset's values show at once, though C2 was just edited");
  check(model.preset.get() === "" && !model.presetSaved.get(), "presets: none current without the fields");
  model.loadPreset("Summer");
  check(JSON.stringify(sent[4]) === '{"loadPreset":"Summer"}', "load: not discarding unless asked");
  model.deletePreset("Summer");
  check(JSON.stringify(sent[5]) === '{"deletePreset":"Summer"}', "delete");
  model.importPreset();
  check(JSON.stringify(sent[6]) === '{"importPreset":true}', "import");

  // settings a preset lacks
  model.onState(JSON.stringify({ T, C, TE, presets, presetsMissing: { Summer: ["C33", "T14"] } }));
  const gaps = model.presetMissing.get();
  check(gaps.Summer.join() === "C33,T14", "missing: per preset");
  model.onState(JSON.stringify({ T, C, TE, presets, presetsMissing: { Summer: ["C33", "T14"] } }));
  check(model.presetMissing.get() === gaps, "missing: the same again isn't a change");
  model.loadPreset("Summer", true, "defaults");
  model.loadPreset("Summer", false, "current");
  check(JSON.stringify(sent[7]) === '{"loadPreset":"Summer","discard":true,"fill":"defaults"}' &&
        JSON.stringify(sent[8]) === '{"loadPreset":"Summer","fill":"current"}', "load: filling in what it lacks");
  const said = (keys, want) => {
    const got = missingText("Atmo", keys);
    check(got === want, `missing in a line: ${got}`);
  };
  said(["C9"], '"Atmo" has no value for 1 setting: Glory.');
  said(["C34", "C33", "T14", "C32"], '"Atmo" has no value for 4 settings: Visibility inside clouds, Structure inside '
    + "clouds, Thunderstorms (experimental) and 1 the app doesn't show.");
  said(["C2", "C1", "C9", "C12", "C3", "C5", "T14"], '"Atmo" has no value for 7 settings: Silver lining strength, '
    + "Silver lining size, Glory, Your aircraft's shadow on clouds and 3 more.");
  said(["T14", "C35"], "\"Atmo\" has no value for 2 settings the app doesn't show.");
  check(presetName("  My set (2) ") === "My set (2)" && presetName("XP12-like, v2 + haze 'b'") !== "" &&
        presetName("a/b") === "" && presetName(".x") === "" && presetName("x.") === "" &&
        presetName("x".repeat(41)) === "" && presetName("x".repeat(40)) !== "",
        "names: trimmed; a slash, dots at the ends and over 40 characters refused");
  check(presetChar("a") && presetChar("'") && presetChar(" ") && !presetChar("\\") && !presetChar("\"") &&
        !presetChar("ä") && !presetChar(":"), "names: the characters the add-on takes");
  check(sameName("default", PRESET_DEFAULT), "names: case doesn't count");

  // what's new: once per release, until closed
  const memory = (stored) => {
    const data = stored === undefined ? {} : { [WHATS_NEW_KEY]: stored };
    return { data, get: (k) => data[k], set: (k, v) => { data[k] = v; } };
  };
  const fresh = memory();
  const first = new WhatsNewModel(fresh);
  check(first.open.get(), "what's new: shows on a first start");
  first.close();
  check(!first.open.get() && fresh.data[WHATS_NEW_KEY] === WHATS_NEW.release, "what's new: closing keeps the release");
  check(!new WhatsNewModel(fresh).open.get(), "what's new: not again once closed");
  check(new WhatsNewModel(memory("0.6.1")).open.get(), "what's new: shows after an update");
  const noStore = new WhatsNewModel();
  check(noStore.open.get(), "what's new: shows without the sim's data store");
  noStore.close();
  check(!noStore.open.get(), "what's new: and closes there too");
  check(/^\d+\.\d+\.\d+$/.test(WHATS_NEW.release) && WHATS_NEW.items.every(([h, t]) => h && t && !/  /.test(t)),
        `what's new: release ${WHATS_NEW.release}, ${WHATS_NEW.items.length} items, each with a heading and a text`);
  const labels = TABS.flatMap((t) => t.settings.map((s) => s.label));
  for (const name of ["Haze strength", "Sun-facing cloud sides", "Haze from METAR", "Powder effect"]) {
    check(labels.some((l) => l.startsWith(name)), `what's new: "${name}" is a setting's name in the app`);
  }

  {
  // performance report: the add-on's report (as perf_report_test printed it on the developer's PC) to the sheet
  const system = {
    pc: { gpu: "NVIDIA GeForce RTX 5080", gpuVendor: "NVIDIA", vramGB: 15.6, driver: "621.14",
      cpu: "AMD Ryzen 9 7900X3D 12-Core Processor", threads: 24, ramGB: 64, windows: "Windows 11 (build 26200)" },
    sim: { store: "Microsoft Store", output: "3840x2160", packages: ["tritrisim-gfx"], cfg: {
      "Video.AntiAliasing": "DLSS", "Video.DLSSMode": "AUTO", "Video.FrameGeneration": "DLSSG",
      "Video.NBFramesToGenerate": "2", "Video.PrimaryScaling": "1.000000", "Video.VSync": "0",
      "Video.AntiAliasingVR": "TAA", "Video.FrameGenerationVR": "NONE", "Video.PrimaryScalingVR": "0.800000",
      "Graphics.Preset": "Custom", "Graphics.VolumetricClouds.Quality": "3", "Graphics.RaytracedShadows.Enabled": "1",
      "GraphicsVR.Preset": "VRMedium", "GraphicsVR.VolumetricClouds.Quality": "1",
      "GraphicsVR.RaytracedShadows.Enabled": "0" } },
    reshade: { loader: "dxgi.dll", version: "6.8.0.0", effectsOn: 2, addons: ["renodx-msfs2024.addon64", "other.addon64"],
      registerSpace: 51 },
  };
  const result = { fpsOn: 61.24, fpsOff: 80.5, msOn: 16.3, msOff: 12.4, p99On: 21.04, p99Off: 15.5, fpsOn1: 61.0,
    fpsOn2: 61.5, drift: 0.008, framesOn: 980, framesOff: 644 };
  const done = { phase: "done", progress: 1, result, system };
  const ctx = { version: "0.7.1", metar: "LIRF 090920Z 09005KT 9999 BKN040 15/12 Q1025 NOSIG", weather: "LIRF: haze",
    altitudeFt: 5012.4, aglFt: 4890.2, changed: ["C16 = 2 (0.5)"], defaultPreset: false };
  const answers = { vr: false, weather: "broken or overcast", impression: "noticeable but fine", stutters: "",
    limitedBy: "GPU", comment: "", contact: "" };
  const f = perf.reportFields(done, ctx, answers);
  check(f.kind === "performance-report" && f.version === "0.7.1" && f.fpsOn === 61.2 && f.fpsOff === 80.5
        && f.p99On === 21 && f.driftPct === 0.8 && f.framesOn === 980 && f.framesOff === 644,
        "report: the measurement, as numbers");
  check(f.gpu === "NVIDIA GeForce RTX 5080" && f.vramGB === 15.6 && f.driver === "621.14" && f.cpu.startsWith("AMD Ryzen 9")
        && f.threads === 24 && f.ramGB === 64 && f.windows === "Windows 11 (build 26200)", "report: the PC");
  check(f.store === "Microsoft Store" && f.output === "3840x2160" && f.antialiasing === "DLSS AUTO"
        && f.frameGeneration === "DLSSG x2" && f.renderScaling === 100 && f.vsync === "off" && f.preset === "Custom"
        && f.cloudQuality === "Ultra" && f.rtShadows === "on", `report: the sim's settings (${JSON.stringify(f)})`);
  check(f.reshade === "ReShade 6.8.0.0 (dxgi.dll)" && f.reshadeEffects === 2
        && f.reshadeAddons === "renodx-msfs2024.addon64, other.addon64" && f.registerSpace === 51
        && f.packages === "tritrisim-gfx", "report: ReShade, its add-ons, the Community packages");
  check(f.impression === "noticeable but fine" && f.limitedBy === "GPU" && f.stutters === "" && f.vr === false
        && f.weather === "broken or overcast" && f.modSettings === "C16 = 2 (0.5)" && f.station === "LIRF"
        && f.altitudeFt === 5012 && f.aglFt === 4890, "report: the answers, the mod's settings, where");
  check(f.simSettings.includes("Video.AntiAliasing DLSS") && !/[A-Z]:\\|Users/.test(JSON.stringify(f)),
        "report: all the sim's settings, and no paths");
  const vr = perf.reportFields(done, ctx, { ...answers, vr: true });
  check(vr.vr === true && vr.antialiasing === "TAA" && vr.frameGeneration === "off" && vr.cloudQuality === "Medium"
        && vr.rtShadows === "off" && vr.renderScaling === 80 && vr.preset === "VRMedium" && vr.vsync === "",
        `report: in VR, the sim's VR settings (${JSON.stringify(vr)})`);
  const odd = perf.reportFields({ ...done, system: { ...system, sim: { ...system.sim, output: "1132x598", packages: [],
    cfg: { "Video.AntiAliasing": "DLSS", "Video.DLSSMode": "DLAA" } }, reshade: { ...system.reshade, loader: "ReShade64.dll",
    effectsOn: 0, addons: [] } } }, { ...ctx, defaultPreset: true }, answers);
  check(odd.output === "1132x598" && odd.antialiasing === "DLSS DLAA" && odd.reshade.startsWith("OptiShade")
        && odd.cloudQuality === "" && odd.renderScaling === undefined && odd.reshadeAddons === "none"
        && odd.modSettings === "Default preset", "report: an odd window size, DLAA, OptiShade, no cloud setting");
  check(perf.sendOutcome({ status: 200, text: '{"ok":true}' }).ok
        && !perf.sendOutcome({ status: 200, text: "<html><title>Please Complete</title>" }).ok
        && perf.sendOutcome({ status: 200, text: '{"ok":false,"error":"no impression"}' }).why.includes("no impression")
        && perf.sendOutcome({ status: 0, text: "" }).why.includes("no answer"),
        "report: only the sheet's ok counts as sent (a page with HTTP 200 doesn't)");

  // the sheet's script (tools/perf_report_sheet.gs), in a stand-in for Apps Script
  const sheetScript = () => {
    const rows = [];
    const sheet = {
      getLastRow: () => rows.length,
      getLastColumn: () => (rows.length > 0 ? rows[0].length : 0),
      getRange: (r, c, nr, nc) => ({
        getValues: () => [Array.from({ length: nc }, (_, i) => (rows[r - 1] ?? [])[c - 1 + i] ?? "")],
        setValues: (v) => { rows[r - 1] = v[0].slice(); return { setFontWeight: () => {} }; },
      }),
      setFrozenRows: () => {},
      appendRow: (row) => rows.push(row),
    };
    const cache = new Map();
    const env = {
      ContentService: { MimeType: { JSON: "json" }, createTextOutput: (text) => ({ text, setMimeType() { return this; } }) },
      SpreadsheetApp: { getActiveSpreadsheet: () => ({ getSheetByName: () => sheet, insertSheet: () => sheet }) },
      LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock: () => {} }) },
      CacheService: { getScriptCache: () => ({ get: (k) => cache.get(k) ?? null, put: (k, v) => cache.set(k, v) }) },
      Utilities: { formatDate: () => "2026-11-09 12:00:00" },
      Date: class extends Date { static now() { return 1794200000000; } },
    };
    vm.createContext(env);
    vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "..", "..", "tools", "perf_report_sheet.gs"), "utf8"), env);
    return { env, rows };
  };
  const gs = sheetScript();
  const gsPost = (body) => JSON.parse(gs.env.doPost({ postData: { contents: body } }).text);
  check(JSON.parse(gs.env.doGet().text).ok === true && gs.rows.length === 0, "sheet: GET answers ok and writes nothing");
  check(gsPost(JSON.stringify(f)).ok === true && gs.rows.length === 2, "sheet: a report adds the header and one row");
  const head = gs.rows[0];
  const col = (label, n = 1) => gs.rows[n][head.indexOf(label)];
  check(col("FPS with the mod") === 61.2 && col("FPS without") === 80.5 && col("Change with the mod (%)") === -24
        && col("GPU") === "NVIDIA GeForce RTX 5080" && col("VR") === "no" && col("Mod settings") === "C16 = 2 (0.5)"
        && col("Received (UTC)") === "2026-11-09 12:00:00" && col("Render scaling (%)") === 100,
        `sheet: the row under its headers (${JSON.stringify(gs.rows[1])})`);
  check(col("Other fields") === "", `sheet: every key the app sends has its column (${col("Other fields")})`);
  check(gsPost("nonsense").ok === false
        && gsPost('{"kind":"performance-report","version":"0.7.1","fpsOn":5000,"fpsOff":60,"impression":"too slow to use"}')
          .ok === false
        && gsPost(JSON.stringify({ ...f, impression: "great" })).ok === false
        && gsPost(JSON.stringify({ ...f, kind: "other" })).ok === false && gs.rows.length === 2,
        "sheet: what doesn't look like a report is turned away");
  gsPost(JSON.stringify({ ...f, comment: '=IMPORTXML("http://example.test")', contact: "x".repeat(5000), newKey: 1 }));
  check(col("Comment", 2) === `'=IMPORTXML("http://example.test")` && col("Contact", 2).length === 1000
        && col("Other fields", 2) === '{"newKey":1}', "sheet: formulas stay text, long text is cut, new keys are kept");
  let accepted = 0;
  for (let i = 0; i < 25; i++) {
    accepted += gsPost(JSON.stringify(f)).ok ? 1 : 0;
  }
  check(accepted === 18 && gs.rows.length === 21, `sheet: at most 20 reports a minute (${accepted} more taken)`);
  check(perf.weatherKind("KJFK 091051Z 18010KT 10SM FEW250 12/03 A3012") === "few or scattered clouds"
        && perf.weatherKind("EGLL 091050Z 24012KT 9999 -RA BKN012 OVC020 10/08 Q1008") === "broken or overcast"
        && perf.weatherKind("WMKK 090900Z 00000KT 8000 -TSRA FEW008 FEW017CB SCT028 27/25 Q1007") === "thunderstorms"
        && perf.weatherKind("LEMD 091030Z 03005KT CAVOK 14/02 Q1027 NOSIG") === "clear"
        && perf.weatherKind("EDDK 091050Z 09004KT 9999 FEW030 07/05 Q1019 TEMPO BKN012") === "few or scattered clouds",
        "report: the weather from the METAR (TEMPO doesn't count)");
  check(perf.station("METAR EDDK 091050Z 09004KT") === "EDDK" && perf.station("LIRF 090920Z") === "LIRF"
        && perf.station("") === "", "report: the station from the METAR");

  // the model: start, the add-on's phases, the review, sending (to the sheet's script above)
  let t = 0;
  const sent = [];
  const posted = [];
  let reachable = true;
  const sheet2 = sheetScript();
  const model = new perf.PerfReportModel((d) => sent.push(d), () => ctx, async (url, body) => {
    posted.push([url, body]);
    return reachable ? { status: 200, text: sheet2.env.doPost({ postData: { contents: body } }).text }
      : { status: 0, text: "" };
  }, () => t, "https://script.example.test/exec");
  model.show();
  model.start();
  check(model.step.get() === "measuring" && JSON.stringify(sent.pop()) === '{"measure":true}', "model: Start asks the add-on");
  model.onReport({ phase: "done", progress: 1, result, system });
  check(model.step.get() === "measuring", "model: an earlier result doesn't count before the new measurement started");
  model.onReport({ phase: "off", progress: 0.5 });
  check(model.progress.get() === 0.5 && model.phase.get() === "off", "model: the add-on's progress");
  model.onReport(done);
  check(model.step.get() === "review" && model.weather.get() === "broken or overcast" && model.summary.get().includes("-24 %"),
        `model: the result to review (${model.summary.get()})`);
  check(model.warning.get() === "" && model.details.get().includes("RTX 5080"), "model: no warning, the details shown");
  await model.send();
  check(model.step.get() === "review" && model.note.get() !== "" && posted.length === 0, "model: Send needs the impression");
  model.impression.set("too slow to use");
  model.vr.set(true);
  check(model.details.get().includes("in VR"), "model: the details follow the answers");
  await model.send();
  check(model.step.get() === "sent" && posted.length === 1 && posted[0][0] === "https://script.example.test/exec"
        && JSON.parse(posted[0][1]).impression === "too slow to use" && JSON.parse(posted[0][1]).vr === true
        && sheet2.rows.length === 2, "model: Send posts the report, and the sheet takes it");
  reachable = false;
  model.show();
  model.start();
  model.onReport({ phase: "on", progress: 0.2 });
  model.onReport({ ...done, result: { ...result, drift: 0.2 } });
  check(model.warning.get().includes("20 %"), "model: a changing view is pointed out");
  model.impression.set("noticeable but fine");
  await model.send();
  check(model.step.get() === "failed" && model.note.get().includes("no answer"), "model: a failed send says so");
  model.show();
  check(model.step.get() === "intro", "model: opened again after a send, it starts over");
  model.start();
  model.onReport({ phase: "settleOn", progress: 0.01 });
  model.onReport({ phase: "stopped", progress: 0 });
  check(model.step.get() === "intro" && model.note.get().startsWith("Stopped"), "model: stopped by the switch");
  model.start();
  model.close();
  check(!model.open.get() && model.step.get() === "intro" && JSON.stringify(sent.pop()) === '{"measure":false}',
        "model: closing during a measurement stops it");
  model.show();
  model.start();
  t += 7000;
  model.onReport(undefined);
  check(model.step.get() === "intro" && model.note.get().includes("install the mod again"),
        "model: an add-on without the report is pointed out");
  const noUrl = new perf.PerfReportModel(() => {}, () => ctx, async () => ({ status: 200, text: '{"ok":true}' }),
                                         () => 0, "");
  noUrl.show();
  noUrl.start();
  noUrl.onReport({ phase: "on", progress: 0.5 });
  noUrl.onReport(done);
  noUrl.impression.set("noticeable but fine");
  await noUrl.send();
  check(noUrl.step.get() === "failed" && noUrl.note.get().includes("no address"), "model: without an address it says so");
  }

  fs.rmSync(out, { force: true });
  fs.rmSync(outDefaults, { force: true });
  fs.rmSync(outNews, { force: true });
  fs.rmSync(outReport, { force: true });
  console.log(failures ? "FAILED" : "all checks passed");
  process.exit(failures ? 1 : 0);
})();
