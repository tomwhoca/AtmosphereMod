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
global.window = { setTimeout, clearTimeout };
const { TABS, SettingsModel, PRESET_DEFAULT, presetChar, presetName, sameName, missingText } = require(out);
const { DEFAULTS } = require(outDefaults);

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

  fs.rmSync(out, { force: true });
  fs.rmSync(outDefaults, { force: true });
  console.log(failures ? "FAILED" : "all checks passed");
  process.exit(failures ? 1 : 0);
})();
