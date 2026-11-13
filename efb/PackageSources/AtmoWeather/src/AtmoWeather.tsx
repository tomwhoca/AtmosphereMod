// AtmosphereMod's EFB app: sends the sim's own METARs to SimConnect clients
// over CommBus, and shows the mod's main settings (Settings.tsx).
//
// MSFS 2024 serves METARs for its live / historical weather to JS only
// (Coherent GET_METAR_BY_LATLON / GET_METAR_BY_IDENT, wrapped by the SDK's
// FacilityLoader). Every aircraft has an EFB, and with BootMode HOT the app
// runs from the start of the flight without being opened.
//
// CommBus events (shared with tools/metarprobe):
//   AtmosphereMod.Metar    app -> SimConnect  {time, lat, lon, ident, metar | error}
//   AtmosphereMod.Status   app -> SimConnect  {state, detail}
//   AtmosphereMod.Request  SimConnect -> app  {} (aircraft position), {ident} or {lat, lon}
//   AtmosphereMod.Settings app -> add-on      {get}, {set: {T3: 4.5, ...}}, {enabled}, {reload}, {savePreset},
//                                             {loadPreset, discard}, {deletePreset}, {measure} (PerfReport.tsx)
//   AtmosphereMod.State    add-on -> app      {enabled, T, C, TE, weather, version, presets, preset, presetSaved,
//                                             presetNote, report} (addon/src/settings_bridge.h)

/// <reference types="@microsoft/msfs-types/js/simvar" />

import {
  App,
  AppBootMode,
  AppInstallProps,
  AppSuspendMode,
  AppView,
  AppViewProps,
  Efb,
  RequiredProps,
  TTButton,
  TVNode,
} from "@efb/efb-api";
import {
  EventBus,
  FacilityLoader,
  FacilityRepository,
  FSComponent,
  MappedSubject,
  Metar,
  Subject,
  VNode,
} from "@microsoft/msfs-sdk";

import { AddonReport, PERF_REPORT_CSS, PerfReportModel, PerfReportView } from "./PerfReport";
import { EVENT_SETTINGS, EVENT_STATE, PRESET_DEFAULT, SETTINGS_CSS, SettingsModel, SettingsView } from "./Settings";
import { HOTKEYS_CSS } from "./Hotkeys";
import { WHATS_NEW_CSS, WhatsNewModel, WhatsNewView } from "./WhatsNew";

declare const BASE_URL: string;
/** The mod's version (build.js, from addon/CMakeLists.txt). */
declare const VERSION: string;

/** The SDK's /JS/Services/CommBus.js. */
interface CommBusListener {
  on(name: string, callback: (args: string) => void): void;
  callSimConnect(name: string, jsonBuf: string): Promise<unknown>;
}
type RegisterCommBus = (callback?: () => void) => CommBusListener;

const EVENT_METAR = "AtmosphereMod.Metar";
const EVENT_STATUS = "AtmosphereMod.Status";
const EVENT_REQUEST = "AtmosphereMod.Request";
const POLL_MS = 60_000;
/** Until the add-on answers, the settings ask for its state this often. */
const STATE_RETRY_MS = 3000;
/** Narrower than this (px), the settings take the narrow layout (.atmo-narrow): a slider and its
 * value need the room a name at 40% of the row leaves them from about here on. */
const NARROW_PX = 800;
/** After the tablet's size or orientation changes, the app's width is measured again this late,
 * once the EFB has laid it out. */
const RELAYOUT_MS = 250;

interface MetarRequest {
  ident?: string;
  lat?: number;
  lon?: number;
}

function parseRequest(args: string): MetarRequest {
  try {
    return (JSON.parse(args) as MetarRequest) ?? {};
  } catch {
    return {};
  }
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`cannot load ${src}`));
    document.head.appendChild(script);
  });
}

/** Fetches the sim's METAR near the aircraft and sends it to SimConnect clients. */
class WeatherBridge {
  public readonly status = Subject.create("starting");
  /** The METAR of the airport nearest the aircraft ("" before the first). */
  public readonly airport = Subject.create("");
  private commBus?: CommBusListener;
  private loader?: FacilityLoader;
  private started = false;
  private readonly listeners: [string, (args: string) => void][] = [];
  private readonly onRegistered: (() => void)[] = [];

  public start(bus: EventBus): void {
    if (this.started) {
      return;
    }
    this.started = true;
    this.loader = new FacilityLoader(FacilityRepository.getRepository(bus));
    this.connect().catch((e) => this.status.set(`CommBus unavailable: ${e}`));
  }

  private async connect(): Promise<void> {
    let register = (window as any).RegisterCommBusListener as RegisterCommBus | undefined;
    if (typeof register !== "function") {
      await loadScript("/JS/Services/CommBus.js");
      register = (window as any).RegisterCommBusListener as RegisterCommBus | undefined;
    }
    if (typeof register !== "function") {
      throw new Error("RegisterCommBusListener is not defined");
    }
    this.commBus = register(() => {
      this.send(EVENT_STATUS, { state: "registered" });
      this.onRegistered.forEach((f) => f());
    });
    this.commBus.on(EVENT_REQUEST, (args) => void this.poll(parseRequest(args)));
    for (const [event, callback] of this.listeners) {
      this.commBus.on(event, callback);
    }
    setInterval(() => void this.poll({}), POLL_MS);
    setTimeout(() => void this.poll({}), 3000);
    this.status.set("running");
  }

  private async poll(request: MetarRequest): Promise<void> {
    // (no place and no station: the aircraft's, its nearest airport)
    const atAircraft = request.lat === undefined && request.lon === undefined && !request.ident;
    const lat = request.lat ?? (SimVar.GetSimVarValue("PLANE LATITUDE", "degree latitude") as number);
    const lon = request.lon ?? (SimVar.GetSimVarValue("PLANE LONGITUDE", "degree longitude") as number);
    const message: Record<string, unknown> = { time: new Date().toISOString(), lat, lon, ident: request.ident ?? null };
    try {
      const metar: Metar | undefined = request.ident
        ? await this.loader!.getMetar(request.ident)
        : await this.loader!.searchMetar(lat, lon);
      message.metar = metar ?? null;
      this.status.set(`running, last poll ${message.time}`);
      if (atAircraft) {
        this.airport.set(metar?.metarString ?? "No METAR near the aircraft");
      }
    } catch (e) {
      message.error = String(e);
      this.status.set(`running, last poll failed: ${e}`);
    }
    this.send(EVENT_METAR, message);
  }

  /** Listens to a CommBus event from SimConnect clients (now, or once connected). */
  public listen(event: string, callback: (args: string) => void, registered?: () => void): void {
    this.listeners.push([event, callback]);
    this.commBus?.on(event, callback);
    if (registered !== undefined) {
      this.onRegistered.push(registered);
    }
  }

  public send(event: string, data: unknown): void {
    this.commBus?.callSimConnect(event, JSON.stringify(data)).catch(() => undefined);
  }
}

const bridge = new WeatherBridge();
const settings = new SettingsModel((data) => bridge.send(EVENT_SETTINGS, data));
const news = new WhatsNewModel();
const report = new PerfReportModel((data) => bridge.send(EVENT_SETTINGS, data), () => ({
  version: VERSION,
  metar: bridge.airport.get(),
  weather: settings.weather.get(),
  altitudeFt: SimVar.GetSimVarValue("PLANE ALTITUDE", "feet") as number,
  aglFt: SimVar.GetSimVarValue("PLANE ALT ABOVE GROUND", "feet") as number,
  changed: settings.changedFromDefaults(),
  defaultPreset: settings.preset.get() === PRESET_DEFAULT && settings.presetSaved.get(),
}));
settings.onReport = (r) => report.onReport(r as AddonReport | undefined);
bridge.listen(EVENT_STATE, (args) => settings.onState(args), () => settings.requestState());
setInterval(() => {
  if (!settings.connected.get()) {
    settings.requestState();
  }
}, STATE_RETRY_MS);

const style = document.createElement("style");
// (the link's status and the version, at the end of the page)
style.textContent = SETTINGS_CSS + HOTKEYS_CSS + WHATS_NEW_CSS + PERF_REPORT_CSS + `
.atmo-link { padding-top: 24px; opacity: 0.6; font-size: 21px; }
`;
document.head.appendChild(style);

/** The mod's version, and the add-on's in the sim where it differs (a partial update). */
const versionLine = MappedSubject.create(
  ([connected, addon]) => (!connected || addon === VERSION ? `AtmosphereMod ${VERSION}`
    : `AtmosphereMod ${VERSION} (EFB app); the add-on in the sim: ${addon || "an older version"}`),
  settings.connected,
  settings.addonVersion,
);

class AtmoWeatherView extends AppView<RequiredProps<AppViewProps, "bus">> {
  private readonly root = FSComponent.createRef<HTMLDivElement>();
  private readonly narrow = Subject.create(false);

  public onAfterRender(node: VNode): void {
    super.onAfterRender(node);
    // the tablet's size and orientation (the EFB's settings)
    const panel = window.PanelInfo;
    panel?.size.width.sub(() => this.relayout());
    panel?.size.height.sub(() => this.relayout());
    panel?.orientation.sub(() => this.relayout());
    this.relayout();
  }

  public onOpen(): void {
    super.onOpen();
    settings.requestState();
    this.relayout();
  }

  public onResume(): void {
    super.onResume();
    settings.requestState();
    this.relayout();
  }

  /** The layout for the app's width: now, and again once the EFB has laid it out. */
  private relayout(): void {
    this.measure();
    setTimeout(() => this.measure(), RELAYOUT_MS);
  }

  private measure(): void {
    const width = this.root.getOrDefault()?.offsetWidth || window.PanelInfo?.size.width.get() || 0;
    if (width > 0) {
      this.narrow.set(width < NARROW_PX);
    }
  }

  public render(): VNode {
    return (
      <div ref={this.root} class={{ "atmo-app": true, "atmo-narrow": this.narrow }}>
        <SettingsView model={settings} metar={bridge.airport}>
          <div class="atmo-buttons">
            <TTButton key="Performance report" type="secondary" disabled={settings.connected.map((c) => !c)}
                      callback={() => report.show()} />
          </div>
          <div class="atmo-link">
            <div>METAR link: {bridge.status}</div>
            <div>{versionLine}</div>
          </div>
        </SettingsView>
        <WhatsNewView model={news} />
        <PerfReportView model={report} />
      </div>
    );
  }
}

class AtmoWeather extends App {
  public get name(): string {
    return "AtmosphereMod";
  }

  public get icon(): string {
    return `${BASE_URL}/Assets/app-icon.svg`;
  }

  // HOT: created and mounted when the EFB starts, so the bridge runs without
  // the app being opened; SLEEP keeps it alive when another app is shown.
  public BootMode = AppBootMode.HOT;
  public SuspendMode = AppSuspendMode.SLEEP;

  public async install(props: AppInstallProps): Promise<void> {
    bridge.start(props.bus);
  }

  public render(): TVNode<AtmoWeatherView> {
    return <AtmoWeatherView bus={this.bus} />;
  }
}

Efb.use(AtmoWeather);
