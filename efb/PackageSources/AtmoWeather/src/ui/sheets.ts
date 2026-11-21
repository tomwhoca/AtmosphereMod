// The sheets over the app: What's new (once per release that has news, and from Tools) and the performance report
// (from Tools: measure, review what will be sent with the user's answers, send). Their logic: src/whatsnew.ts and
// src/report.ts.

import { IMPRESSION, LIMITED_BY, PHASE_TEXT, PerfReportModel, Step, STUTTERS, WEATHER } from "../report";
import { PREVIOUS, WHATS_NEW } from "../whatsnew";
import { button, choice, sheet, switchEl, textField } from "./controls";
import { h, Scope } from "./dom";
import { icon } from "./icons";
import { Store } from "./model";

export interface Sheet {
  el: HTMLElement;
  body: HTMLElement;
}

export function whatsNewSheet(scope: Scope, close: () => void): Sheet {
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
  return sheet(scope, WHATS_NEW.title, close, ...items, toggle, box,
               h("div", { class: "actions end" }, button(scope, "Got it", "primary", close)));
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
  return sheet(scope, "Performance report", () => model.close(), intro, measuring, review, sent, failed, note);
}
