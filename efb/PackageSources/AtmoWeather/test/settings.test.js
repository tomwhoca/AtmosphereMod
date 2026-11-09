// Checks src/Settings.tsx without the sim: bundles it for Node with stand-ins
// for the MSFS and EFB SDKs (test/stubs), then tests the friendly units against
// the shipping defaults and the model's messages. Run by tools/test_efb_settings.py.

const esbuild = require("esbuild");
const path = require("path");
const os = require("os");
const fs = require("fs");

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
  // performance report: the add-on's report (as perf_report_test printed it on the developer's PC) to the form
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
  const f = perf.formFields(done, ctx, answers);
  check(f.q5_q5_textbox3 === "NVIDIA GeForce RTX 5080" && f.q15_gpuMemory === "16" && f.q16_gpuDriver === "621.14",
        "report: the GPU, its memory and driver");
  check(f.q4_q4_textbox2.startsWith("AMD Ryzen 9") && f.q6_q6_number4 === "64" && f.q17_windowsVersion === "Windows 11",
        "report: the CPU, RAM and Windows");
  check(f.q18_msfs2024 === "Microsoft Store / Xbox app" && f.q19_atmospheremodVersion === "0.7.1"
        && f.q20_reshade === "ReShade with full add-on support", "report: the sim's store, the mod's version, ReShade");
  check(JSON.stringify(f["q21_otherGraphics[]"]) === JSON.stringify(["other ReShade effects or presets", "RenoDX",
        "TriTriSim GFX", "other"]) && f["q21_otherGraphics[other]"] === "ReShade add-ons: other.addon64",
        "report: other graphics mods, from the effects, add-ons and Community packages");
  check(f.q22_displayResolution === "3840x2160 (4K)" && f.q23_antialiasing === "DLSS" && f.q24_renderScaling === "100"
        && f.q25_frameGeneration === "on" && f.q26_cloudQuality === "Ultra" && f.q27_raytracedShadows === "on",
        "report: the sim's settings");
  check(f.q13_q13_textbox11 === "preset Custom, DLSS AUTO, frame generation DLSSG x2",
        `report: notable settings (${f.q13_q13_textbox11})`);
  check(f.q9_q9_number7 === "61.2" && f.q10_q10_number8 === "80.5" && f.q34_overallHow === "noticeable but fine"
        && f.q32_theSims === "GPU" && f.q33_stuttersWith === undefined, "report: the measurement and the answers");
  check(f.q28_whereAnd === "LIRF, 5012 ft, 4890 ft above the ground" && f.q29_atmospheremodSettings === "my own settings",
        "report: where, and the mod's settings");
  check(f.formID === perf.FORM_ID && f.simple_spc === `${perf.FORM_ID}-${perf.FORM_ID}` && f.website === undefined,
        "report: the form's id and spam check, the trap field left out");
  check(f.q37_detailsFrom.includes("61.2 fps with the mod") && f.q37_detailsFrom.includes("C16 = 2 (0.5)")
        && f.q37_detailsFrom.includes("register space 51") && !/[A-Z]:\\|Users/.test(f.q37_detailsFrom),
        "report: the details have the measurement and settings, and no paths");
  const vr = perf.formFields(done, ctx, { ...answers, vr: true });
  check(vr.q22_displayResolution === "VR headset" && vr.q23_antialiasing === "TAA" && vr.q25_frameGeneration === "off"
        && vr.q26_cloudQuality === "Medium" && vr.q27_raytracedShadows === "off" && vr.q24_renderScaling === "80",
        "report: in VR, the sim's VR settings");
  const odd = perf.formFields({ ...done, system: { ...system, sim: { ...system.sim, output: "1132x598",
    cfg: { "Video.AntiAliasing": "DLSS", "Video.DLSSMode": "DLAA" } }, reshade: { ...system.reshade, loader: "ReShade64.dll",
    effectsOn: 0, addons: [] } } }, ctx, answers);
  check(odd.q22_displayResolution === "other" && odd["q22_displayResolution[other]"] === "1132x598"
        && odd.q23_antialiasing === "DLAA" && odd.q20_reshade === "OptiShade" && odd.q26_cloudQuality === "don't know"
        && JSON.stringify(odd["q21_otherGraphics[]"]) === JSON.stringify(["TriTriSim GFX"]),
        "report: an odd window size, DLAA, OptiShade, no cloud setting");
  const enc = perf.encodeForm({ a: "x y&z", "b[]": ["1", "2"] });
  check(enc === "a=x+y%26z&b%5B%5D=1&b%5B%5D=2", `report: the form's encoding (${enc})`);
  check(perf.weatherKind("KJFK 091051Z 18010KT 10SM FEW250 12/03 A3012") === "few or scattered clouds"
        && perf.weatherKind("EGLL 091050Z 24012KT 9999 -RA BKN012 OVC020 10/08 Q1008") === "broken or overcast"
        && perf.weatherKind("WMKK 090900Z 00000KT 8000 -TSRA FEW008 FEW017CB SCT028 27/25 Q1007") === "thunderstorms"
        && perf.weatherKind("LEMD 091030Z 03005KT CAVOK 14/02 Q1027 NOSIG") === "clear"
        && perf.weatherKind("EDDK 091050Z 09004KT 9999 FEW030 07/05 Q1019 TEMPO BKN012") === "few or scattered clouds",
        "report: the weather from the METAR (TEMPO doesn't count)");
  check(perf.station("METAR EDDK 091050Z 09004KT") === "EDDK" && perf.station("LIRF 090920Z") === "LIRF"
        && perf.station("") === "", "report: the station from the METAR");

  // the model: start, the add-on's phases, the review, sending
  let t = 0;
  const sent = [];
  const posted = [];
  let status = 200;
  const model = new perf.PerfReportModel((d) => sent.push(d), () => ctx, async (url, body) => {
    posted.push([url, body]);
    return status;
  }, () => t);
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
  check(model.step.get() === "sent" && posted.length === 1 && posted[0][0] === perf.SUBMIT_URL
        && posted[0][1].includes("q34_overallHow=too+slow+to+use") && posted[0][1].includes("q22_displayResolution=VR+headset"),
        "model: Send posts the form");
  status = 0;
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
  }

  fs.rmSync(out, { force: true });
  fs.rmSync(outDefaults, { force: true });
  fs.rmSync(outNews, { force: true });
  fs.rmSync(outReport, { force: true });
  console.log(failures ? "FAILED" : "all checks passed");
  process.exit(failures ? 1 : 0);
})();
