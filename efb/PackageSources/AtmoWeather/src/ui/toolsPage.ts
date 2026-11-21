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

  const reloadKey = h("span", { class: "muted" });
  scope.add(store.state.watch((s) => {
    const k = s && s.hotkeys ? s.hotkeys.reload : "Ctrl+Shift+F10";
    reloadKey.textContent = k ? `(also ${k})` : "";
  }));
  const allKeys = TABS.reduce<string[]>((all, t) => all.concat(t.settings.map((s) => s.key)), []);
  const file = section("Settings", "sliders",
    h("div", { class: "muted", text: "The settings live in tunables.ini. Read it again after editing it by hand; it "
                                     + "also reloads the mod's shaders." }),
    h("div", { class: "actions" }, button(scope, "Reload tunables.ini", "", () => store.reload(), live), reloadKey),
    h("div", { class: "actions" },
      twoTaps(scope, "Reset all settings", "Every setting to its default? Tap again", "danger",
              () => store.reset(allKeys), () => true, live)));

  const about = h("table", { class: "kv" });
  const fill = () => {
    about.innerHTML = "";
    const s = store.state.get();
    const row = (k: string, v: string) => about.appendChild(h("tr", null, h("td", { text: k }), h("td", { text: v })));
    row("EFB app", store.host.version);
    row("Add-on in the sim", s ? s.version || "an older one" : "not connected");
    row("METAR link", store.link.get() || "-");
  };
  scope.add(store.state.watch(fill));
  scope.add(store.link.watch(fill));
  return h("div", null, h("div", { class: "tiles" }, report, news, bug, appearance),
           h("div", { class: "cols" }, file, section("About", "info", about)));
}
