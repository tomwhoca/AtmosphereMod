// Performance report: the add-on measures the mod's cost (it switches the mod
// on, off and on again and times the frames: addon/src/perf_report.h) and
// finds the PC and the sim's settings; this shows all of it, asks for the
// user's impression, and sends it to the mod's performance form on Jotform
// when the user taps Send. Nothing is sent before.
//
//   AtmosphereMod.Settings  app -> add-on  {measure: true} starts, {measure: false} stops
//   AtmosphereMod.State     add-on -> app  {..., report: {phase, progress, result?, system?}}

import { Switch, TabSelector, TextBox, TTButton } from "@efb/efb-api";
import {
  ComponentProps, DisplayComponent, FSComponent, Subject, Subscribable, Subscription, VNode,
} from "@microsoft/msfs-sdk";

/** The form (the same one people fill in on the web). */
export const FORM_ID = "263122374395054";
export const FORM_URL = `https://form.jotform.com/${FORM_ID}`;
export const SUBMIT_URL = `https://eu-submit.jotform.com/submit/${FORM_ID}`;

/** What the add-on sends (perf_report.cpp state_json and system_json). */
export interface AddonReport {
  phase: string;
  progress: number;
  result?: {
    fpsOn: number; fpsOff: number; msOn: number; msOff: number; p99On: number; p99Off: number;
    fpsOn1: number; fpsOn2: number; drift: number; framesOn: number; framesOff: number;
  };
  system?: {
    pc: { gpu: string; gpuVendor: string; vramGB: number; driver: string; cpu: string; threads: number; ramGB: number;
      windows: string };
    sim: { store: string; output: string; cfg: Record<string, string>; packages: string[] };
    reshade: { loader: string; version: string; effectsOn: number; addons: string[]; registerSpace: number };
  };
}

/** What the app knows itself when the report is made. */
export interface Context {
  version: string;
  /** The METAR of the airport nearest the aircraft. */
  metar: string;
  /** The add-on's weather line (the METAR's haze). */
  weather: string;
  altitudeFt: number;
  aglFt: number;
  /** The settings that differ from the shipping defaults: "C16 = 2 (0.5)". */
  changed: string[];
  /** The settings are exactly the Default preset. */
  defaultPreset: boolean;
}

export const WEATHER = ["clear", "few or scattered clouds", "broken or overcast", "thunderstorms"];
export const IMPRESSION = ["no noticeable difference", "noticeable but fine", "too slow to use"];
export const STUTTERS = ["none", "occasional", "frequent"];
export const LIMITED_BY = ["GPU", "main thread (CPU)", "don't know"];

/** The user's part. */
export interface Answers {
  vr: boolean;
  weather: string;
  impression: string;
  stutters: string;
  limitedBy: string;
  comment: string;
  contact: string;
}

/** The weather in the view as the METAR has it: thunderstorms, the most cloud reported, or clear. */
export function weatherKind(metar: string): string {
  const body = metar.replace(/\s(RMK|TEMPO|BECMG|NOSIG)\b.*$/, "");
  if (/(^|\s)[+-]?(VC)?TS[A-Z]*(\s|$)/.test(body) || /\d{3}CB\b/.test(body)) {
    return WEATHER[3];
  }
  if (/(^|\s)(BKN|OVC|VV)\d{3}/.test(body)) {
    return WEATHER[2];
  }
  if (/(^|\s)(FEW|SCT)\d{3}/.test(body)) {
    return WEATHER[1];
  }
  return WEATHER[0];
}

/** The station's code in a METAR ("" if none). */
export function station(metar: string): string {
  const m = /^(?:METAR\s+|SPECI\s+)?([A-Z][A-Z0-9]{3})\s/.exec(metar.trim() + " ");
  return m ? m[1] : "";
}

const CLOUD_QUALITY = ["Low", "Medium", "High", "Ultra"];

/** The sim's settings that apply: the VR ones in VR. */
function simSettings(report: AddonReport, vr: boolean): {
  aa: string; dlss: string; fsr: string; frameGen: string; frames: string; scaling: string; preset: string;
  clouds: string; rtShadows: string;
} {
  const cfg = report.system?.sim.cfg ?? {};
  const v = (key: string): string => cfg[key] ?? "";
  const vrKey = (key: string): string => (vr && cfg[`${key}VR`] !== undefined ? v(`${key}VR`) : v(key));
  const graphics = vr ? "GraphicsVR" : "Graphics";
  return {
    aa: vrKey("Video.AntiAliasing"),
    dlss: vrKey("Video.DLSSMode"),
    fsr: vrKey("Video.FSRMode"),
    frameGen: vrKey("Video.FrameGeneration"),
    frames: vr ? "" : v("Video.NBFramesToGenerate"),
    scaling: vrKey("Video.PrimaryScaling"),
    preset: v(`${graphics}.Preset`),
    clouds: v(`${graphics}.VolumetricClouds.Quality`),
    rtShadows: v(`${graphics}.RaytracedShadows.Enabled`),
  };
}

const round1 = (x: number): string => (Math.round(x * 10) / 10).toString();

/** The form's fields, by the names Jotform gave them. */
export function formFields(report: AddonReport, ctx: Context, answers: Answers): Record<string, string | string[]> {
  const pc = report.system?.pc;
  const sim = report.system?.sim;
  const reshade = report.system?.reshade;
  const result = report.result;
  const s = simSettings(report, answers.vr);
  const f: Record<string, string | string[]> = {
    formID: FORM_ID,
    simple_spc: `${FORM_ID}-${FORM_ID}`,
    q4_q4_textbox2: pc?.cpu ?? "",
    q5_q5_textbox3: pc?.gpu ?? "",
    q15_gpuMemory: pc ? String(Math.round(pc.vramGB)) : "",
    q16_gpuDriver: pc?.driver ?? "",
    q6_q6_number4: pc ? String(pc.ramGB) : "",
    q19_atmospheremodVersion: ctx.version,
    q20_reshade: reshade?.loader.toLowerCase() === "reshade64.dll" ? "OptiShade" : "ReShade with full add-on support",
    q24_renderScaling: s.scaling !== "" ? String(Math.round(parseFloat(s.scaling) * 100)) : "",
    q13_q13_textbox11: [
      s.preset !== "" ? `preset ${s.preset}` : "",
      s.aa !== "" ? `${s.aa}${/DLSS/i.test(s.aa) && s.dlss ? ` ${s.dlss}` : /FSR/i.test(s.aa) && s.fsr ? ` ${s.fsr}` : ""}` : "",
      s.frameGen !== "" && !/^(NONE|OFF|0)$/i.test(s.frameGen) ? `frame generation ${s.frameGen}${s.frames ? ` x${s.frames}` : ""}` : "",
      sim?.cfg["Video.VSync"] === "1" ? "V-Sync on" : "",
    ].filter((x) => x !== "").join(", "),
    q29_atmospheremodSettings: ctx.defaultPreset ? "Default preset" : "my own settings",
    q30_weatherAnd: answers.weather,
    q28_whereAnd: [station(ctx.metar), `${Math.round(ctx.altitudeFt)} ft`, `${Math.round(ctx.aglFt)} ft above the ground`]
      .filter((x) => x !== "").join(", "),
    q9_q9_number7: result ? round1(result.fpsOn) : "",
    q10_q10_number8: result ? round1(result.fpsOff) : "",
    q34_overallHow: answers.impression,
    q35_anythingElse: answers.comment,
    q36_githubOr: answers.contact,
    q37_detailsFrom: detailsText(report, ctx, answers),
    q38_sentFrom: `AtmosphereMod ${ctx.version} EFB app`,
  };
  if (pc?.windows.startsWith("Windows 1")) {
    f.q17_windowsVersion = pc.windows.startsWith("Windows 11") ? "Windows 11" : "Windows 10";
  }
  if (sim?.store === "Steam") {
    f.q18_msfs2024 = "Steam";
  } else if (sim?.store === "Microsoft Store") {
    f.q18_msfs2024 = "Microsoft Store / Xbox app";
  }
  const others: string[] = [];
  const addons = reshade?.addons ?? [];
  if ((reshade?.effectsOn ?? 0) > 0) {
    others.push("other ReShade effects or presets");
  }
  if (addons.some((a) => /renodx/i.test(a))) {
    others.push("RenoDX");
  }
  if ((sim?.packages ?? []).some((p) => p.includes("tritri"))) {
    others.push("TriTriSim GFX");
  }
  if ((sim?.packages ?? []).some((p) => p.includes("cloudsurf"))) {
    others.push("CloudSurf");
  }
  const otherAddons = addons.filter((a) => !/renodx/i.test(a));
  if (otherAddons.length > 0) {
    others.push("other");
    f["q21_otherGraphics[other]"] = `ReShade add-ons: ${otherAddons.join(", ")}`;
  }
  f["q21_otherGraphics[]"] = others.length > 0 ? others : ["none"];
  const display: Record<string, string> = {
    "1920x1080": "1920x1080", "2560x1440": "2560x1440", "3440x1440": "3440x1440 ultrawide", "3840x2160": "3840x2160 (4K)",
  };
  if (answers.vr) {
    f.q22_displayResolution = "VR headset";
  } else if (sim && display[sim.output] !== undefined) {
    f.q22_displayResolution = display[sim.output];
  } else if (sim) {
    f.q22_displayResolution = "other";
    f["q22_displayResolution[other]"] = sim.output;
  }
  const aa = s.aa.toUpperCase();
  const aaChoice = aa === "DLSS" && s.dlss.toUpperCase() === "DLAA" ? "DLAA" : aa.startsWith("DLSS") ? "DLSS"
    : aa === "DLAA" ? "DLAA" : aa.startsWith("FSR") ? "FSR" : aa.startsWith("XESS") ? "XeSS" : aa === "TAA" ? "TAA" : "";
  if (aaChoice !== "") {
    f.q23_antialiasing = aaChoice;
  } else if (s.aa !== "") {
    f.q23_antialiasing = "other";
    f["q23_antialiasing[other]"] = s.aa;
  }
  if (s.frameGen !== "") {
    f.q25_frameGeneration = /^(NONE|OFF|0)$/i.test(s.frameGen) ? "off" : "on";
  }
  const quality = parseInt(s.clouds, 10);
  f.q26_cloudQuality = quality >= 0 && quality < CLOUD_QUALITY.length ? CLOUD_QUALITY[quality] : "don't know";
  f.q27_raytracedShadows = s.rtShadows === "1" ? "on" : s.rtShadows === "0" ? "off" : "don't know";
  if (answers.stutters !== "") {
    f.q33_stuttersWith = answers.stutters;
  }
  if (answers.limitedBy !== "") {
    f.q32_theSims = answers.limitedBy;
  }
  return f;
}

/** Everything the add-on found, in lines, for the form's hidden "Details from the mod" field (and the
 * app's "what will be sent"). */
export function detailsText(report: AddonReport, ctx: Context, answers: Answers): string {
  const r = report.result;
  const pc = report.system?.pc;
  const sim = report.system?.sim;
  const reshade = report.system?.reshade;
  const lines = [`AtmosphereMod ${ctx.version}, performance report from the EFB app`];
  if (r) {
    lines.push(`Measured: ${round1(r.fpsOn)} fps with the mod (${round1(r.fpsOn1)} and ${round1(r.fpsOn2)}), `
      + `${round1(r.fpsOff)} without; 99 % of frames under ${round1(r.p99On)} ms with the mod, ${round1(r.p99Off)} ms `
      + `without; ${r.framesOn} + ${r.framesOff} frames; the two parts with the mod differ by ${Math.round(r.drift * 100)} %`);
  }
  if (pc) {
    lines.push(`GPU: ${pc.gpu} (${pc.gpuVendor}), ${round1(pc.vramGB)} GB, driver ${pc.driver}`);
    lines.push(`CPU: ${pc.cpu}, ${pc.threads} threads; ${pc.ramGB} GB RAM; ${pc.windows}`);
  }
  if (sim) {
    lines.push(`Sim: ${sim.store || "unknown"}, picture ${sim.output}${answers.vr ? ", in VR" : ""}`);
    lines.push(`Sim settings: ${Object.entries(sim.cfg).map(([k, v]) => `${k.replace(/^Video\./, "")} ${v}`).join("; ")}`);
    if (sim.packages.length > 0) {
      lines.push(`Community: ${sim.packages.join(", ")}`);
    }
  }
  if (reshade) {
    lines.push(`ReShade: ${reshade.loader} ${reshade.version}, ${reshade.effectsOn} effect(s) on, add-ons: `
      + `${reshade.addons.length > 0 ? reshade.addons.join(", ") : "none"}, register space ${reshade.registerSpace}`);
  }
  lines.push(`Mod settings: ${ctx.defaultPreset ? "the Default preset" : ctx.changed.length > 0
    ? `changed from the defaults: ${ctx.changed.join(", ")}` : "the defaults"}`);
  lines.push(`Weather: ${ctx.weather || "no METAR yet"}${ctx.metar ? `; METAR ${ctx.metar}` : ""}`);
  lines.push(`Aircraft: ${Math.round(ctx.altitudeFt)} ft, ${Math.round(ctx.aglFt)} ft above the ground`);
  return lines.join("\n");
}

/** The fields as a form posts them (application/x-www-form-urlencoded). */
export function encodeForm(fields: Record<string, string | string[]>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(fields)) {
    for (const v of Array.isArray(value) ? value : [value]) {
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(v).replace(/%20/g, "+")}`);
    }
  }
  return parts.join("&");
}

/** Posts the form; resolves to the HTTP status (0: no answer, or the sim's browser refused). */
export function postForm(url: string, body: string): Promise<number> {
  return new Promise((resolve) => {
    try {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url, true);
      xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
      xhr.timeout = 20000;
      xhr.onload = () => resolve(xhr.status);
      xhr.onerror = () => resolve(0);
      xhr.ontimeout = () => resolve(0);
      xhr.send(body);
    } catch {
      resolve(0);
    }
  });
}

export type Step = "intro" | "measuring" | "review" | "sending" | "sent" | "failed";

const RUNNING = ["settleOn", "on", "settleOff", "off", "settleOn2", "on2"];
/** No sign of a measurement this long after Start: the add-on is older than the app. */
const START_WAIT_MS = 6000;

export class PerfReportModel {
  public readonly open = Subject.create(false);
  public readonly step = Subject.create<Step>("intro");
  public readonly progress = Subject.create(0);
  public readonly phase = Subject.create("");
  public readonly note = Subject.create("");
  /** The result in a line or two, and what will be sent, for the review. */
  public readonly summary = Subject.create("");
  public readonly warning = Subject.create("");
  public readonly details = Subject.create("");
  public readonly vr = Subject.create(false);
  public readonly weather = Subject.create(WEATHER[0]);
  public readonly impression = Subject.create("");
  public readonly stutters = Subject.create("");
  public readonly limitedBy = Subject.create("");
  public readonly comment = Subject.create("");
  public readonly contact = Subject.create("");
  private report?: AddonReport;
  private context?: Context;
  private startedAt = 0;
  private sawRunning = false;

  public constructor(
    private readonly sendToAddon: (data: unknown) => void,
    private readonly getContext: () => Context,
    private readonly post: (url: string, body: string) => Promise<number> = postForm,
    private readonly now: () => number = () => Date.now(),
  ) {
    this.vr.sub(() => this.refresh());
    this.weather.sub(() => this.refresh());
  }

  public show(): void {
    if (this.step.get() === "sent" || this.step.get() === "failed") {
      this.step.set("intro");
    }
    this.open.set(true);
  }

  public close(): void {
    if (this.step.get() === "measuring") {
      this.stop();
    }
    this.open.set(false);
  }

  public start(): void {
    this.note.set("");
    this.startedAt = this.now();
    this.sawRunning = false;
    this.progress.set(0);
    this.phase.set("");
    this.step.set("measuring");
    this.sendToAddon({ measure: true });
  }

  public stop(): void {
    this.sendToAddon({ measure: false });
    this.step.set("intro");
  }

  /** Each state from the add-on (with or without a report). */
  public onReport(report: AddonReport | undefined): void {
    if (this.step.get() !== "measuring") {
      return;
    }
    if (report !== undefined && RUNNING.includes(report.phase)) {
      this.sawRunning = true;
      this.progress.set(report.progress);
      this.phase.set(report.phase);
      return;
    }
    if (this.sawRunning && report?.phase === "done" && report.result !== undefined) {
      this.report = report;
      this.context = this.getContext();
      this.weather.set(weatherKind(this.context.metar));
      this.impression.set("");
      this.refresh();
      this.step.set("review");
      return;
    }
    if (this.sawRunning && report?.phase === "stopped") {
      this.note.set("Stopped: the mod was switched during the measurement. Start again when you're ready.");
      this.step.set("intro");
      return;
    }
    if (!this.sawRunning && this.now() - this.startedAt > START_WAIT_MS) {
      this.note.set("The add-on in the sim doesn't have the performance report yet: install the mod again.");
      this.step.set("intro");
    }
  }

  private answers(): Answers {
    return {
      vr: this.vr.get(), weather: this.weather.get(), impression: this.impression.get(), stutters: this.stutters.get(),
      limitedBy: this.limitedBy.get(), comment: this.comment.get(), contact: this.contact.get(),
    };
  }

  private refresh(): void {
    const report = this.report;
    const ctx = this.context;
    if (report?.result === undefined || ctx === undefined) {
      return;
    }
    const r = report.result;
    const change = r.fpsOff > 0 ? Math.round((r.fpsOn / r.fpsOff - 1) * 100) : 0;
    this.summary.set(`With the mod: ${round1(r.fpsOn)} fps. Without: ${round1(r.fpsOff)} fps`
      + ` (${change > 0 ? "+" : ""}${change} % with the mod).`
      + ` 99 % of frames take under ${round1(r.p99On)} ms with the mod, ${round1(r.p99Off)} ms without.`);
    this.warning.set(r.drift > 0.1 ? `The view changed while measuring: the two parts with the mod differ by `
      + `${Math.round(r.drift * 100)} %. Measure again with the sim paused, if you can.` : "");
    this.details.set(detailsText(report, ctx, this.answers()));
  }

  public async send(): Promise<void> {
    if (this.report === undefined || this.context === undefined) {
      return;
    }
    if (this.impression.get() === "") {
      this.note.set("Choose how the mod runs for you first.");
      return;
    }
    this.note.set("");
    this.step.set("sending");
    const body = encodeForm(formFields(this.report, this.context, this.answers()));
    const status = await this.post(SUBMIT_URL, body);
    if (status >= 200 && status < 400) {
      this.step.set("sent");
    } else {
      this.note.set(status === 0 ? "The sim's browser couldn't reach the form (no answer)."
        : `The form answered with an error (HTTP ${status}).`);
      this.step.set("failed");
    }
  }
}

const PHASE_TEXT: Record<string, string> = {
  settleOn: "The mod on: settling (1 of 3)",
  on: "The mod on: measuring (1 of 3)",
  settleOff: "The mod off: settling (2 of 3)",
  off: "The mod off: measuring (2 of 3)",
  settleOn2: "The mod on again: settling (3 of 3)",
  on2: "The mod on again: measuring (3 of 3)",
};

interface ChoiceProps extends ComponentProps {
  options: readonly string[];
  selected: Subject<string>;
}

/** One of a few options, as a row of tabs. */
class Choice extends DisplayComponent<ChoiceProps> {
  public render(): VNode {
    const { options, selected } = this.props;
    return (
      <div class="atmo-choice">
        {options.map((o) => (
          <TabSelector tabName={o} active={selected.map((s) => s === o)} hidden={Subject.create(false)}
                       callback={() => selected.set(o)} />
        ))}
      </div>
    );
  }
}

interface PerfReportViewProps extends ComponentProps {
  model: PerfReportModel;
}

/** Over the app's page, like What's new (WhatsNew.tsx). */
export class PerfReportView extends DisplayComponent<PerfReportViewProps> {
  private readonly bar = FSComponent.createRef<HTMLDivElement>();
  private readonly subs: Subscription[] = [];

  public onAfterRender(node: VNode): void {
    super.onAfterRender(node);
    this.subs.push(this.props.model.progress.sub((p) => {
      const bar = this.bar.getOrDefault();
      if (bar !== null) {
        bar.style.width = `${Math.round(p * 100)}%`;
      }
    }, true));
  }

  public render(): VNode {
    const { model } = this.props;
    const at = (...steps: Step[]): Subscribable<boolean> => model.step.map((s) => !steps.includes(s));
    const hasNote = model.note.map((n) => n === "");
    return (
      <div class={{ "atmo-news": true, "atmo-report": true, "atmo-hidden": model.open.map((o) => !o) }}>
        <div class="atmo-news-card">
          <div class="atmo-news-title">Performance report</div>
          <div class={{ "atmo-hidden": at("intro") }}>
            <div class="atmo-news-text">
              This measures what AtmosphereMod costs on your PC and sends me the result, with your PC's and the
              sim's settings, so I can see where the mod runs well and where it needs work.
            </div>
            <div class="atmo-news-item">
              <div class="atmo-news-text">1. Find a view with clouds (most of the mod's cost is in them) and hold it
                still: pause the sim or slew. If you can, switch off V-Sync and frame-rate limits for now.</div>
              <div class="atmo-news-text">2. Tap Start. For about 36 seconds the mod switches itself on, off and on
                again while the frames are timed. Don't touch anything meanwhile; you can close the tablet.</div>
              <div class="atmo-news-text">3. Check what will be sent, add how the mod runs for you, and tap Send.
                Nothing is sent before.</div>
            </div>
            <div class="atmo-buttons">
              <TTButton key="Start" type="secondary" callback={() => model.start()} />
              <TTButton key="Close" type="secondary" callback={() => model.close()} />
            </div>
          </div>
          <div class={{ "atmo-hidden": at("measuring") }}>
            <div class="atmo-news-text">{model.phase.map((p) => PHASE_TEXT[p] ?? "Starting")}</div>
            <div class="atmo-progress"><div ref={this.bar} class="atmo-progress-bar" /></div>
            <div class="atmo-buttons">
              <TTButton key="Stop" type="secondary" callback={() => model.stop()} />
            </div>
          </div>
          <div class={{ "atmo-hidden": at("review", "sending") }}>
            <div class="atmo-news-item">
              <div class="atmo-news-head">{model.summary}</div>
              <div class={{ "atmo-news-text": true, "atmo-unsaved": true, "atmo-hidden": model.warning.map((w) => w === "") }}>
                {model.warning}
              </div>
            </div>
            <div class="atmo-news-text">The weather in the view (from the METAR: change it if the view differs)</div>
            <Choice options={WEATHER} selected={model.weather} />
            <div class="atmo-row">
              <div class="atmo-label">Flying in VR</div>
              <div class="atmo-control atmo-switch"><Switch checked={model.vr} callback={(on) => model.vr.set(on)} /></div>
            </div>
            <div class="atmo-news-text">How does the mod run for you? (needed)</div>
            <Choice options={IMPRESSION} selected={model.impression} />
            <div class="atmo-news-text">Stutters with the mod (optional)</div>
            <Choice options={STUTTERS} selected={model.stutters} />
            <div class="atmo-news-text">The sim's FPS counter says it's limited by (optional)</div>
            <Choice options={LIMITED_BY} selected={model.limitedBy} />
            <div class="atmo-report-text">
              <TextBox model={model.comment} placeholder="Anything else (optional)" showDeleteIcon={true} />
            </div>
            <div class="atmo-report-text">
              <TextBox model={model.contact} placeholder="Your GitHub or flightsim.to name, for questions (optional)"
                       showDeleteIcon={true} />
            </div>
            <div class="atmo-news-text">What will be sent, besides your answers above:</div>
            <div class="atmo-report-details">{model.details}</div>
            <div class="atmo-news-text atmo-report-privacy">
              It goes to the mod's performance form on Jotform, which also gets your IP address, as any website does.
              No names or file paths are in it.
            </div>
            <div class={{ "atmo-buttons": true, "atmo-hidden": at("review") }}>
              <TTButton key="Send" type="secondary" callback={() => void model.send()} />
              <TTButton key="Measure again" type="secondary" callback={() => model.start()} />
              <TTButton key="Close" type="secondary" callback={() => model.close()} />
            </div>
            <div class={{ "atmo-news-text": true, "atmo-hidden": at("sending") }}>Sending...</div>
          </div>
          <div class={{ "atmo-hidden": at("sent") }}>
            <div class="atmo-news-text">Sent. Thank you!</div>
            <div class="atmo-buttons">
              <TTButton key="Close" type="secondary" callback={() => model.close()} />
            </div>
          </div>
          <div class={{ "atmo-hidden": at("failed") }}>
            <div class="atmo-news-text">It couldn't be sent from the sim. You can fill in the same form on the web
              instead: {FORM_URL}</div>
            <div class="atmo-buttons">
              <TTButton key="Try again" type="secondary" callback={() => void model.send()} />
              <TTButton key="Close" type="secondary" callback={() => model.close()} />
            </div>
          </div>
          <div class={{ "atmo-note": true, "atmo-hidden": hasNote }}>{model.note}</div>
        </div>
      </div>
    );
  }

  public destroy(): void {
    this.subs.forEach((s) => s.destroy());
    super.destroy();
  }
}

export const PERF_REPORT_CSS = `
.atmo-report { z-index: 31; }
.atmo-progress { height: 14px; margin: 16px 0; border-radius: 7px; background-color: rgba(255, 255, 255, 0.15); }
.atmo-progress-bar { width: 0; height: 100%; border-radius: 7px; background-color: rgb(29, 137, 230); }
.atmo-choice { display: flex; flex-wrap: wrap; margin: 4px 0 14px; }
.atmo-report-text { margin: 8px 0; }
.atmo-report-text > .textbox { width: 100%; }
.atmo-report-details { font-family: monospace; font-size: 19px; white-space: pre-wrap; opacity: 0.75; margin: 6px 0 12px;
  overflow-wrap: anywhere; }
.atmo-report-privacy { font-size: 21px; opacity: 0.7; }
`;
