// The sheets over the app: What's new (once per release that has news, and from Tools), the performance report
// (from Tools: measure, review what will be sent with the user's answers, send) and sharing a preset in Community
// Presets (from Presets). Their logic: src/whatsnew.ts, src/report.ts and src/community.ts.

import { changesFromDefault, CommunityApi } from "../community";
import { NAME_RULE, PRESET_NAME_MAX, presetChar, presetName } from "../presets";
import { IMPRESSION, LIMITED_BY, PHASE_TEXT, PerfReportModel, Step, STUTTERS, WEATHER } from "../report";
import { PREVIOUS, WHATS_NEW } from "../whatsnew";
import { aboutTile } from "./about";
import { button, choice, sheet, switchEl, textField } from "./controls";
import { h, Scope, Value } from "./dom";
import { icon } from "./icons";
import { sameName, Store } from "./model";

/** The author last typed when sharing, offered next time. */
const AUTHOR_KEY = "atmo-ui-author";
/** The add-on answers an export within this long, or doesn't have Community Presets. */
const EXPORT_WAIT_MS = 6000;

export interface Sheet {
  el: HTMLElement;
  body: HTMLElement;
}

export function whatsNewSheet(scope: Scope, close: () => void, openUrl: (url: string) => void): Sheet {
  const items = WHATS_NEW.items.map(([head, text]) =>
    h("div", { class: "news-item" }, h("div", { class: "news-head", text: head }), h("div", { class: "news-text", text })));
  const box = h("div", { style: "display: none" }, ...PREVIOUS.map((v) =>
    h("div", { class: "news-version" }, h("div", { class: "news-release", text: v.release }),
      ...v.items.map(([head, text]) => h("div", { class: "news-line" }, h("span", { class: "news-label", text: `${head}: ` }),
                                         text)))));
  const chevron = h("span", null, icon("down"));
  const toggle = h("div", { class: "more" }, h("span", { text: "Changes in previous versions" }), chevron);
  scope.listen(toggle, "click", () => {
    const open = box.style.display === "none";
    box.style.display = open ? "" : "none";
    chevron.innerHTML = "";
    chevron.appendChild(icon(open ? "up" : "down"));
  });
  // (it always ends with About This Mod and its two donation buttons: the user's wish)
  return sheet(scope, WHATS_NEW.title, close, ...items, toggle, box,
               h("div", { class: "tiles news-about" }, aboutTile(scope, openUrl)),
               h("div", { class: "actions end" }, button(scope, "Got it", "", close)));
}

export function reportSheet(scope: Scope, store: Store, model: PerfReportModel): Sheet {
  const at = (el: HTMLElement, ...steps: Step[]): HTMLElement => {
    scope.add(model.step.watch((s) => { el.style.display = steps.indexOf(s) >= 0 ? "" : "none"; }));
    return el;
  };
  const p = (text: string, cls = "news-text") => h("div", { class: cls, text });
  const label = (text: string) => h("div", { class: "q", text });

  const intro = at(h("div", null,
    p("This measures what AtmosphereMod costs on your PC and sends me the result, with your PC's and the sim's "
      + "settings, so I can see where the mod runs well and where it needs work."),
    h("ol", { class: "steps" },
      h("li", { text: "Find a view with clouds (most of the mod's cost is in them) and hold it still: pause the sim or "
                      + "slew. Keep other windows off the sim meanwhile. A frame-rate cap is fine: the GPU's time per "
                      + "frame shows the cost anyway." }),
      h("li", { text: "Tap Start. For about 36 seconds the mod switches itself on, off and on again while the frames "
                      + "are timed. Don't touch anything meanwhile; you can close the tablet." }),
      h("li", { text: "Check what will be sent, add how the mod runs for you, and tap Send. Nothing is sent before." })),
    h("div", { class: "actions" }, button(scope, "Start", "primary", () => model.start()),
      button(scope, "Close", "", () => model.close()))), "intro");

  const phase = p("");
  scope.add(model.phase.watch((ph) => { phase.textContent = PHASE_TEXT[ph] || "Starting"; }));
  const fill = h("div");
  scope.add(model.progress.watch((v) => { fill.style.width = `${Math.round(v * 100)}%`; }));
  const measuring = at(h("div", null, phase, h("div", { class: "progress" }, fill),
    h("div", { class: "actions" }, button(scope, "Stop", "", () => model.stop()))), "measuring");

  const summary = h("div", { class: "news-head" });
  scope.add(model.summary.watch((t) => { summary.textContent = t; }));
  const warning = h("div", { class: "note warn" });
  scope.add(model.warning.watch((t) => {
    warning.textContent = t;
    warning.style.display = t ? "" : "none";
  }));
  const details = h("div", { class: "details" });
  scope.add(model.details.watch((t) => { details.textContent = t; }));
  const sending = p("Sending ...");
  at(sending, "sending");
  const review = at(h("div", null, summary, warning,
    label("The weather in the view (from the METAR: change it if the view differs)"), choice(scope, WEATHER, model.weather),
    h("div", { class: "row" }, h("div", { class: "text" }, h("div", { class: "label", text: "Flying in VR" })),
      switchEl(scope, model.vr, () => model.vr.set(!model.vr.get()))),
    label("How does the mod run for you? (needed)"), choice(scope, IMPRESSION, model.impression),
    label("Stutters with the mod (optional)"), choice(scope, STUTTERS, model.stutters),
    label("The sim's FPS counter says it's limited by (optional)"), choice(scope, LIMITED_BY, model.limitedBy),
    h("div", { class: "fieldrow" }, textField(scope, store.host, { placeholder: "Anything else (optional)",
                                                                  value: model.comment })),
    label("Your username on GitHub or flightsim.to (optional). If I asked you for a report, it's how I find yours, "
          + "and I can ask you about it."),
    h("div", { class: "fieldrow" }, textField(scope, store.host, { placeholder: "Username", value: model.contact })),
    label("What will be sent, besides your answers above:"), details,
    p("It goes to a Google Sheet I keep for these reports. Google gets your IP address with it, as any website does, "
      + "but it isn't saved in the sheet. Apart from the username you type, no names or file paths are in it.",
      "news-text small"),
    at(h("div", { class: "actions" }, button(scope, "Send", "primary", () => void model.send()),
      button(scope, "Measure again", "", () => model.start()), button(scope, "Close", "", () => model.close())),
       "review"),
    sending), "review", "sending");

  const sent = at(h("div", null, p("Sent. Thank you!"),
    h("div", { class: "actions" }, button(scope, "Close", "primary", () => model.close()))), "sent");
  const failed = at(h("div", null,
    p("It couldn't be sent (why is below). Try again in a moment: the measurement is kept until you close this."),
    h("div", { class: "actions" }, button(scope, "Try again", "primary", () => void model.send()),
      button(scope, "Close", "", () => model.close()))), "failed");
  const note = h("div", { class: "note warn" });
  scope.add(model.note.watch((t) => {
    note.textContent = t;
    note.style.display = t ? "" : "none";
  }));
  return sheet(scope, "Performance Report", () => model.close(), intro, measuring, review, sent, failed, note);
}

/** Shares one of the user's presets in Community Presets: its look from the add-on (exportPreset), what it changes
 * from the mod's defaults, a name, the author (required, remembered) and a description; sent on Send. */
export function shareSheet(scope: Scope, store: Store, preset: string, api: CommunityApi, close: () => void): Sheet {
  type ShareStep = "reading" | "missing" | "ready" | "sending" | "sent";
  const step = new Value<ShareStep>("reading");
  const at = (el: HTMLElement, ...steps: ShareStep[]): HTMLElement => {
    scope.add(step.watch((s) => { el.style.display = steps.indexOf(s) >= 0 ? "" : "none"; }));
    return el;
  };
  const name = new Value(preset);
  const author = new Value(store.host.load(AUTHOR_KEY) || "");
  const description = new Value("");
  const note = h("div", { class: "note warn" });
  const showNote = (t: string) => {
    note.textContent = t;
    note.style.display = t ? "" : "none";
  };
  showNote("");

  // the preset's look, as the add-on reads it from the preset's file (an answer newer than any before)
  const before = store.state.get() && store.state.get()!.presetExport;
  const n0 = before ? before.n : 0;
  let values: Record<string, number> | undefined;
  const changes = h("div", { class: "details" });
  scope.add(store.state.watch((s) => {
    const e = s && s.presetExport;
    if (step.get() === "reading" && e && e.n > n0 && sameName(e.name, preset)) {
      values = e.values;
      const c = changesFromDefault(values);
      changes.textContent = c.lines.length === 0 && c.hidden === 0 ? "The same as the mod's defaults."
        : c.lines.concat(c.hidden ? [`And ${c.hidden} the app doesn't show.`] : []).join("\n");
      step.set("ready");
    }
  }));
  store.exportPreset(preset);
  const timer = setTimeout(() => step.get() === "reading" && step.set("missing"), EXPORT_WAIT_MS);
  scope.add(() => clearTimeout(timer));

  const send = async () => {
    showNote("");
    const n = presetName(name.get());
    if (n === "") {
      showNote(NAME_RULE);
      return;
    }
    if (author.get().trim() === "") {
      showNote("Your name or username, please: it's shown with the preset.");
      return;
    }
    store.host.save(AUTHOR_KEY, author.get().trim());
    step.set("sending");
    const r = await api.share({ name: n, author: author.get().trim(), description: description.get().trim(),
                                version: store.host.version, values: values || {} });
    if (r.ok) {
      step.set("sent");
      store.shared.set(store.shared.get() + 1);
    } else {
      step.set("ready");
      showNote(r.error.charAt(0).toUpperCase() + r.error.slice(1) + ".");
    }
  };
  const label = (text: string) => h("div", { class: "q", text });
  const form = at(h("div", null,
    h("div", { class: "news-text", text: `Share “${preset}” with other pilots. It's public at once: anyone can `
                                         + "add it to their presets." }),
    label("Name"),
    h("div", { class: "fieldrow" }, textField(scope, store.host, { placeholder: "The preset's name", value: name,
                                                                  accepts: presetChar, max: PRESET_NAME_MAX })),
    label("Author, shown with it (your name or a username, e.g. on flightsim.to)"),
    h("div", { class: "fieldrow" }, textField(scope, store.host, { placeholder: "Author", value: author, max: 40 })),
    label("Description (optional)"),
    h("div", { class: "fieldrow" }, textField(scope, store.host, { placeholder: "What it's for, in a sentence",
                                                                  value: description, max: 300 })),
    label("What it changes from the mod's defaults (as saved)"), changes,
    h("div", { class: "news-text small", text: "Sent: this preset's haze, sky and cloud settings, the name, author and "
                                               + "description above, and the mod's version. Your weather radar settings "
                                               + "stay with you, and your IP address isn't stored." }),
    at(h("div", { class: "actions" }, button(scope, "Share", "primary", () => void send()),
      button(scope, "Close", "", close)), "ready"),
    at(h("div", { class: "news-text", text: "Sharing ..." }), "sending")), "ready", "sending");
  const reading = at(h("div", { class: "news-text", text: "Reading the preset ..." }), "reading");
  const missing = at(h("div", null,
    h("div", { class: "news-text", text: "The add-on in the sim didn't send the preset: sharing needs the add-on of this "
                                         + "version. Install the mod again." }),
    h("div", { class: "actions" }, button(scope, "Close", "", close))), "missing");
  const sent = at(h("div", null,
    h("div", { class: "news-text", text: "Shared: it's in Community Presets now. Thank you!" }),
    h("div", { class: "actions" }, button(scope, "Close", "primary", close))), "sent");
  return sheet(scope, "Share a Preset", close, reading, missing, form, sent, note);
}
