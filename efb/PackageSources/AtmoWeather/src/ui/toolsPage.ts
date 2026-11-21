// The Tools page: the look (dark or light), the performance report and What's new (each opens its sheet), a bug
// report (GitHub's new issue form in the PC's browser), the settings file (read it again; every setting back to the
// default), and what's installed.

import { TABS } from "../specs";
import { WHATS_NEW } from "../whatsnew";
import { button, section, tileLabel, twoTaps } from "./controls";
import { h, Scope, Value } from "./dom";
import { bugReportUrl } from "./update";
import { Store } from "./model";

export type SheetName = "" | "news" | "report" | "share";

export function toolsPage(scope: Scope, store: Store, theme: Value<string>, open: (s: SheetName) => void): HTMLElement {
  const live = new Value(false);
  scope.add(store.state.watch((s) => live.set(!!s)));

  const look = (name: string, label: string) => {
    const b = button(scope, label, "", () => theme.set(name));
    scope.add(theme.watch((t) => { b.className = `btn${t === name ? " chosen" : ""}`; }));
    return b;
  };
  const appearance = h("div", { class: "tile sun half" }, tileLabel("contrast", "Appearance"),
    h("div", { class: "actions" }, look("auto", "Auto"), look("dark", "Dark"), look("light", "Light")),
    h("p", { class: "tp", text: "Auto: light by day in the sim, dark at dawn, dusk and night." }));

  const report = h("div", { class: "tile sky half" }, tileLabel("tools", "Performance Report"),
    h("div", { class: "big", text: "~1 minute" }),
    h("p", { class: "tp", text: "Measures what the mod costs on your PC, with it and without, and if you like sends "
                                + "me the result. Best in a view with clouds." }),
    h("div", { class: "actions" }, button(scope, "Open", "primary", () => open("report"), live)));

  const news = h("div", { class: "tile dusk half" }, tileLabel("info", "What's New"),
    h("div", { class: "big", text: `Version ${WHATS_NEW.release}` }),
    h("p", { class: "tp", text: "What this version brings, and the changes in earlier ones." }),
    h("div", { class: "actions" }, button(scope, "Open", "primary", () => open("news"))));

  // (a bug report: GitHub's form, the versions filled in; GitHub asks for an account)
  const bug = h("div", { class: "tile aurora half" }, tileLabel("bug", "Report a Bug"),
    h("div", { class: "big", text: "On GitHub" }),
    h("p", { class: "tp", text: "Opens a new issue in your PC's browser, with the mod's versions filled in. You need "
                                + "a GitHub account. How to see it, and a screenshot, help most." }),
    h("div", { class: "actions" }, button(scope, "Open an issue", "primary", () => {
      const s = store.state.get();
      store.host.openUrl(bugReportUrl({ app: store.host.version, addon: s ? s.version : undefined,
                                        modOn: s ? !!s.enabled : undefined }));
    })));

  // ---- the settings file, and what's installed: rows like the Hotkeys page's, a name with a line under it, and the
  // button or the value at the right
  const line = (name: string, desc: HTMLElement | null, end: HTMLElement) =>
    h("div", { class: "row line" }, h("div", { class: "text" }, h("div", { class: "label", text: name }), desc), end);
  const reloadDesc = h("div", { class: "rdesc" });
  scope.add(store.state.watch((s) => {
    const k = s && s.hotkeys ? s.hotkeys.reload : "Ctrl+Shift+F10";
    reloadDesc.textContent = `After editing it by hand.${k ? ` Key: ${k}` : ""}`;
  }));
  const allKeys = TABS.reduce<string[]>((all, t) => all.concat(t.settings.map((s) => s.key)), []);
  const file = section("Settings", "sliders",
    line("Reload tunables.ini", reloadDesc, button(scope, "Reload", "small", () => store.reload(), live)),
    line("Reset all settings", h("div", { class: "rdesc", text: "Every setting back to its default." }),
         twoTaps(scope, "Reset", "Tap again", "small danger", () => store.reset(allKeys), () => true, live)));

  const appVal = h("div", { class: "val", text: store.host.version });
  const addonVal = h("div", { class: "val" });
  scope.add(store.state.watch((s) => {
    addonVal.textContent = !s ? "Not connected" : s.version || "An older one";
    addonVal.className = `val${s && s.version ? "" : " none"}`;
  }));
  // (the METAR link as on Home: live while it polls, failed, or not started)
  const linkVal = h("div", { class: "val" });
  scope.add(store.link.watch((l) => {
    const polling = /^running/.test(l) && !/failed/.test(l);
    const failed = /failed: /.test(l) || /^CommBus unavailable/.test(l);
    linkVal.innerHTML = "";
    if (polling) {
      linkVal.appendChild(h("span", { class: "dot" }));
    }
    linkVal.appendChild(document.createTextNode(polling ? "Live" : failed ? "Failed" : "Not started"));
    linkVal.className = `val${polling ? "" : failed ? " bad" : " none"}`;
  }));
  const about = section("About", "info",
    line("EFB app", null, appVal), line("Add-on", null, addonVal), line("METAR link", null, linkVal));
  return h("div", null, h("div", { class: "tiles" }, report, news, bug, appearance),
           h("div", { class: "cols" }, file, about));
}
