// AtmosphereMod's EFB app: sends the sim's own METARs to SimConnect clients over CommBus, and shows the mod's
// settings in a UI of its own (src/ui, its own bundle AtmoUI.js). This shell keeps only what the UI can't: the
// app's place in the EFB, CommBus, and the METAR link (the SDK's FacilityLoader). The UI gets them as a SimBridge
// (ui/sim.ts), which makes it the Host; the UI module can then be reloaded in the running sim (developer builds).
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
//                                             {loadPreset, discard}, {deletePreset}, {measure} (src/report.ts), ...
//   AtmosphereMod.State    add-on -> app      {enabled, T, C, TE, weather, version, presets, preset, presetSaved,
//                                             presetNote, report, ...} (addon/src/settings_bridge.h)

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

import type { SimBridge } from "./ui/sim";

declare const BASE_URL: string;
/** The mod's version (build.js, from addon/CMakeLists.txt). */
declare const VERSION: string;
/** Developer builds (build_efb.py --dev): window.__atmoUiReload, for tools/efb_reload.py. Never in a release. */
declare const DEV: boolean;

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
  /** Each event's listeners: CommBus gets one handler per event, so a listener can go (the UI reloads). */
  private readonly listeners = new Map<string, ((args: string) => void)[]>();

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
    this.commBus = register(() => this.send(EVENT_STATUS, { state: "registered" }));
    this.commBus.on(EVENT_REQUEST, (args) => void this.poll(parseRequest(args)));
    this.listeners.forEach((_, event) => this.handle(event));
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

  /** The one CommBus handler of an event: its listeners in turn. */
  private handle(event: string): void {
    this.commBus?.on(event, (args) => (this.listeners.get(event) ?? []).slice().forEach((cb) => cb(args)));
  }

  /** Listens to a CommBus event from SimConnect clients (now, or once connected). Returns the unsubscribe. */
  public listen(event: string, callback: (args: string) => void): () => void {
    let list = this.listeners.get(event);
    if (list === undefined) {
      list = [];
      this.listeners.set(event, list);
      this.handle(event);
    }
    list.push(callback);
    return () => {
      const i = list!.indexOf(callback);
      if (i >= 0) {
        list!.splice(i, 1);
      }
    };
  }

  public send(event: string, data: unknown): void {
    this.commBus?.callSimConnect(event, JSON.stringify(data)).catch(() => undefined);
  }
}

const bridge = new WeatherBridge();

/** The UI module's bundle (src/ui/main.ts): it sets window.AtmoUI. */
interface AtmoUiModule {
  mountInSim(root: HTMLElement, bridge: SimBridge): () => void;
  build: string;
}
const UI_URL = `${BASE_URL}/AtmoUI.js`;

class AtmoWeatherView extends AppView<RequiredProps<AppViewProps, "bus">> {
  private readonly root = FSComponent.createRef<HTMLDivElement>();
  private readonly relayouts: (() => void)[] = [];
  private unmountUi?: () => void;

  /** What the UI gets of the shell. */
  private readonly simBridge: SimBridge = {
    version: VERSION,
    send: (event, data) => bridge.send(event, data),
    listen: (event, callback) => bridge.listen(event, callback),
    onAirport: (callback) => {
      const sub = bridge.airport.sub(callback, true);
      return () => sub.destroy();
    },
    onStatus: (callback) => {
      const sub = bridge.status.sub(callback, true);
      return () => sub.destroy();
    },
    onRelayout: (callback) => {
      this.relayouts.push(callback);
      return () => {
        const i = this.relayouts.indexOf(callback);
        if (i >= 0) {
          this.relayouts.splice(i, 1);
        }
      };
    },
  };

  /** The UI into its root: loaded once, and again (re-evaluated) by the developers' reload. */
  private async startUi(): Promise<void> {
    if (!(window as any).AtmoUI) {
      await loadScript(UI_URL);
    }
    this.mountUi();
    if (DEV) {
      (window as any).__atmoUiReload = async (): Promise<string> => {
        const text = await (await fetch(`${UI_URL}?t=${Date.now()}`)).text();
        (0, eval)(text);
        this.mountUi();
        return `reloaded AtmoUI ${(window as any).AtmoUI.build}, ${text.length} chars`;
      };
    }
  }

  private mountUi(): void {
    const ui = (window as any).AtmoUI as AtmoUiModule | undefined;
    const root = this.root.getOrDefault();
    if (!ui || !root) {
      return;
    }
    if (this.unmountUi) {
      this.unmountUi();
    }
    this.unmountUi = ui.mountInSim(root, this.simBridge);
  }

  /** The tablet changed, or the app was shown again: the root to the app's area, then the UI told. */
  private relayout(): void {
    const panel = window.PanelInfo;
    const root = this.root.getOrDefault();
    if (root && panel) {
      root.style.width = `${panel.size.width.get()}px`;
      root.style.height = `${panel.size.height.get()}px`;
    }
    this.relayouts.slice().forEach((cb) => cb());
  }

  public onAfterRender(node: VNode): void {
    super.onAfterRender(node);
    const panel = window.PanelInfo;
    if (panel) {
      panel.size.width.sub(() => this.relayout());
      panel.size.height.sub(() => this.relayout());
      panel.orientation.sub(() => this.relayout());
    }
    this.relayout();
    this.startUi().catch((e) => console.error(`AtmosphereMod: the UI didn't load: ${e}`));
  }

  public onOpen(): void {
    super.onOpen();
    this.relayout();
  }

  public onResume(): void {
    super.onResume();
    this.relayout();
  }

  public render(): VNode {
    return <div ref={this.root} class="atmo-ui-root" style="position: relative; width: 100%; height: 100%" />;
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
