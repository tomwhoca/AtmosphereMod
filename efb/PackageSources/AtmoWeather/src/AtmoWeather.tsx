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
//   AtmosphereMod.Settings app -> add-on      {get}, {set: {T3: 4.5, ...}}, {enabled}, {reload}
//   AtmosphereMod.State    add-on -> app      {enabled, T, C, TE, weather} (addon/src/settings_bridge.h)

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
  TVNode,
} from "@efb/efb-api";
import { EventBus, FacilityLoader, FacilityRepository, FSComponent, Metar, Subject, VNode } from "@microsoft/msfs-sdk";

import { EVENT_SETTINGS, EVENT_STATE, SETTINGS_CSS, SettingsModel, SettingsView } from "./Settings";

declare const BASE_URL: string;

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
  public readonly lastMetar = Subject.create("(none yet)");
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
    const lat = request.lat ?? (SimVar.GetSimVarValue("PLANE LATITUDE", "degree latitude") as number);
    const lon = request.lon ?? (SimVar.GetSimVarValue("PLANE LONGITUDE", "degree longitude") as number);
    const message: Record<string, unknown> = { time: new Date().toISOString(), lat, lon, ident: request.ident ?? null };
    try {
      const metar: Metar | undefined = request.ident
        ? await this.loader!.getMetar(request.ident)
        : await this.loader!.searchMetar(lat, lon);
      message.metar = metar ?? null;
      this.lastMetar.set(metar ? metar.metarString : "no METAR returned");
      this.status.set(`running, last poll ${message.time}`);
    } catch (e) {
      message.error = String(e);
      this.lastMetar.set(`error: ${e}`);
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
bridge.listen(EVENT_STATE, (args) => settings.onState(args), () => settings.requestState());
setInterval(() => {
  if (!settings.connected.get()) {
    settings.requestState();
  }
}, STATE_RETRY_MS);

const style = document.createElement("style");
style.textContent = SETTINGS_CSS + `
.atmo-link { padding: 0 32px 104px; opacity: 0.6; font-size: 15px; }
.atmo-link pre { white-space: pre-wrap; font-family: monospace; margin: 4px 0 0; }
`;
document.head.appendChild(style);

class AtmoWeatherView extends AppView<RequiredProps<AppViewProps, "bus">> {
  public onOpen(): void {
    super.onOpen();
    settings.requestState();
  }

  public onResume(): void {
    super.onResume();
    settings.requestState();
  }

  public render(): VNode {
    return (
      <div style="height: 100%; display: flex; flex-direction: column;">
        <div style="flex: 1 1 auto; min-height: 0;">
          <SettingsView model={settings} />
        </div>
        <div class="atmo-link">
          METAR link: {bridge.status}
          <pre>{bridge.lastMetar}</pre>
        </div>
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
